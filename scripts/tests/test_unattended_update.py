"""Unattended updates are opt-in, and a trial boot of a new system always ends.

pengyos-update's own suite tests the logic (tools/pengyos-update/spec.md). This
checks the image wiring it relies on:
- the auto timer is off until an administrator turns it on. Buildroot's
  preset-all enables any unit that no preset names, so it needs a `disable` line;
- the trial-only units do nothing on a normal boot;
- the kernel restarts after a panic;
- systemd pets the watchdog;
- confirm runs the health gate.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BOARD = REPO / "buildroot-external" / "board"
SERVER = BOARD / "server" / "rootfs-overlay"
USR_UNITS = SERVER / "usr" / "lib" / "systemd" / "system"
ETC_UNITS = SERVER / "etc" / "systemd" / "system"
PRESET = SERVER / "usr" / "lib" / "systemd" / "system-preset" / "10-pengyos-server.preset"


def unit_lines(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text().splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def test_the_auto_timer_is_preset_disabled_and_not_linked_anywhere():
    assert "disable pengyos-update-auto.timer" in unit_lines(PRESET)
    for overlay in BOARD.glob("*/rootfs-overlay"):
        links = [p for p in overlay.rglob("pengyos-update-auto.timer") if p.is_symlink()]
        assert not links, links
    timer = unit_lines(USR_UNITS / "pengyos-update-auto.timer")
    assert "OnCalendar=*-*-* 04:00:00" in timer
    assert not any(ln.startswith(("RandomizedDelaySec", "Persistent=true")) for ln in timer)
    service = unit_lines(USR_UNITS / "pengyos-update-auto.service")
    assert "ExecStart=/usr/bin/pengyos-update auto-run" in service
    assert not any(ln == "[Install]" for ln in service), "started by its timer only"


def test_confirm_runs_the_health_gate_with_room_for_retries():
    lines = unit_lines(ETC_UNITS / "pengyos-update-confirm.service")
    assert "ExecStart=/usr/bin/pengyos-update confirm --boot" in lines
    timeout = next(ln for ln in lines if ln.startswith("TimeoutStartSec="))
    assert re.fullmatch(r"TimeoutStartSec=(\d+)min", timeout)
    assert int(timeout.split("=")[1][:-3]) * 60 > 180, "longer than the default health_timeout"


def test_trial_only_units_are_conditioned_on_the_grub_marker():
    deadline = USR_UNITS / "pengyos-update-deadline.service"
    lines = unit_lines(deadline)
    assert "ConditionKernelCommandLine=pengyos.trial=1" in lines
    assert "DefaultDependencies=no" in lines
    link = USR_UNITS / "sysinit.target.wants" / "pengyos-update-deadline.service"
    assert link.is_symlink() and link.resolve() == deadline.resolve()
    # The 15 min deadline must outlast the health gate's retries (180 s default).
    assert any(re.search(r"update-trial-reboot deadline (\d+)$", ln)
               and int(ln.rsplit(" ", 1)[1]) >= 600 for ln in lines)
    for unit in ("emergency", "rescue"):
        dropin = unit_lines(USR_UNITS / f"{unit}.service.d" / "pengyos-update-trial.conf")
        assert f"ExecStartPre=-/usr/lib/pengyos/update-trial-reboot emergency {unit}.service" in dropin
    helper = (SERVER / "usr" / "lib" / "pengyos" / "update-trial-reboot").read_text()
    first = next(ln for ln in helper.splitlines() if ln and not ln.startswith("#"))
    assert first.startswith("grep -qw 'pengyos.trial=1' /proc/cmdline") and first.endswith("exit 0")


def test_the_grub_marker_is_what_the_units_look_for():
    import sys
    sys.path.insert(0, str(REPO / "tools" / "pengyos-install" / "src"))
    from pengyos_install.image import render_grub_cfg
    cfg = render_grub_cfg({"a": "x", "b": "y"})
    assert 'set pengyos_trial="pengyos.trial=1"' in cfg


def test_a_panic_restarts_and_systemd_pets_the_watchdog():
    frag = unit_lines(BOARD / "server" / "linux.fragment")
    assert "CONFIG_PANIC_TIMEOUT=10" in frag
    assert {"CONFIG_ITCO_WDT=m", "CONFIG_SP5100_TCO=m"} <= set(frag)
    wd = unit_lines(SERVER / "usr" / "lib" / "systemd" / "system.conf.d" / "60-pengyos-watchdog.conf")
    assert "[Manager]" in wd and "RuntimeWatchdogSec=60s" in wd


def test_the_image_health_check_covers_firewall_ssh_and_network():
    check = (SERVER / "usr" / "lib" / "pengyos" / "update-health.d" / "10-critical-units").read_text()
    for unit in ("nftables.service", "sshd.service", "systemd-networkd.service"):
        assert unit in check
    assert (SERVER / "etc" / "pengyos" / "update-health.d" / "README").is_file()
