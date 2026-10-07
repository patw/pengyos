"""Tests for the clean step (pengyos_build.clean).

Hermetic: make is a fake ``subprocess.run``-like spawner that records the
argv/env and writes its "output" straight into the log file handle it is
handed, so the capture path (merged stdout+stderr, log location, truncation)
is exercised without spawning a process.  A scoping test proves the whole
operation is confined to the output dir (source tree and external tree
untouched).  CLI-level process tests live in ``tests/test_cli.py`` and use a
fake ``make`` on ``PATH``.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from pengyos_build import hostshim
from pengyos_build.clean import (
    CLEAN_LOG,
    CleanError,
    clean_argv,
    clean_target,
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


def _resolver_from(mapping: dict[str, str]) -> hostshim.Callable:
    return lambda name: mapping.get(name)


@pytest.fixture
def build_tree(tmp_path: Path) -> dict[str, Path]:
    """A build root with an acquired source tree, an external tree, and a
    configured per-target output dir (with a sentinel ``.config``)."""
    root = tmp_path / "root"
    source = root / "buildroot-2026.02.3"
    source.mkdir(parents=True)
    (source / "Makefile").write_text("# source tree marker\n")
    external = tmp_path / "ext"
    external.mkdir()
    (external / "external.mk").write_text("# external tree marker\n")
    output = root / "output" / "demo"
    output.mkdir(parents=True)
    (output / ".config").write_text("# BR2_CONFIG\n")
    return {"root": root, "source": source, "external": external, "output": output}


class TestCleanArgv:
    def test_golden_argv_without_board(self):
        target = _target(external_tree=Path("/external"))
        argv = clean_argv("/src/br", "/out/demo", target)
        assert argv == (
            "make",
            "-C",
            "/src/br",
            "O=/out/demo",
            "BR2_EXTERNAL=/external",
            "clean",
        )

    def test_golden_argv_with_board(self):
        target = _target(external_tree=Path("/external"), board="pengyos/qemu-x86_64")
        argv = clean_argv("/src/br", "/out/demo", target)
        assert argv == (
            "make",
            "-C",
            "/src/br",
            "O=/out/demo",
            "BR2_EXTERNAL=/external",
            "BR2_BOARD=pengyos/qemu-x86_64",
            "clean",
        )

    def test_defconfig_is_not_repassed(self):
        target = _target(defconfig="pengyos_server_defconfig")
        argv = clean_argv("/s", "/o", target)
        assert argv[-1] == "clean"
        assert "pengyos_server_defconfig" not in argv


class TestCleanTarget:
    def test_success_writes_merged_log(self, build_tree):
        target = _target(external_tree=build_tree["external"])
        spawner, calls = _fake_make(("# stdout line\n", "# stderr line (merged)\n"))
        result = clean_target(
            target, build_tree["root"], env={"PATH": "/usr/bin"}, runner=spawner
        )

        assert result.target == "demo"
        assert result.source == build_tree["source"]
        assert result.output == build_tree["output"]
        assert result.argv == clean_argv(
            build_tree["source"], build_tree["output"], target
        )
        assert result.log == build_tree["output"] / LOGS_SUBDIR / CLEAN_LOG
        assert str(result) == f"cleaned demo: log {result.log}"

        log = result.log.read_text()
        assert "# stdout line\n" in log
        assert "# stderr line (merged)\n" in log
        # stderr must come through the SAME handle: merged, in order.
        assert log.index("stdout line") < log.index("stderr line")

    def test_spawner_receives_golden_argv_and_env(self, build_tree):
        target = _target(external_tree=build_tree["external"], board="b/q")
        spawner, calls = _fake_make()
        env = {"PATH": "/usr/bin", "FOO": "bar"}
        clean_target(target, build_tree["root"], env=env, runner=spawner)

        call = calls[0]
        assert call["argv"] == list(
            clean_argv(build_tree["source"], build_tree["output"], target)
        )
        # A COPY of the caller's env (hostshim prepends PATH in the copy;
        # the caller's dict must not be mutated).
        assert call["env"]["FOO"] == "bar"
        assert env == {"PATH": "/usr/bin", "FOO": "bar"}
        # Merged capture: stderr redirected onto the log handle, text mode.
        assert call["stderr"] is subprocess.STDOUT
        assert call["text"] is True
        # A real (writable) log file handle, not None/stdout.
        assert hasattr(call["stdout"], "write")

    def test_log_is_truncated_on_rerun(self, build_tree):
        target = _target(external_tree=build_tree["external"])
        first, _ = _fake_make(("first clean\n",))
        clean_target(target, build_tree["root"], env={"PATH": "/x"}, runner=first)
        second, _ = _fake_make(("second clean\n",))
        clean_target(target, build_tree["root"], env={"PATH": "/x"}, runner=second)
        log = (build_tree["output"] / LOGS_SUBDIR / CLEAN_LOG).read_text()
        assert log == "second clean\n"  # rewritten, never appended

    def test_scoped_to_output_dir_only(self, build_tree):
        """The whole operation must leave the source tree, the external tree,
        and the build root itself untouched; only the output dir may change."""
        target = _target(external_tree=build_tree["external"])
        root = build_tree["root"]
        source = build_tree["source"]
        external = build_tree["external"]

        # Sentinel state OUTSIDE the output dir.
        (root / "root-sentinel.txt").write_text("root\n")
        (source / "pkg").mkdir()
        (source / "pkg" / "sentinel.c").write_text("src\n")
        (external / "configs" / "sentinel_defconfig").parent.mkdir()
        (external / "configs" / "sentinel_defconfig").write_text("cfg\n")

        spawner, _ = _fake_make(("cleaning\n",))
        clean_target(target, root, env={"PATH": "/usr/bin"}, runner=spawner)

        # Everything outside <out> is byte-identical / still present.
        assert (root / "root-sentinel.txt").read_text() == "root\n"
        assert (source / "Makefile").read_text() == "# source tree marker\n"
        assert (source / "pkg" / "sentinel.c").read_text() == "src\n"
        assert (external / "external.mk").read_text() == "# external tree marker\n"
        assert (external / "configs" / "sentinel_defconfig").read_text() == "cfg\n"

        # Inside the output dir: the log was written and the output dir
        # itself (and its .config, as make clean does) still exists — clean
        # removes artifacts, it never nukes the configured dir.
        assert (build_tree["output"] / LOGS_SUBDIR / CLEAN_LOG).is_file()
        assert build_tree["output"].is_dir()
        assert (build_tree["output"] / ".config").is_file()

    def test_make_failure_raises_with_rc_and_log(self, build_tree):
        target = _target(external_tree=build_tree["external"])
        spawner, _ = _fake_make(("error: something\n"), rc=2)
        with pytest.raises(CleanError) as exc:
            clean_target(target, build_tree["root"], env={"PATH": "/x"}, runner=spawner)
        msg = str(exc.value)
        assert "exited 2" in msg
        assert "logs/clean.log" in msg

    def test_missing_source_tree_suggests_acquire(self, tmp_path):
        target = _target(external_tree=tmp_path / "ext")
        (tmp_path / "ext").mkdir()
        # No buildroot-2026.02.3 dir under the root.
        with pytest.raises(CleanError) as exc:
            clean_target(target, tmp_path / "root", env={"PATH": "/x"})
        assert "not found" in str(exc.value)
        assert "pengyos-build acquire demo" in str(exc.value)

    def test_missing_external_tree_is_an_error(self, build_tree):
        target = _target(external_tree=build_tree["root"] / "no-such-ext")
        with pytest.raises(CleanError) as exc:
            clean_target(
                target, build_tree["root"], env={"PATH": "/x"}, runner=_fake_make()[0]
            )
        assert "external tree not found" in str(exc.value)

    def test_unconfigured_target_suggests_configure(self, build_tree):
        target = _target(external_tree=build_tree["external"])
        (build_tree["output"] / ".config").unlink()
        build_tree["output"].rmdir()  # never configured
        with pytest.raises(CleanError) as exc:
            clean_target(
                target, build_tree["root"], env={"PATH": "/x"}, runner=_fake_make()[0]
            )
        assert "not configured yet" in str(exc.value)
        assert "pengyos-build configure demo" in str(exc.value)

    def test_make_not_found(self, build_tree):
        target = _target(external_tree=build_tree["external"])

        def spawner(argv, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", "make")

        with pytest.raises(CleanError) as exc:
            clean_target(target, build_tree["root"], env={"PATH": "/x"}, runner=spawner)
        assert "make not found on PATH" in str(exc.value)
        assert "make" in str(exc.value)

    def test_build_root_defaults_to_env(self, build_tree, monkeypatch):
        target = _target(external_tree=build_tree["external"])
        env = {"PENGYOS_BUILD_ROOT": str(build_tree["root"]), "PATH": "/x"}
        spawner, _ = _fake_make()
        result = clean_target(target, None, env=env, runner=spawner)
        assert result.output == build_tree["root"] / "output" / "demo"


class TestHostShimIntegration:
    """clean must run make through the GNU-over-uutils PATH shim."""

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
        result = clean_target(
            target,
            build_tree["root"],
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

    def test_no_shim_dir_when_host_is_stock_gnu(self, build_tree, gnu_host):
        target = _target(external_tree=build_tree["external"])
        resolver = _resolver_from({"install": str(gnu_host["gnu_install"])})
        spawner, calls = _fake_make()
        result = clean_target(
            target,
            build_tree["root"],
            env={"PATH": "/usr/bin"},
            gnu_dir=gnu_host["gnu_dir"],
            resolver=resolver,
            runner=spawner,
        )
        # Nothing was uutils -> the shim dir is never created, PATH untouched.
        assert not (result.output / HOSTSHIM_DIRNAME).exists()
        assert calls[0]["env"]["PATH"] == "/usr/bin"


class TestDryRun:
    """dry_run=True: resolve the exact make argv; run nothing, write nothing."""

    def test_returns_exact_argv_without_running_make(self, build_tree):
        target = _target(external_tree=build_tree["external"], board="b/q")
        spawner, calls = _fake_make()
        result = clean_target(
            target,
            build_tree["root"],
            env={"PATH": "/usr/bin"},
            runner=spawner,
            dry_run=True,
        )
        assert result.argv == clean_argv(build_tree["source"], build_tree["output"], target)
        assert calls == []  # make was never invoked
        assert not (build_tree["output"] / LOGS_SUBDIR / CLEAN_LOG).exists()
        assert not (build_tree["output"] / HOSTSHIM_DIRNAME).exists()
        # The configured output dir (and its .config) is untouched.
        assert build_tree["output"].is_dir()
        assert (build_tree["output"] / ".config").is_file()

    def test_still_checks_preconditions(self, build_tree):
        target = _target(external_tree=build_tree["root"] / "no-such-ext")
        with pytest.raises(CleanError) as exc:
            clean_target(
                target, build_tree["root"], env={"PATH": "/x"}, dry_run=True
            )
        assert "external tree not found" in str(exc.value)

    def test_unconfigured_target_still_suggests_configure(self, build_tree):
        (build_tree["output"] / ".config").unlink()
        build_tree["output"].rmdir()
        target = _target(external_tree=build_tree["external"])
        with pytest.raises(CleanError) as exc:
            clean_target(
                target, build_tree["root"], env={"PATH": "/x"}, dry_run=True
            )
        assert "pengyos-build configure demo" in str(exc.value)
