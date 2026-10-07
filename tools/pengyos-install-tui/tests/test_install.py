"""Tests for ``pengyos-install-tui install`` (the real console installer)."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ENGINE_SRC = Path(__file__).resolve().parents[2] / "pengyos-install" / "src"
sys.path.insert(0, str(ENGINE_SRC))

from pengyos_install.image import Disk  # noqa: E402
from pengyos_install_tui.install import run_install  # noqa: E402


@pytest.fixture
def payload(tmp_path):
    f = {"file": "x.zst", "sha256": "0", "raw_size": 1000, "raw_sha256": "0"}
    (tmp_path / "manifest.json").write_text(json.dumps({
        "format": "pengyos-layout-1", "edition": "desktop", "version": "t",
        "files": {"rootfs": f, "esp": dict(f, file="e.zst")},
    }))
    return str(tmp_path)


DISKS = [
    Disk("sda", "/dev/sda", 8_000_000_000, "USB", removable=True, is_boot=True),
    Disk("vda", "/dev/vda", 20_000_000_000, "QEMU"),
    Disk("vdb", "/dev/vdb", 8_000_000_000, "tiny"),
]


KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample pat@laptop"


def _drive(payload, lines, secrets, install=None):
    lines, secrets = iter(lines), iter(secrets)
    calls = []

    def fake_install(target, answers, **kw):
        calls.append((target, answers, kw))

    out = io.StringIO()
    rc = run_install(
        inp=lambda prompt: next(lines),
        secret=lambda prompt: next(secrets),
        out=out,
        payload=payload,
        disks=DISKS,
        install=install or fake_install,
    )
    return rc, out.getvalue(), calls


def test_full_install_with_reprompts(payload):
    rc, out, calls = _drive(
        payload,
        ["9", "2", "1", "bad host", "", "Pat", "pat", "not-a-key", KEY, "", "erase"],
        ["short", "short", "hunter22", "nomatch", "hunter22", "hunter22"],
    )
    assert rc == 0
    assert "enter a number from 1 to 2" in out
    assert "too small" in out  # vdb
    assert "invalid hostname" in out
    assert "invalid username" in out
    assert "at least 6 characters" in out and "do not match" in out
    assert "(running system)" in out
    ((target, answers, kw),) = calls
    assert target == "/dev/vda"
    assert (answers.hostname, answers.username, answers.password) == ("pengyos", "pat", "hunter22")
    assert "not an SSH public key" in out
    assert answers.authorized_keys == KEY
    # A Desktop starts at its login screen unless automatic login is chosen.
    assert answers.autologin is False and "password required" in out
    assert kw["confirm"] is True and kw["payload_dir"] == payload


def test_desktop_autologin_opt_in(payload):
    rc, out, calls = _drive(payload, ["1", "box", "pat", "", "y", "erase"], ["hunter22", "hunter22"])
    assert rc == 0 and "login:     automatic" in out
    ((_, answers, _),) = calls
    assert answers.autologin is True


def test_not_confirmed_changes_nothing(payload):
    rc, out, calls = _drive(payload, ["1", "box", "pat", "", "", "no"], ["hunter22", "hunter22"])
    assert rc == 1 and calls == [] and "Nothing was changed" in out
    assert "console login only" in out  # the key was skipped


def test_eof_before_confirm_changes_nothing(payload):
    def eof(prompt):
        raise EOFError

    out = io.StringIO()
    rc = run_install(inp=eof, secret=eof, out=out, payload=payload, disks=DISKS,
                     install=lambda *a, **k: pytest.fail("must not install"))
    assert rc == 1 and "nothing was changed" in out.getvalue()


def test_missing_payload_is_reported(tmp_path):
    out = io.StringIO()
    rc = run_install(out=out, payload=str(tmp_path / "none"), disks=DISKS)
    assert rc == 1 and "install payload" in out.getvalue()
