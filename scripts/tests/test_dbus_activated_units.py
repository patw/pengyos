"""Tests for the COSMIC tray watcher and the build check that guards its class.

Found on behoweather running 26.10.14 (2026-10-08): the panel had no tray icons —
no Discord, no SyncThingy, nothing clickable. The journal said why:

    com.system76.CosmicAppletStatusArea: Failed to start status notifier watcher:
        org.freedesktop.systemd1.NoSuchUnit: Unit com.system76.CosmicStatusNotifierWatcher.service not found.
    com.system76.CosmicAppletStatusArea: Failed to register status notifier host:
        org.freedesktop.DBus.Error.ServiceUnknown: The name org.kde.StatusNotifierWatcher ...

Upstream's `_install_status_notifier_watcher` installs two files: the dbus
activation file (/usr/share/dbus-1/services) and the systemd **user unit** it
names with `SystemdService=` (/usr/lib/systemd/user). The session bus here is
`dbus-daemon --session --address=systemd: --systemd-activation`, so activation is
answered by systemd --user, which knows units only — the dbus file is a pointer
to a unit that the image did not ship. The 2026-09-30 "no tray icons" fix had
shipped half the pair, and nothing checked the other half.

`board/common/check-dbus-units.py` now checks the whole class at build time.
These tests drive it with a fixture tree: the positive control (a complete pair
passes) and the negative control (the shipped-but-pointer-only case fails and
names the unit).

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
POST_BUILD = REPO / "buildroot-external/board/desktop/post-build.sh"
CHECKER = REPO / "buildroot-external/board/common/check-dbus-units.py"

WATCHER = "com.system76.CosmicStatusNotifierWatcher"
DBUS_FILE = f"usr/share/dbus-1/services/{WATCHER}.service"
UNIT_FILE = f"usr/lib/systemd/user/{WATCHER}.service"

DBUS_FILE_BODY = (f"[D-BUS Service]\nName={WATCHER}\n"
                  f"Exec=/usr/bin/cosmic-applet-status-area --status-notifier-watcher\n"
                  f"SystemdService={WATCHER}.service\n")
UNIT_BODY = ("[Unit]\nDescription=COSMIC Status Notifier Watcher backend\n\n"
             "[Service]\nType=dbus\nBusName=" + WATCHER + "\n"
             "ExecStart=/usr/bin/cosmic-applet-status-area --status-notifier-watcher\n")


def _tree(root: Path, dbus_body: str = DBUS_FILE_BODY, with_unit: bool = True) -> Path:
    """A minimal TARGET_DIR holding the watcher's dbus file, and its unit or not."""
    (root / DBUS_FILE).parent.mkdir(parents=True, exist_ok=True)
    (root / DBUS_FILE).write_text(dbus_body)
    if with_unit:
        (root / UNIT_FILE).parent.mkdir(parents=True, exist_ok=True)
        (root / UNIT_FILE).write_text(UNIT_BODY)
    return root


def _run(target: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECKER), str(target)],
                          capture_output=True, text=True)


# --- the shipped pair ---------------------------------------------------------

def test_checker_is_executable_and_runnable():
    assert os.access(CHECKER, os.X_OK), "board/common/check-dbus-units.py must be +x"


def test_post_build_installs_both_halves_of_the_watcher():
    """Regression guard: the dbus file alone was the 26.10.14 bug. Both upstream
    files must be installed, from upstream's own .service.in templates."""
    text = POST_BUILD.read_text()
    assert f"cosmic-applet-status-area/data/dbus-1/{WATCHER}.service.in" in text
    assert f"cosmic-applet-status-area/data/{WATCHER}.service.in" in text
    assert f'"${{TARGET_DIR}}/usr/lib/systemd/user/{WATCHER}.service"' in text


def test_post_build_runs_the_check():
    text = POST_BUILD.read_text()
    assert "check-dbus-units.py" in text


# --- positive control ---------------------------------------------------------

def test_a_complete_pair_passes(tmp_path):
    r = _run(_tree(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    # a check that cannot report a positive finding cannot be trusted
    assert "1 dbus-activated service(s) checked" in r.stdout, r.stdout


# --- negative controls --------------------------------------------------------

def test_a_pointer_without_its_unit_fails_and_names_it(tmp_path):
    """Exactly the 26.10.14 image: the dbus file is there, the unit is not."""
    r = _run(_tree(tmp_path, with_unit=False))
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"{WATCHER}.service" in r.stderr
    assert "does not ship" in r.stderr


def test_a_unit_in_the_overlay_counts(tmp_path):
    """etc/systemd/user is the mutable-overlay path; a unit shipped there is a
    unit shipped."""
    root = _tree(tmp_path, with_unit=False)
    (root / "etc/systemd/user").mkdir(parents=True, exist_ok=True)
    (root / f"etc/systemd/user/{WATCHER}.service").write_text(UNIT_BODY)
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr


def test_fork_activated_services_are_not_flagged(tmp_path):
    """No SystemdService= means dbus activates it itself — not our problem, and a
    check that cries about those gets ignored."""
    body = ("[D-BUS Service]\nName=org.example.Forky\n"
            "Exec=/usr/libexec/forky-helper\n")
    root = _tree(tmp_path, with_unit=False)
    (root / "usr/share/dbus-1/services/org.example.Forky.service").write_text(body)
    r = _run(root)
    assert r.returncode == 1, r.stdout + r.stderr  # the watcher is still broken here
    assert "org.example.Forky" not in r.stderr, r.stderr
    assert "1 dbus-activated service(s) checked" in r.stdout, r.stdout


def test_template_units_resolve_for_an_instance(tmp_path):
    root = tmp_path / "t"
    d = root / "usr/share/dbus-1/system-services"
    d.mkdir(parents=True)
    (d / "com.example.Instances.service").write_text(
        "[D-BUS Service]\nName=com.example.Instances\n"
        "SystemdService=com.example.Instances@1.service\n")
    u = root / "usr/lib/systemd/system"
    u.mkdir(parents=True)
    (u / "com.example.Instances@.service").write_text("[Service]\nExecStart=/x\n")
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr


def test_system_bus_is_checked_too(tmp_path):
    root = tmp_path / "s"
    d = root / "usr/share/dbus-1/system-services"
    d.mkdir(parents=True)
    (d / "com.example.System.service").write_text(
        "[D-BUS Service]\nName=com.example.System\nSystemdService=com.example.System.service\n")
    r = _run(root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "system unit com.example.System.service" in r.stderr, r.stderr


def test_checker_reports_a_clean_tree_as_clean(tmp_path):
    """An image with no dbus-activated services at all must pass, not error."""
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 dbus-activated service(s) checked" in r.stdout, r.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
