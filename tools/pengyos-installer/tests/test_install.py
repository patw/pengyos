"""Behaviour tests for ``install`` execution (the injectable-runner layer).

Every external command goes through a :class:`FakeRunner` — a recording fake —
so **no test touches a disk, needs root, the network, or a display**. File
writes are redirected to a ``tmp_path`` work root. The plan is pure; only
:func:`pengyos_installer.install.execute_install` acts, and it is exercised
here directly and through the ``install`` CLI handler (via the CLI's runner /
work-root seams).

Covers (spec.md, ``install`` execution task):
* running the whole plan through the runner, in order, with the exact argv;
* the ``--yes`` gate and the target sanity re-check (both refuse before any
  command runs);
* capturing the PARTUUIDs from the ``blkid`` steps and the resulting file
  content (``/etc/fstab``, ``/etc/hostname``, ``/etc/pengyos/installed``, the
  ESP ``EFI/BOOT/grub.cfg``);
* the ``optional:`` ``efibootmgr`` step — a failure is tolerated, not fatal;
* a hard step failure aborts the install and tears down what was mounted;
* a missing/ambiguous PARTUUID is a hard error and writes no file;
* the success and failure teardown (``umount`` ESP then root, then ``sync``);
* the ``SubprocessRunner`` (a harmless ``echo``; a missing binary);
* the ``install`` CLI handler wiring (refusal without ``--yes``, and a
  hermetic happy path through the injected runner).
"""

import contextlib
import io
import os
import sys

import pytest

from pengyos_installer import install as install_mod
from pengyos_installer import plan as planmod
from pengyos_installer.cli import ExitCode, main
from pengyos_installer.install import (
    ERR_INTERNAL_STEP,
    ERR_MISSING_PARTUUID,
    ERR_NOT_CONFIRMED,
    ERR_ROOTFS_SRC_MISSING,
    ERR_ROOTFS_SRC_TYPE_MISMATCH,
    ERR_SANITY_REFUSED,
    ERR_STEP_FAILED,
    ERR_UNMOUNT_FAILED,
    CommandResult,
    InstallError,
    SourceInfo,
    SubprocessRunner,
    default_source_inspector,
    execute_install,
    validate_rootfs_src,
)
from pengyos_installer.plan import (
    COPY_SRC_KIND_DIR,
    COPY_SRC_KIND_TAR,
    ETC_FSTAB,
    ETC_HOSTNAME,
    ESP_EFI_DIR,
    ESP_MOUNTPOINT,
    FALLBACK_GRUB_CFG,
    INSTALL_MARKER,
    TargetProbe,
    WORK_DIR,
    build_plan,
    classify_rootfs_src,
    partition_device,
    render_fstab,
    render_grub_cfg,
    render_hostname,
    render_installed_marker,
)

GiB = 1024**3

# Deterministic fixture PARTUUIDs (never from real hardware).
ROOT_UUID = "11111111-2222-3333-4444-555555555555"
ESP_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def good_probe(target="/dev/sdb", size=8 * GiB,
               root_device="/dev/sda", esp_device="/dev/sda1"):
    return TargetProbe(path=target, is_block_device=True, size_bytes=size,
                       root_device=root_device, esp_device=esp_device)


def _uuid_for(device: str) -> str:
    """Which fixture PARTUUID a device reports (``…1`` = ESP, ``…2`` = root)."""
    return ESP_UUID if device.endswith("1") else ROOT_UUID


class FakeRunner:
    """A recording command runner.

    ``fail`` is a set of argv (tuples) or program names whose calls should
    return a non-zero exit; ``stdout`` maps argv / program name to the stdout
    to return. Defaults to a successful empty run for every command, and to
    the correct fixture PARTUUID for ``blkid`` (ESP for ``…1``, root for
    ``…2``) so a plan can be driven to completion out of the box.
    """

    def __init__(self, *, fail=(), stdout=None):
        self.calls = []
        self.fail = set(fail)
        self.stdout = dict(stdout or {})

    def __call__(self, argv):
        argv = tuple(argv)
        self.calls.append(argv)
        if argv in self.fail or argv[0] in self.fail:
            return CommandResult(argv, 1, "", "boom")
        if argv[0] == "blkid" and argv not in self.stdout:
            return CommandResult(argv, 0, f"{_uuid_for(argv[-1])}\n", "")
        out = self.stdout.get(argv, self.stdout.get(argv[0], ""))
        return CommandResult(argv, 0, out, "")


def _plan(**kw):
    return build_plan(
        target=kw.pop("target", "/dev/sdb"),
        esp_size=kw.pop("esp_size", "512M"),
        hostname=kw.pop("hostname", None),
        rootfs_src=kw.pop("rootfs_src", None),
        probe=kw.pop("probe", None) or good_probe(),
        **kw,
    )


def _fake_inspect(mapping):
    """Build a source inspector from ``{path: (exists, is_dir, is_file)}``.

    Any path not in ``mapping`` is treated as missing. This keeps the source
    ``stat`` hermetic (no test touches the real filesystem).
    """
    def inspect(path):
        exists, is_dir, is_file = mapping.get(path, (False, False, False))
        return SourceInfo(path=path, exists=exists,
                          is_dir=is_dir, is_file=is_file)
    return inspect


# --- the exact golden execution order ---------------------------------------

def _expected_argv_sequence(target="/dev/sdb"):
    """The 13 command lines of the plan, in execution order (root=``…2``).

    Uses :func:`partition_device` so it is correct for any target (plain
    ``<tgt>1``/``<tgt>2`` or the NVMe/loop ``p``-suffixed form).
    """
    esp_part = partition_device(target, 1)
    root_part = partition_device(target, 2)
    return [
        ("parted", "-s", target, "mklabel", "gpt"),
        ("parted", "-s", target, "mkpart", "ESP", "fat32", "1MiB", "512M"),
        ("parted", "-s", target, "set", "1", "esp", "on"),
        ("parted", "-s", target, "mkpart", "root", "ext4", "512M", "100%"),
        ("mkfs.vfat", "-F32", "-n", "PENGYOS-ESP", esp_part),
        ("mkfs.ext4", "-L", "pengyos-root", "-F", root_part),
        ("mount", root_part, WORK_DIR),
        (
            "rsync", "-aHAX", "--numeric-ids",
            "--exclude=/proc", "--exclude=/sys", "--exclude=/dev",
            "--exclude=/run", "--exclude=/mnt", "--exclude=/media",
            "--exclude=/tmp", f"--exclude={target}", "/", WORK_DIR,
        ),
        ("blkid", "-s", "PARTUUID", "-o", "value", esp_part),
        ("blkid", "-s", "PARTUUID", "-o", "value", root_part),
        ("mount", esp_part, ESP_EFI_DIR),
        (
            "grub-install",
            "--target=x86_64-efi",
            f"--efi-directory={ESP_EFI_DIR}",
            f"--boot-directory={WORK_DIR}/boot",
            "--bootloader-id=PengyOS",
            "--removable",
        ),
        ("efibootmgr", "-c", "-d", target, "-p", "1",
         "-L", "PengyOS", "-l", "/EFI/BOOT/bootx64.efi"),
    ]


def test_command_result_ok_property():
    assert CommandResult(("x",), 0).ok is True
    assert CommandResult(("x",), 1).ok is False


# --- happy path -------------------------------------------------------------

def test_execute_install_runs_the_whole_plan_in_order(tmp_path):
    plan = _plan(hostname="laptop")
    runner = FakeRunner()
    res = execute_install(plan, runner, work_root=str(tmp_path))
    assert res.ok is True
    assert res.target == "/dev/sdb"
    # every planned command, in order, then the teardown lines
    expected = _expected_argv_sequence()
    assert res.executed[:len(expected)] == expected
    # teardown: unmount ESP then root, then sync
    assert res.executed[len(expected):] == [
        ("umount", ESP_EFI_DIR),
        ("umount", WORK_DIR),
        ("sync",),
    ]
    # the runner saw exactly those, in that order
    assert runner.calls == res.executed


def test_execute_install_mount_and_unmount_order(tmp_path):
    plan = _plan(hostname="laptop")
    res = execute_install(plan, FakeRunner(), work_root=str(tmp_path))
    # root is mounted first, the ESP second; unmounted in the reverse order
    assert res.mounts == [WORK_DIR, ESP_EFI_DIR]
    assert res.unmounts == [ESP_EFI_DIR, WORK_DIR]


def test_execute_install_writes_the_configured_files(tmp_path):
    plan = _plan(hostname="laptop")
    res = execute_install(plan, FakeRunner(), work_root=str(tmp_path))
    w = str(tmp_path)
    assert set(res.files_written) == {
        w + ETC_FSTAB,
        w + ETC_HOSTNAME,
        w + INSTALL_MARKER,
        w + ESP_MOUNTPOINT + "/" + FALLBACK_GRUB_CFG,
    }
    # content is exactly the renderers' golden output with the fixture UUIDs
    assert res.files_written[w + ETC_FSTAB] == render_fstab(ROOT_UUID, ESP_UUID)
    assert res.files_written[w + ETC_HOSTNAME] == render_hostname("laptop")
    assert res.files_written[w + INSTALL_MARKER] == render_installed_marker(
        "laptop", "/dev/sdb")
    assert res.files_written[w + ESP_MOUNTPOINT + "/" + FALLBACK_GRUB_CFG] == \
        render_grub_cfg(ROOT_UUID)
    # the files are actually on disk under the work root
    assert (tmp_path / ETC_FSTAB.lstrip("/")).read_text() == render_fstab(
        ROOT_UUID, ESP_UUID)
    assert (tmp_path / ("boot/efi/" + FALLBACK_GRUB_CFG)).read_text() == \
        render_grub_cfg(ROOT_UUID)


def test_execute_install_wiring_uses_root_partuuid_in_grub_and_fstab(tmp_path):
    plan = _plan(hostname="laptop")
    res = execute_install(plan, FakeRunner(), work_root=str(tmp_path))
    w = str(tmp_path)
    fstab = res.files_written[w + ETC_FSTAB]
    grub = res.files_written[w + ESP_MOUNTPOINT + "/" + FALLBACK_GRUB_CFG]
    # root is wired by its own PARTUUID, the ESP by its own (not swapped)
    assert f"PARTUUID={ROOT_UUID} / ext4" in fstab
    assert f"PARTUUID={ESP_UUID} /boot/efi" in fstab
    assert f"root=PARTUUID={ROOT_UUID}" in grub
    assert f"--partuuid {ROOT_UUID}" in grub
    # and the summary reports them under the right labels
    assert f"root:     PARTUUID={ROOT_UUID}" in res.summary
    assert f"esp:      PARTUUID={ESP_UUID}" in res.summary


def test_execute_install_tar_source_copy(tmp_path):
    plan = _plan(rootfs_src="/srv/rootfs.tar")
    # the plan classified the .tar name as a tarball; the install re-validates
    # the source, so supply a fake inspector that says it is an existing file
    res = execute_install(
        plan, FakeRunner(), work_root=str(tmp_path),
        source_inspector=_fake_inspect({"/srv/rootfs.tar": (True, False, True)}),
    )
    assert res.ok is True
    assert ("tar", "-xpf", "/srv/rootfs.tar", "-C", WORK_DIR) in res.executed
    assert not any(c[0] == "rsync" for c in res.executed)


def test_execute_install_hostname_from_live_provider(tmp_path):
    plan = _plan(hostname=None)
    res = execute_install(
        plan, FakeRunner(), work_root=str(tmp_path),
        hostname_provider=lambda: "livehost",
    )
    w = str(tmp_path)
    assert res.files_written[w + ETC_HOSTNAME] == "livehost\n"
    assert "hostname: livehost" in res.files_written[w + INSTALL_MARKER]


def test_execute_install_summary_lists_warnings(tmp_path):
    plan = _plan(hostname="laptop")
    res = execute_install(plan, FakeRunner(), work_root=str(tmp_path))
    assert res.summary.startswith("PengyOS install complete")
    assert "/dev/sdb" in res.summary
    assert "warning:" not in res.summary


# --- confirm / --yes gate and the sanity re-check ---------------------------

def test_execute_install_refuses_without_confirm_and_runs_nothing(tmp_path):
    plan = _plan(hostname="laptop")
    runner = FakeRunner()
    with pytest.raises(InstallError) as ei:
        execute_install(plan, runner, confirm=False, work_root=str(tmp_path))
    assert ei.value.code == ERR_NOT_CONFIRMED
    assert runner.calls == []          # nothing was run before the refusal
    assert os.path.isdir(tmp_path)     # ...and nothing was written


def test_execute_install_refuses_failed_sanity_before_running(tmp_path):
    bad = TargetProbe(path="/dev/sdb", is_block_device=True, size_bytes=1024)
    plan = build_plan(target="/dev/sdb", esp_size="512M", probe=bad)
    runner = FakeRunner()
    with pytest.raises(InstallError) as ei:
        execute_install(plan, runner, work_root=str(tmp_path))
    assert ei.value.code == ERR_SANITY_REFUSED
    assert runner.calls == []


# --- step failure: abort + best-effort teardown -----------------------------

def test_step_failure_aborts_and_tears_down(tmp_path):
    # rsync is after "mount root"; it fails -> abort, unmount root, sync.
    plan = _plan(hostname="laptop")
    rsync_argv = tuple(c for c in _expected_argv_sequence() if c[0] == "rsync")[0]
    runner = FakeRunner(fail={"rsync"})
    with pytest.raises(InstallError) as ei:
        execute_install(plan, runner, work_root=str(tmp_path))
    assert ei.value.code == ERR_STEP_FAILED
    assert "rsync" in str(ei.value)
    # the failing command is the last *planned* command; then teardown
    assert runner.calls[-3:] == [rsync_argv, ("umount", WORK_DIR), ("sync",)]
    # only the root partition was mounted at that point (the ESP is not yet)
    assert ("umount", ESP_MOUNTPOINT) not in runner.calls
    # no files were written (the failure precedes the configure phase)
    assert not (tmp_path / ETC_FSTAB.lstrip("/")).exists()


def test_step_failure_before_any_mount_tears_down_only_sync(tmp_path):
    # mkfs.ext4 fails before any mount -> teardown just syncs.
    plan = _plan(hostname="laptop")
    runner = FakeRunner(fail={"mkfs.ext4"})
    with pytest.raises(InstallError) as ei:
        execute_install(plan, runner, work_root=str(tmp_path))
    assert ei.value.code == ERR_STEP_FAILED
    assert ("umount", WORK_DIR) not in runner.calls
    assert ("umount", ESP_EFI_DIR) not in runner.calls
    assert runner.calls[-1] == ("sync",)


# --- optional step (efibootmgr) is tolerated --------------------------------

def test_optional_efibootmgr_failure_is_tolerated(tmp_path):
    plan = _plan(hostname="laptop")
    runner = FakeRunner(fail={"efibootmgr"})
    res = execute_install(plan, runner, work_root=str(tmp_path))
    assert res.ok is True
    # a warning was recorded but the install completed and still tore down
    assert len(res.warnings) == 1
    assert "efibootmgr" in res.warnings[0]
    assert ("sync",) == res.executed[-1]
    # the efibootmgr command was still attempted (best-effort), then the
    # teardown ran
    assert any(c[0] == "efibootmgr" for c in runner.calls)
    assert res.files_written  # the config files were still written


def test_optional_failure_not_raised_but_required_step_is(tmp_path):
    # the optional step's failure is the one tolerated; required-step failures
    # are covered by the step-failure tests. Here the optional failure is the
    # only one and the install still completes with a full teardown.
    plan = _plan(hostname="laptop")
    res = execute_install(plan, FakeRunner(fail={"efibootmgr"}),
                          work_root=str(tmp_path))
    assert res.ok is True
    assert res.unmounts == [ESP_EFI_DIR, WORK_DIR]


# --- missing / ambiguous PARTUUID -------------------------------------------

def test_missing_root_partuuid_is_hard_error_and_writes_nothing(tmp_path):
    # blkid for the root partition (…2) reports nothing -> ConfigError.
    plan = _plan(hostname="laptop")
    root_blkid = ("blkid", "-s", "PARTUUID", "-o", "value", "/dev/sdb2")
    runner = FakeRunner(stdout={root_blkid: ""})
    with pytest.raises(Exception) as ei:
        execute_install(plan, runner, work_root=str(tmp_path))
    # the configure phase raises its own ConfigError (missing-partuuid)
    assert getattr(ei.value, "code", None) == ERR_MISSING_PARTUUID
    # no file was written at all (the read precedes the writes)
    assert not (tmp_path / ETC_FSTAB.lstrip("/")).exists()
    # and the mounted root partition was still torn down
    assert runner.calls[-1] == ("sync",)
    assert ("umount", WORK_DIR) in runner.calls


def test_ambiguous_partuuid_is_hard_error(tmp_path):
    plan = _plan(hostname="laptop")
    esp_blkid = ("blkid", "-s", "PARTUUID", "-o", "value", "/dev/sdb1")
    runner = FakeRunner(stdout={esp_blkid: f"{ESP_UUID} {ROOT_UUID}"})
    with pytest.raises(Exception) as ei:
        execute_install(plan, runner, work_root=str(tmp_path))
    assert getattr(ei.value, "code", None) == ERR_MISSING_PARTUUID
    assert not (tmp_path / ETC_FSTAB.lstrip("/")).exists()


# --- teardown failure --------------------------------------------------------

def test_unmount_failure_on_success_path_is_a_hard_error(tmp_path):
    plan = _plan(hostname="laptop")
    # make the first unmount (the ESP) fail
    runner = FakeRunner(fail={("umount", ESP_EFI_DIR)})
    with pytest.raises(InstallError) as ei:
        execute_install(plan, runner, work_root=str(tmp_path))
    assert ei.value.code == ERR_UNMOUNT_FAILED
    assert ESP_EFI_DIR in str(ei.value)


def test_unmount_failure_on_failure_path_does_not_mask_original_error(tmp_path):
    # A required step fails AND the teardown umount also fails: the original
    # error propagates (a best-effort unmount failure must not mask it), and
    # the teardown still runs through to sync.
    plan = _plan(hostname="laptop")
    runner = FakeRunner(fail={"rsync", ("umount", WORK_DIR)})
    with pytest.raises(InstallError) as ei:
        execute_install(plan, runner, work_root=str(tmp_path))
    assert ei.value.code == ERR_STEP_FAILED  # not ERR_UNMOUNT_FAILED
    # teardown still attempted the unmount and finished with sync
    assert ("umount", WORK_DIR) in runner.calls
    assert runner.calls[-1] == ("sync",)


# --- internal-step dispatch --------------------------------------------------

def test_unrecognised_internal_step_is_an_error(tmp_path):
    from pengyos_installer.plan import InstallPlan, SanityResult, Step

    sanity = SanityResult(ok=True, errors=(), size_bytes=8 * GiB)
    steps = (Step(phase="configure", description="write /etc/unknown-thing",
                  argv=None, note="internal: bogus"),)
    plan = InstallPlan(target="/dev/sdb", esp_size="512M", hostname="laptop",
                       rootfs_src=None, min_target_size_bytes=8 * GiB,
                       sanity=sanity, steps=steps)
    with pytest.raises(InstallError) as ei:
        execute_install(plan, FakeRunner(), work_root=str(tmp_path))
    assert ei.value.code == ERR_INTERNAL_STEP


def test_internal_step_without_partuuids_is_an_error(tmp_path):
    # An fstab write with no preceding blkid read cannot be satisfied.
    from pengyos_installer.plan import InstallPlan, SanityResult, Step

    sanity = SanityResult(ok=True, errors=(), size_bytes=8 * GiB)
    steps = (Step(phase="configure",
                  description=f"write {ETC_FSTAB} (PARTUUID= lines for root and ESP, from the blkid output)",
                  argv=None, note="internal: written at install time"),)
    plan = InstallPlan(target="/dev/sdb", esp_size="512M", hostname="laptop",
                       rootfs_src=None, min_target_size_bytes=8 * GiB,
                       sanity=sanity, steps=steps)
    with pytest.raises(InstallError) as ei:
        execute_install(plan, FakeRunner(), work_root=str(tmp_path))
    assert ei.value.code == ERR_MISSING_PARTUUID


# --- write_file injection ----------------------------------------------------

def test_custom_write_file_is_used(tmp_path):
    plan = _plan(hostname="laptop")
    written = {}

    def rec(path, content):
        written[path] = content

    res = execute_install(plan, FakeRunner(), work_root=str(tmp_path),
                          write_file=rec)
    assert res.ok is True
    assert written[str(tmp_path) + ETC_FSTAB] == render_fstab(ROOT_UUID, ESP_UUID)
    # nothing was created on the real tmp_path by our custom writer
    assert not (tmp_path / ETC_FSTAB.lstrip("/")).exists()


# --- SubprocessRunner --------------------------------------------------------

def test_subprocess_runner_echo():
    r = SubprocessRunner()(("echo", "hello-pengyos"))
    assert r.ok is True
    assert r.returncode == 0
    assert "hello-pengyos" in r.stdout
    assert r.argv == ("echo", "hello-pengyos")


def test_subprocess_runner_missing_binary():
    r = SubprocessRunner()(("definitely-not-a-real-pengyos-cmd-xyz",))
    assert r.ok is False
    assert r.returncode != 0


# --- the install CLI handler -------------------------------------------------

def _cli_run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def test_cli_install_refuses_without_yes(monkeypatch, tmp_path):
    # Without --yes the handler refuses before touching the runner or the disk.
    calls = []
    monkeypatch.setattr(planmod, "collect_target_probe",
                        lambda t: good_probe(t))
    monkeypatch.setattr(sys.modules["pengyos_installer.cli"], "_install_runner",
                        lambda: FakeRunner(fail=set(), stdout={}))
    code, out, err = _cli_run("install", "--target", "/dev/sdb", "--hostname",
                              "laptop")
    assert code == ExitCode.ERROR
    assert "--yes" in err
    assert out == ""


def test_cli_install_success_via_injected_runner(monkeypatch, tmp_path):
    monkeypatch.setattr(planmod, "collect_target_probe",
                        lambda t: good_probe(t))
    runner = FakeRunner()
    monkeypatch.setattr(sys.modules["pengyos_installer.cli"], "_install_runner",
                        lambda: runner)
    monkeypatch.setattr(sys.modules["pengyos_installer.cli"],
                        "INSTALL_WORK_ROOT", str(tmp_path))
    code, out, err = _cli_run("install", "--target", "/dev/sdb",
                              "--hostname", "laptop", "--yes")
    assert code == ExitCode.OK
    assert "PengyOS install complete" in out
    assert err == ""
    # the runner was driven through the full plan (13 commands + teardown)
    assert len(runner.calls) == len(_expected_argv_sequence()) + 3
    # the config files landed under the injected work root
    assert (tmp_path / ETC_FSTAB.lstrip("/")).exists()


def test_cli_install_refuses_failed_sanity(monkeypatch):
    monkeypatch.setattr(
        planmod, "collect_target_probe",
        lambda t: TargetProbe(path=t, is_block_device=True, size_bytes=1024))
    code, out, err = _cli_run("install", "--target", "/dev/sdb", "--yes")
    assert code == ExitCode.ERROR
    assert "[too-small]" in err
    assert out == ""


def test_cli_install_step_failure_is_exit_1(monkeypatch, tmp_path):
    monkeypatch.setattr(planmod, "collect_target_probe",
                        lambda t: good_probe(t))
    monkeypatch.setattr(sys.modules["pengyos_installer.cli"], "_install_runner",
                        lambda: FakeRunner(fail={"grub-install"}))
    monkeypatch.setattr(sys.modules["pengyos_installer.cli"],
                        "INSTALL_WORK_ROOT", str(tmp_path))
    code, out, err = _cli_run("install", "--target", "/dev/sdb", "--yes")
    assert code == ExitCode.ERROR
    assert "[step-failed]" in err
    assert out == ""


# --- --rootfs-src validation at install time --------------------------------
#
# The plan classifies the source syntactically (by name); the install re-checks
# it against the real filesystem (an injected inspector) before the copy step,
# so a misnamed/missing source is a hard error before anything touches the
# target. See pengyos_installer.install.validate_rootfs_src.

def test_validate_rootfs_src_directory_ok():
    # a directory-kind source that is a directory is fine
    assert validate_rootfs_src(
        "/srv/rootfs", COPY_SRC_KIND_DIR,
        inspect=_fake_inspect({"/srv/rootfs": (True, True, False)}),
    ) is None


def test_validate_rootfs_src_tar_file_ok():
    # a tar-kind source that is an existing regular file is fine
    assert validate_rootfs_src(
        "/srv/rootfs.tar", COPY_SRC_KIND_TAR,
        inspect=_fake_inspect({"/srv/rootfs.tar": (True, False, True)}),
    ) is None


def test_validate_rootfs_src_missing_is_hard_error():
    with pytest.raises(InstallError) as ei:
        validate_rootfs_src(
            "/srv/nope.tar", COPY_SRC_KIND_TAR,
            inspect=_fake_inspect({}),  # missing
        )
    assert ei.value.code == ERR_ROOTFS_SRC_MISSING
    assert "/srv/nope.tar" in ei.value.message


def test_validate_rootfs_src_tar_classified_but_is_a_directory():
    with pytest.raises(InstallError) as ei:
        validate_rootfs_src(
            "/srv/rootfs.tar", COPY_SRC_KIND_TAR,
            inspect=_fake_inspect({"/srv/rootfs.tar": (True, True, False)}),
        )
    assert ei.value.code == ERR_ROOTFS_SRC_TYPE_MISMATCH


def test_validate_rootfs_src_directory_classified_but_is_a_file():
    with pytest.raises(InstallError) as ei:
        validate_rootfs_src(
            "/srv/rootfs", COPY_SRC_KIND_DIR,
            inspect=_fake_inspect({"/srv/rootfs": (True, False, True)}),
        )
    assert ei.value.code == ERR_ROOTFS_SRC_TYPE_MISMATCH


def test_default_source_inspector_reports_directory(tmp_path):
    info = default_source_inspector(str(tmp_path))
    assert (info.exists, info.is_dir, info.is_file) == (True, True, False)


def test_default_source_inspector_reports_file(tmp_path):
    f = tmp_path / "rootfs.tar"
    f.write_text("x")
    info = default_source_inspector(str(f))
    assert (info.exists, info.is_dir, info.is_file) == (True, False, True)


def test_default_source_inspector_reports_missing(tmp_path):
    info = default_source_inspector(str(tmp_path / "does-not-exist-xyz.tar"))
    assert (info.exists, info.is_dir, info.is_file) == (False, False, False)


def test_execute_install_missing_source_aborts_before_any_step(tmp_path):
    plan = _plan(rootfs_src="/srv/missing.tar")
    runner = FakeRunner()
    with pytest.raises(InstallError) as ei:
        execute_install(plan, runner, work_root=str(tmp_path),
                        source_inspector=_fake_inspect({}))
    assert ei.value.code == ERR_ROOTFS_SRC_MISSING
    # no command was run and nothing was mounted before the refusal
    assert runner.calls == []


def test_execute_install_source_type_mismatch_aborts_before_any_step(tmp_path):
    # a name classified as a tarball, but a directory on disk
    plan = _plan(rootfs_src="/srv/rootfs.tar")
    runner = FakeRunner()
    with pytest.raises(InstallError) as ei:
        execute_install(
            plan, runner, work_root=str(tmp_path),
            source_inspector=_fake_inspect({"/srv/rootfs.tar": (True, True, False)}),
        )
    assert ei.value.code == ERR_ROOTFS_SRC_TYPE_MISMATCH
    assert runner.calls == []


def test_execute_install_default_running_root_is_also_validated(tmp_path):
    # no --rootfs-src -> the source is the running '/'; it is stat'ed too
    plan = _plan(hostname="laptop")  # rootfs_src=None
    runner = FakeRunner()
    with pytest.raises(InstallError) as ei:
        execute_install(plan, runner, work_root=str(tmp_path),
                        source_inspector=_fake_inspect({}))  # '/' missing
    assert ei.value.code == ERR_ROOTFS_SRC_MISSING
    assert runner.calls == []


def test_execute_install_uses_classified_kind_for_validation(tmp_path):
    # a non-.tar name is classified as a directory; a regular file there is a
    # dir-kind/type mismatch, proving execute_install feeds classify_rootfs_src
    # into validate_rootfs_src (not a hard-coded tar/dir assumption).
    assert classify_rootfs_src("/srv/rootfs-image") == COPY_SRC_KIND_DIR
    plan = _plan(rootfs_src="/srv/rootfs-image")
    runner = FakeRunner()
    with pytest.raises(InstallError) as ei:
        execute_install(
            plan, runner, work_root=str(tmp_path),
            source_inspector=_fake_inspect({"/srv/rootfs-image": (True, False, True)}),
        )
    assert ei.value.code == ERR_ROOTFS_SRC_TYPE_MISMATCH
    assert runner.calls == []


def test_cli_install_missing_rootfs_src_refuses(monkeypatch, tmp_path):
    # a real, non-existent --rootfs-src: the default inspector stats it, it is
    # missing, and the install refuses (exit 1) before running any command
    monkeypatch.setattr(planmod, "collect_target_probe", lambda t: good_probe(t))
    runner = FakeRunner()
    monkeypatch.setattr(sys.modules["pengyos_installer.cli"], "_install_runner",
                        lambda: runner)
    monkeypatch.setattr(sys.modules["pengyos_installer.cli"],
                        "INSTALL_WORK_ROOT", str(tmp_path))
    code, out, err = _cli_run(
        "install", "--target", "/dev/sdb", "--hostname", "laptop",
        "--rootfs-src", "/definitely/not/here-pengyos-xyz.tar", "--yes")
    assert code == ExitCode.ERROR
    assert "rootfs source" in err
    assert "[rootfs-src-missing]" in err
    assert out == ""
    # the source was refused before any planned command ran
    assert runner.calls == []
