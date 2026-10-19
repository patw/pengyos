"""board/common/prune-iwlwifi.py keeps, per Intel Wi-Fi device family, the one
firmware file the kernel would load, and nothing it would need goes."""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "buildroot-external" / "board" / "common" / "prune-iwlwifi.py"


def tree(tmp: pathlib.Path, names: list[str], firmware: list[str]) -> pathlib.Path:
    mod = tmp / "lib/modules/7.2.11/kernel/drivers/net/wireless/intel/iwlwifi"
    mod.mkdir(parents=True)
    # A module's .modinfo is NUL-separated key=value strings.
    (mod / "iwlwifi.ko").write_bytes(
        b"\x7fELF junk\x00" + b"".join(b"firmware=" + n.encode() + b"\x00" for n in names) + b"license=GPL\x00")
    fw = tmp / "usr/lib/firmware/intel/iwlwifi"
    fw.mkdir(parents=True)
    for f in firmware:
        (fw / f).write_bytes(b"x" * 1024)
    return fw


def run(tmp: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), str(tmp)], capture_output=True, text=True)


def test_keeps_the_newest_loadable_file_per_family(tmp_path):
    fw = tree(tmp_path,
              ["iwlwifi-so-a0-hr-b0-89.ucode", "iwlwifi-gl-c0-fm-c0-c106.ucode", "iwlwifi-cc-a0-77.ucode"],
              ["iwlwifi-so-a0-hr-b0-79.ucode", "iwlwifi-so-a0-hr-b0-86.ucode", "iwlwifi-so-a0-hr-b0-89.ucode",
               # newer than this kernel can load: the kernel would use 101
               "iwlwifi-gl-c0-fm-c0-c101.ucode", "iwlwifi-gl-c0-fm-c0-c112.ucode",
               # the plain-numbered scheme of the same chip is its own family
               "iwlwifi-gl-c0-fm-c0-98.ucode", "iwlwifi-gl-c0-fm-c0-101.ucode",
               # the kernel's maximum is missing: the newest below it stays
               "iwlwifi-cc-a0-72.ucode", "iwlwifi-cc-a0-74.ucode",
               # not named by the module at all: its newest file stays
               "iwlwifi-7265D-27.ucode", "iwlwifi-7265D-29.ucode",
               "iwlwifi-gl-c0-fm-c0.pnvm"])
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in fw.iterdir()) == [
        "iwlwifi-7265D-29.ucode",
        "iwlwifi-cc-a0-74.ucode",
        "iwlwifi-gl-c0-fm-c0-101.ucode",
        "iwlwifi-gl-c0-fm-c0-c101.ucode",
        "iwlwifi-gl-c0-fm-c0.pnvm",
        "iwlwifi-so-a0-hr-b0-89.ucode",
    ]


def test_an_unreadable_module_refuses_to_guess(tmp_path):
    fw = tmp_path / "usr/lib/firmware/intel/iwlwifi"
    fw.mkdir(parents=True)
    (fw / "iwlwifi-cc-a0-77.ucode").write_bytes(b"x")
    r = run(tmp_path)
    assert r.returncode == 1
    assert (fw / "iwlwifi-cc-a0-77.ucode").exists()


def test_a_module_that_names_no_firmware_keeps_everything(tmp_path):
    # Core and Server build iwlwifi without IWLMVM: the module loads, its
    # .modinfo names no firmware, and the prune has nothing to reason from.
    # Keeping every file costs space and can never cost a device, so it must not
    # fail the edition's build (it did, on the first 26.10.33 core build).
    fw = tree(tmp_path, [], ["iwlwifi-cc-a0-77.ucode", "iwlwifi-cc-a0-74.ucode"])
    r = run(tmp_path)
    assert r.returncode == 0
    assert "names no firmware" in r.stdout
    assert sorted(f.name for f in fw.iterdir()) == ["iwlwifi-cc-a0-74.ucode", "iwlwifi-cc-a0-77.ucode"]
def test_no_iwlwifi_firmware_is_nothing_to_do(tmp_path):
    assert run(tmp_path).returncode == 0
