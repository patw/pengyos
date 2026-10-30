"""Fetch a user's SSH public keys from GitHub (``https://github.com/<user>.keys``).

At a bare console nobody can paste, and typing a 100-character key by hand
is no way to start, so the installer can fetch keys instead, the way
Ubuntu Server's ``ssh-import-id gh:<user>`` does. The live stick brings up any
wired NIC with DHCP, so this works as soon as a cable is plugged in.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Callable

from pengyos_install import image as engine

__all__ = ["KeyFetchError", "github_keys", "valid_github_user"]

# GitHub logins: alphanumerics and single hyphens, no leading hyphen, max 39.
_GITHUB_USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
_MAX_BYTES = 64 * 1024
TIMEOUT = 10


class KeyFetchError(Exception):
    """The keys could not be fetched; the message says why, for the user."""


def valid_github_user(name: str) -> bool:
    return bool(_GITHUB_USER_RE.match(name))


def github_keys(user: str, *, opener: Callable = urllib.request.urlopen,
                timeout: float = TIMEOUT) -> str:
    """The user's public keys as authorized_keys lines, each tagged
    ``<user>@github`` so ``authorized_keys`` says where it came from."""
    if not valid_github_user(user):
        raise KeyFetchError(f"{user!r} is not a GitHub username")
    url = f"https://github.com/{user}.keys"
    try:
        with opener(url, timeout=timeout) as resp:
            text = resp.read(_MAX_BYTES).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise KeyFetchError(f"GitHub has no user named {user!r}") from None
        raise KeyFetchError(f"github.com answered {exc.code}; try again or paste a key") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise KeyFetchError(
            f"could not reach github.com ({reason}); is a network cable plugged in?"
        ) from None
    keys = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        key = f"{parts[0]} {parts[1]} {user}@github"
        try:
            engine.ProvisionAnswers("h", "u", "xxxxxx", authorized_keys=key).validate()
        except engine.ImageInstallError:
            continue
        keys.append(key)
    if not keys:
        raise KeyFetchError(f"github.com/{user} has no SSH keys; add one there or paste a key")
    return "\n".join(keys)
