"""The host half of AMD GPU compute in containers (2026-11-04, Strix Halo).

ROCm runs inside the container (gufo, halogen-flash-server, rocm/dev images); the
host must give it four things, and each was missing until 26.11.1:

1. /dev/kfd: CONFIG_HSA_AMD has no Kconfig default, so it was never on. Unified
   memory (HSA_AMD_SVM) needs DEVICE_PRIVATE, which needs ZONE_DEVICE, which needs
   MEMORY_HOTREMOVE (and so MEMORY_HOTPLUG). The Server
   fragment sets them; check-kernel-fragments.py --built proves they reach .config.
2. crun: rootless `--group-add keep-groups` works only with it.
3. no locked-memory limit: `--ulimit memlock=-1` cannot exceed systemd's 8 MiB.
4. pengyos-gpu-memory (tested in tools/pengyos-gpu-memory).

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "buildroot-external"


def test_kfd_in_the_server_fragment():
    frag = (EXT / "board/server/linux.fragment").read_text().splitlines()
    for line in ("CONFIG_HSA_AMD=y", "CONFIG_HSA_AMD_SVM=y", "CONFIG_DEVICE_PRIVATE=y",
                 "CONFIG_ZONE_DEVICE=y", "CONFIG_MEMORY_HOTREMOVE=y", "CONFIG_MEMORY_HOTPLUG=y"):
        assert line in frag, line


def test_crun_in_every_edition_with_podman():
    for d in EXT.glob("configs/pengyos_*_defconfig"):
        s = d.read_text()
        if "BR2_PACKAGE_PODMAN=y" in s:
            assert "BR2_PACKAGE_CRUN=y" in s, d.name


def test_memlock_unlimited_for_system_and_user_managers():
    for scope in ("system", "user"):
        f = EXT / f"board/server/rootfs-overlay/usr/lib/systemd/{scope}.conf.d/60-pengyos-memlock.conf"
        lines = [l.strip() for l in f.read_text().splitlines()]
        assert "[Manager]" in lines and "DefaultLimitMEMLOCK=infinity" in lines, scope


def test_desktop_lifts_pipewires_memlock_cap():
    """PipeWire's 25-pw-rlimits.conf caps @pipewire (every desktop user) at 4 GiB.

    pam_limits applies limits.d in name order and a later entry for the same group
    wins, so the Desktop override must sort after it and say unlimited.
    """
    d = EXT / "board/desktop/rootfs-overlay/etc/security/limits.d"
    f = d / "60-pengyos-memlock.conf"
    assert f.name > "25-pw-rlimits.conf"
    rules = [l.split() for l in f.read_text().splitlines() if l.strip() and not l.startswith("#")]
    assert ["@pipewire", "-", "memlock", "unlimited"] in rules
    # nothing sorting after it may put a cap back
    for other in d.glob("*.conf"):
        if other.name > f.name:
            assert "memlock" not in other.read_text(), other.name
