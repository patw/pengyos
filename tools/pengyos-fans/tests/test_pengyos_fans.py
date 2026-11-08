"""Tests for `pengyos-fans`, the shipped tool in the Server overlay.

The suite runs the real script against a sandbox root (`--root DIR`) holding fake
hwmon chips. A background "physics" thread makes each fake fan follow the PWM that
drives it, so `detect` sees fans spin up and down as on a real board. No root, no
hardware.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO = TOOL_DIR.parents[1]
OVERLAY = REPO / "buildroot-external/board/server/rootfs-overlay"
SCRIPT = OVERLAY / "usr/bin/pengyos-fans"
FAST = ["--spin", "0.4", "--settle", "0.15", "--tick", "0.05"]

# behopc's NCT6798D as the survey found it: pwm1/2/4 drive fan1/2/4, pwm5 and pwm6
# drive nothing with a tach. Curves: 5 points, the last critical.
NCT_CURVE = [(30, 51), (50, 102), (70, 178), (75, 255), (125, 255)]


class Machine:
    def __init__(self, root: Path):
        self.root = root
        self.links: dict[Path, Path] = {}  # pwm file -> fan input it drives
        self.n = 0
        (root / "sys/class/hwmon").mkdir(parents=True)
        (root / "etc/pengyos").mkdir(parents=True)
        (root / "run").mkdir()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def chip(self, name: str, device: str, temps=None) -> Path:
        dev = self.root / "sys/devices" / device
        h = dev / "hwmon" / f"hwmon{self.n}"
        h.mkdir(parents=True)
        (h / "name").write_text(name + "\n")
        (h / "device").symlink_to(dev)
        (self.root / f"sys/class/hwmon/hwmon{self.n}").symlink_to(h)
        self.n += 1
        for i, (label, t) in enumerate(temps or [], start=1):
            (h / f"temp{i}_input").write_text(f"{int(t * 1000)}\n")
            if label:
                (h / f"temp{i}_label").write_text(label + "\n")
        return h

    def pwm(self, h: Path, n: int, value=58, enable="5", curve=NCT_CURVE, fan: int | None = None,
            idle=500, label=None, readable=True, stall=0):
        (h / f"pwm{n}").write_text(f"{value}\n" if readable else "")
        if enable is not None:
            (h / f"pwm{n}_enable").write_text(f"{enable}\n")
        (h / f"pwm{n}_temp_sel").write_text("1\n")
        for i, (t, d) in enumerate(curve or [], start=1):
            (h / f"pwm{n}_auto_point{i}_temp").write_text(f"{t * 1000}\n")
            (h / f"pwm{n}_auto_point{i}_pwm").write_text(f"{d}\n")
        if fan is not None:
            (h / f"fan{fan}_input").write_text(f"{idle}\n")
            if label:
                (h / f"fan{fan}_label").write_text(label + "\n")
            self.links[h / f"pwm{n}"] = (h / f"fan{fan}_input", h / f"pwm{n}_enable", idle, stall)

    def physics(self):
        """In manual mode a fan turns at 300 + 1250 * duty RPM (1550 at full) and
        stops below its stall duty (percent); otherwise at its idle speed, which is
        whatever the chip's own curve gives it."""
        def loop():
            while not self.stop.is_set():
                for pwm, (fan, en, idle, stall) in self.links.items():
                    try:
                        v = int((pwm.read_text().strip() or "0"))
                        e = en.read_text().strip() if en.exists() else "1"
                    except (OSError, ValueError):
                        continue
                    if e != "1":
                        rpm = idle
                    elif v * 100 < stall * 255:
                        rpm = 0
                    else:
                        rpm = 300 + int(1250 * v / 255)
                    fan.write_text(f"{rpm}\n")
                time.sleep(0.01)
        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join()

    def conf(self) -> dict:
        return json.loads((self.root / "etc/pengyos/fans.json").read_text())

    def r(self, path: str) -> str:
        return (self.root / path.lstrip("/")).read_text().strip()


@pytest.fixture
def m(tmp_path):
    mach = Machine(tmp_path / "root")
    yield mach
    mach.close()


def run(m: Machine, *args, check=True, env=None):
    pr = subprocess.run([sys.executable, str(SCRIPT), "--root", str(m.root), *FAST, *args],
                        capture_output=True, text=True, env=env)
    if check and pr.returncode != 0:
        raise AssertionError(f"exit {pr.returncode}\n{pr.stdout}\n{pr.stderr}")
    return pr


def behopc(m: Machine) -> Path:
    m.chip("coretemp", "platform/coretemp.0", [("Package id 0", 45), ("Core 0", 44)])
    h = m.chip("nct6798", "platform/nct6775.656", [("SYSTIN", 30), ("CPUTIN", 40)])
    for n, fan, idle in ((1, 1, 538), (2, 2, 378), (4, 4, 505)):
        m.pwm(h, n, fan=fan, idle=idle)
    m.pwm(h, 5, value=156, curve=[(30, 153), (50, 178), (65, 216), (75, 255), (125, 255)])
    m.pwm(h, 6, value=255, curve=[(0, 255), (100, 255), (100, 255), (100, 255), (100, 255)])
    m.physics()
    return h


def nct_path(m, n=1):
    return "sys/class/hwmon/hwmon1/" + (f"pwm{n}" if n else "")


# --- status before anything ------------------------------------------------------

def test_a_mini_pc_has_nothing_to_control(m):
    m.chip("coretemp", "platform/coretemp.0", [("Package id 0", 45)])
    out = run(m, "status").stdout
    assert "run by its firmware; there is nothing to control" in out
    assert json.loads(run(m, "status", "--json").stdout)["kind"] == "none"
    pr = run(m, "profile", "quiet", check=False)
    assert pr.returncode == 3


def test_a_board_says_to_detect_first(m):
    behopc(m)
    out = run(m, "status").stdout
    assert "Not mapped yet: nct6798:pwm1, nct6798:pwm2, nct6798:pwm4, nct6798:pwm5, nct6798:pwm6" in out
    assert "sudo pengyos-fans detect" in out
    pr = run(m, "profile", "quiet", check=False)
    assert pr.returncode == 4 and "detect" in pr.stderr


def test_gpu_and_psu_fans_are_not_ours(m):
    g = m.chip("amdgpu", "pci0000:00/0000:03:00.0")
    m.pwm(g, 1, enable="2", curve=None, fan=1)
    p = m.chip("corsairpsu", "pci0000:00/usb1/1-4/1-4:1.0")
    m.pwm(p, 1, enable="0", curve=None, fan=1)
    assert json.loads(run(m, "status", "--json").stdout)["kind"] == "none"


# --- detect ----------------------------------------------------------------------

def test_detect_maps_each_control_to_its_fan_and_puts_it_back(m):
    behopc(m)
    before = {f: m.r(nct_path(m, 0) + f) for f in os.listdir(m.root / nct_path(m, 0)) if f.startswith("pwm")}
    out = run(m, "detect").stdout
    assert "nct6798:pwm1: drives nct6798:fan1 (550 RPM at 20% .. 1550 RPM at 100%)" in out
    assert "nct6798:pwm5: no fan answered" in out
    c = m.conf()["channels"]
    assert {k: v["fan"] for k, v in c.items()} == {
        "nct6798:pwm1": "fan1", "nct6798:pwm2": "fan2", "nct6798:pwm4": "fan4",
        "nct6798:pwm5": None, "nct6798:pwm6": None}
    assert c["nct6798:pwm1"]["role"] == "fan" and c["nct6798:pwm5"]["role"] == "none"
    assert c["nct6798:pwm1"]["follow"] == "chip"
    assert c["nct6798:pwm1"]["baseline"] == {"enable": "5", "pwm": "58",
                                             "curve": [list(x) for x in NCT_CURVE]}
    assert c["nct6798:pwm1"]["device"] == "/sys/devices/platform/nct6775.656"
    after = {f: m.r(nct_path(m, 0) + f) for f in before}
    # Every enable and curve point back as found. A bare pwmN in curve mode is the
    # chip's own output (a real chip rewrites it at once; this fake does not).
    def settings(d):
        return {k: v for k, v in d.items() if not k[3:].isdigit()}
    assert settings(after) == settings(before)
    assert len(settings(before)) == 5 * (1 + 1 + 2 * 5)
    assert m.conf()["profile"] == "firmware"


def test_a_fan_that_stalls_at_the_floor_gets_its_own_minimum(m):
    # behotv's fan4 stopped at 20 % on the first hardware test (2026-11-08)
    m.chip("k10temp", "pci0000:00/0000:00:18.3", [("Tctl", 40)])
    h = m.chip("nct6797", "platform/nct6775.2592", [("SYSTIN", 30)])
    m.pwm(h, 4, value=122, fan=4, idle=725, stall=35)
    m.physics()
    out = run(m, "detect").stdout
    assert "stops below 40%, so never under 45%" in out
    assert m.conf()["channels"]["nct6797:pwm4"]["min_duty"] == 45
    run(m, "profile", "quiet")
    assert [d for _, d in points(m, 4)][:2] == [round(45 * 2.55), round(45 * 2.55)]


def test_a_fan_already_near_full_speed_is_still_found(m):
    # behoserver under load: case fans already at ~1450 RPM on the BIOS curve
    m.chip("k10temp", "pci0000:00/0000:00:18.3", [("Tctl", 75)])
    h = m.chip("nct6798", "platform/nct6775.656", [("SYSTIN", 30)])
    m.pwm(h, 1, value=193, fan=1, idle=1450)
    m.physics()
    out = run(m, "detect").stdout
    assert "nct6798:pwm1: drives nct6798:fan1" in out
    assert m.conf()["channels"]["nct6798:pwm1"]["role"] == "fan"


def test_one_fan_belongs_to_one_control():
    # behotv 2026-11-08: pwm5 seemed to drive fan4, which pwm4 drives
    tool = load_tool()
    found = {
        "nct6797:pwm4": {"fan": "fan4", "role": "fan", "_swing": 1203},
        "nct6797:pwm5": {"fan": "fan4", "role": "fan", "_swing": 195},
        "nct6797:pwm2": {"fan": "fan2", "role": "fan", "_swing": 1206},
    }
    said = tool.one_fan_each(found)
    assert said == ["  nct6797:pwm5: moves nct6797:fan4 less than nct6797:pwm4 does; left alone"]
    assert found["nct6797:pwm5"] == {"fan": None, "role": "none"}
    assert found["nct6797:pwm4"] == {"fan": "fan4", "role": "fan"}


def test_detect_keeps_names_and_roles(m):
    behopc(m)
    run(m, "detect")
    run(m, "name", "nct6798:pwm1", "CPU")
    run(m, "role", "nct6798:pwm6", "pump")
    run(m, "detect")
    c = m.conf()["channels"]
    assert c["nct6798:pwm1"]["name"] == "CPU"
    assert c["nct6798:pwm6"]["role"] == "pump"


def test_detect_marks_a_pump_by_its_label(m):
    m.chip("k10temp", "pci0000:00/0000:00:18.3", [("Tctl", 50)])
    h = m.chip("x53", "pci0000:00/usb1/1-2/1-2:1.0", [("Coolant temp", 31)])
    m.pwm(h, 1, value=150, enable="2", curve=None, fan=1, idle=1800, label="Pump speed")
    m.pwm(h, 2, value=80, enable="2", curve=None, fan=2, idle=600, label="Fan speed")
    m.links[h / "pwm1"] = (h / "fan1_input", h / "pwm1_enable", 1300, 0)
    m.physics()
    run(m, "detect")
    c = m.conf()["channels"]
    assert c["x53:pwm1"]["role"] == "pump"
    assert c["x53:pwm2"]["role"] == "fan" and c["x53:pwm2"]["follow"] == "cpu"
    assert m.r("sys/class/hwmon/hwmon1/pwm1_enable") == "2"


# --- profiles on a chip curve ----------------------------------------------------

def points(m, n):
    h = m.root / nct_path(m, 0)
    i, out = 1, []
    while (h / f"pwm{n}_auto_point{i}_pwm").exists():
        out.append((int(m.r(nct_path(m, 0) + f"pwm{n}_auto_point{i}_temp")) // 1000,
                    int(m.r(nct_path(m, 0) + f"pwm{n}_auto_point{i}_pwm"))))
        i += 1
    return out


def test_quiet_is_written_into_the_chip_and_the_critical_point_stays(m):
    behopc(m)
    run(m, "detect")
    out = run(m, "profile", "quiet").stdout
    assert "Profile: quiet." in out and "service drives" not in out
    assert points(m, 1) == [(40, 51), (60, 89), (75, 153), (85, 255), (125, 255)]
    assert m.r(nct_path(m, 1) + "_enable") == "5"
    assert points(m, 5) == [(30, 153), (50, 178), (65, 216), (75, 255), (125, 255)]  # role none
    assert m.conf()["profile"] == "quiet"


def test_full_and_back_to_firmware_byte_for_byte(m):
    behopc(m)
    run(m, "detect")
    run(m, "profile", "full")
    assert [d for _, d in points(m, 2)] == [255] * 5
    run(m, "profile", "firmware")
    assert points(m, 2) == NCT_CURVE
    assert m.r(nct_path(m, 2) + "_enable") == "5"


def test_firmware_at_boot_writes_nothing(m):
    behopc(m)
    run(m, "detect")
    (m.root / nct_path(m, 1)).write_text("99\n")
    run(m, "apply")
    run(m, "run", "--ticks", "2")
    run(m, "release")
    assert m.r(nct_path(m, 1)) == "99"
    assert points(m, 1) == NCT_CURVE


def test_a_short_chip_curve_keeps_the_ends_of_the_profile(m):
    m.chip("coretemp", "platform/coretemp.0", [("Package id 0", 45)])
    h = m.chip("nct6775", "platform/nct6775.656")
    m.pwm(h, 1, curve=[(30, 60), (60, 150), (90, 255)], fan=1)
    m.physics()
    run(m, "detect")
    run(m, "profile", "balanced")
    pts = [(int(m.r(f"sys/class/hwmon/hwmon1/pwm1_auto_point{i}_temp")) // 1000,
            int(m.r(f"sys/class/hwmon/hwmon1/pwm1_auto_point{i}_pwm"))) for i in (1, 2, 3)]
    assert pts == [(35, 76), (80, 255), (90, 255)]


# --- software curves -------------------------------------------------------------

def commander(m: Machine, cpu=45.0) -> Path:
    m.chip("coretemp", "platform/coretemp.0", [("Package id 0", cpu)])
    h = m.chip("corsaircpro", "pci0000:00/0000:00:14.0/usb1/1-9/1-9:1.0", [(None, 28)])
    for n in (1, 2):
        m.pwm(h, n, enable=None, curve=None, fan=n, idle=0, readable=False)
    m.physics()
    return h


def set_cpu(m, t):
    (m.root / "sys/class/hwmon/hwmon0/temp1_input").write_text(f"{int(t * 1000)}\n")


def cp(m, n):
    return int(m.r(f"sys/class/hwmon/hwmon1/pwm{n}"))


def test_a_usb_controller_gets_a_software_curve(m):
    commander(m)
    out = run(m, "detect").stdout
    assert "corsaircpro:pwm1: drives corsaircpro:fan1" in out
    out = run(m, "profile", "balanced").stdout
    assert "The service drives corsaircpro:pwm1, corsaircpro:pwm2." in out
    # 45 °C on balanced: 30 + (45-35)*(15/20) = 37.5 %
    assert cp(m, 1) == round(37.5 * 255 / 100)
    st = json.loads(run(m, "status", "--json").stdout)
    assert st["channels"][0]["control"] == "software curve (cpu)"


def load_tool():
    from importlib.machinery import SourceFileLoader
    import importlib.util
    loader = SourceFileLoader("pengyos_fans", str(SCRIPT))
    spec = importlib.util.spec_from_loader("pengyos_fans", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_the_service_ramps_down_slowly_and_up_at_once(m):
    commander(m, cpu=80)
    run(m, "detect")
    run(m, "profile", "balanced")
    assert cp(m, 1) == 255
    tool = load_tool()
    tool.ROOT, tool.REALROOT = str(m.root), os.path.realpath(m.root)
    cfg, last, fails = tool.load(), {"corsaircpro:pwm1": 100.0}, {}
    set_cpu(m, 35)  # wants 30 %: down 4 points a tick
    seen = []
    for _ in range(3):
        tool.tick(cfg, last, fails)
        seen.append(round(last["corsaircpro:pwm1"]))
    assert seen == [96, 92, 88]
    set_cpu(m, 80)  # wants 100 %: at once
    tool.tick(cfg, last, fails)
    assert cp(m, 1) == 255


def test_the_service_holds_its_duty_while_running(m):
    commander(m, cpu=80)
    run(m, "detect")
    run(m, "profile", "balanced")
    set_cpu(m, 35)
    p = subprocess.Popen([sys.executable, str(SCRIPT), "--root", str(m.root), "--tick", "0.05", "run"],
                         stdout=subprocess.PIPE, text=True)
    time.sleep(1.5)
    mid = cp(m, 1)
    set_cpu(m, 85)
    time.sleep(0.5)
    hot = cp(m, 1)
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=10)
    assert round(30 * 255 / 100) <= mid < 240
    assert hot == 255
    assert cp(m, 1) == 255  # stopped: full speed, never a low fixed duty


def test_a_lost_temperature_goes_to_full_speed(m):
    commander(m, cpu=40)
    run(m, "detect")
    run(m, "profile", "quiet")
    os.remove(m.root / "sys/class/hwmon/hwmon0/temp1_input")
    p = subprocess.Popen([sys.executable, str(SCRIPT), "--root", str(m.root), "--tick", "0.05", "run"],
                         stdout=subprocess.PIPE, text=True)
    time.sleep(1.0)
    during = cp(m, 1)
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=10)
    assert during == 255


def test_never_below_the_floor(m):
    commander(m, cpu=20)
    run(m, "detect")
    run(m, "profile", "quiet")
    assert cp(m, 1) == round(20 * 255 / 100)


def test_follow_gpu_takes_a_chip_channel_into_software_and_release_returns_the_curve(m, tmp_path):
    behopc(m)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    smi = bin_ / "nvidia-smi"
    smi.write_text("#!/bin/sh\necho 75\n")
    smi.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}")
    run(m, "detect", env=env)
    run(m, "profile", "balanced", env=env)
    run(m, "follow", "nct6798:pwm2", "cpu+gpu", env=env)
    run(m, "apply", env=env)
    assert m.r(nct_path(m, 2) + "_enable") == "1"
    # gpu 75 - 10 = 65 °C on balanced: 45 + (65-55)*25/15 = 61.7 %, above the cpu's 37.5
    assert int(m.r(nct_path(m, 2))) == round((45 + 10 * 25 / 15) * 255 / 100)
    assert "software curve (cpu+gpu)" in run(m, "status", env=env).stdout
    run(m, "release", env=env)
    assert m.r(nct_path(m, 2) + "_enable") == "5"
    assert points(m, 2) == [(35, 76), (55, 115), (70, 178), (80, 255), (125, 255)]


def test_cpu_plus_gpu_without_a_gpu_reading_is_the_cpu_curve(m, tmp_path):
    behopc(m)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    smi = bin_ / "nvidia-smi"
    smi.write_text("#!/bin/sh\necho 75\n")
    smi.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}")
    run(m, "detect")
    run(m, "profile", "balanced")
    run(m, "follow", "nct6798:pwm2", "cpu+gpu", env=env)
    smi.write_text("#!/bin/sh\nexit 9\n")  # the GPU went away
    run(m, "apply", env=env)
    assert int(m.r(nct_path(m, 2))) == round(37.5 * 255 / 100)


def test_follow_gpu_needs_a_gpu(m):
    behopc(m)
    run(m, "detect")
    pr = run(m, "follow", "nct6798:pwm2", "gpu", check=False, env=dict(os.environ, PATH="/usr/bin:/bin"))
    assert pr.returncode == 2 and "no GPU temperature" in pr.stderr


def test_a_fan_gets_its_own_curve_on_the_chip(m):
    behopc(m)
    run(m, "detect")
    run(m, "profile", "balanced")
    run(m, "curve", "nct6798:pwm2", "30:25", "60:40", "75:70", "85:100")
    assert points(m, 2) == [(30, 64), (60, 102), (75, 178), (85, 255), (125, 255)]
    assert points(m, 1)[0] == (35, 76)  # the others keep the profile
    st = json.loads(run(m, "status", "--json").stdout)
    row = next(r for r in st["channels"] if r["id"] == "nct6798:pwm2")
    assert row["own_curve"] and row["curve"] == [[30, 25], [60, 40], [75, 70], [85, 100]]
    assert row["control"].endswith(", own curve")
    assert row["chip_curve"] and row["max_points"] == 4 and row["min_duty"] == 20
    run(m, "profile", "full")
    assert [d for _, d in points(m, 2)] == [255] * 5  # full is full, own curve or not
    run(m, "profile", "quiet")
    run(m, "curve", "nct6798:pwm2", "default")
    assert points(m, 2)[0] == (40, 51)


def test_curves_are_checked(m):
    behopc(m)
    run(m, "detect")
    for bad in (["30:25"], ["60:20", "40:50", "80:100"], ["30:50", "60:40", "80:100"],
                ["30:25", "60:80"], ["30-25", "80:100"], ["30:25", "40:30", "50:40", "60:50", "80:100"]):
        pr = run(m, "curve", "nct6798:pwm1", *bad, check=False)
        assert pr.returncode == 2, bad
    assert "holds 4 points" in pr.stderr


def test_a_software_fan_follows_its_own_curve(m):
    commander(m, cpu=50)
    run(m, "detect")
    run(m, "profile", "quiet")
    run(m, "curve", "corsaircpro:pwm1", "40:30", "60:50", "80:100")
    assert cp(m, 1) == round(40 * 255 / 100)
    assert cp(m, 2) == round((20 + 10 * 15 / 20) * 255 / 100)  # quiet at 50 °C


def test_follow_chip_needs_a_chip_curve(m):
    commander(m)
    run(m, "detect")
    pr = run(m, "follow", "corsaircpro:pwm1", "chip", check=False)
    assert pr.returncode == 2
    pr = run(m, "name", "nosuch:pwm9", "x", check=False)
    assert pr.returncode == 6 and "corsaircpro:pwm1" in pr.stderr


def test_firmware_on_a_controller_without_a_curve_says_full_speed(m):
    commander(m)
    run(m, "detect")
    run(m, "profile", "quiet")
    out = run(m, "profile", "firmware").stdout
    assert "full speed (it has no curve of its own)" in out
    assert cp(m, 1) == 255


# --- status ----------------------------------------------------------------------

def test_status_table(m):
    behopc(m)
    run(m, "detect")
    run(m, "name", "nct6798:pwm1", "CPU")
    run(m, "profile", "quiet")
    out = run(m, "status").stdout
    assert out.splitlines()[0].startswith("Profile: quiet")
    line = next(l for l in out.splitlines() if "nct6798:pwm1" in l)
    assert "CPU" in line and "fan1" in line and "RPM" in line and "chip curve (SYSTIN)" in line
    assert "left alone" in next(l for l in out.splitlines() if "nct6798:pwm6" in l)
    st = json.loads(run(m, "status", "--json").stdout)
    assert st["kind"] == "channels" and st["undetected"] == []


def test_a_second_chip_of_the_same_kind_gets_its_own_name(m):
    m.chip("coretemp", "platform/coretemp.0", [("Package id 0", 45)])
    for port in ("1-9", "1-10"):
        h = m.chip("corsaircpro", f"pci0000:00/usb1/{port}/{port}:1.0")
        m.pwm(h, 1, enable=None, curve=None, fan=1, idle=0, readable=False)
    st = json.loads(run(m, "status", "--json").stdout)
    assert st["undetected"] == ["corsaircpro:pwm1", "corsaircpro.2:pwm1"]


# --- laptops ---------------------------------------------------------------------

def test_a_laptop_gets_the_firmware_profiles(m):
    m.chip("k10temp", "pci0000:00/0000:00:18.3", [("Tctl", 50)])
    acpi = m.root / "sys/firmware/acpi"
    acpi.mkdir(parents=True)
    (acpi / "platform_profile").write_text("balanced\n")
    (acpi / "platform_profile_choices").write_text("low-power balanced performance custom\n")
    out = run(m, "status").stdout
    assert "Firmware fan profile: balanced" in out
    run(m, "profile", "quiet")
    assert m.r("sys/firmware/acpi/platform_profile") == "low-power"
    (acpi / "platform_profile").write_text("balanced\n")
    run(m, "apply")  # at boot, without power-profiles-daemon
    assert m.r("sys/firmware/acpi/platform_profile") == "low-power"


# --- privilege -------------------------------------------------------------------

def test_changes_need_root():
    if os.geteuid() == 0:
        pytest.skip("running as root")
    pr = subprocess.run([sys.executable, str(SCRIPT), "profile", "quiet"], capture_output=True, text=True)
    assert pr.returncode == 5 and "sudo" in pr.stderr
