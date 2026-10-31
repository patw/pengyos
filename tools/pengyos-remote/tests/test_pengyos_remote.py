"""Tests for the `pengyos-remote` shipped tool.

Run as a program with HOME in a temp dir and `--root DIR` (fake systemctl,
openssl, ip). Asserted: the files `password` and `on` write and their modes,
the unit verbs sent to the user manager, what `status` reports, and every
misuse refused with nothing written. The RDP server itself is tested by
connecting a client to a Desktop (spec.md).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOL_DIR.parents[1]
DESKTOP = REPO_ROOT / "buildroot-external" / "board" / "desktop" / "rootfs-overlay"
SCRIPT = DESKTOP / "usr" / "bin" / "pengyos-remote"
UNIT = DESKTOP / "usr" / "lib" / "systemd" / "user" / "pengyos-rdp-server.service"
HERE = Path(__file__).resolve().parent
UNIT_NAME = "pengyos-rdp-server.service"


class Box:
    def __init__(self, tmp: Path):
        self.root = tmp / "root"
        self.home = tmp / "home"
        self.home.mkdir()
        bindir = self.root / "usr" / "bin"
        bindir.mkdir(parents=True)
        for name in ("systemctl", "openssl", "ip"):
            shutil.copy(HERE / "fakes.py", bindir / name)
            (bindir / name).chmod(0o755)
        self.conf = self.home / ".config" / "pengyos-remote"
        self.state = self.home / ".local" / "state" / "pengyos-remote"

    def run(self, *args: str, ok: int | None = 0, stdin: str = "") -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
        env["HOME"] = str(self.home)
        cp = subprocess.run(["python3", str(SCRIPT), "--root", str(self.root), *args],
                            capture_output=True, text=True, env=env, input=stdin)
        if ok is not None:
            assert cp.returncode == ok, (cp.returncode, cp.stdout, cp.stderr)
        return cp

    def calls(self) -> list[list[str]]:
        log = self.root / "calls.log"
        if not log.exists():
            return []
        return [c[1:] for c in map(json.loads, log.read_text().splitlines()) if c[0] == "systemctl"]

    def status(self) -> dict:
        return json.loads(self.run("status", "--json").stdout)


@pytest.fixture()
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def mode(p: Path) -> int:
    return p.stat().st_mode & 0o777


def test_password_is_private(box: Box):
    box.run("password", "--stdin", stdin="correct horse\n")
    pw = box.conf / "password"
    assert pw.read_text() == "correct horse\n"
    assert mode(pw) == 0o600 and mode(box.conf) == 0o700
    assert box.status()["password_set"] is True


@pytest.mark.parametrize("pw, says", [("short", "8 to 128"), ("x" * 129, "8 to 128")])
def test_bad_passwords_write_nothing(box: Box, pw, says):
    assert says in box.run("password", "--stdin", stdin=pw + "\n", ok=2).stderr
    assert not (box.conf / "password").exists()


def test_password_without_a_terminal_needs_stdin(box: Box):
    assert "--stdin" in box.run("password", ok=2).stderr


def test_on_needs_a_password_first(box: Box):
    assert "set an RDP password first" in box.run("on", ok=2).stderr
    assert box.calls() == []


def test_on_makes_the_certificate_and_enables_the_unit(box: Box):
    box.run("password", "--stdin", stdin="correct horse\n")
    out = box.run("on").stdout
    assert "made a certificate" in out and "AB:CD:EF" in out
    assert "connect to" in out and "asks you on screen" in out
    assert mode(box.conf / "key.pem") == 0o600
    assert (box.conf / "env").read_text() == "RDP_ADDR=0.0.0.0:3389\n"
    assert mode(box.state) == 0o700
    assert ["--user", "enable", UNIT_NAME] in box.calls()
    assert ["--user", "restart", UNIT_NAME] in box.calls()
    s = box.status()
    assert s["enabled"] and s["state"] == "running" and s["fingerprint"] == "AB:CD:EF"
    # A second `on` keeps the certificate (clients trusted it).
    assert "made a certificate" not in box.run("on").stdout


def test_on_outside_a_desktop_session_waits_for_login(box: Box):
    box.run("password", "--stdin", stdin="correct horse\n")
    (box.root / "no-session").touch()
    assert "starts when you log in" in box.run("on").stdout
    s = box.status()
    assert s["enabled"] and s["state"] == "stopped"
    assert "runs while you are logged in" in box.run("status").stdout


def test_port_choice(box: Box):
    box.run("password", "--stdin", stdin="correct horse\n")
    assert "1025 to 65535" in box.run("on", "--port", "443", ok=2).stderr
    box.run("on", "--port", "3390")
    assert box.status()["port"] == 3390


def test_local_only_listens_on_loopback(box: Box):
    box.run("password", "--stdin", stdin="correct horse\n")
    out = box.run("on", "--local").stdout
    assert "localhost:3389 through an SSH tunnel" in out
    assert (box.conf / "env").read_text() == "RDP_ADDR=127.0.0.1:3389\n"
    assert box.status()["local_only"] is True
    assert "ssh -L 3389:127.0.0.1:3389" in box.run("status").stdout
    box.run("on")
    assert box.status()["local_only"] is False


def test_status_lists_lan_addresses_not_container_bridges(box: Box):
    s = box.status()
    assert s["addresses"] == ["10.0.23.4", "192.168.1.20", "fd00::4"]
    assert not s["enabled"] and not s["password_set"]
    out = box.run("status").stdout
    assert "remote desktop: off" in out and "NOT SET" in out and "[fd00::4]:3389" in out


def test_off_disables_and_stops(box: Box):
    box.run("password", "--stdin", stdin="correct horse\n")
    box.run("on")
    box.run("off")
    assert ["--user", "disable", "--now", UNIT_NAME] in box.calls()
    s = box.status()
    assert not s["enabled"] and s["state"] == "stopped"


def test_new_password_restarts_a_running_server(box: Box):
    box.run("password", "--stdin", stdin="correct horse\n")
    box.run("on")
    out = box.run("password", "--stdin", stdin="battery staple\n").stdout
    assert "restarted" in out
    assert [c for c in box.calls() if c[1] == "restart"].__len__() == 2


def test_forget_removes_the_saved_permission(box: Box):
    assert "nothing to forget" in box.run("forget").stdout
    box.state.mkdir(parents=True)
    (box.state / "restore-token").write_text("tok")
    assert box.status()["consent_saved"] is True
    assert "asks again" in box.run("forget").stdout
    assert not (box.state / "restore-token").exists()


def test_status_shows_the_log_when_it_is_not_running(box: Box):
    box.run("password", "--stdin", stdin="correct horse\n")
    (box.root / "no-session").touch()
    box.run("on")
    (box.state / "server.log").write_text("portal: starting\nError: portal request was cancelled\n")
    out = box.run("status").stdout
    assert "last log lines" in out and "cancelled" in out


def test_the_unit_runs_in_the_session_only():
    text = UNIT.read_text()
    assert "ExecStart=/usr/libexec/pengyos-rdp-server" in text
    assert "Requisite=graphical-session.target" in text
    assert "WantedBy=graphical-session.target" in text
    assert "EnvironmentFile=-%h/.config/pengyos-remote/env" in text
    assert "StandardOutput=append:%h/.local/state/pengyos-remote/server.log" in text
