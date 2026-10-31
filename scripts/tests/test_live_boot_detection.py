"""A boot is live because of the kernel line, not because an installer stick exists.

Installed systems always boot with `pengyos.data=` (preinit mounts the data
partition from it); a live stick never does. The installer stick's
PENGYOS-PAYLOAD partition says only "an installer is plugged in". Used as the
live test, it opened the installer, as root, in behopc's installed desktop when
a stick was left in a USB port at boot (2026-10-31), and skipped the Firefox
setup there. greetd-config, the PipeWire root drop-ins and the Server's
console installer already test the kernel line; this keeps everything else
doing the same.
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
BOARD = REPO / "buildroot-external" / "board"
UNITS = [p for p in BOARD.glob("*/rootfs-overlay/**/*")
         if p.is_file() and (p.suffix in (".service", ".conf", ".socket", ".path", ".timer"))]
LABEL = "/dev/disk/by-label/PENGYOS-PAYLOAD"


def conditions(path: pathlib.Path) -> list[str]:
    return [l.strip() for l in path.read_text(errors="replace").splitlines()
            if re.match(r"\s*Condition\w+=", l)]


def test_units_gated_on_the_stick_also_require_a_live_boot():
    for unit in UNITS:
        conds = conditions(unit)
        if f"ConditionPathExists={LABEL}" in conds:
            assert "ConditionKernelCommandLine=!pengyos.data" in conds, (
                f"{unit.relative_to(REPO)} starts whenever an installer stick is plugged in, "
                "installed systems included: add ConditionKernelCommandLine=!pengyos.data")


def test_no_unit_uses_the_stick_to_mean_installed():
    for unit in UNITS:
        assert f"ConditionPathExists=!{LABEL}" not in conditions(unit), (
            f"{unit.relative_to(REPO)}: 'no installer stick' does not mean 'installed'; "
            "use ConditionKernelCommandLine=pengyos.data")


def test_the_live_installer_refuses_an_installed_system():
    unit = BOARD / "desktop" / "rootfs-overlay" / "etc" / "systemd" / "system" / "pengyos-live-installer.service"
    assert "ConditionKernelCommandLine=!pengyos.data" in conditions(unit)
    script = (BOARD / "desktop" / "rootfs-overlay" / "usr" / "libexec" / "pengyos" / "live-installer").read_text()
    guard = script.index("pengyos\\.data=")
    assert guard < script.index("mount -o ro"), "the guard must come before the payload is mounted"


def test_browser_setup_runs_on_installed_systems():
    unit = BOARD / "desktop" / "rootfs-overlay" / "etc" / "systemd" / "system" / "pengyos-browser-setup.service"
    assert "ConditionKernelCommandLine=pengyos.data" in conditions(unit)
