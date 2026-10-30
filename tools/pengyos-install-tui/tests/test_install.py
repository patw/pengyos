"""``pengyos-install-tui install`` line by line (the plain front-end)."""

from __future__ import annotations

import io

import pytest
from conftest import DISKS, GH_KEYS, KEY, FakeGitHub

from pengyos_install_tui.install import run_install


def _drive(payload, lines, secrets, install=None, github=None, disks=DISKS):
    lines, secrets = iter(lines), iter(secrets)
    calls = []

    def fake_install(target, answers, **kw):
        calls.append((target, answers, kw))

    out = io.StringIO()
    rc = run_install(
        inp=lambda prompt: next(lines),
        secret=lambda prompt: next(secrets),
        out=out, payload=payload, disks=disks,
        install=install or fake_install,
        fetch_keys=github or FakeGitHub(),
        plain=True,
    )
    return rc, out.getvalue(), calls


def test_full_install_with_reprompts(payload):
    rc, out, calls = _drive(
        payload,
        ["9", "",                       # disk: out of range, then the default
         "bad host", "",                # computer name: refused, then the default
         "Pat", "pat",                  # username
         "",                            # GitHub user: skip
         "not-a-key", KEY,              # key: refused, then a real one
         "",                            # Pengy: Enter skips the page
         "erase"],
        ["short", "short", "hunter22", "nomatch", "hunter22", "hunter22"],
    )
    assert rc == 0
    assert "enter a number from 1 to 1" in out
    assert "too small" in out and "the installer stick" in out
    assert "invalid hostname" in out and "invalid username" in out
    assert "at least 6 characters" in out and "do not match" in out
    assert "not an SSH public key" in out
    ((target, answers, kw),) = calls
    assert target == "/dev/vda"
    assert (answers.hostname, answers.username, answers.password) == ("pengyos", "pat", "hunter22")
    assert answers.authorized_keys == KEY
    assert answers.pengy_base_url == "" and "not set up" in out
    assert kw["confirm"] is True and kw["payload_dir"] == payload
    assert "PengyOS is installed" in out


def test_github_keys(payload):
    gh = FakeGitHub()
    rc, out, calls = _drive(payload, ["1", "box", "pat", "patw", "", "", "erase"],
                            ["hunter22", "hunter22"], github=gh)
    assert rc == 0 and gh.calls == ["patw"]
    ((_, answers, _),) = calls
    assert answers.authorized_keys == GH_KEYS
    assert "2 keys (2 from github.com/patw)" in out


def test_skipping_ssh_warns_then_asks_again(payload):
    rc, out, calls = _drive(payload, ["1", "box", "pat", "", "", "", "", "erase"],
                            ["hunter22", "hunter22"])
    assert rc == 0 and "only be used from its own keyboard" in out
    assert "console login only" in out


def test_pengy_page_fills_the_endpoint_answers(payload):
    rc, out, calls = _drive(
        payload,
        ["1", "box", "pat", "", KEY, "http://10.0.23.6:8086/v1", "qwen3:8b", "erase"],
        ["hunter22", "hunter22", "sk-secret"],
    )
    assert rc == 0
    ((_, answers, _),) = calls
    assert answers.pengy_base_url == "http://10.0.23.6:8086/v1"
    assert answers.pengy_api_key == "sk-secret"
    assert answers.pengy_model == "qwen3:8b"
    assert "http://10.0.23.6:8086/v1  model qwen3:8b" in out
    assert "sk-secret" not in out


def test_pengy_page_reprompts_a_bad_url(payload):
    rc, out, calls = _drive(payload, ["1", "box", "pat", "", KEY, "not a url", "", "erase"],
                            ["hunter22", "hunter22"])
    assert rc == 0 and "invalid Pengy base URL" in out
    assert calls[0][1].pengy_base_url == ""


def test_desktop_autologin_opt_in(desktop_payload):
    rc, out, calls = _drive(desktop_payload, ["1", "box", "pat", "", KEY, "", "y", "erase"],
                            ["hunter22", "hunter22"])
    assert rc == 0 and "automatic" in out
    assert calls[0][1].autologin is True


def test_not_confirmed_changes_nothing(payload):
    rc, out, calls = _drive(payload, ["1", "box", "pat", "", KEY, "", "no"],
                            ["hunter22", "hunter22"])
    assert rc == 1 and calls == [] and "Nothing was changed" in out


def test_eof_before_confirm_changes_nothing(payload):
    def eof(prompt):
        raise EOFError

    out = io.StringIO()
    rc = run_install(inp=eof, secret=eof, out=out, payload=payload, disks=DISKS, plain=True,
                     install=lambda *a, **k: pytest.fail("must not install"))
    assert rc == 1 and "nothing was changed" in out.getvalue()


def test_no_usable_disk(payload):
    rc, out, calls = _drive(payload, [], [], disks=DISKS[:1])
    assert rc == 1 and "No disk to install to was found" in out


def test_missing_payload_is_reported(tmp_path):
    out = io.StringIO()
    rc = run_install(out=out, payload=str(tmp_path / "none"), disks=DISKS, plain=True)
    assert rc == 1 and "install payload" in out.getvalue()


def test_not_a_terminal_falls_back_to_plain(payload, monkeypatch):
    """stdin/stdout not TTYs (a pipe, a test): the line-by-line installer runs."""
    monkeypatch.setenv("TERM", "xterm")
    out = io.StringIO()
    answers = iter(["1", "box", "pat", "", KEY, "", "no"])
    secrets = iter(["hunter22", "hunter22"])
    rc = run_install(inp=lambda p: next(answers), secret=lambda p: next(secrets), out=out,
                     payload=payload, disks=DISKS, install=lambda *a, **k: None)
    assert rc == 1 and "== Choose a disk ==" in out.getvalue()
