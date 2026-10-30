"""Import SSH public keys from somewhere else, so nobody has to type one.

At a bare console nothing can be pasted, and the installed system's sshd is
key-only, so the SSH page imports keys from one of three sources:

* ``patw`` or ``gh:patw`` — the user's keys on GitHub
  (``https://github.com/<user>.keys``, as ``ssh-import-id gh:<user>`` does);
* ``https://...`` or ``http://...`` — any authorized_keys-style file, e.g. on a NAS;
* ``user@host`` or ``user@host:port`` — another machine that takes a password
  over SSH: its ``~/.ssh/authorized_keys`` is copied, so whoever can log in
  there can log in here.

The live stick brings up any wired NIC with DHCP, so all three work as soon as
a cable is plugged in. Only lines the engine accepts as keys are kept; a line
with options (``from="..." ssh-ed25519 ...``) is skipped and counted.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pengyos_install import image as engine

__all__ = ["KeyFetchError", "PasswordRefused", "KeySource", "Imported", "parse_source", "fetch_keys",
           "valid_github_user"]

# GitHub logins: alphanumerics and single hyphens, no leading hyphen, max 39.
_GITHUB_USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
_SSH_RE = re.compile(r"^([A-Za-z0-9._][A-Za-z0-9._-]{0,31})@"
                     r"(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252}))(?::(\d{1,5}))?$")
_URL_RE = re.compile(r"^https?://[^\s\x00-\x1f]+$")
_MAX_BYTES = 64 * 1024
TIMEOUT = 15


class KeyFetchError(Exception):
    """The keys could not be imported; the message says why, for the user."""


class PasswordRefused(KeyFetchError):
    """``user@host`` turned the password down."""


@dataclass(frozen=True)
class KeySource:
    kind: str  # "github" | "url" | "ssh"
    target: str  # the GitHub user, the URL, or the host
    user: str = ""
    port: int = 22

    @property
    def label(self) -> str:
        if self.kind == "github":
            return f"github.com/{self.target}"
        if self.kind == "ssh":
            return f"{self.user}@{self.target}" + (f":{self.port}" if self.port != 22 else "")
        return self.target

    @property
    def needs_password(self) -> bool:
        return self.kind == "ssh"


@dataclass(frozen=True)
class Imported:
    keys: str  # authorized_keys lines
    skipped: int = 0  # lines that were not plain keys (options, junk)

    @property
    def count(self) -> int:
        return len(self.keys.splitlines())


def valid_github_user(name: str) -> bool:
    return bool(_GITHUB_USER_RE.match(name))


def parse_source(text: str) -> KeySource | None:
    """What the user typed in the "Import keys from" field, or None."""
    text = text.strip()
    if _URL_RE.match(text):
        return KeySource("url", text)
    m = _SSH_RE.match(text)
    if m:
        port = int(m.group(3) or 22)
        return KeySource("ssh", m.group(2), user=m.group(1), port=port) \
            if 0 < port < 65536 else None
    name = text[3:] if text.lower().startswith("gh:") else text
    return KeySource("github", name) if valid_github_user(name) else None


def _is_key(line: str) -> bool:
    try:
        engine.ProvisionAnswers("h", "u", "xxxxxx", authorized_keys=line).validate()
    except engine.ImageInstallError:
        return False
    return True


def _keep_keys(text: str, *, tag: str = "") -> Imported:
    keys, skipped = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if tag:  # GitHub sends bare "type base64": say where the key came from
            parts = line.split()
            line = f"{parts[0]} {parts[1]} {tag}" if len(parts) >= 2 else line
        if _is_key(line):
            keys.append(line)
        else:
            skipped += 1
    return Imported("\n".join(keys), skipped)


def _download(url: str, label: str, opener: Callable, timeout: float) -> str:
    try:
        with opener(url, timeout=timeout) as resp:
            return resp.read(_MAX_BYTES).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise KeyFetchError(f"{label}: not found (404)") from None
        raise KeyFetchError(f"{label} answered {exc.code}; try again or paste a key") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise KeyFetchError(
            f"could not reach {label} ({reason}); is this machine on the network?"
        ) from None


def _github(src: KeySource, opener: Callable, timeout: float) -> Imported:
    try:
        text = _download(f"https://github.com/{src.target}.keys", "github.com", opener, timeout)
    except KeyFetchError as exc:
        if "(404)" in str(exc):
            raise KeyFetchError(f"GitHub has no user named {src.target!r}") from None
        raise
    got = _keep_keys(text, tag=f"{src.target}@github")
    if not got.keys:
        raise KeyFetchError(f"github.com/{src.target} has no SSH keys; add one there or paste a key")
    return got


def _url(src: KeySource, opener: Callable, timeout: float) -> Imported:
    got = _keep_keys(_download(src.target, src.target, opener, timeout))
    if not got.keys:
        raise KeyFetchError(f"{src.target} has no SSH public keys in it")
    return got


#: Prints the password for ssh (SSH_ASKPASS); it reads it from the environment
#: so it is never written to a file.
_ASKPASS = '#!/bin/sh\nprintf "%s\\n" "$PENGYOS_KEYS_PASSWORD"\n'


def _ssh(src: KeySource, password: str, run: Callable, timeout: float) -> Imported:
    with tempfile.TemporaryDirectory(prefix="pengyos-keys-") as tmp:
        askpass = Path(tmp) / "askpass"
        askpass.write_text(_ASKPASS)
        askpass.chmod(0o700)
        env = dict(os.environ, SSH_ASKPASS=str(askpass), SSH_ASKPASS_REQUIRE="force",
                   PENGYOS_KEYS_PASSWORD=password)
        argv = [
            "ssh", "-p", str(src.port),
            # A machine on the LAN seen for the first time: trust it for this one
            # copy (the known_hosts is thrown away with the temp dir).
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"UserKnownHostsFile={tmp}/known_hosts",
            "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "PubkeyAuthentication=no",
            "-o", "PreferredAuthentications=keyboard-interactive,password",
            "-o", "NumberOfPasswordPrompts=1",
            "-o", "ConnectTimeout=10",
            "-o", "LogLevel=ERROR",
            f"{src.user}@{src.target.strip('[]')}",
            "cat .ssh/authorized_keys",
        ]
        try:
            # A new session: no controlling terminal, so ssh asks SSH_ASKPASS and
            # never draws on the installer's screen.
            r = run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    timeout=timeout, start_new_session=True)
        except FileNotFoundError:
            raise KeyFetchError("ssh is not available on this installer") from None
        except subprocess.TimeoutExpired:
            raise KeyFetchError(f"{src.target} did not answer; is this machine on the network?") \
                from None
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        low = err.lower()
        if "permission denied" in low:
            raise PasswordRefused(f"{src.label} refused the password")
        if "could not resolve" in low:
            raise KeyFetchError(f"cannot find {src.target} on the network; try its IP address")
        if "connection refused" in low:
            raise KeyFetchError(f"{src.target} does not accept SSH connections")
        if any(s in low for s in ("timed out", "no route", "unreachable")):
            raise KeyFetchError(f"could not reach {src.target}; is this machine on the network?")
        if "no such file" in low:
            raise KeyFetchError(f"{src.label} has no ~/.ssh/authorized_keys")
        last = err.splitlines()[-1] if err else f"exit {r.returncode}"
        raise KeyFetchError(f"ssh to {src.label} failed: {last}")
    got = _keep_keys(r.stdout)
    if not got.keys:
        raise KeyFetchError(f"{src.label}'s ~/.ssh/authorized_keys has no keys in it")
    return got


def fetch_keys(src: KeySource, password: str = "", *,
               opener: Callable = urllib.request.urlopen,
               run: Callable = subprocess.run, timeout: float = TIMEOUT) -> Imported:
    """Import the keys *src* names (*password* is for ``user@host`` only)."""
    if src.kind == "github":
        return _github(src, opener, timeout)
    if src.kind == "url":
        return _url(src, opener, timeout)
    return _ssh(src, password, run, timeout)
