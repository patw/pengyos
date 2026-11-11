"""board/server's pengyos-battery-shutdown powers off at SHUTDOWN_AT percent on
battery, against a fake /sys/class/power_supply and a fake poweroff."""
from __future__ import annotations

import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "buildroot-external" / "board" / "server" / "rootfs-overlay"
SCRIPT = OVERLAY / "usr" / "libexec" / "pengyos" / "pengyos-battery-shutdown"
CONF = (OVERLAY / "etc" / "pengyos" / "battery.conf").read_text()


def supply(ps: pathlib.Path, name: str, kind: str, **attrs: str) -> pathlib.Path:
    d = ps / name
    d.mkdir(parents=True)
    (d / "type").write_text(kind + "\n")
    for k, v in attrs.items():
        (d / k).write_text(v + "\n")
    return d


def run(ps: pathlib.Path, conf: str = CONF) -> subprocess.CompletedProcess:
    c = ps.parent / "battery.conf"
    c.write_text(conf)
    env = {"PENGYOS_POWER_SUPPLY": str(ps), "PENGYOS_POWEROFF": "echo POWEROFF",
           "PATH": "/usr/bin:/bin"}
    return subprocess.run(["sh", str(SCRIPT), str(c), "--once"],
                          capture_output=True, text=True, env=env, timeout=10)


def test_shipped_conf_powers_off_at_5_percent_on_battery(tmp_path):
    ps = tmp_path / "ps"
    supply(ps, "AC", "Mains", online="0")
    supply(ps, "BAT0", "Battery", status="Discharging", capacity="5")
    r = run(ps)
    assert r.returncode == 0, r.stderr
    assert "POWEROFF" in r.stdout and "battery at 5%" in r.stdout


def test_above_the_threshold_does_nothing(tmp_path):
    ps = tmp_path / "ps"
    supply(ps, "BAT0", "Battery", status="Discharging", capacity="6")
    r = run(ps)
    assert "watching BAT0: powers off at 5%" in r.stdout
    assert "POWEROFF" not in r.stdout


def test_ac_online_never_powers_off(tmp_path):
    ps = tmp_path / "ps"
    supply(ps, "AC", "Mains", online="1")
    supply(ps, "BAT0", "Battery", status="Discharging", capacity="1")
    assert "POWEROFF" not in run(ps).stdout


def test_held_at_the_charge_limit_is_not_on_battery(tmp_path):
    # "Not charging" is AC holding pengyos-battery-limit's 80%.
    ps = tmp_path / "ps"
    supply(ps, "BAT0", "Battery", status="Not charging", capacity="2")
    assert "POWEROFF" not in run(ps).stdout


def test_two_packs_count_together(tmp_path):
    # A ThinkPad drains the external pack first: 1% + 60% is not "low".
    ps = tmp_path / "ps"
    supply(ps, "BAT0", "Battery", status="Discharging", energy_now="200000",
           energy_full="20000000", capacity="1")
    supply(ps, "BAT1", "Battery", status="Discharging", energy_now="12000000",
           energy_full="20000000", capacity="60")
    assert "POWEROFF" not in run(ps).stdout
    (ps / "BAT1" / "energy_now").write_text("600000\n")
    assert "battery at 2%" in run(ps).stdout


def test_a_mouse_battery_is_not_the_system(tmp_path):
    ps = tmp_path / "ps"
    supply(ps, "hidpp_battery_0", "Battery", scope="Device", status="Discharging", capacity="1")
    r = run(ps)
    assert "no battery: nothing to watch" in r.stdout and "POWEROFF" not in r.stdout


def test_off_disables_it(tmp_path):
    ps = tmp_path / "ps"
    supply(ps, "BAT0", "Battery", status="Discharging", capacity="1")
    r = run(ps, "SHUTDOWN_AT=off\n")
    assert r.returncode == 0 and "POWEROFF" not in r.stdout


def test_a_bad_value_is_refused(tmp_path):
    ps = tmp_path / "ps"
    supply(ps, "BAT0", "Battery", status="Discharging", capacity="1")
    for bad in ("SHUTDOWN_AT=ten\n", "SHUTDOWN_AT=80\n"):
        r = run(ps, bad)
        assert r.returncode == 1 and "POWEROFF" not in r.stdout


def test_battery_limit_still_reads_the_shared_conf(tmp_path):
    ps = tmp_path / "ps"
    bat = supply(ps, "BAT0", "Battery", charge_control_end_threshold="100")
    c = tmp_path / "battery.conf"
    c.write_text(CONF)
    r = subprocess.run(["sh", str(OVERLAY / "usr/libexec/pengyos/pengyos-battery-limit"), str(c)],
                       capture_output=True, text=True,
                       env={"PENGYOS_POWER_SUPPLY": str(ps), "PENGYOS_BATTERY_WAIT": "1",
                            "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    assert (bat / "charge_control_end_threshold").read_text().strip() == "80"
