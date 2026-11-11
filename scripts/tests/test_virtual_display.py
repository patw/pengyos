"""board/desktop's pengyos-virtual-display: a virtual 1080p display when a Desktop
boots with no monitor, against a fake /sys/class/drm and DRM debugfs."""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "buildroot-external" / "board" / "desktop" / "rootfs-overlay"
SCRIPT = OVERLAY / "usr" / "libexec" / "pengyos" / "pengyos-virtual-display"
EDID = OVERLAY / "usr" / "share" / "pengyos" / "virtual-display.edid"


def gpu(tmp: pathlib.Path, ports: dict[str, str], card: int = 0, modes: bool = True):
    """Fake connectors cardN-PORT with STATUS, and their debugfs dirs."""
    drm, dbg = tmp / "drm", tmp / "dri"
    for name, status in ports.items():
        c = drm / f"card{card}-{name}"
        c.mkdir(parents=True)
        (c / "status").write_text(status + "\n")
        (c / "modes").write_text("1920x1080\n" if modes else "")
        d = dbg / str(card) / name
        d.mkdir(parents=True)
        (d / "edid_override").write_text("")
    return drm, dbg


def run(tmp: pathlib.Path, mode: str = "boot", conf: str = "VIRTUAL_DISPLAY=auto\n"):
    (tmp / "display.conf").write_text(conf)
    env = {"PENGYOS_DRM": str(tmp / "drm"), "PENGYOS_DRI_DEBUG": str(tmp / "dri"),
           "PENGYOS_VIRTUAL_EDID": str(EDID), "PENGYOS_VIRTUAL_STATE": str(tmp / "run" / "vd"),
           "PENGYOS_DISPLAY_CONF": str(tmp / "display.conf"),
           "PENGYOS_VIRTUAL_WAIT": "0", "PENGYOS_GPU_WAIT": "0", "PATH": "/usr/bin:/bin"}
    return subprocess.run(["sh", str(SCRIPT), mode], capture_output=True, text=True,
                          env=env, timeout=10)


def forced(tmp: pathlib.Path, port: str, card: int = 0) -> bool:
    status = (tmp / "drm" / f"card{card}-{port}" / "status").read_text().strip()
    edid = (tmp / "dri" / str(card) / port / "edid_override").read_bytes()
    return status == "on" and edid == EDID.read_bytes()


def test_no_monitor_forces_the_last_hdmi_port(tmp_path):
    gpu(tmp_path, {"HDMI-A-1": "disconnected", "HDMI-A-2": "disconnected",
                   "DP-1": "disconnected"})
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert forced(tmp_path, "HDMI-A-2")
    assert not forced(tmp_path, "HDMI-A-1")
    assert (tmp_path / "drm" / "card0-DP-1" / "status").read_text().strip() == "disconnected"
    assert "HDMI-A-2 is a virtual 1920x1080 display" in r.stdout
    assert (tmp_path / "run" / "vd").read_text().strip().endswith("card0-HDMI-A-2")


def test_a_connected_monitor_means_nothing_is_forced(tmp_path):
    gpu(tmp_path, {"HDMI-A-1": "connected", "HDMI-A-2": "disconnected"})
    r = run(tmp_path)
    assert "a monitor is connected" in r.stdout
    assert not forced(tmp_path, "HDMI-A-2")
    assert not (tmp_path / "run" / "vd").exists()


def test_a_vm_virtual_output_counts_as_a_monitor(tmp_path):
    gpu(tmp_path, {"Virtual-1": "connected"})
    assert "a monitor is connected" in run(tmp_path).stdout


def test_never_displayport_or_a_laptop_panel(tmp_path):
    gpu(tmp_path, {"DP-1": "disconnected", "eDP-1": "disconnected"})
    r = run(tmp_path)
    assert "no HDMI, DVI or VGA port" in r.stdout
    assert not (tmp_path / "run" / "vd").exists()


def test_dvi_then_vga_when_there_is_no_hdmi(tmp_path):
    gpu(tmp_path, {"VGA-1": "disconnected", "DVI-D-1": "disconnected"})
    run(tmp_path)
    assert forced(tmp_path, "DVI-D-1")


def test_second_card_uses_its_own_debugfs_dir(tmp_path):
    gpu(tmp_path, {"HDMI-A-1": "disconnected"}, card=1)
    run(tmp_path)
    assert forced(tmp_path, "HDMI-A-1", card=1)


def test_no_modes_after_forcing_is_undone(tmp_path):
    gpu(tmp_path, {"HDMI-A-1": "disconnected"}, modes=False)
    r = run(tmp_path)
    assert "gave forced HDMI-A-1 no modes" in r.stdout
    assert (tmp_path / "drm" / "card0-HDMI-A-1" / "status").read_text().strip() == "detect"
    assert (tmp_path / "dri" / "0" / "HDMI-A-1" / "edid_override").read_text().strip() == "reset"
    assert not (tmp_path / "run" / "vd").exists()


def test_no_gpu_driver_is_not_an_error(tmp_path):
    (tmp_path / "drm").mkdir()
    r = run(tmp_path)
    assert r.returncode == 0 and "no display connectors" in r.stdout


def test_off_in_the_conf(tmp_path):
    gpu(tmp_path, {"HDMI-A-1": "disconnected"})
    r = run(tmp_path, conf="VIRTUAL_DISPLAY=off\n")
    assert "VIRTUAL_DISPLAY=off" in r.stdout
    assert not forced(tmp_path, "HDMI-A-1")


def test_shipped_conf_is_auto(tmp_path):
    gpu(tmp_path, {"HDMI-A-1": "disconnected"})
    run(tmp_path, conf=(OVERLAY / "etc" / "pengyos" / "display.conf").read_text())
    assert forced(tmp_path, "HDMI-A-1")


def test_hotplug_releases_when_a_monitor_appears_elsewhere(tmp_path):
    gpu(tmp_path, {"HDMI-A-1": "disconnected", "HDMI-A-2": "disconnected"})
    run(tmp_path)
    assert forced(tmp_path, "HDMI-A-2")
    # our own forcing raises a hotplug too: with no other monitor it is a no-op
    run(tmp_path, "hotplug")
    assert forced(tmp_path, "HDMI-A-2")
    (tmp_path / "drm" / "card0-HDMI-A-1" / "status").write_text("connected\n")
    r = run(tmp_path, "hotplug")
    assert "HDMI-A-1 has a monitor" in r.stdout
    assert (tmp_path / "drm" / "card0-HDMI-A-2" / "status").read_text().strip() == "detect"
    assert (tmp_path / "dri" / "0" / "HDMI-A-2" / "edid_override").read_text().strip() == "reset"
    assert not (tmp_path / "run" / "vd").exists()


def test_hotplug_without_a_forced_port_does_nothing(tmp_path):
    gpu(tmp_path, {"HDMI-A-1": "connected", "HDMI-A-2": "disconnected"})
    r = run(tmp_path, "hotplug")
    assert r.returncode == 0 and r.stdout == ""
    assert (tmp_path / "drm" / "card0-HDMI-A-2" / "status").read_text().strip() == "disconnected"


def test_edid_matches_its_generator_and_is_1080p60():
    spec = importlib.util.spec_from_file_location("vedid", ROOT / "scripts" / "virtual-display-edid.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    b = EDID.read_bytes()
    assert b == mod.edid()
    assert len(b) == 128 and sum(b) % 256 == 0 and b[:8] == bytes.fromhex("00ffffffffffff00")
    t = b[54:72]
    clock_khz = (t[0] | t[1] << 8) * 10
    ha, va = t[2] | (t[4] >> 4) << 8, t[5] | (t[7] >> 4) << 8
    htotal, vtotal = ha + (t[3] | (t[4] & 0xF) << 8), va + (t[6] | (t[7] & 0xF) << 8)
    assert (ha, va, clock_khz) == (1920, 1080, 148500)
    assert round(clock_khz * 1000 / (htotal * vtotal)) == 60


def test_service_runs_before_greetd_and_is_enabled():
    unit = (OVERLAY / "etc/systemd/system/pengyos-virtual-display.service").read_text()
    assert "Before=greetd.service" in unit
    assert "ExecStart=/usr/libexec/pengyos/pengyos-virtual-display boot" in unit
    link = OVERLAY / "etc/systemd/system/multi-user.target.wants/pengyos-virtual-display.service"
    assert link.is_symlink() and link.resolve().name == "pengyos-virtual-display.service"
    rule = (OVERLAY / "usr/lib/udev/rules.d/60-pengyos-virtual-display.rules").read_text()
    assert 'RUN+="/usr/libexec/pengyos/pengyos-virtual-display hotplug"' in rule
