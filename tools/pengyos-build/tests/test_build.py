"""Tests for the build step + status stage scanner (pengyos_build.build).

Hermetic: make is a fake ``subprocess.run``-like spawner that records the
argv/env and streams its "output" straight into the log file handle it is
handed, so the capture path (merged stdout+stderr streaming, log location,
truncation) is exercised without spawning a process.  CLI-level process tests
live in ``tests/test_cli.py`` and use a fake ``make`` on ``PATH``.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from pengyos_build import hostshim
from pengyos_build.build import (
    BUILD_LOG,
    ERROR,
    NOT_STARTED,
    STAGE_ORDER,
    BuildError,
    StatusReport,
    build_argv,
    build_log_path,
    build_target,
    default_jobs,
    detect_stage,
    status_for,
)
from pengyos_build.configure import HOSTSHIM_DIRNAME, LOGS_SUBDIR
from pengyos_build.targets import Target


def _target(**overrides) -> Target:
    base = dict(
        name="demo",
        br_version="2026.02.3",
        external_tree=Path("/external"),
        defconfig="demo_defconfig",
        board="",
    )
    base.update(overrides)
    return Target(**base)


def _fake_make(stdout_lines: tuple[str, ...] = ("make says hi\n",), rc: int = 0):
    """A spawner standing in for make; returns (spawner, call_log)."""
    calls: list[dict] = []

    def spawner(argv, **kwargs):
        calls.append({"argv": list(argv), **kwargs})
        out = kwargs.get("stdout")
        if out is not None:
            for line in stdout_lines:
                out.write(line)
        return subprocess.CompletedProcess(list(argv), rc)

    return spawner, calls


@pytest.fixture
def build_tree(tmp_path: Path) -> dict[str, Path]:
    """A build root with the acquired source tree, the external tree, AND an
    already-configured output dir (so the build step's preconditions pass)."""
    root = tmp_path / "root"
    source = root / "buildroot-2026.02.3"
    source.mkdir(parents=True)
    output = root / "output" / "demo"
    output.mkdir(parents=True)
    (output / LOGS_SUBDIR).mkdir()
    external = tmp_path / "ext"
    external.mkdir()
    return {
        "root": root,
        "source": source,
        "output": output,
        "external": external,
    }


def _resolver_from(mapping: dict[str, str]) -> "hostshim.Callable":
    return lambda name: mapping.get(name)


class TestBuildArgv:
    def test_golden_argv_without_board(self):
        target = _target(external_tree=Path("/external"))
        argv = build_argv("/src/br", "/out/demo", target, jobs=8)
        assert argv == (
            "make",
            "-C",
            "/src/br",
            "O=/out/demo",
            "BR2_EXTERNAL=/external",
            "-j8",
        )

    def test_golden_argv_with_board(self):
        target = _target(external_tree=Path("/external"), board="pengyos/qemu-x86_64")
        argv = build_argv("/src/br", "/out/demo", target, jobs=4)
        assert argv == (
            "make",
            "-C",
            "/src/br",
            "O=/out/demo",
            "BR2_EXTERNAL=/external",
            "BR2_BOARD=pengyos/qemu-x86_64",
            "-j4",
        )

    def test_defconfig_is_not_repassed(self):
        # A build must build the already-configured .config, not re-run kconfig:
        # the bare defconfig must NOT appear in the build argv.
        target = _target(defconfig="pengyos_server_defconfig")
        argv = build_argv("/s", "/o", target, jobs=2)
        assert "pengyos_server_defconfig" not in argv
        # ...unlike the configure argv, which ends in the defconfig.
        from pengyos_build.configure import configure_argv

        cfg = configure_argv("/s", "/o", target)
        assert cfg[-1] == "pengyos_server_defconfig"

    def test_jobs_always_last(self):
        target = _target(board="b/q")
        argv = build_argv("/s", "/o", target, jobs=16)
        assert argv[-1] == "-j16"


class TestDefaultJobs:
    def test_returns_positive_int(self, monkeypatch):
        monkeypatch.setattr(os, "cpu_count", lambda: 12)
        assert default_jobs() == 12

    def test_clamps_to_one_when_cpu_count_is_none(self, monkeypatch):
        monkeypatch.setattr(os, "cpu_count", lambda: None)
        assert default_jobs() == 1

    def test_clamps_to_one_when_cpu_count_is_zero(self, monkeypatch):
        monkeypatch.setattr(os, "cpu_count", lambda: 0)
        assert default_jobs() == 1


class TestBuildTarget:
    def test_success_streams_merged_log(self, build_tree):
        target = _target(external_tree=build_tree["external"])
        spawner, calls = _fake_make(("# stdout line\n", "# stderr line (merged)\n"))
        result = build_target(
            target,
            build_tree["root"],
            jobs=6,
            env={"PATH": "/usr/bin"},
            runner=spawner,
        )

        assert result.target == "demo"
        assert result.source == build_tree["source"]
        assert result.output == build_tree["output"]
        assert result.jobs == 6
        assert result.argv == build_argv(
            build_tree["source"], build_tree["output"], target, 6
        )
        assert result.log == build_tree["output"] / LOGS_SUBDIR / BUILD_LOG
        assert str(result) == f"built demo (6 jobs): log {result.log}"

        log = result.log.read_text()
        assert "# stdout line\n" in log
        assert "# stderr line (merged)\n" in log
        # stderr must come through the SAME handle: streamed, merged, in order.
        assert log.index("stdout line") < log.index("stderr line")

    def test_spawner_receives_golden_argv_and_env(self, build_tree):
        target = _target(external_tree=build_tree["external"], board="b/q")
        spawner, calls = _fake_make()
        env = {"PATH": "/usr/bin", "FOO": "bar"}
        build_target(
            target, build_tree["root"], jobs=3, env=env, runner=spawner
        )

        call = calls[0]
        assert call["argv"] == list(
            build_argv(build_tree["source"], build_tree["output"], target, 3)
        )
        # A COPY of the caller's env (hostshim prepends PATH in the copy).
        assert call["env"]["FOO"] == "bar"
        assert "PATH" in call["env"]
        assert env == {"PATH": "/usr/bin", "FOO": "bar"}
        # Merged streaming: stderr redirected onto the log handle, text mode.
        assert call["stderr"] is subprocess.STDOUT
        assert call["text"] is True
        # A real (writable) log file handle, not None/stdout.
        assert hasattr(call["stdout"], "write")

    def test_default_jobs_is_nproc(self, build_tree, monkeypatch):
        monkeypatch.setattr(os, "cpu_count", lambda: 8)
        target = _target(external_tree=build_tree["external"])
        spawner, calls = _fake_make()
        result = build_target(
            target, build_tree["root"], env={"PATH": "/usr/bin"}, runner=spawner
        )
        assert result.jobs == 8
        assert result.argv[-1] == "-j8"

    def test_explicit_jobs_override_default(self, build_tree, monkeypatch):
        monkeypatch.setattr(os, "cpu_count", lambda: 8)
        target = _target(external_tree=build_tree["external"])
        spawner, _ = _fake_make()
        result = build_target(
            target,
            build_tree["root"],
            jobs=2,
            env={"PATH": "/usr/bin"},
            runner=spawner,
        )
        assert result.jobs == 2
        assert result.argv[-1] == "-j2"

    def test_jobs_below_one_is_an_error(self, build_tree):
        target = _target(external_tree=build_tree["external"])
        with pytest.raises(BuildError) as exc:
            build_target(target, build_tree["root"], jobs=0, env={"PATH": "/x"})
        assert "positive integer" in str(exc.value)

    def test_log_is_truncated_on_rerun(self, build_tree):
        target = _target(external_tree=build_tree["external"])
        first, _ = _fake_make(("first build\n",))
        build_target(target, build_tree["root"], env={"PATH": "/x"}, runner=first)
        second, _ = _fake_make(("second build\n",))
        build_target(target, build_tree["root"], env={"PATH": "/x"}, runner=second)
        log = (build_tree["output"] / LOGS_SUBDIR / BUILD_LOG).read_text()
        assert log == "second build\n"  # rewritten, never appended

    def test_make_failure_raises_with_rc_and_log(self, build_tree):
        target = _target(external_tree=build_tree["external"])
        spawner, _ = _fake_make(("error: something\n"), rc=2)
        with pytest.raises(BuildError) as exc:
            build_target(target, build_tree["root"], env={"PATH": "/x"}, runner=spawner)
        msg = str(exc.value)
        assert "exited 2" in msg
        assert "logs/build.log" in msg

    def test_missing_source_tree_suggests_acquire(self, tmp_path):
        target = _target(external_tree=tmp_path / "ext")
        (tmp_path / "ext").mkdir()
        # An output dir exists (configured) but the source tree does not.
        (tmp_path / "root" / "output" / "demo").mkdir(parents=True)
        with pytest.raises(BuildError) as exc:
            build_target(target, tmp_path / "root", env={"PATH": "/x"})
        assert "not found" in str(exc.value)
        assert "pengyos-build acquire demo" in str(exc.value)

    def test_unconfigured_target_suggests_configure(self, tmp_path):
        target = _target(external_tree=tmp_path / "ext")
        (tmp_path / "ext").mkdir()
        (tmp_path / "root" / "buildroot-2026.02.3").mkdir(parents=True)
        # No output/demo dir.
        with pytest.raises(BuildError) as exc:
            build_target(
                target, tmp_path / "root", env={"PATH": "/x"}, runner=_fake_make()[0]
            )
        assert "not configured" in str(exc.value)
        assert "pengyos-build configure demo" in str(exc.value)

    def test_make_not_found(self, build_tree):
        target = _target(external_tree=build_tree["external"])

        def spawner(argv, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", "make")

        with pytest.raises(BuildError) as exc:
            build_target(target, build_tree["root"], env={"PATH": "/x"}, runner=spawner)
        assert "make not found on PATH" in str(exc.value)
        assert "make" in str(exc.value)

    def test_build_root_defaults_to_env(self, build_tree, monkeypatch):
        target = _target(external_tree=build_tree["external"])
        env = {"PENGYOS_BUILD_ROOT": str(build_tree["root"]), "PATH": "/x"}
        spawner, _ = _fake_make()
        result = build_target(target, None, env=env, runner=spawner)
        assert result.output == build_tree["root"] / "output" / "demo"


class TestHostShimIntegration:
    """build must run make through the GNU-over-uutils PATH shim."""

    @pytest.fixture
    def gnu_host(self, tmp_path: Path) -> dict[str, Path]:
        host = tmp_path / "host"
        gnu_install = host / "bin" / "gnuinstall"
        gnu_install.parent.mkdir(parents=True)
        gnu_install.write_bytes(b"")
        gnu_install.chmod(
            gnu_install.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        uu_install = host / "uutils" / "coreutils" / "install"
        uu_install.parent.mkdir(parents=True)
        uu_install.write_bytes(b"")
        return {"gnu_dir": host / "bin", "gnu_install": gnu_install, "uu": uu_install}

    def test_shim_created_in_output_dir_and_path_prepended(self, build_tree, gnu_host):
        target = _target(external_tree=build_tree["external"])
        resolver = _resolver_from({"install": str(gnu_host["uu"])})
        spawner, calls = _fake_make()
        env = {"PATH": "/usr/bin:/bin"}
        result = build_target(
            target,
            build_tree["root"],
            jobs=4,
            env=env,
            gnu_dir=gnu_host["gnu_dir"],
            resolver=resolver,
            runner=spawner,
        )

        shim_dir = result.output / HOSTSHIM_DIRNAME
        link = shim_dir / "install"
        assert link.is_symlink()
        assert os.path.realpath(str(link)) == os.path.realpath(
            str(gnu_host["gnu_install"])
        )
        # make ran with the shim dir first on PATH.
        child_path = calls[0]["env"]["PATH"]
        assert child_path.startswith(f"{shim_dir}{os.pathsep}")
        # The caller's env dict was not mutated.
        assert env["PATH"] == "/usr/bin:/bin"


# ---------------------------------------------------------------------------
# status: stage detection
# ---------------------------------------------------------------------------

# Sample Buildroot-style log fragments, one per stage.  A real Buildroot log
# accumulates these in order, so a "later" stage log also contains the earlier
# markers — detect_stage must return the furthest one.
FETCHING_LOG = (
    ">>> zlib 1.3 Downloading\n"
    "wget -O /build/zlib-1.3.tar.gz https://zlib.net/zlib-1.3.tar.gz\n"
    ">>> zlib 1.3 Fetching\n"
)
BUILDING_LOG = (
    ">>> zlib 1.3 Downloading\n"
    ">>> host-zlib 1.3 Building\n"
    "  gcc -c -O2 target/zlib/foo.c\n"
    ">>> host-openssl 3.0.0 Compiling\n"
)
IMAGES_LOG = (
    ">>> host-zlib 1.3 Building\n"
    "Generating rootfs image\n"
    "  cp image-rootfs.ext4 images/rootfs.ext4\n"
    "  cp image-sdcard.img images/sdcard.img\n"
)
DONE_LOG = (
    ">>> host-zlib 1.3 Building\n"
    "Generating rootfs image\n"
    "Build completed successfully\n"
)


class TestDetectStage:
    def test_stage_order_is_exposed(self):
        assert STAGE_ORDER == ("fetching", "building", "images", "done")

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (None, NOT_STARTED),
            ("", NOT_STARTED),
            ("just some preamble, no build activity\n", NOT_STARTED),
            (FETCHING_LOG, "fetching"),
            (BUILDING_LOG, "building"),
            (IMAGES_LOG, "images"),
            (DONE_LOG, "done"),
        ],
    )
    def test_stages(self, text, expected):
        assert detect_stage(text) == expected

    def test_furthest_stage_wins_when_log_accumulates(self):
        # A full build log contains every earlier marker; the stage is the
        # furthest one reached, not the first.
        full = FETCHING_LOG + BUILDING_LOG + IMAGES_LOG
        assert detect_stage(full) == "images"

    def test_error_marker_wins_over_done(self):
        # A make failure prints *** [target] Error even if earlier stages ran.
        log = IMAGES_LOG + "make[2]: *** [host-foo] Error 1\n"
        assert detect_stage(log) == ERROR

    def test_error_marker_with_error_colon_line(self):
        log = "cc1: error: something failed\n"
        assert detect_stage(log) == ERROR

    def test_building_word_in_preamble_is_building(self):
        # "Building" is a build marker even without a full Buildroot header.
        assert detect_stage("Building the thing...\n") == "building"

    def test_error_is_a_distinct_name(self):
        assert ERROR not in STAGE_ORDER
        assert NOT_STARTED not in STAGE_ORDER


class TestBuildLogPath:
    def test_path_under_output_dir(self):
        target = _target()
        assert build_log_path("/root", target) == Path(
            "/root/output/demo/logs/build.log"
        )


class TestStatusFor:
    def test_missing_log_is_not_started(self, build_tree):
        target = _target(external_tree=build_tree["external"])
        # No build.log written yet (only the logs dir exists).
        report = status_for(target, build_tree["root"], env={"PATH": "/x"})
        assert report.target == "demo"
        assert report.stage == NOT_STARTED
        assert report.log == build_tree["output"] / LOGS_SUBDIR / BUILD_LOG
        assert str(report) == "demo: not_started"
        assert isinstance(report, StatusReport)

    def test_present_log_reports_stage(self, build_tree):
        target = _target(external_tree=build_tree["external"])
        (build_tree["output"] / LOGS_SUBDIR / BUILD_LOG).write_text(
            BUILDING_LOG, encoding="utf-8"
        )
        report = status_for(target, build_tree["root"], env={"PATH": "/x"})
        assert report.stage == "building"
        assert str(report) == "demo: building"

    def test_status_reflects_a_real_build_run(self, build_tree):
        target = _target(external_tree=build_tree["external"])
        spawner, _ = _fake_make(tuple(DONE_LOG.splitlines(keepends=True)))
        build_target(
            target, build_tree["root"], jobs=2, env={"PATH": "/x"}, runner=spawner
        )
        report = status_for(target, build_tree["root"], env={"PATH": "/x"})
        assert report.stage == "done"

    def test_status_after_failed_build_is_error(self, build_tree):
        target = _target(external_tree=build_tree["external"])
        # A build that failed (non-zero rc) still leaves a log with the
        # error marker; status reads it as "error" (the build is not re-run).
        (build_tree["output"] / LOGS_SUBDIR / BUILD_LOG).write_text(
            IMAGES_LOG + "make[2]: *** [host-foo] Error 1\n", encoding="utf-8"
        )
        report = status_for(target, build_tree["root"], env={"PATH": "/x"})
        assert report.stage == ERROR

    def test_build_root_defaults_to_env(self, build_tree, monkeypatch):
        target = _target(external_tree=build_tree["external"])
        (build_tree["output"] / LOGS_SUBDIR / BUILD_LOG).write_text(
            FETCHING_LOG, encoding="utf-8"
        )
        env = {"PENGYOS_BUILD_ROOT": str(build_tree["root"]), "PATH": "/x"}
        report = status_for(target, None, env=env)
        assert report.stage == "fetching"


class TestDryRun:
    """dry_run=True: resolve the exact make argv (resolved -j<jobs>); run
    nothing, write nothing."""

    def test_returns_exact_argv_without_running_make(self, build_tree):
        target = _target(external_tree=build_tree["external"], board="b/q")
        spawner, calls = _fake_make()
        result = build_target(
            target,
            build_tree["root"],
            jobs=4,
            env={"PATH": "/usr/bin"},
            runner=spawner,
            dry_run=True,
        )
        assert result.argv == build_argv(
            build_tree["source"], build_tree["output"], target, jobs=4
        )
        assert result.jobs == 4
        assert calls == []  # make was never invoked
        # No log written (the pre-existing logs dir stays empty), no host shim.
        assert not (build_tree["output"] / LOGS_SUBDIR / BUILD_LOG).exists()
        assert not (build_tree["output"] / HOSTSHIM_DIRNAME).exists()

    def test_dry_run_resolves_default_jobs(self, build_tree):
        target = _target(external_tree=build_tree["external"])
        spawner, calls = _fake_make()
        result = build_target(
            target, build_tree["root"], env={"PATH": "/x"}, runner=spawner, dry_run=True
        )
        assert result.argv[-1] == f"-j{default_jobs()}"
        assert calls == []

    def test_invalid_jobs_still_rejected(self, build_tree):
        target = _target(external_tree=build_tree["external"])
        with pytest.raises(BuildError) as exc:
            build_target(
                target,
                build_tree["root"],
                jobs=0,
                env={"PATH": "/x"},
                dry_run=True,
            )
        assert "positive integer" in str(exc.value)

    def test_unconfigured_target_still_an_error(self, build_tree):
        (build_tree["output"] / LOGS_SUBDIR).rmdir()
        build_tree["output"].rmdir()
        target = _target(external_tree=build_tree["external"])
        with pytest.raises(BuildError) as exc:
            build_target(
                target, build_tree["root"], env={"PATH": "/x"}, dry_run=True
            )
        assert "pengyos-build configure demo" in str(exc.value)
