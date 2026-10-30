"""board/server's pengyos-battery-limit sets each battery's charge limit from
/etc/pengyos/battery.conf, against a fake /sys/class/power_supply."""
from __future__ import annotations

import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "buildroot-external" / "board" / "server" / "rootfs-overlay"
SCRIPT = OVERLAY / "usr" / "libexec" / "pengyos" / "pengyos-battery-limit"


def supply(ps: pathlib.Path, name: str, kind: str, **attrs: str) -> pathlib.Path:
    d = ps / name
    d.mkdir(parents=True)
    (d / "type").write_text(kind + "\n")
    for k, v in attrs.items():
        (d / k).write_text(v + "\n")
    return d


def run(ps: pathlib.Path, conf: str | None) -> subprocess.CompletedProcess:
    args = ["sh", str(SCRIPT)]
    if conf is not None:
        c = ps.parent / "battery.conf"
        c.write_text(conf)
        args.append(str(c))
    env = {"PENGYOS_POWER_SUPPLY": str(ps), "PENGYOS_BATTERY_WAIT": "1", "PATH": "/usr/bin:/bin"}
    return subprocess.run(args, capture_output=True, text=True, env=env)


def test_sets_the_shipped_limit(tmp_path):
    ps = tmp_path / "ps"
    bat = supply(ps, "BAT0", "Battery", charge_control_end_threshold="100")
    supply(ps, "AC", "Mains")
    r = run(ps, (OVERLAY / "etc" / "pengyos" / "battery.conf").read_text())
    assert r.returncode == 0, r.stderr
    assert (bat / "charge_control_end_threshold").read_text().strip() == "80"
    assert "BAT0: charges up to 80%" in r.stdout


def test_start_threshold_is_moved_below_the_limit(tmp_path):
    """ThinkPads refuse an end threshold at or below the start one."""
    ps = tmp_path / "ps"
    bat = supply(ps, "BAT0", "Battery", charge_control_end_threshold="100",
                 charge_control_start_threshold="96")
    assert run(ps, "LIMIT=60\n").returncode == 0
    assert (bat / "charge_control_start_threshold").read_text().strip() == "55"
    assert (bat / "charge_control_end_threshold").read_text().strip() == "60"


def test_a_low_start_threshold_is_left_alone(tmp_path):
    ps = tmp_path / "ps"
    bat = supply(ps, "BAT0", "Battery", charge_control_end_threshold="100",
                 charge_control_start_threshold="40")
    run(ps, "LIMIT=80\n")
    assert (bat / "charge_control_start_threshold").read_text().strip() == "40"


def test_off_charges_to_full_again(tmp_path):
    ps = tmp_path / "ps"
    bat = supply(ps, "BAT0", "Battery", charge_control_end_threshold="80",
                 charge_control_start_threshold="75")
    assert run(ps, "LIMIT=off\n").returncode == 0
    assert (bat / "charge_control_end_threshold").read_text().strip() == "100"
    assert (bat / "charge_control_start_threshold").read_text().strip() == "75"


def test_dell_switches_to_custom_mode(tmp_path):
    ps = tmp_path / "ps"
    bat = supply(ps, "BAT0", "Battery", charge_control_end_threshold="100",
                 charge_types="[Standard] Adaptive Custom")
    run(ps, "LIMIT=80\n")
    assert (bat / "charge_types").read_text().strip() == "Custom"


def test_no_battery_and_no_driver_support_are_not_errors(tmp_path):
    ps = tmp_path / "ps"
    supply(ps, "AC", "Mains")
    r = run(ps, "LIMIT=80\n")
    assert r.returncode == 0 and "no battery" in r.stdout
    supply(ps, "BAT1", "Battery")  # no threshold files: the driver offers none
    r = run(ps, "LIMIT=80\n")
    assert r.returncode == 0 and "BAT1: this laptop offers no charge limit" in r.stdout


def test_bad_limits_are_refused(tmp_path):
    ps = tmp_path / "ps"
    bat = supply(ps, "BAT0", "Battery", charge_control_end_threshold="100")
    for bad in ("LIMIT=abc\n", "LIMIT=5\n", "LIMIT=101\n", "LIMIT=\n"):
        r = run(ps, bad)
        assert r.returncode == 1 and "not a number from 20 to 100" in r.stderr, bad
    assert (bat / "charge_control_end_threshold").read_text().strip() == "100"


def test_units_are_server_only():
    """The Desktop inherits the Server overlay: these units must not run there."""
    for unit in ("pengyos-battery-limit.service", "pengyos-console-blank.service"):
        text = (OVERLAY / "etc" / "systemd" / "system" / unit).read_text()
        assert "ConditionOSRelease=|VARIANT_ID=server\n" in text
        assert "ConditionOSRelease=|VARIANT_ID=server-nvidia\n" in text


def test_desktop_keeps_suspend_on_lid_close():
    name = pathlib.Path("usr/lib/systemd/logind.conf.d/60-pengyos-lid.conf")
    server = (OVERLAY / name).read_text()
    desktop = (ROOT / "buildroot-external/board/desktop/rootfs-overlay" / name).read_text()
    assert "HandleLidSwitch=ignore" in server
    assert "HandleLidSwitch=suspend" in desktop


def _preset(unit: str, boards: list[str]) -> str:
    """systemd's rule: all preset files in name order, the first line naming the unit wins."""
    files = sorted((p for b in boards for p in
                    (ROOT / "buildroot-external/board" / b / "rootfs-overlay/usr/lib/systemd/system-preset")
                    .glob("*.preset")), key=lambda p: p.name)
    for f in files:
        for line in f.read_text().splitlines():
            words = line.split()
            if len(words) == 2 and words[1] == unit:
                return words[0]
    return "enable"  # Buildroot/systemd's catch-all


def test_dbus_wpa_supplicant_is_off_on_the_server_only():
    assert _preset("wpa_supplicant.service", ["core", "server"]) == "disable"
    assert _preset("wpa_supplicant.service", ["core", "server", "desktop"]) == "enable"
