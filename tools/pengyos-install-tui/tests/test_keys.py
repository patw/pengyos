"""Fetching SSH keys from GitHub (pengyos_install_tui.keys), with a fake opener."""

from __future__ import annotations

import io
import urllib.error

import pytest

from pengyos_install_tui.keys import KeyFetchError, github_keys, valid_github_user


class Opener:
    def __init__(self, body: bytes = b"", error: Exception | None = None):
        self.body, self.error, self.urls = body, error, []

    def __call__(self, url, timeout):
        self.urls.append((url, timeout))
        if self.error:
            raise self.error
        return io.BytesIO(self.body)


@pytest.mark.parametrize("name,ok", [
    ("patw", True), ("a", True), ("Pat-W2", True), ("x" * 39, True),
    ("x" * 40, False), ("-pat", False), ("pat-", False), ("pa--t", False),
    ("pat w", False), ("pat/../x", False), ("", False),
])
def test_valid_github_user(name, ok):
    assert valid_github_user(name) is ok


def test_keys_are_fetched_and_tagged():
    op = Opener(b"ssh-ed25519 AAAAC3Nza1\nssh-rsa AAAAB3Nza2\n\n")
    keys = github_keys("patw", opener=op)
    assert op.urls == [("https://github.com/patw.keys", 10)]
    assert keys.splitlines() == ["ssh-ed25519 AAAAC3Nza1 patw@github",
                                 "ssh-rsa AAAAB3Nza2 patw@github"]


def test_lines_that_are_not_keys_are_dropped():
    keys = github_keys("patw", opener=Opener(b"<html>\nssh-ed25519 AAAA1\n"))
    assert keys == "ssh-ed25519 AAAA1 patw@github"


def test_no_keys():
    with pytest.raises(KeyFetchError, match="has no SSH keys"):
        github_keys("patw", opener=Opener(b"\n"))


def test_unknown_user():
    err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    with pytest.raises(KeyFetchError, match="no user named 'nobody'"):
        github_keys("nobody", opener=Opener(error=err))


def test_server_error():
    err = urllib.error.HTTPError("u", 503, "Busy", {}, None)
    with pytest.raises(KeyFetchError, match="answered 503"):
        github_keys("patw", opener=Opener(error=err))


def test_no_network_says_check_the_cable():
    err = urllib.error.URLError(OSError("Network is unreachable"))
    with pytest.raises(KeyFetchError, match="network cable"):
        github_keys("patw", opener=Opener(error=err))


def test_invalid_user_never_reaches_the_network():
    op = Opener()
    with pytest.raises(KeyFetchError):
        github_keys("../etc", opener=op)
    assert op.urls == []
