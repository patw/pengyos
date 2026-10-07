"""Unit tests for the IT environment harness (``pengyos_installer.itenv``).

These run in the hermetic suite: no root, no loop devices, no qemu. The
pure builders and checks are golden-tested against fakes; ``attach_loop`` /
``detach_loop`` run against a recording fake runner and a temp-dir symlink
tree (the only real I/O is ``os.symlink`` in tmp, which needs no privilege).

The real, destructive end-to-end flow (loop install + qemu-verify boot)
lives in ``tests/test_integration.py`` behind ``PENGYOS_IT=1``.
"""

from __future__ import annotations

import os

import pytest

from pengyos_installer import itenv


# --- helpers ----------------------------------------------------------------


class FakeResult:
    """Duck-typed stand-in for a runner's result (the IT's CommandResult)."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def fake_runner(results):
    """A recording fake runner: pops one FakeResult per argv, records argvs."""
    calls: list = []

    def run(argv):
        calls.append(tuple(argv))
        return results.pop(0)

    return run, calls


# --- it_enabled ---------------------------------------------------------------


def test_it_enabled_true_only_for_exactly_1():
    assert itenv.it_enabled({"PENGYOS_IT": "1"}) is True


@pytest.mark.parametrize(
    "value", [None, "", "0", "true", "yes", "2"]
)
def test_it_enabled_false_for_everything_else(value):
    env = {} if value is None else {"PENGYOS_IT": value}
    assert itenv.it_enabled(env) is False


def test_it_enabled_defaults_to_real_environ(monkeypatch):
    monkeypatch.delenv("PENGYOS_IT", raising=False)
    assert itenv.it_enabled() is False
    monkeypatch.setenv("PENGYOS_IT", "1")
    assert itenv.it_enabled() is True


# --- argv goldens --------------------------------------------------------------


def test_losetup_attach_argv_golden():
    assert itenv.losetup_attach_argv("/tmp/pengyos-it.img") == (
        "losetup", "--show", "-f", "/tmp/pengyos-it.img",
    )


def test_losetup_detach_argv_golden():
    assert itenv.losetup_detach_argv("/dev/loop7") == ("losetup", "-d", "/dev/loop7")


def test_required_binaries_are_stable_and_complete():
    # Order is golden (it feeds missing_requirements' message order) and the
    # set must cover every external tool the install plan + IT itself run.
    assert itenv.REQUIRED_BINARIES == (
        "parted",
        "mkfs.vfat",
        "mkfs.ext4",
        "rsync",
        "blkid",
        "grub-install",
        "efibootmgr",
        "losetup",
        "qemu-system-x86_64",
        "pengyos-qemu-verify",
    )


# --- missing_requirements -------------------------------------------------------


def test_missing_requirements_none_when_root_and_all_binaries_present():
    assert itenv.missing_requirements(euid=0, which=lambda n: "/usr/sbin/" + n) == []


def test_missing_requirements_root_comes_first_then_binaries_in_order():
    which = lambda n: "/x/" + n if n in ("rsync", "blkid") else None
    missing = itenv.missing_requirements(euid=1000, which=which)
    assert missing == [
        "root: requires euid 0 (loop devices, parted/mkfs/mount)",
        "binary: parted not found on PATH",
        "binary: mkfs.vfat not found on PATH",
        "binary: mkfs.ext4 not found on PATH",
        "binary: grub-install not found on PATH",
        "binary: efibootmgr not found on PATH",
        "binary: losetup not found on PATH",
        "binary: qemu-system-x86_64 not found on PATH",
        "binary: pengyos-qemu-verify not found on PATH",
    ]


def test_missing_requirements_root_only():
    missing = itenv.missing_requirements(euid=1000, which=lambda n: "/x")
    assert missing == ["root: requires euid 0 (loop devices, parted/mkfs/mount)"]


def test_missing_requirements_custom_binary_subset():
    missing = itenv.missing_requirements(
        euid=0, which=lambda n: None, binaries=("losetup", "qemu-system-x86_64")
    )
    assert missing == [
        "binary: losetup not found on PATH",
        "binary: qemu-system-x86_64 not found on PATH",
    ]


# --- find_rootfs_source -----------------------------------------------------------


def test_find_rootfs_source_explicit_env_wins(tmp_path):
    src = tmp_path / "rootfs.tar"
    src.write_bytes(b"")
    assert itenv.find_rootfs_source(
        environ={itenv.ROOTFS_SRC_ENV_VAR: str(src)},
        home="/nonexistent-home",
        exists=os.path.exists,
    ) == str(src)


def test_find_rootfs_source_explicit_env_missing_falls_through(tmp_path):
    (tmp_path / "rootfs.tar").write_bytes(b"")
    assert itenv.find_rootfs_source(
        environ={itenv.ROOTFS_SRC_ENV_VAR: str(tmp_path / "nope.tar")},
        home=str(tmp_path.parent),  # candidate <parent>/build/... absent
        exists=os.path.exists,
    ) is None


def test_find_rootfs_source_buildroot_candidate():
    home = "/home/tester"
    assert itenv.find_rootfs_source(
        environ={},
        home=home,
        exists=lambda p: p == f"{home}/build/pengyos/output/images/rootfs.tar",
    ) == f"{home}/build/pengyos/output/images/rootfs.tar"


def test_find_rootfs_source_none_when_nothing_found():
    assert itenv.find_rootfs_source(environ={}, home="/home/tester",
                                    exists=lambda p: False) is None


def test_find_rootfs_source_none_home_and_empty_env_no_crash(monkeypatch):
    monkeypatch.delenv("HOME", raising=False)
    assert itenv.find_rootfs_source(environ={}, home=None,
                                    exists=os.path.exists) is None


def test_find_rootfs_source_empty_env_value_is_not_used(tmp_path):
    (tmp_path / "rootfs.tar").write_bytes(b"")
    assert itenv.find_rootfs_source(
        environ={itenv.ROOTFS_SRC_ENV_VAR: ""},
        home=str(tmp_path),
        exists=os.path.exists,
    ) is None


# --- make_sparse_image ---------------------------------------------------------------


def test_make_sparse_image_sets_apparent_size_without_writing(tmp_path):
    path = tmp_path / "img"
    itenv.make_sparse_image(str(path), 5 * 1024**3)
    st = os.stat(str(path))
    assert st.st_size == 5 * 1024**3
    # Sparse: the on-disk allocation is far below the apparent size.
    assert st.st_blocks * 512 < 1024**2


def test_make_sparse_image_default_size_is_6_gib():
    assert itenv.DEFAULT_IMAGE_SIZE_BYTES == 6 * 1024**3
    # Above the documented 4 GiB minimum (plan.MIN_TARGET_SIZE_BYTES).
    from pengyos_installer.plan import MIN_TARGET_SIZE_BYTES

    assert itenv.DEFAULT_IMAGE_SIZE_BYTES > MIN_TARGET_SIZE_BYTES


def test_make_sparse_image_directory_is_a_runtime_error(tmp_path):
    d = tmp_path / "adir"
    d.mkdir()
    with pytest.raises(RuntimeError, match="cannot create sparse image"):
        itenv.make_sparse_image(str(d), 1024)


# --- attach_loop ---------------------------------------------------------------------


def test_attach_loop_builds_symlink_tree(tmp_path):
    run, calls = fake_runner([
        FakeResult(0, "/dev/loop7\n", ""),
    ])
    setup = itenv.attach_loop(str(tmp_path / "img"), str(tmp_path), run)

    assert calls == [("losetup", "--show", "-f", str(tmp_path / "img"))]
    assert setup.device == "/dev/loop7"
    assert setup.target == str(tmp_path / "loop7")
    # The partition symlinks are named with the p-suffixed form the plan
    # emits (plan.partition_device: the loop name ends in a digit), and they
    # point at the kernel's real p-suffixed loop partition nodes.
    assert setup.partitions == (
        str(tmp_path / "loop7p1"),
        str(tmp_path / "loop7p2"),
    )
    assert os.readlink(setup.target) == "/dev/loop7"
    assert os.readlink(setup.partitions[0]) == "/dev/loop7p1"
    assert os.readlink(setup.partitions[1]) == "/dev/loop7p2"


@pytest.mark.parametrize(
    "result",
    [
        FakeResult(1, "", "losetup: cannot open: permission denied"),
        FakeResult(0, "", ""),  # no device printed
        FakeResult(0, "/dev/sdX\n", ""),  # not a loop device
    ],
)
def test_attach_loop_refuses_on_bad_losetup_output(tmp_path, result):
    run, _ = fake_runner([result])
    with pytest.raises(RuntimeError, match="losetup attach failed"):
        itenv.attach_loop(str(tmp_path / "img"), str(tmp_path), run)


def test_attach_loop_multiline_stdout_takes_first_line(tmp_path):
    run, _ = fake_runner([FakeResult(0, "/dev/loop3\nextra noise\n")])
    setup = itenv.attach_loop(str(tmp_path / "img"), str(tmp_path), run)
    assert setup.device == "/dev/loop3"


def test_attach_loop_strips_whitespace_device(tmp_path):
    run, _ = fake_runner([FakeResult(0, "  /dev/loop9  \n")])
    setup = itenv.attach_loop(str(tmp_path / "img"), str(tmp_path), run)
    assert setup.device == "/dev/loop9"


# --- detach_loop -------------------------------------------------------------------------


def _setup(tmp_path, device="/dev/loop7"):
    return itenv.LoopSetup(
        image=str(tmp_path / "img"),
        device=device,
        target=str(tmp_path / "loop7"),
        partitions=(str(tmp_path / "loop7p1"), str(tmp_path / "loop7p2")),
        workdir=str(tmp_path),
    )


def test_detach_loop_detaches_and_removes_symlinks(tmp_path):
    setup = _setup(tmp_path)
    for p in (setup.target, *setup.partitions):
        os.symlink(setup.device, p)
    run, calls = fake_runner([FakeResult(0, "", "")])
    warnings = itenv.detach_loop(setup, run)
    assert calls == [("losetup", "-d", "/dev/loop7")]
    assert warnings == []
    for p in (setup.target, *setup.partitions):
        assert not os.path.lexists(p)


def test_detach_loop_dangling_symlinks_are_removed_too(tmp_path):
    # The partition symlinks may still be dangling (parted never ran); they
    # are removed anyway so the workdir comes back clean.
    setup = _setup(tmp_path)
    os.symlink(setup.device, setup.target)
    os.symlink(setup.device, setup.partitions[0])  # no p1 node exists
    run, _ = fake_runner([FakeResult(0, "", "")])
    itenv.detach_loop(setup, run)
    for p in (setup.target, *setup.partitions):
        assert not os.path.lexists(p)


def test_detach_loop_failure_raises_unless_best_effort(tmp_path):
    setup = _setup(tmp_path)
    os.symlink(setup.device, setup.target)
    run, _ = fake_runner([FakeResult(1, "", "losetup: busy")])
    with pytest.raises(RuntimeError, match="losetup detach failed"):
        itenv.detach_loop(setup, run)

    run, _ = fake_runner([FakeResult(1, "", "losetup: busy")])
    warnings = itenv.detach_loop(setup, run, best_effort=True)
    assert warnings == [
        "losetup detach failed for /dev/loop7: losetup: busy"
    ]
    assert not os.path.lexists(setup.target)


# --- qemu_verify_boot_argv -----------------------------------------------------------------


def test_qemu_verify_boot_argv_default_golden():
    assert itenv.qemu_verify_boot_argv("/tmp/installed.img") == (
        "pengyos-qemu-verify", "boot", "/tmp/installed.img", "--uefi",
        "--expect", "login:",
        "--timeout", "300",
    )


def test_qemu_verify_boot_argv_reports_and_ram():
    assert itenv.qemu_verify_boot_argv(
        "/tmp/i.img",
        timeout=420.9,  # floats render as whole seconds
        json_out="/tmp/r.json",
        junit_out="/tmp/r.xml",
        ram_mb=1024,
    ) == (
        "pengyos-qemu-verify", "boot", "/tmp/i.img", "--uefi",
        "--expect", "login:",
        "--timeout", "420",
        "--ram", "1024",
        "--json", "/tmp/r.json",
        "--junit", "/tmp/r.xml",
    )


def test_qemu_verify_boot_argv_multiple_expects_in_order():
    argv = itenv.qemu_verify_boot_argv(
        "/tmp/i.img", expect=("PengyOS", "login:")
    )
    assert argv[3:] == (
        "--uefi",
        "--expect", "PengyOS",
        "--expect", "login:",
        "--timeout", "300",
    )


def test_qemu_verify_boot_argv_never_passes_a_kernel():
    # The installed disk boots through OVMF -> the EFI/BOOT loader; a
    # --kernel would bypass GRUB and prove nothing about the install.
    argv = itenv.qemu_verify_boot_argv("/tmp/i.img")
    assert "--kernel" not in argv
    assert "--uefi" in argv


def test_default_expect_matches_qemu_verify_default():
    # pengyos-qemu-verify's own default readiness marker is 'login:'.
    assert itenv.DEFAULT_EXPECT == "login:"


def test_boot_timeout_env_default():
    assert itenv.BOOT_TIMEOUT_DEFAULT == 300.0
    assert itenv.BOOT_TIMEOUT_ENV_VAR == "PENGYOS_IT_TIMEOUT"
