"""``pengyos-install-tui install`` line by line (the plain front-end)."""

from __future__ import annotations

import io

import pytest
from conftest import DISKS, GH_KEYS, HOST_KEYS, HOST_PASSWORD, KEY, FakeFetch

from pengyos_install_tui.install import Restart, run_install


def _drive(payload, lines, secrets, install=None, fetcher=None, disks=DISKS):
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
        fetch_keys=fetcher or FakeFetch(),
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
    gh = FakeFetch()
    rc, out, calls = _drive(payload, ["1", "box", "pat", "patw", "", "", "erase"],
                            ["hunter22", "hunter22"], fetcher=gh)
    assert rc == 0 and gh.calls == [("github.com/patw", "")]
    assert "Password for" not in out  # only asked for user@host
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


def test_keys_from_another_machine(payload):
    f = FakeFetch()
    rc, out, calls = _drive(payload, ["1", "box", "pat", "pat@behoserver", "", "", "", "erase"],
                            ["hunter22", "hunter22", "wrong", HOST_PASSWORD], fetcher=f)
    assert rc == 0 and "refused the password" in out
    assert f.calls == [("pat@behoserver", "wrong"), ("pat@behoserver", HOST_PASSWORD)]
    assert calls[0][1].authorized_keys == HOST_KEYS
    assert "2 keys (2 from pat@behoserver, 1 line skipped)" in out
    assert HOST_PASSWORD not in out


def test_wifi_line_by_line(payload):
    from conftest import WIFI_PASSWORD, FakeWifi

    w = FakeWifi()
    lines = iter(["1", "box", "pat", "7", "r", "1", "", KEY, "", "erase"])
    secrets = iter(["hunter22", "hunter22", "wrong password", WIFI_PASSWORD])
    out = io.StringIO()
    calls = []
    rc = run_install(inp=lambda p: next(lines), secret=lambda p: next(secrets), out=out,
                     payload=payload, disks=DISKS, plain=True, wifi=w, fetch_keys=FakeFetch(),
                     install=lambda t, a, **k: calls.append(a))
    text = out.getvalue()
    assert rc == 0 and w.scans == 2
    assert "0) No Wi-Fi" in text and "1) Home" in text and "r) Scan again" in text
    assert "enter a number from 0 to 2, or r" in text
    assert "Connecting to Home ..." in text and "Home refused the password" in text
    assert "Wi-Fi Home (wlp2s0, 192.168.1.77)" in text
    assert w.joined[-1] == ("wlp2s0", "Home", WIFI_PASSWORD)


def test_wifi_password_enter_skips_wifi(payload):
    from conftest import FakeWifi

    w = FakeWifi()
    lines = iter(["1", "box", "pat", "1", KEY, "", "erase"])
    secrets = iter(["hunter22", "hunter22", ""])
    out = io.StringIO()
    rc = run_install(inp=lambda p: next(lines), secret=lambda p: next(secrets), out=out,
                     payload=payload, disks=DISKS, plain=True, wifi=w, fetch_keys=FakeFetch(),
                     install=lambda *a, **k: None)
    assert rc == 0 and w.joined == [] and "none yet" in out.getvalue()


def test_restart_comes_from_the_kernel(tmp_path):
    """The hardware report: systemctl lived on the pulled stick (EIO)."""
    trigger = tmp_path / "sysrq-trigger"
    trigger.write_text("")
    runs = []
    Restart(sysrq=str(trigger), run=lambda *a, **k: runs.append(a), pause=lambda s: None)()
    assert runs == [] and trigger.read_text() == "ub"


def test_restart_without_the_trigger_falls_back_to_systemctl(tmp_path):
    runs = []
    Restart(sysrq=str(tmp_path / "none" / "sysrq-trigger"),
            run=lambda argv, **k: runs.append(argv))()
    assert runs == [["systemctl", "reboot"]]


def test_plain_install_restarts_through_restart(payload):
    lines = iter(["1", "box", "pat", "", KEY, "", "erase", ""])
    secrets = iter(["hunter22", "hunter22"])
    seen = []
    rc = run_install(inp=lambda p: next(lines), secret=lambda p: next(secrets),
                     out=io.StringIO(), payload=payload, disks=DISKS, plain=True,
                     install=lambda *a, **k: None, fetch_keys=FakeFetch(),
                     reboot=True, restart=lambda: seen.append("restart"))
    assert rc == 0 and seen == ["restart"]
