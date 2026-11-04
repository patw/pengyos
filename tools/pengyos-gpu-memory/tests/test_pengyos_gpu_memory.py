"""Tests for `pengyos-gpu-memory`, the shipped tool in the Server overlay.

The suite runs the real script against a sandbox root (`--root DIR`) holding a
fake /proc/meminfo, /sys/module/ttm/parameters/pages_limit and /sys/class/drm.
No root, no GPU.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO = TOOL_DIR.parents[1]
SCRIPT = REPO / "buildroot-external/board/server/rootfs-overlay/usr/bin/pengyos-gpu-memory"
FEATURES = REPO / "buildroot-external/board/server/features-required.txt"
GIB = 1 << 30
CONF = "etc/modprobe.d/60-pengyos-gpu-memory.conf"


def machine(tmp: Path, ram_gib=128, pages_limit=None, cards=(("card1", "amdgpu", 512 << 20),)) -> Path:
    r = tmp / "root"
    (r / "proc").mkdir(parents=True)
    kib = ram_gib * GIB // 1024 - 300 * 1024  # MemTotal is a little under the nominal size
    (r / "proc/meminfo").write_text(f"MemTotal:       {kib} kB\nMemFree:  1 kB\n")
    t = r / "sys/module/ttm/parameters"
    t.mkdir(parents=True)
    (t / "pages_limit").write_text(f"{pages_limit if pages_limit is not None else kib // 8}\n")
    drm = r / "sys/class/drm"
    drm.mkdir(parents=True)
    for i, (card, drv, vram) in enumerate(cards):
        dev = r / f"sys/devices/pci0000:00/0000:c{i}:00.0"
        (dev).mkdir(parents=True)
        drvdir = r / f"sys/bus/pci/drivers/{drv}"
        drvdir.mkdir(parents=True, exist_ok=True)
        (dev / "driver").symlink_to(drvdir)
        (dev / "mem_info_vram_total").write_text(f"{vram}\n")
        (dev / "mem_info_gtt_total").write_text(f"{64 * GIB}\n")
        (drm / card).mkdir()
        (drm / card / "device").symlink_to(dev)
        (drm / f"{card}-DP-1").mkdir()
    return r


def run(r: Path, *args, check=True):
    pr = subprocess.run([sys.executable, str(SCRIPT), "--root", str(r), *args],
                        capture_output=True, text=True)
    if check:
        assert pr.returncode == 0, pr.stderr
    return pr


def status(r: Path) -> dict:
    return json.loads(run(r, "status", "--json").stdout)


def test_status_reads_the_machine(tmp_path):
    r = machine(tmp_path)
    s = status(r)
    assert s["configured"] is None
    assert [c["card"] for c in s["cards"]] == ["card1"]  # connectors (card1-DP-1) skipped
    assert s["cards"][0]["vram"] == 512 << 20 and s["cards"][0]["pci"] == "0000:c0:00.0"
    assert s["ceiling"] == s["memtotal"] - 4 * GIB
    assert "kernel default" in run(r).stdout


def test_set_max_writes_ttm_and_gttsize(tmp_path):
    r = machine(tmp_path)
    run(r, "set", "max")
    txt = (r / CONF).read_text()
    total = status(r)["memtotal"]
    want = total - 4 * GIB
    assert f"options ttm pages_limit={want // 4096}\n" in txt
    assert f"options amdgpu gttsize={want // (1 << 20)}\n" in txt
    assert f"MemTotal={total}" in txt
    assert oct((r / CONF).stat().st_mode & 0o777) == "0o644"


def test_set_sizes(tmp_path):
    r = machine(tmp_path)
    run(r, "set", "100G")
    assert "pages_limit=26214400" in (r / CONF).read_text()
    run(r, "set", "50%")
    pl = int((r / CONF).read_text().split("pages_limit=")[1].split()[0])
    assert abs(pl * 4096 - status(r)["memtotal"] / 2) < 4096
    run(r, "set", "65536M")
    assert "gttsize=65536\n" in (r / CONF).read_text()


def test_refuses_out_of_range_and_bad_size(tmp_path):
    r = machine(tmp_path)
    assert run(r, "set", "126G", check=False).returncode == 4  # above RAM - 4 GiB
    assert run(r, "set", "0.5G", check=False).returncode == 4
    assert run(r, "set", "lots", check=False).returncode == 2
    assert not (r / CONF).exists()


def test_no_amdgpu_needs_force(tmp_path):
    r = machine(tmp_path, cards=(("card0", "i915", 0),))
    pr = run(r, "set", "max", check=False)
    assert pr.returncode == 3 and "no amdgpu" in pr.stderr
    assert "no amdgpu GPU found" in " ".join(status(r)["notes"])
    run(r, "set", "max", "--force")
    assert (r / CONF).exists()


def test_pending_reboot_and_ram_mismatch_notes(tmp_path):
    r = machine(tmp_path)
    run(r, "set", "64G")
    notes = " ".join(status(r)["notes"])
    assert "reboot" in notes
    # the same file on a machine with 96 GB of RAM
    r2 = machine(tmp_path / "b", ram_gib=96, pages_limit=64 * GIB // 4096)
    (r2 / "etc/modprobe.d").mkdir(parents=True)
    (r2 / CONF).write_text((r / CONF).read_text())
    notes = " ".join(status(r2)["notes"])
    assert "computed for" in notes and "reboot" not in notes


def test_big_carve_out_gets_the_bios_note(tmp_path):
    r = machine(tmp_path, cards=(("card0", "amdgpu", 64 * GIB),))
    assert "UMA frame buffer" in " ".join(status(r)["notes"])
    r = machine(tmp_path / "b")
    assert not any("UMA" in n for n in status(r)["notes"])


def test_reset(tmp_path):
    r = machine(tmp_path)
    run(r, "set", "max")
    assert "removed" in run(r, "reset").stdout
    assert not (r / CONF).exists()
    assert "nothing to reset" in run(r, "reset").stdout


def test_not_root_is_refused():
    if os.geteuid() == 0:
        return
    pr = subprocess.run([sys.executable, str(SCRIPT), "reset"], capture_output=True, text=True)
    assert pr.returncode == 5 and "sudo" in pr.stderr


def test_image_claims_it():
    assert "usr/bin/pengyos-gpu-memory" in FEATURES.read_text()
    assert os.access(SCRIPT, os.X_OK)
