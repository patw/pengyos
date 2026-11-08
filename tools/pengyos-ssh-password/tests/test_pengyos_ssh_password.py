"""Tests for `pengyos-ssh-password`, the shipped tool in the Server overlay.

The suite runs the real script against a sandbox root (`--root DIR`) holding
/etc/passwd, /etc/hostname and home directories. Fake `sshd` and `systemctl`
come first on PATH and log their calls; FAKE_SSHD_FAIL makes `sshd -t` refuse.
No root, no sshd.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO = TOOL_DIR.parents[1]
OVERLAY = REPO / "buildroot-external/board/server/rootfs-overlay"
SCRIPT = OVERLAY / "usr/bin/pengyos-ssh-password"
FEATURES = REPO / "buildroot-external/board/server/features-required.txt"
DROPIN = "etc/ssh/sshd_config.d/50-pengyos-ssh-password.conf"
MOTD = "etc/motd.d/50-pengyos-ssh-password"
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFakeFakeFakeFakeFakeFakeFakeFakeFake pat@laptop"

FAKE = """#!/bin/sh
echo "$(basename "$0") $*" >> "$FAKE_LOG"
case "$(basename "$0") $1" in
  "sshd -t") [ -z "$FAKE_SSHD_FAIL" ] || { echo "bad configuration option" >&2; exit 255; } ;;
  "systemctl is-active") [ -z "$FAKE_SSHD_STOPPED" ] || exit 3 ;;
esac
exit 0
"""


def machine(tmp: Path, users=(("pat", 1000),), keys=None) -> Path:
    r = tmp / "root"
    (r / "etc").mkdir(parents=True)
    rows = ["root:x:0:0:root:/root:/bin/bash", "sshd:x:74:74::/var/empty:/usr/bin/nologin"]
    for name, uid in users:
        rows.append(f"{name}:x:{uid}:{uid}::/home/{name}:/bin/bash")
        (r / "home" / name).mkdir(parents=True)
    rows.append("nobody:x:65534:65534::/:/usr/bin/nologin")
    (r / "etc/passwd").write_text("\n".join(rows) + "\n")
    (r / "etc/hostname").write_text("box\n")
    for name, text in (keys or {}).items():
        d = r / "home" / name / ".ssh"
        d.mkdir(parents=True)
        (d / "authorized_keys").write_text(text)
    return r


def run(r: Path, *args, check=True, **env):
    bindir = r.parent / "fakebin"
    if not bindir.exists():
        bindir.mkdir()
        for name in ("sshd", "systemctl"):
            (bindir / name).write_text(FAKE)
            (bindir / name).chmod(0o755)
    e = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", FAKE_LOG=str(r.parent / "calls"),
             SUDO_USER="pat", **env)
    pr = subprocess.run([sys.executable, str(SCRIPT), "--root", str(r), *args],
                        capture_output=True, text=True, env=e)
    if check:
        assert pr.returncode == 0, pr.stderr
    return pr


def calls(r: Path) -> list[str]:
    f = r.parent / "calls"
    return f.read_text().splitlines() if f.exists() else []


def status(r: Path) -> dict:
    return json.loads(run(r, "status", "--json").stdout)


def test_default_is_keys_only(tmp_path):
    r = machine(tmp_path)
    s = status(r)
    assert s["password_logins"] is False and s["root_password_login"] is False
    assert s["accounts"] == [{"user": "pat", "keys": 0}]
    assert "off (keys only" in run(r).stdout


def test_on_writes_the_dropin_checks_and_reloads(tmp_path):
    r = machine(tmp_path)
    out = run(r, "on").stdout
    text = (r / DROPIN).read_text()
    assert "PasswordAuthentication yes" in text
    assert "PermitRootLogin" not in text.replace("PermitRootLogin prohibit-password in", "")
    assert (r / MOTD).read_text().startswith("SSH password logins are ON")
    assert oct((r / DROPIN).stat().st_mode & 0o777) == "0o644"
    assert calls(r) == ["sshd -t", "systemctl is-active --quiet sshd", "systemctl reload sshd"]
    assert "ON (root: keys only); sshd reloaded" in out
    assert status(r)["password_logins"] is True


def test_on_leaves_the_penalties_at_openssh_defaults(tmp_path):
    # 26.11.6 wrote PerSourcePenalties authfail:15s, which made sshd drop the
    # connection ssh-copy-id installs the key with (its two probes count as
    # failures). Running `on` again rewrites an old drop-in without it.
    r = machine(tmp_path)
    (r / DROPIN).parent.mkdir(parents=True, exist_ok=True)
    (r / DROPIN).write_text("PasswordAuthentication yes\nPerSourcePenalties authfail:15s\n")
    run(r, "on")
    active = [l for l in (r / DROPIN).read_text().splitlines() if l and not l.startswith("#")]
    assert active == ["PasswordAuthentication yes"]


def test_on_is_undone_when_sshd_rejects_it(tmp_path):
    r = machine(tmp_path)
    pr = run(r, "on", check=False, FAKE_SSHD_FAIL="1")
    assert pr.returncode == 6 and "nothing changed" in pr.stderr
    assert not (r / DROPIN).exists() and not (r / MOTD).exists()
    assert "systemctl reload sshd" not in calls(r)


def test_on_offline_only_writes_files(tmp_path):
    # The installer runs it in the new system's chroot: no sshd to test or reload.
    r = machine(tmp_path)
    run(r, "on", "--offline")
    assert (r / DROPIN).exists() and (r / MOTD).exists()
    assert calls(r) == []


def test_on_with_sshd_stopped_does_not_reload(tmp_path):
    r = machine(tmp_path)
    out = run(r, "on", FAKE_SSHD_STOPPED="1").stdout
    assert "systemctl reload sshd" not in calls(r)
    assert "not running" in out


def test_status_when_on_shows_how_to_add_a_key(tmp_path):
    r = machine(tmp_path)
    run(r, "on")
    out = run(r, "status").stdout
    assert "ON (keys and passwords; root: keys only)" in out
    assert "ssh-keygen -t ed25519" in out and "ssh-copy-id pat@box" in out
    assert "id_ed25519.pub | ssh pat@box" in out  # the Windows way


def test_off_refuses_while_nobody_has_a_key(tmp_path):
    r = machine(tmp_path)
    run(r, "on")
    pr = run(r, "off", check=False)
    assert pr.returncode == 3
    assert "nobody could log in over SSH" in pr.stderr and "--force" in pr.stderr
    assert (r / DROPIN).exists()


def test_off_force_turns_it_off_anyway(tmp_path):
    r = machine(tmp_path)
    run(r, "on")
    run(r, "off", "--force")
    assert not (r / DROPIN).exists() and not (r / MOTD).exists()


def test_off_once_an_account_has_a_key(tmp_path):
    r = machine(tmp_path, keys={"pat": "# my laptop\n" + KEY + "\n"})
    assert status(r)["accounts"] == [{"user": "pat", "keys": 1}]
    run(r, "on")
    out = run(r, "off").stdout
    assert "off (keys only); sshd reloaded" in out and "stay open" in out
    assert not (r / DROPIN).exists() and not (r / MOTD).exists()
    assert calls(r)[-1] == "systemctl reload sshd"


def test_off_when_already_off(tmp_path):
    r = machine(tmp_path)
    assert "already off" in run(r, "off").stdout
    assert calls(r) == []


def test_keys_with_options_count_and_junk_does_not(tmp_path):
    r = machine(tmp_path, users=(("pat", 1000), ("sam", 1001)),
                keys={"pat": 'from="10.0.0.0/8" ' + KEY + "\nnot a key\n\n", "sam": "# none\n"})
    assert status(r)["accounts"] == [{"user": "pat", "keys": 1}, {"user": "sam", "keys": 0}]


def test_system_accounts_are_not_listed(tmp_path):
    r = machine(tmp_path)
    assert [a["user"] for a in status(r)["accounts"]] == ["pat"]


def test_image_wiring():
    """sshd reads the drop-in before its own key-only lines, and reload works."""
    cfg = (OVERLAY / "etc/ssh/sshd_config").read_text().splitlines()
    active = [ln for ln in cfg if ln.strip() and not ln.lstrip().startswith("#")]
    inc = active.index("Include /etc/ssh/sshd_config.d/*.conf")
    assert inc < active.index("PasswordAuthentication no")  # first value wins in sshd
    assert "PermitRootLogin prohibit-password" in active
    unit = (OVERLAY / "etc/systemd/system/sshd.service").read_text()
    assert "ExecReload=/usr/sbin/sshd -t" in unit and "ExecReload=/usr/bin/kill -HUP $MAINPID" in unit
    assert "usr/bin/pengyos-ssh-password" in FEATURES.read_text()
    assert os.access(SCRIPT, os.X_OK)
