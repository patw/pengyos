"""Importing SSH keys (pengyos_install_tui.keys): GitHub and URLs with a fake
opener, user@host with a fake subprocess.run."""

from __future__ import annotations

import io
import os
import subprocess
import urllib.error

import pytest

from pengyos_install_tui.keys import (
    KeyFetchError, KeySource, PasswordRefused, fetch_keys, parse_source, valid_github_user,
)


class Opener:
    def __init__(self, body: bytes = b"", error: Exception | None = None):
        self.body, self.error, self.urls = body, error, []

    def __call__(self, url, timeout):
        self.urls.append(url)
        if self.error:
            raise self.error
        return io.BytesIO(self.body)


class Run:
    """subprocess.run stand-in that also runs the askpass helper, the way ssh
    would, to prove the password reaches it."""

    def __init__(self, rc=0, stdout="", stderr="", error=None):
        self.rc, self.stdout, self.stderr, self.error = rc, stdout, stderr, error
        self.argv = self.kw = self.askpass_said = None

    def __call__(self, argv, **kw):
        self.argv, self.kw = argv, kw
        if self.error:
            raise self.error
        env = kw["env"]
        self.askpass_said = subprocess.run([env["SSH_ASKPASS"]], env=env, capture_output=True,
                                           text=True).stdout
        return subprocess.CompletedProcess(argv, self.rc, self.stdout, self.stderr)


# ------------------------------------------------------------- parse_source
@pytest.mark.parametrize("text,kind,label", [
    ("patw", "github", "github.com/patw"),
    ("gh:patw", "github", "github.com/patw"),
    ("GH:Pat-W2", "github", "github.com/Pat-W2"),
    ("https://nas.lan/keys.txt", "url", "https://nas.lan/keys.txt"),
    ("http://10.0.0.5:8080/k", "url", "http://10.0.0.5:8080/k"),
    ("pat@behoserver", "ssh", "pat@behoserver"),
    ("pat@192.168.1.10", "ssh", "pat@192.168.1.10"),
    ("pat@behoserver.lan:2222", "ssh", "pat@behoserver.lan:2222"),
    ("pat@[fe80::1]", "ssh", "pat@[fe80::1]"),
])
def test_parse_source(text, kind, label):
    src = parse_source(text)
    assert src.kind == kind and src.label == label
    assert src.needs_password is (kind == "ssh")


@pytest.mark.parametrize("text", [
    "", "-pat", "pa--t", "x" * 40, "ftp://nas/keys", "https://a b", "pat@", "@host",
    "pat@host:0", "pat@host:99999", "pat@-host", "pat@host;rm -rf", "a b",
])
def test_parse_source_rejects(text):
    assert parse_source(text) is None


@pytest.mark.parametrize("name,ok", [
    ("patw", True), ("a", True), ("Pat-W2", True), ("x" * 39, True),
    ("x" * 40, False), ("-pat", False), ("pat-", False), ("pa--t", False),
    ("pat w", False), ("pat/../x", False), ("", False),
])
def test_valid_github_user(name, ok):
    assert valid_github_user(name) is ok


# ------------------------------------------------------------------ GitHub
def test_github_keys_are_fetched_and_tagged():
    op = Opener(b"ssh-ed25519 AAAAC3Nza1\nssh-rsa AAAAB3Nza2\n\n")
    got = fetch_keys(parse_source("patw"), opener=op)
    assert op.urls == ["https://github.com/patw.keys"]
    assert got.keys.splitlines() == ["ssh-ed25519 AAAAC3Nza1 patw@github",
                                     "ssh-rsa AAAAB3Nza2 patw@github"]
    assert got.count == 2 and got.skipped == 0


def test_github_no_keys():
    with pytest.raises(KeyFetchError, match="has no SSH keys"):
        fetch_keys(parse_source("patw"), opener=Opener(b"\n"))


def test_github_unknown_user():
    err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    with pytest.raises(KeyFetchError, match="no user named 'nobody'"):
        fetch_keys(parse_source("nobody"), opener=Opener(error=err))


def test_github_server_error():
    err = urllib.error.HTTPError("u", 503, "Busy", {}, None)
    with pytest.raises(KeyFetchError, match="answered 503"):
        fetch_keys(parse_source("patw"), opener=Opener(error=err))


def test_no_network_says_check_the_cable():
    err = urllib.error.URLError(OSError("Network is unreachable"))
    with pytest.raises(KeyFetchError, match="network cable"):
        fetch_keys(parse_source("patw"), opener=Opener(error=err))


# --------------------------------------------------------------------- URL
def test_url_keeps_keys_and_their_comments_and_counts_the_rest():
    body = (b"# my keys\n"
            b"ssh-ed25519 AAAA1 pat@laptop\n"
            b'from="10.0.0.0/8" ssh-ed25519 AAAA2 pat@ops\n'
            b"<html>\n"
            b"ssh-rsa AAAA3\n")
    got = fetch_keys(parse_source("http://nas.lan/keys"), opener=Opener(body))
    assert got.keys.splitlines() == ["ssh-ed25519 AAAA1 pat@laptop", "ssh-rsa AAAA3"]
    assert got.skipped == 2


def test_url_404_and_empty():
    err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    with pytest.raises(KeyFetchError, match=r"not found \(404\)"):
        fetch_keys(parse_source("https://nas/k"), opener=Opener(error=err))
    with pytest.raises(KeyFetchError, match="no SSH public keys"):
        fetch_keys(parse_source("https://nas/k"), opener=Opener(b"hello\n"))


# ---------------------------------------------------------------- user@host
AUTH_KEYS = "ssh-ed25519 AAAA1 pat@laptop\ncommand=\"x\" ssh-ed25519 AAAA2 bot\nssh-rsa AAAA3 pat@desk\n"


def test_ssh_copies_authorized_keys_with_the_password():
    run = Run(stdout=AUTH_KEYS)
    got = fetch_keys(parse_source("pat@behoserver:2222"), "s3cret pass", run=run)
    assert got.keys.splitlines() == ["ssh-ed25519 AAAA1 pat@laptop", "ssh-rsa AAAA3 pat@desk"]
    assert got.skipped == 1
    argv, kw = run.argv, run.kw
    assert argv[:3] == ["ssh", "-p", "2222"]
    assert argv[-2:] == ["pat@behoserver", "cat .ssh/authorized_keys"]
    assert "PubkeyAuthentication=no" in argv and "StrictHostKeyChecking=accept-new" in argv
    # The password goes to ssh only through SSH_ASKPASS, never on the command line,
    # and ssh runs detached from the installer's terminal.
    assert not any("s3cret" in a for a in argv)
    assert kw["env"]["SSH_ASKPASS_REQUIRE"] == "force"
    assert run.askpass_said == "s3cret pass\n"
    assert kw["start_new_session"] is True and kw["stdin"] == subprocess.DEVNULL
    # The askpass helper and the throwaway known_hosts are gone afterwards.
    assert not os.path.exists(kw["env"]["SSH_ASKPASS"])


def test_ssh_ipv6_host_is_passed_bare():
    run = Run(stdout=AUTH_KEYS)
    fetch_keys(parse_source("pat@[fe80::1]"), "pw", run=run)
    assert run.argv[-2] == "pat@fe80::1"


@pytest.mark.parametrize("stderr,exc,needle", [
    ("pat@behoserver: Permission denied (publickey,password).", PasswordRefused,
     "refused the password"),
    ("ssh: Could not resolve hostname behoserver: Name or service not known", KeyFetchError,
     "cannot find behoserver"),
    ("ssh: connect to host behoserver port 22: Connection refused", KeyFetchError,
     "does not accept SSH"),
    ("ssh: connect to host behoserver port 22: Connection timed out", KeyFetchError,
     "network cable"),
    ("cat: .ssh/authorized_keys: No such file or directory", KeyFetchError,
     "has no ~/.ssh/authorized_keys"),
    ("something odd", KeyFetchError, "failed: something odd"),
])
def test_ssh_errors_are_explained(stderr, exc, needle):
    with pytest.raises(exc, match=needle):
        fetch_keys(parse_source("pat@behoserver"), "pw", run=Run(rc=255, stderr=stderr))


def test_ssh_empty_authorized_keys():
    with pytest.raises(KeyFetchError, match="has no keys in it"):
        fetch_keys(parse_source("pat@behoserver"), "pw", run=Run(stdout="# nothing\n"))


def test_ssh_timeout_and_missing_ssh():
    with pytest.raises(KeyFetchError, match="did not answer"):
        fetch_keys(parse_source("pat@b"), "pw",
                   run=Run(error=subprocess.TimeoutExpired("ssh", 15)))
    with pytest.raises(KeyFetchError, match="ssh is not available"):
        fetch_keys(parse_source("pat@b"), "pw", run=Run(error=FileNotFoundError()))


def test_keysource_label_hides_the_default_port():
    assert KeySource("ssh", "h", user="u").label == "u@h"
    assert KeySource("ssh", "h", user="u", port=2200).label == "u@h:2200"
