"""Tests for the CLI (pengyos_build.cli)."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import pengyos_build
from pengyos_build.cli import build_parser, main

ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*argv: str, **env: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI as a real process: ``python -m pengyos_build ...``."""
    base_env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}
    base_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "pengyos_build", *argv],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=base_env,
    )


class TestModule:
    def test_version_string(self):
        assert pengyos_build.__version__
        # The pyproject version and the package version must agree.
        import tomllib

        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        assert pyproject["project"]["version"] == pengyos_build.__version__

    def test_console_script_declared(self):
        import tomllib

        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        assert pyproject["project"]["scripts"]["pengyos-build"] == (
            "pengyos_build.cli:main"
        )


class TestParser:
    def test_parser_prog_name(self):
        assert build_parser().prog == "pengyos-build"

    def test_no_command_returns_2(self, capsys):
        assert main([]) == 2
        err = capsys.readouterr().err
        assert "usage:" in err

    def test_version_subcommand_returns_0(self, capsys):
        assert main(["version"]) == 0
        out = capsys.readouterr().out
        assert out.strip() == pengyos_build.__version__

    def test_unknown_command_returns_2(self):
        # argparse exits with 2 on an unrecognised subcommand.
        with pytest.raises(SystemExit) as exc:
            main(["not-a-command"])
        assert exc.value.code == 2

    def test_help_exits_0(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "pengyos-build" in out
        assert "version" in out


class TestProcessEntryPoint:
    def test_python_m_version(self):
        proc = _run_cli("version")
        assert proc.returncode == 0
        assert proc.stdout.strip() == pengyos_build.__version__

    def test_python_m_dashed_version_flag(self):
        proc = _run_cli("--version")
        assert proc.returncode == 0
        assert pengyos_build.__version__ in proc.stdout

    def test_no_args_nonzero(self):
        proc = _run_cli()
        assert proc.returncode == 2
        assert "usage:" in proc.stderr


class TestAcquireCommand:
    """End-to-end ``acquire`` with a fake ``git`` on PATH (no network).

    The fake git models the exact commands ``acquire_buildroot`` issues:
    ``clone --no-checkout`` creates ``<dest>/.git``; ``checkout``/``fetch``
    are no-ops; every ``rev-parse`` returns one fixed commit (so HEAD equals
    the tag's commit and the post-condition holds).  Optionally it appends
    each argv to ``$PENGYOS_GITLOG`` so a test can assert the remote URL.
    """

    TARGETS_TOML = """\
[[targets]]
name = "demo"
br_version = "2026.02.3"
external_tree = "ext"
defconfig = "demo_defconfig"
"""

    COMMIT = "abcdef0123456789abcdef0123456789abcdef01"
    SHORT = "abcdef012345"

    @staticmethod
    def _fake_git(path: Path) -> None:
        path.write_text(
            "#!/bin/sh\n"
            f'commit="{TestAcquireCommand.COMMIT}"\n'
            'if [ -n "$PENGYOS_GITLOG" ]; then\n'
            '  printf \'%s\\n\' "$*" >> "$PENGYOS_GITLOG"\n'
            "fi\n"
            'if [ "$1" = "clone" ]; then\n'
            '  for a in "$@"; do dest="$a"; done\n'
            '  mkdir -p "$dest/.git"\n'
            "  exit 0\n"
            "fi\n"
            'if [ "$1" = "-C" ]; then\n'
            '  case "$3" in\n'
            "    rev-parse) echo \"$commit\"; exit 0 ;;\n"
            "    checkout|fetch) exit 0 ;;\n"
            "  esac\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        path.chmod(0o755)

    @pytest.fixture
    def env(self, tmp_path: Path) -> dict[str, str]:
        workspace = tmp_path / "ws"
        (workspace / "ext").mkdir(parents=True)
        (workspace / "targets.toml").write_text(self.TARGETS_TOML)
        root = tmp_path / "root"
        root.mkdir()

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        self._fake_git(bin_dir / "git")

        gitlog = tmp_path / "gitlog"
        return {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "PENGYOS_BUILD_ROOT": str(root),
            "PENGYOS_GITLOG": str(gitlog),
            "workspace": str(workspace),
            "root": str(root),
            "gitlog": str(gitlog),
        }

    def test_acquire_fresh_clone_prints_summary(self, env):
        proc = _run_cli(
            "acquire", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 0, proc.stderr
        expected = f"cloned: {env['root']}/buildroot-2026.02.3 @ 2026.02.3 ({self.SHORT})"
        assert proc.stdout.strip() == expected
        # The fake git materialised the work tree.
        assert (Path(env["root"]) / "buildroot-2026.02.3" / ".git").is_dir()

    def test_acquire_uses_blobless_clone_and_tag(self, env):
        proc = _run_cli(
            "acquire", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 0, proc.stderr
        log = Path(env["gitlog"]).read_text()
        # Blobless clone (the M1 transport) with the default remote.
        assert "--filter=blob:none" in log
        assert "--no-checkout" in log
        assert "https://git.buildroot.net/buildroot.git" in log
        # The pinned tag was checked out.
        assert f"checkout 2026.02.3" in log

    def test_acquire_idempotent_up_to_date(self, env):
        # Pre-create an existing git work tree already at the tag's commit.
        dest = Path(env["root"]) / "buildroot-2026.02.3"
        (dest / ".git").mkdir(parents=True)
        proc = _run_cli(
            "acquire", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == (
            f"up-to-date: {env['root']}/buildroot-2026.02.3 "
            f"@ 2026.02.3 ({self.SHORT})"
        )
        # Up-to-date runs only the read-only probes: no clone/checkout/fetch.
        log = Path(env["gitlog"]).read_text()
        assert "clone" not in log
        assert "checkout" not in log
        assert "fetch" not in log

    def test_acquire_respects_remote_override(self, env, tmp_path: Path):
        remote = "file:///does/not/matter"
        proc = _run_cli(
            "acquire", "demo", "--remote", remote,
            "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 0, proc.stderr
        log = Path(env["gitlog"]).read_text()
        assert remote in log

    def test_acquire_git_failure_is_exit_1(self, env, tmp_path: Path):
        # A fake git that fails on `clone`.
        bin_dir = env["PATH"].split(":")[0]
        (Path(bin_dir) / "git").write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
        (Path(bin_dir) / "git").chmod(0o755)
        proc = _run_cli(
            "acquire", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "git command failed (exit 3)" in proc.stderr

    def test_acquire_non_git_dest_is_exit_1(self, env):
        # A dest that exists as a plain file must not be overwritten.
        dest = Path(env["root"]) / "buildroot-2026.02.3"
        dest.write_text("user data", encoding="utf-8")
        proc = _run_cli(
            "acquire", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "not a directory" in proc.stderr
        # The user's file is untouched.
        assert dest.read_text(encoding="utf-8") == "user data"

    def test_acquire_unknown_target_is_exit_1(self, env):
        proc = _run_cli(
            "acquire", "nope", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "unknown target 'nope'" in proc.stderr

    def test_acquire_bad_targets_file_is_exit_1(self, tmp_path: Path):
        proc = _run_cli(
            "acquire", "demo", "--targets", str(tmp_path / "missing.toml")
        )
        assert proc.returncode == 1
        assert "targets file not found" in proc.stderr


class TestConfigureCommand:
    """End-to-end ``configure`` with a fake ``make`` on PATH (no network).

    The fake make echoes its argv and one stderr line, so the captured log
    proves: exact argv, O=/BR2_EXTERNAL/BR2_BOARD wiring, and merged
    stdout+stderr capture.
    """

    TARGETS_TOML = """\
[[targets]]
name = "demo"
br_version = "2026.02.3"
external_tree = "ext"
defconfig = "demo_defconfig"
board = "pengyos/qemu-x86_64"
"""

    @pytest.fixture
    def env(self, tmp_path: Path) -> dict[str, str]:
        """A build root with source + external trees and a fake make on PATH."""
        workspace = tmp_path / "ws"
        (workspace / "ext").mkdir(parents=True)
        (workspace / "targets.toml").write_text(self.TARGETS_TOML)
        root = tmp_path / "root"
        (root / "buildroot-2026.02.3").mkdir(parents=True)

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make = bin_dir / "make"
        make.write_text(
            "#!/bin/sh\n"
            'echo "make got: $@"\n'
            "echo 'make stderr line' 1>&2\n"
            "exit 0\n"
        )
        make.chmod(0o755)

        return {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "PENGYOS_BUILD_ROOT": str(root),
            "workspace": str(workspace),
            "root": str(root),
        }

    def test_success_writes_merged_log(self, env):
        proc = _run_cli(
            "configure", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 0, proc.stderr
        assert "configured demo" in proc.stdout

        root = Path(env["root"])
        log = root / "output" / "demo" / "logs" / "configure.log"
        assert log.is_file()
        text = log.read_text()
        source = root / "buildroot-2026.02.3"
        out = root / "output" / "demo"
        expected_argv = (
            f"-C {source} O={out} "
            f"BR2_EXTERNAL={env['workspace']}/ext "
            "BR2_BOARD=pengyos/qemu-x86_64 demo_defconfig"
        )
        assert f"make got: {expected_argv}" in text
        # stderr was merged into the same log.
        assert "make stderr line" in text

    def test_make_failure_is_exit_1_and_names_log(self, env):
        bin_dir = env["PATH"].split(":")[0]
        make = Path(bin_dir) / "make"
        make.write_text(make.read_text().replace("exit 0", "exit 3"))
        proc = _run_cli(
            "configure", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "make exited 3" in proc.stderr
        assert "logs/configure.log" in proc.stderr

    def test_missing_source_tree_suggests_acquire(self, env):
        (Path(env["root"]) / "buildroot-2026.02.3").rmdir()
        proc = _run_cli(
            "configure", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "pengyos-build acquire demo" in proc.stderr

    def test_unknown_target_is_exit_1(self, env):
        proc = _run_cli(
            "configure", "nope", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "unknown target 'nope'" in proc.stderr

    def test_bad_targets_file_is_exit_1(self, tmp_path: Path):
        proc = _run_cli(
            "configure", "demo", "--targets", str(tmp_path / "missing.toml")
        )
        assert proc.returncode == 1
        assert "targets file not found" in proc.stderr

class TestCleanCommand:
    """End-to-end ``clean`` with a fake ``make`` on PATH (no network).

    The fake make echoes its argv, so the captured log proves the exact clean
    argv (``… clean``, defconfig NOT re-passed).  A configured target =
    source tree + an existing per-target output dir.
    """

    TARGETS_TOML = """\
[[targets]]
name = "demo"
br_version = "2026.02.3"
external_tree = "ext"
defconfig = "demo_defconfig"
board = "pengyos/qemu-x86_64"
"""

    @pytest.fixture
    def env(self, tmp_path: Path) -> dict[str, str]:
        workspace = tmp_path / "ws"
        (workspace / "ext").mkdir(parents=True)
        (workspace / "targets.toml").write_text(self.TARGETS_TOML)
        root = tmp_path / "root"
        (root / "buildroot-2026.02.3").mkdir(parents=True)
        (root / "output" / "demo").mkdir(parents=True)  # already configured

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make = bin_dir / "make"
        make.write_text(
            "#!/bin/sh\n"
            'echo "make got: $@"\n'
            "exit 0\n"
        )
        make.chmod(0o755)

        return {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "PENGYOS_BUILD_ROOT": str(root),
            "workspace": str(workspace),
            "root": str(root),
        }

    def test_success_captures_clean_log(self, env):
        proc = _run_cli(
            "clean", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 0, proc.stderr
        assert "cleaned demo" in proc.stdout

        root = Path(env["root"])
        log = root / "output" / "demo" / "logs" / "clean.log"
        assert log.is_file()
        text = log.read_text()
        source = root / "buildroot-2026.02.3"
        out = root / "output" / "demo"
        expected_argv = (
            f"-C {source} O={out} "
            f"BR2_EXTERNAL={env['workspace']}/ext "
            "BR2_BOARD=pengyos/qemu-x86_64 clean"
        )
        assert f"make got: {expected_argv}" in text

    def test_make_failure_is_exit_1_and_names_clean_log(self, env):
        bin_dir = env["PATH"].split(":")[0]
        make = Path(bin_dir) / "make"
        make.write_text(make.read_text().replace("exit 0", "exit 7"))
        proc = _run_cli(
            "clean", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "make exited 7" in proc.stderr
        assert "logs/clean.log" in proc.stderr

    def test_unconfigured_target_is_exit_1(self, env):
        (Path(env["root"]) / "output" / "demo").rmdir()
        proc = _run_cli(
            "clean", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "pengyos-build configure demo" in proc.stderr

    def test_missing_source_tree_suggests_acquire(self, env):
        (Path(env["root"]) / "buildroot-2026.02.3").rmdir()
        proc = _run_cli(
            "clean", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "pengyos-build acquire demo" in proc.stderr

    def test_unknown_target_is_exit_1(self, env):
        proc = _run_cli(
            "clean", "nope", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "unknown target 'nope'" in proc.stderr


class TestBuildCommand:
    """End-to-end ``build`` with a fake ``make`` on PATH (no network).

    The fake make echoes its argv and prints a "Build completed" line, so the
    captured log proves the exact build argv (including ``-jN``) AND gives
    ``status`` something to scan.  A configured target = source tree + an
    existing per-target output dir.
    """

    TARGETS_TOML = """\
[[targets]]
name = "demo"
br_version = "2026.02.3"
external_tree = "ext"
defconfig = "demo_defconfig"
board = "pengyos/qemu-x86_64"
"""

    @pytest.fixture
    def env(self, tmp_path: Path) -> dict[str, str]:
        workspace = tmp_path / "ws"
        (workspace / "ext").mkdir(parents=True)
        (workspace / "targets.toml").write_text(self.TARGETS_TOML)
        root = tmp_path / "root"
        (root / "buildroot-2026.02.3").mkdir(parents=True)
        (root / "output" / "demo").mkdir(parents=True)  # already configured

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        make = bin_dir / "make"
        make.write_text(
            "#!/bin/sh\n"
            'echo "make got: $@"\n'
            "echo 'Build completed successfully'\n"
            "exit 0\n"
        )
        make.chmod(0o755)

        return {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "PENGYOS_BUILD_ROOT": str(root),
            "workspace": str(workspace),
            "root": str(root),
        }

    def test_success_streams_build_log_with_jobs(self, env):
        proc = _run_cli(
            "build", "demo", "--jobs", "4",
            "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 0, proc.stderr
        assert "built demo (4 jobs)" in proc.stdout

        root = Path(env["root"])
        log = root / "output" / "demo" / "logs" / "build.log"
        assert log.is_file()
        text = log.read_text()
        source = root / "buildroot-2026.02.3"
        out = root / "output" / "demo"
        expected_argv = (
            f"-C {source} O={out} "
            f"BR2_EXTERNAL={env['workspace']}/ext "
            "BR2_BOARD=pengyos/qemu-x86_64 -j4"
        )
        assert f"make got: {expected_argv}" in text
        # The stage line was streamed to the log as well.
        assert "Build completed successfully" in text

    def test_default_jobs_is_nproc(self, env):
        proc = _run_cli(
            "build", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 0, proc.stderr
        text = (Path(env["root"]) / "output" / "demo" / "logs" / "build.log").read_text()
        # The default is -j$(nproc): a positive -jN, N == the host core count.
        import re, os
        m = re.search(r"-j(\d+)\b", text)
        assert m is not None
        assert int(m.group(1)) == (os.cpu_count() or 1)

    def test_make_failure_is_exit_1_and_names_build_log(self, env):
        bin_dir = env["PATH"].split(":")[0]
        make = Path(bin_dir) / "make"
        make.write_text(make.read_text().replace("exit 0", "exit 5"))
        proc = _run_cli(
            "build", "demo", "--jobs", "2",
            "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "make exited 5" in proc.stderr
        assert "logs/build.log" in proc.stderr

    def test_unconfigured_target_is_exit_1(self, env):
        (Path(env["root"]) / "output" / "demo").rmdir()
        proc = _run_cli(
            "build", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "pengyos-build configure demo" in proc.stderr

    def test_missing_source_tree_suggests_acquire(self, env):
        (Path(env["root"]) / "buildroot-2026.02.3").rmdir()
        proc = _run_cli(
            "build", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "pengyos-build acquire demo" in proc.stderr

    def test_unknown_target_is_exit_1(self, env):
        proc = _run_cli(
            "build", "nope", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "unknown target 'nope'" in proc.stderr


class TestStatusCommand:
    """End-to-end ``status`` — scan the build log and report the stage."""

    TARGETS_TOML = """\
[[targets]]
name = "demo"
br_version = "2026.02.3"
external_tree = "ext"
defconfig = "demo_defconfig"
"""

    @pytest.fixture
    def env(self, tmp_path: Path) -> dict[str, str]:
        workspace = tmp_path / "ws"
        (workspace / "ext").mkdir(parents=True)
        (workspace / "targets.toml").write_text(self.TARGETS_TOML)
        root = tmp_path / "root"
        (root / "output" / "demo").mkdir(parents=True)
        return {
            "PATH": "/usr/bin:/bin",
            "PENGYOS_BUILD_ROOT": str(root),
            "workspace": str(workspace),
            "root": str(root),
        }

    def test_status_after_build_reports_done(self, env):
        root = Path(env["root"])
        log = root / "output" / "demo" / "logs" / "build.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            ">>> host-zlib 1.3 Building\nBuild completed successfully\n",
            encoding="utf-8",
        )
        proc = _run_cli(
            "status", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "demo: done"

    def test_status_without_build_log_is_not_started(self, env):
        proc = _run_cli(
            "status", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "demo: not_started"

    def test_status_reports_error_stage(self, env):
        root = Path(env["root"])
        log = root / "output" / "demo" / "logs" / "build.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("make[2]: *** [host-foo] Error 1\n", encoding="utf-8")
        proc = _run_cli(
            "status", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "demo: error"

    def test_status_unknown_target_is_exit_1(self, env):
        proc = _run_cli(
            "status", "nope", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "unknown target 'nope'" in proc.stderr


class TestManifestCommand:
    """End-to-end ``manifest`` — writes deterministic JSON from the outputs.

    No make involved: the manifest is a pure read of the external tree
    (defconfig hash) and the per-target output dir (image hashes + window).
    The external tree is a plain (non-git) dir, so ``git describe`` is absent.
    """

    TARGETS_TOML = """\
[[targets]]
name = "demo"
br_version = "2026.02.3"
external_tree = "ext"
defconfig = "demo_defconfig"
"""

    @pytest.fixture
    def env(self, tmp_path: Path) -> dict[str, str]:
        workspace = tmp_path / "ws"
        (workspace / "ext" / "configs").mkdir(parents=True)
        (workspace / "ext" / "configs" / "demo_defconfig").write_text(
            "BR2_TOOLCHAIN_USES_GLIBC=y\n"
        )
        (workspace / "targets.toml").write_text(self.TARGETS_TOML)
        root = tmp_path / "root"
        out = root / "output" / "demo"
        (out / "images").mkdir(parents=True)
        (out / "images" / "rootfs.ext4").write_bytes(b"ROOTFSBYTES")
        return {
            "PATH": "/usr/bin:/bin",
            "PENGYOS_BUILD_ROOT": str(root),
            "workspace": str(workspace),
            "root": str(root),
        }

    def test_manifest_written_to_default_path(self, env):
        import json

        proc = _run_cli(
            "manifest", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 0, proc.stderr
        assert "manifest for demo" in proc.stdout

        path = Path(env["root"]) / "output" / "demo" / "build-manifest.json"
        assert path.is_file()
        data = json.loads(path.read_text())
        assert data["target"] == "demo"
        assert data["inputs"]["br_version"] == "2026.02.3"
        assert data["inputs"]["defconfig"] == "demo_defconfig"
        assert "defconfig_sha256" in data["inputs"]
        # Non-git external tree: the describe field is JSON null.
        assert data["inputs"]["external_tree_describe"] is None
        assert data["build"]["images"] == {
            "rootfs.ext4": data["build"]["images"]["rootfs.ext4"]
        }

    def test_manifest_custom_json_out(self, env, tmp_path: Path):
        import json

        dest = tmp_path / "custom" / "m.json"
        proc = _run_cli(
            "manifest", "demo", "--json", str(dest),
            "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 0, proc.stderr
        assert dest.is_file()
        json.loads(dest.read_text())

    def test_manifest_hash_matches_image(self, env):
        import hashlib, json

        proc = _run_cli(
            "manifest", "demo", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 0, proc.stderr
        path = Path(env["root"]) / "output" / "demo" / "build-manifest.json"
        data = json.loads(path.read_text())
        expected = hashlib.sha256(b"ROOTFSBYTES").hexdigest()
        assert data["build"]["images"]["rootfs.ext4"] == expected

    def test_manifest_missing_external_tree_is_exit_1(self, env):
        # Point the target's external tree somewhere that doesn't exist.
        workspace = Path(env["workspace"])
        toml = (
            '[[targets]]\n'
            'name = "demo"\n'
            'br_version = "2026.02.3"\n'
            'external_tree = "missing-ext"\n'
            'defconfig = "demo_defconfig"\n'
        )
        (workspace / "bad.toml").write_text(toml)
        proc = _run_cli(
            "manifest", "demo", "--targets", str(workspace / "bad.toml"), **env
        )
        assert proc.returncode == 1
        assert "external tree not found" in proc.stderr

    def test_manifest_unknown_target_is_exit_1(self, env):
        proc = _run_cli(
            "manifest", "nope", "--targets", f"{env['workspace']}/targets.toml", **env
        )
        assert proc.returncode == 1
        assert "unknown target 'nope'" in proc.stderr


class TestDryRunCommand:
    """End-to-end ``--dry-run``: print the EXACT make invocation on stdout
    and run nothing.  The fake ``make`` on PATH records a sentinel file if
    invoked — after a dry run the sentinel must not exist.  For
    ``build``/``clean`` the target is pre-configured (output dir present);
    for ``configure`` it is not (a dry run must not create it)."""

    TARGETS_TOML = """\
[[targets]]
name = "demo"
br_version = "2026.02.3"
external_tree = "ext"
defconfig = "demo_defconfig"
board = "pengyos/qemu-x86_64"
"""

    @pytest.fixture
    def env(self, tmp_path: Path) -> dict[str, str]:
        workspace = tmp_path / "ws"
        (workspace / "ext").mkdir(parents=True)
        (workspace / "targets.toml").write_text(self.TARGETS_TOML)
        root = tmp_path / "root"
        (root / "buildroot-2026.02.3").mkdir(parents=True)
        (root / "output" / "demo").mkdir(parents=True)  # configured (build/clean)

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sentinel = tmp_path / "make-ran"
        make = bin_dir / "make"
        make.write_text(f"#!/bin/sh\ntouch {sentinel}\nexit 0\n")
        make.chmod(0o755)

        return {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "PENGYOS_BUILD_ROOT": str(root),
            "workspace": str(workspace),
            "root": str(root),
            "sentinel": str(sentinel),
        }

    def _targets(self, env: dict[str, str]) -> list[str]:
        return ["--targets", f"{env['workspace']}/targets.toml"]

    @staticmethod
    def _expected(env: dict[str, str], *tail: str) -> str:
        root = Path(env["root"])
        argv = [
            "make",
            "-C",
            str(root / "buildroot-2026.02.3"),
            f"O={root / 'output' / 'demo'}",
            f"BR2_EXTERNAL={env['workspace']}/ext",
            "BR2_BOARD=pengyos/qemu-x86_64",
        ] + list(tail)
        return " ".join(shlex.quote(a) for a in argv)

    def test_configure_dry_run_prints_exact_argv_and_runs_nothing(self, env):
        (Path(env["root"]) / "output" / "demo").rmdir()  # not configured yet
        (Path(env["root"]) / "output").rmdir()
        proc = _run_cli(
            "configure", "demo", "--dry-run", *self._targets(env), **env
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == self._expected(
            env, "demo_defconfig"
        )
        assert not Path(env["sentinel"]).is_file()  # make never ran
        assert not (Path(env["root"]) / "output").exists()  # nothing created
        assert proc.stderr == ""

    def test_build_dry_run_prints_resolved_jobs(self, env):
        proc = _run_cli(
            "build", "demo", "--dry-run", "--jobs", "4", *self._targets(env), **env
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == self._expected(env, "-j4")
        assert not Path(env["sentinel"]).is_file()
        # No log written, no host shim applied.
        out = Path(env["root"]) / "output" / "demo"
        assert not (out / "logs" / "build.log").exists()
        assert not (out / ".hostbin").exists()

    def test_build_dry_run_default_jobs_is_nproc(self, env):
        import os

        proc = _run_cli(
            "build", "demo", "--dry-run", *self._targets(env), **env
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == self._expected(
            env, f"-j{os.cpu_count() or 1}"
        )

    def test_clean_dry_run_prints_exact_argv(self, env):
        proc = _run_cli(
            "clean", "demo", "--dry-run", *self._targets(env), **env
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == self._expected(env, "clean")
        assert not Path(env["sentinel"]).is_file()
        out = Path(env["root"]) / "output" / "demo"
        assert not (out / "logs" / "clean.log").exists()
        assert not (out / ".hostbin").exists()

    def test_dry_run_still_checks_preconditions(self, env):
        (Path(env["root"]) / "buildroot-2026.02.3").rmdir()
        proc = _run_cli(
            "build", "demo", "--dry-run", *self._targets(env), **env
        )
        assert proc.returncode == 1
        assert "pengyos-build acquire demo" in proc.stderr
        assert not Path(env["sentinel"]).is_file()
