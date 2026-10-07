"""Opt-in integration test: install to a loop device, boot the result.

Gated twice, per the project rule "tests must not need root or real
hardware — integration tests are opt-in behind ``PENGYOS_IT=1``":

1. module-level ``skipif`` on ``PENGYOS_IT=1`` (the unit suite never sees
   the IT body at all);
2. inside the IT, an actionable skip for each missing prerequisite
   (root, a binary, a rootfs source) via
   :func:`pengyos_installer.itenv.missing_requirements` /
   :func:`pengyos_installer.itenv.find_rootfs_source`.

The IT body is the *production* flow, unmocked: the real
:class:`pengyos_installer.install.SubprocessRunner` runs the plan's argv
(``parted`` / ``mkfs.*`` / ``mount`` / ``tar``|``rsync`` / ``blkid`` /
``grub-install``) against a loop-backed target, and the resulting disk
image is booted headless with the sibling tool
``pengyos-qemu-verify`` (OVMF -> the ``EFI/BOOT`` loader the install
wrote -> the installed GRUB -> ``login:``).

To run it::

    PENGYOS_IT=1 sudo pytest -q tests/test_integration.py   # or the whole suite

The loop-device symlink-tree (``<workdir>/loopN`` target + ``p``-suffixed
partition symlinks) is documented in :mod:`pengyos_installer.itenv`; the
partition symlink names match the ``p``-suffixed form the plan now emits
(via :func:`pengyos_installer.plan.partition_device`), so the plan's argv
resolves straight onto the loop device's real partition nodes.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from pengyos_installer import install as install_mod
from pengyos_installer import itenv
from pengyos_installer.plan import WORK_DIR, build_plan

#: The single gate for every IT in this module.
requires_it = pytest.mark.skipif(
    os.environ.get("PENGYOS_IT") != itenv.IT_ENABLED_VALUE,
    reason=(
        "opt-in only: set PENGYOS_IT=1 (needs root + parted/mkfs.*/rsync/"
        "blkid/grub-install/efibootmgr/losetup/qemu/OVMF; "
        "see pengyos_installer/itenv.py)"
    ),
)


def test_it_gate_is_off_by_default(monkeypatch):
    # The hermetic suite must never run the IT: with the variable absent
    # the gate reports disabled (skipif reads the same variable).
    monkeypatch.delenv("PENGYOS_IT", raising=False)
    assert itenv.it_enabled() is False


def test_it_requirements_messages_are_actionable():
    # Whatever this host is missing, the messages name *why* (root or a
    # binary) — that is the contract the IT's skip message is built from.
    missing = itenv.missing_requirements(euid=os.geteuid(), which=shutil.which)
    for msg in missing:
        assert msg.startswith(("root:", "binary:")), msg


@requires_it
def test_install_to_loop_device_then_boot_with_qemu_verify(tmp_path):
    missing = itenv.missing_requirements(euid=os.geteuid(), which=shutil.which)
    if missing:
        pytest.skip("IT prerequisites missing: " + "; ".join(missing))
    source = itenv.find_rootfs_source()
    if source is None:
        pytest.skip(
            "no rootfs source: set "
            f"{itenv.ROOTFS_SRC_ENV_VAR}=<rootfs.tar.gz> or build "
            "~/build/pengyos/output/images/rootfs.tar first"
        )

    workdir = tmp_path / "it"
    workdir.mkdir()
    image = workdir / "installed.img"
    itenv.make_sparse_image(str(image), itenv.DEFAULT_IMAGE_SIZE_BYTES)
    timeout = float(
        os.environ.get(itenv.BOOT_TIMEOUT_ENV_VAR, itenv.BOOT_TIMEOUT_DEFAULT)
    )

    runner = install_mod.SubprocessRunner()
    setup = itenv.attach_loop(str(image), str(workdir), runner)
    try:
        # -- the production install flow, unmocked -----------------------
        plan = build_plan(
            target=setup.target,
            esp_size="512M",
            hostname="pengyos-it",
            rootfs_src=source,
        )
        assert plan.sanity.ok, plan.sanity.to_dict()
        # The whole plan points at the symlinked target, so every argv
        # (parted/mkfs/mount/blkid/grub) resolves through the loop device.
        assert plan.steps[1].argv[2] == setup.target
        assert plan.target == setup.target

        result = install_mod.execute_install(
            plan, runner, confirm=True, hostname_provider=lambda: "pengyos-it"
        )
        assert result.ok, result.warnings
        assert "PengyOS install complete" in result.summary

        # The installed rootfs was actually configured: fstab keys the root
        # partition by the PARTUUID that blkid read off the real device.
        fstab = result.files_written[WORK_DIR + "/etc/fstab"]
        root_entry = next(
            line for line in fstab.splitlines() if line.endswith(" ext4 defaults 0 1")
        )
        root_uuid = root_entry.split()[0]
        assert root_uuid.startswith("PARTUUID=") and len(root_uuid) > len("PARTUUID=")
        assert root_uuid in result.summary
        grub_cfg = result.files_written[WORK_DIR + "/boot/efi/EFI/BOOT/grub.cfg"]
        assert f"root=PARTUUID={root_uuid[len('PARTUUID='):]} ro quiet" in grub_cfg

        # -- boot the result with pengyos-qemu-verify ---------------------
        json_out = workdir / "boot.json"
        junit_out = workdir / "boot.xml"
        argv = itenv.qemu_verify_boot_argv(
            str(image),
            timeout=timeout,
            json_out=str(json_out),
            junit_out=str(junit_out),
        )
        boot = subprocess.run(
            argv, capture_output=True, text=True, cwd=str(workdir)
        )
        assert boot.returncode == 0, (
            "qemu-verify boot of the installed image failed "
            f"(rc={boot.returncode}):\n--- stdout ---\n{boot.stdout}"
            f"\n--- stderr ---\n{boot.stderr}"
            f"\n(reports: {json_out}, {junit_out})"
        )
        assert json_out.is_file(), "qemu-verify wrote no JSON report"
        assert junit_out.is_file(), "qemu-verify wrote no JUnit report"
    finally:
        # Never leave a loop device attached, even on failure.
        warnings = itenv.detach_loop(setup, runner, best_effort=True)
        assert warnings == []
