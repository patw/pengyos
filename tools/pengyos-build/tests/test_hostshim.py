"""Tests for the host-tooling hygiene step (pengyos_build.hostshim).

Pure-Python, hermetic: a fixture directory tree simulates a host that ships
uutils/coreutils as the default ``/usr/bin/<tool>`` with the GNU versions
alongside as ``/usr/bin/gnu<tool>``, and an injectable resolver stands in
for the shell's ``command -v`` + ``readlink -f``.  One test additionally
drives the *real* resolver against a real symlink tree on ``PATH``.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from pengyos_build import hostshim
from pengyos_build.hostshim import (
    ShimPlan,
    apply_host_shims,
    build_host_shims,
    default_resolver,
    discover_gnu_binaries,
    is_uutils,
    plan_host_shims,
    run_build_with_host_shim,
    shim_path,
)


def _make_executable(path: Path) -> Path:
    """Create an empty, executable regular file (a stand-in for a binary)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def host_tree(tmp_path: Path) -> dict[str, Path]:
    """A fixture 'host' mixing GNU and uutils coreutils.

    Layout (all under ``tmp_path``)::

        bin/gnuinstall      GNU install binary
        bin/gnuwc           GNU wc binary
        bin/gnuenv          GNU env binary (env is *not* a uutils shim target)
        bin/env             a genuine GNU env (not under coreutils)
        uutils/coreutils/install   uutils install (the *default* `install`)
        uutils/coreutils/wc        uutils wc (the *default* `wc`)
    """
    root = tmp_path / "host"
    gnu_install = _make_executable(root / "bin" / "gnuinstall")
    gnu_wc = _make_executable(root / "bin" / "gnuwc")
    gnu_env = _make_executable(root / "bin" / "gnuenv")
    # A genuine GNU `env` that is NOT under a coreutils dir (should not shim).
    env_bin = _make_executable(root / "bin" / "env")

    # uutils coreutils tree: these are what `install`/`wc` resolve to.
    uu_install = _make_executable(root / "uutils" / "coreutils" / "install")
    uu_wc = _make_executable(root / "uutils" / "coreutils" / "wc")

    return {
        "bin": root / "bin",
        "gnu_install": gnu_install,
        "gnu_wc": gnu_wc,
        "gnu_env": gnu_env,
        "env": env_bin,
        "uu_install": uu_install,
        "uu_wc": uu_wc,
    }


def _resolver_from(mapping: dict[str, str]) -> hostshim.Callable:
    """A resolver backed by an explicit name -> resolved-path map."""
    return lambda name: mapping.get(name)


class TestIsUutils:
    @pytest.mark.parametrize(
        "resolved",
        [
            "/usr/lib/coreutils/install",
            "/opt/uutils/coreutils/wc",
            "/usr/local/bin/coreutils/tr",
        ],
    )
    def test_matches_coreutils_dir(self, resolved):
        assert is_uutils(resolved) is True

    @pytest.mark.parametrize(
        "resolved",
        [
            "/usr/bin/install",            # stock GNU, no coreutils dir
            "/usr/lib/coreutils",          # coreutils is the *leaf*, not a dir
            "/home/u/coreutils-notes",     # substring, not a path component
            "/usr/bin/gnuinstall",
        ],
    )
    def test_no_match(self, resolved):
        assert is_uutils(resolved) is False


class TestDiscoverGnuBinaries:
    def test_finds_only_executable_gnu_prefixed(self, host_tree):
        binn = host_tree["bin"]
        # A `gnu*` file that is NOT executable must be excluded.
        _make_executable(binn / "gnuhead")
        binn.joinpath("gnuhead").chmod(0o644)  # strip exec bit
        # A non-gnu executable must be excluded.
        found = discover_gnu_binaries(binn)
        names = [p.name for p in found]
        assert "gnuinstall" in names
        assert "gnuwc" in names
        assert "gnuenv" in names
        assert "gnuhead" not in names
        assert "env" not in names
        assert names == sorted(names)

    def test_missing_dir_yields_empty(self, tmp_path):
        assert discover_gnu_binaries(tmp_path / "nope") == ()

    def test_plain_file_dir_yields_empty(self, tmp_path):
        f = tmp_path / "gnuinstall"
        _make_executable(f)
        assert discover_gnu_binaries(f) == ()


class TestPlanHostShims:
    def test_picks_gnu_over_uutils(self, host_tree):
        resolver = _resolver_from(
            {
                "install": str(host_tree["uu_install"]),
                "wc": str(host_tree["uu_wc"]),
                "env": str(host_tree["env"]),  # genuine GNU, not coreutils
            }
        )
        plan = plan_host_shims(
            [host_tree["gnu_install"], host_tree["gnu_wc"], host_tree["gnu_env"]],
            resolver=resolver,
        )
        assert plan.tools == ("install", "wc")
        assert plan.shims == (
            ("install", host_tree["gnu_install"]),
            ("wc", host_tree["gnu_wc"]),
        )

    def test_missing_resolver_target_is_skipped(self, host_tree):
        # `install` not found at all -> no shim for it.
        resolver = _resolver_from({"install": None, "wc": str(host_tree["uu_wc"])})
        plan = plan_host_shims(
            [host_tree["gnu_install"], host_tree["gnu_wc"]], resolver=resolver
        )
        assert plan.tools == ("wc",)

    def test_non_gnu_prefixed_and_bare_gnu_ignored(self, host_tree):
        resolver = _resolver_from({"anything": str(host_tree["uu_install"])})
        bare_gnu = _make_executable(host_tree["bin"] / "gnu")
        # A path not starting with `gnu` and a bare `gnu` are both ignored.
        plan = plan_host_shims(
            [host_tree["env"], bare_gnu],
            resolver=resolver,
        )
        assert plan.shims == ()

    def test_empty_input(self):
        assert plan_host_shims([]) == ShimPlan()
        assert bool(ShimPlan()) is False

    def test_no_uutils_host_produces_empty_plan(self, host_tree):
        # Every tool resolves to a genuine GNU binary -> nothing to shim.
        resolver = _resolver_from(
            {
                "install": str(host_tree["gnu_install"]),
                "wc": str(host_tree["gnu_wc"]),
            }
        )
        plan = plan_host_shims(
            [host_tree["gnu_install"], host_tree["gnu_wc"]], resolver=resolver
        )
        assert plan == ShimPlan()


class TestApplyHostShims:
    def test_creates_symlinks_to_gnu(self, host_tree, tmp_path):
        plan = ShimPlan(
            shims=(
                ("install", host_tree["gnu_install"]),
                ("wc", host_tree["gnu_wc"]),
            )
        )
        shim_dir = tmp_path / "hostbin"
        returned_dir, created = apply_host_shims(plan, shim_dir)
        assert returned_dir == shim_dir
        assert len(created) == 2

        install_link = shim_dir / "install"
        assert install_link.is_symlink()
        assert os.path.realpath(str(install_link)) == os.path.realpath(
            str(host_tree["gnu_install"])
        )
        assert (shim_dir / "wc").is_symlink()

    def test_idempotent_noop(self, host_tree, tmp_path):
        plan = ShimPlan(shims=(("install", host_tree["gnu_install"]),))
        shim_dir = tmp_path / "hostbin"
        _, created_first = apply_host_shims(plan, shim_dir)
        assert len(created_first) == 1
        # Second application with the same target creates nothing.
        _, created_second = apply_host_shims(plan, shim_dir)
        assert created_second == ()
        assert (shim_dir / "install").is_symlink()

    def test_stale_symlink_is_repointed(self, host_tree, tmp_path):
        shim_dir = tmp_path / "hostbin"
        shim_dir.mkdir()
        # Point `install` at the wrong target first.
        wrong = _make_executable(tmp_path / "other" / "install")
        (shim_dir / "install").symlink_to(wrong)
        plan = ShimPlan(shims=(("install", host_tree["gnu_install"]),))
        _, created = apply_host_shims(plan, shim_dir)
        assert len(created) == 1
        assert os.path.realpath(str(shim_dir / "install")) == os.path.realpath(
            str(host_tree["gnu_install"])
        )

    def test_real_file_is_replaced(self, host_tree, tmp_path):
        shim_dir = tmp_path / "hostbin"
        shim_dir.mkdir()
        _make_executable(shim_dir / "install")  # a real file blocks the link
        plan = ShimPlan(shims=(("install", host_tree["gnu_install"]),))
        _, created = apply_host_shims(plan, shim_dir)
        assert len(created) == 1
        assert (shim_dir / "install").is_symlink()

    def test_directory_collision_raises(self, host_tree, tmp_path):
        shim_dir = tmp_path / "hostbin"
        (shim_dir / "install").mkdir(parents=True)
        plan = ShimPlan(shims=(("install", host_tree["gnu_install"]),))
        with pytest.raises(FileExistsError):
            apply_host_shims(plan, shim_dir)


class TestBuildHostShims:
    def test_full_pipeline_with_fixture(self, host_tree, tmp_path):
        resolver = _resolver_from(
            {
                "install": str(host_tree["uu_install"]),
                "wc": str(host_tree["uu_wc"]),
                "env": str(host_tree["env"]),
            }
        )
        shim_dir = tmp_path / "hostbin"
        returned_dir, plan, created = build_host_shims(
            shim_dir, gnu_dir=host_tree["bin"], resolver=resolver
        )
        assert returned_dir == shim_dir
        assert plan.tools == ("install", "wc")
        assert len(created) == 2
        for tool in ("install", "wc"):
            assert (shim_dir / tool).is_symlink()

    def test_noop_when_nothing_is_uutils(self, host_tree, tmp_path):
        resolver = _resolver_from(
            {
                "install": str(host_tree["gnu_install"]),
                "wc": str(host_tree["gnu_wc"]),
            }
        )
        shim_dir = tmp_path / "hostbin"
        returned_dir, plan, created = build_host_shims(
            shim_dir, gnu_dir=host_tree["bin"], resolver=resolver
        )
        assert plan == ShimPlan()
        assert created == ()
        # The shim dir is never created when there is nothing to shim.
        assert not returned_dir.exists()


class TestShimPath:
    def test_prepends(self):
        out = shim_path("/b/hostbin", "/usr/bin:/bin")
        assert out == f"/b/hostbin{os.pathsep}/usr/bin:/bin"

    def test_empty_existing(self):
        assert shim_path("/b/hostbin", "") == "/b/hostbin"


class TestDefaultResolverReal:
    """Drive the *real* resolver (which + realpath) against a real symlink tree."""

    def test_resolves_symlink_into_coreutils(self, tmp_path, monkeypatch):
        root = tmp_path / "fakehost"
        # uutils coreutils install target.
        uu = _make_executable(root / "uutils" / "coreutils" / "install")
        # Default `install` is a symlink into the uutils tree.
        bin_dir = root / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "install").symlink_to(uu)
        # A GNU `gnuinstall` alongside.
        _make_executable(bin_dir / "gnuinstall")

        monkeypatch.setenv("PATH", str(bin_dir))
        resolved = default_resolver("install")
        assert resolved is not None
        assert is_uutils(resolved) is True
        assert resolved == os.path.realpath(str(uu))

    def test_not_found_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        assert default_resolver("definitely-not-a-real-tool-xyz") is None


class TestRunBuildWithHostShim:
    def test_prepends_path_only_when_shims_created(self, host_tree, tmp_path):
        resolver = _resolver_from({"install": str(host_tree["uu_install"])})
        shim_dir = tmp_path / "hostbin"
        seen = {}

        def fake_runner(argv, env):
            seen["argv"] = argv
            seen["env"] = env
            return None

        base_env = {"PATH": "/usr/bin:/bin"}
        result = run_build_with_host_shim(
            ["make", "-j8"],
            shim_dir,
            gnu_dir=host_tree["bin"],
            resolver=resolver,
            env=base_env,
            runner=fake_runner,
        )
        assert result is None
        assert seen["argv"] == ["make", "-j8"]
        assert seen["env"]["PATH"].startswith(f"{shim_dir}{os.pathsep}")
        assert "/usr/bin:/bin" in seen["env"]["PATH"]
        # Caller's dict is not mutated.
        assert base_env["PATH"] == "/usr/bin:/bin"

    def test_no_shims_leaves_path_untouched(self, host_tree, tmp_path):
        resolver = _resolver_from({"install": str(host_tree["gnu_install"])})
        shim_dir = tmp_path / "hostbin"
        seen = {}

        def fake_runner(argv, env):
            seen["env"] = env
            return None

        base_env = {"PATH": "/usr/bin:/bin"}
        run_build_with_host_shim(
            ["make"],
            shim_dir,
            gnu_dir=host_tree["bin"],
            resolver=resolver,
            env=base_env,
            runner=fake_runner,
        )
        assert seen["env"]["PATH"] == "/usr/bin:/bin"
