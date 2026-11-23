"""Tests for pengyos_build.prune: only old versions of listed packages go."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pengyos_build.prune import PruneError, current_dirs, find_old, prune_target
from pengyos_build.targets import Target

T0 = 1_700_000_000  # dirs are built at T0, show-info is written at T0 + 100


def _target() -> Target:
    return Target(name="demo", br_version="2026.08.2", external_tree=Path("/external"),
                  defconfig="demo_defconfig", board="")


def _dir(out: Path, name: str, when: float = T0) -> Path:
    d = out / "build" / name
    d.mkdir(parents=True)
    (d / "payload").write_bytes(b"x" * 8192)
    s = d / ".stamp_built"
    s.touch()
    os.utime(s, (when, when))
    os.utime(d, (when, when))
    return d


def _show_info(out: Path, packages: dict[str, str], when: float = T0 + 100) -> Path:
    f = out / "images" / "demo.show-info.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({n: {"type": "target", "build_dir": bd}
                             for n, bd in packages.items()}))
    os.utime(f, (when, when))
    return f


@pytest.fixture
def out(tmp_path: Path) -> Path:
    o = tmp_path / "output" / "demo"
    (o / "build").mkdir(parents=True)
    return o


def test_an_old_version_is_found_and_the_current_one_kept(out):
    _dir(out, "pengy-1.15.5")
    _dir(out, "pengy-1.16.1")
    si = _show_info(out, {"pengy": "build/pengy-1.16.1/"})
    old = find_old(out, si)
    assert [o.path.name for o in old] == ["pengy-1.15.5"]
    assert old[0].current == "pengy-1.16.1" and old[0].size > 0


def test_the_package_dir_is_the_first_part_of_a_nested_build_dir(out):
    # glibc, gcc and llvm build in a subdir: build/<pkg>-<ver>/build/
    _dir(out, "host-llvm-22.1.8")
    si = _show_info(out, {"host-llvm": "build/host-llvm-22.1.8/buildroot-build/"})
    assert current_dirs(json.loads(si.read_text())) == {"host-llvm": "host-llvm-22.1.8"}
    assert find_old(out, si) == []


def test_a_dir_newer_than_show_info_is_kept(out):
    # The version bump is in .mk, not .config: yesterday's show-info calls the
    # new version old. The new dir was made after show-info, so it stays.
    _dir(out, "pengy-1.15.5")
    _dir(out, "pengy-1.16.1", when=T0 + 500)
    si = _show_info(out, {"pengy": "build/pengy-1.15.5/"})
    assert find_old(out, si) == []


def test_nothing_goes_when_the_current_dir_is_missing(out):
    _dir(out, "uv-0.13.8")
    si = _show_info(out, {"uv": "build/uv-0.13.9/"})
    assert find_old(out, si) == []


def test_a_longer_package_name_is_not_an_old_version(out):
    _dir(out, "util-linux-2.41.5")
    _dir(out, "util-linux-libs-2.41.5")
    _dir(out, "host-gcc-final-15.3.0")
    si = _show_info(out, {"util-linux": "build/util-linux-2.41.5/",
                          "host-gcc": "build/host-gcc-16.1.0/"})
    _dir(out, "host-gcc-16.1.0")
    assert find_old(out, si) == []


def test_unlisted_packages_symlinks_and_unstamped_dirs_are_left(out):
    _dir(out, "glibc-2.44")  # toolchain: show-info does not list it
    (out / "build" / "buildroot-config").mkdir()  # no stamps
    _dir(out, "foo-1.0")
    (out / "build" / "foo-0.9").symlink_to(out / "build" / "foo-1.0")
    si = _show_info(out, {"foo": "build/foo-1.0/"})
    assert find_old(out, si) == []


def test_v_prefixed_versions_count(out):
    _dir(out, "aardvark-dns-v1.16.0")
    _dir(out, "aardvark-dns-v1.17.1")
    si = _show_info(out, {"aardvark-dns": "build/aardvark-dns-v1.17.1/"})
    assert [o.path.name for o in find_old(out, si)] == ["aardvark-dns-v1.16.0"]


def test_missing_or_empty_show_info_refuses(out):
    with pytest.raises(PruneError, match="build the target first"):
        find_old(out, out / "images" / "demo.show-info.json")
    si = _show_info(out, {})
    with pytest.raises(PruneError, match="names no build dirs"):
        find_old(out, si)


def test_dry_run_keeps_and_apply_removes(out):
    _dir(out, "pengy-1.15.5")
    _dir(out, "pengy-1.16.1")
    _show_info(out, {"pengy": "build/pengy-1.16.1/"})
    root = out.parent.parent
    r = prune_target(_target(), root)
    assert not r.applied and (out / "build" / "pengy-1.15.5").is_dir()
    assert "would remove 1" in str(r)
    r = prune_target(_target(), root, apply=True)
    assert r.applied and not (out / "build" / "pengy-1.15.5").exists()
    assert (out / "build" / "pengy-1.16.1").is_dir()
    assert "removed 1" in str(r)
