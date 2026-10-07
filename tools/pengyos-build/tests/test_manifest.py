"""Tests for the build manifest (pengyos_build.manifest).

Hermetic: no make, no network.  The external tree is a fixture directory with
a ``configs/<defconfig>`` file; the ``git describe`` comes from an injectable
git runner (a fake) so real git is never required; output images and the build
log are fixture files whose mtimes are set explicitly so the derived
``started_at``/``finished_at`` window is deterministic.  The manifest's
wall-clock ``generated_at`` uses an injectable clock.  CLI-level process tests
live in ``tests/test_cli.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pengyos_build import __version__
from pengyos_build.manifest import (
    SCHEMA_VERSION,
    ManifestError,
    build_manifest,
    build_window,
    collect_images,
    defconfig_path,
    external_tree_describe,
    manifest_default_path,
    utc_iso,
    write_manifest,
)
from pengyos_build.targets import Target

FIXED_NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)


def _target(external: Path, **overrides) -> Target:
    base = dict(
        name="demo",
        br_version="2026.02.3",
        external_tree=external,
        defconfig="demo_defconfig",
        board="",
    )
    base.update(overrides)
    return Target(**base)


def _fake_git(out: str = "v1.2-3-gabc1234", rc: int = 0):
    """A git runner standing in for ``git describe`` (records calls)."""
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        return subprocess.CompletedProcess(list(argv), rc, stdout=out, stderr="")

    return runner, calls


@pytest.fixture
def tree(tmp_path: Path) -> dict[str, Path]:
    """A build root with an external tree (git + configs) and an output dir.

    Output holds a build log and two images (one nested) with fixed mtimes so
    the derived window is deterministic.
    """
    root = tmp_path / "root"
    root.mkdir()
    source = root / "buildroot-2026.02.3"
    source.mkdir()

    external = tmp_path / "ext"
    (external / "configs").mkdir(parents=True)
    (external / ".git").mkdir()  # makes external_tree_describe take the git path
    dcfg = external / "configs" / "demo_defconfig"
    dcfg.write_text("BR2_TOOLCHAIN_USES_GLIBC=y\n", encoding="utf-8")

    out = root / "output" / "demo"
    logs = out / "logs"
    logs.mkdir(parents=True)
    build_log = logs / "build.log"
    build_log.write_text("Build completed successfully\n", encoding="utf-8")
    images = out / "images"
    images.mkdir()
    img1 = images / "rootfs.ext4"
    img1.write_bytes(b"EXT4DATA")
    nested = images / "sub"
    nested.mkdir()
    img2 = nested / "Image.gz"
    img2.write_bytes(b"KERNELDATA")

    # Fix mtimes: log first (earliest), nested image last (latest).
    os.utime(build_log, (1_000_000.0, 1_000_000.0))
    os.utime(img1, (1_000_005.0, 1_000_005.0))
    os.utime(img2, (1_000_010.0, 1_000_010.0))

    return {
        "root": root,
        "external": external,
        "defconfig": dcfg,
        "output": out,
        "build_log": build_log,
        "images": images,
    }


class TestDefconfigPath:
    def test_resolves_in_external_configs(self, tree):
        target = _target(tree["external"])
        assert defconfig_path(target) == tree["external"] / "configs" / "demo_defconfig"


class TestSha256:
    def test_defconfig_hash_matches_file(self, tree):
        import hashlib

        target = _target(tree["external"])
        manifest = build_manifest(target, tree["root"], clock=lambda: FIXED_NOW)
        expected = hashlib.sha256(tree["defconfig"].read_bytes()).hexdigest()
        assert manifest["inputs"]["defconfig_sha256"] == expected


class TestCollectImages:
    def test_maps_every_image_relative_sorted(self, tree):
        import hashlib

        result = collect_images(tree["output"])
        assert set(result) == {"rootfs.ext4", "sub/Image.gz"}
        # Sorted by relative path.
        assert list(result) == sorted(result)
        assert result["rootfs.ext4"] == hashlib.sha256(b"EXT4DATA").hexdigest()
        assert result["sub/Image.gz"] == hashlib.sha256(b"KERNELDATA").hexdigest()

    def test_missing_images_dir_is_empty(self, tree):
        assert collect_images(tree["root"] / "output" / "nope") == {}

    def test_hashes_match_written_files(self, tree):
        import hashlib

        result = collect_images(tree["output"])
        for rel, digest in result.items():
            on_disk = hashlib.sha256((tree["images"] / rel).read_bytes()).hexdigest()
            assert digest == on_disk


class TestExternalTreeDescribe:
    def test_non_git_tree_is_none(self, tmp_path):
        ext = tmp_path / "plain"
        ext.mkdir()
        assert external_tree_describe(ext) is None

    def test_git_tree_uses_injected_runner(self, tree):
        runner, calls = _fake_git("v1.2-3-gabc1234")
        value = external_tree_describe(tree["external"], runner=runner)
        assert value == "v1.2-3-gabc1234"
        assert calls[0] == [
            "git",
            "-C",
            str(tree["external"]),
            "describe",
            "--always",
            "--dirty",
        ]

    def test_git_failure_is_none(self, tree):
        runner, _ = _fake_git("", rc=128)
        assert external_tree_describe(tree["external"], runner=runner) is None


class TestBuildWindow:
    def test_no_artifacts_is_none_none(self, tree):
        empty = tree["root"] / "output" / "empty"
        empty.mkdir(parents=True)
        assert build_window(empty) == (None, None)

    def test_window_is_earliest_to_latest_mtime(self, tree):
        started, finished = build_window(tree["output"])
        assert started == datetime.fromtimestamp(
            1_000_000.0, tz=timezone.utc
        ).isoformat()
        assert finished == datetime.fromtimestamp(
            1_000_010.0, tz=timezone.utc
        ).isoformat()
        assert started <= finished

    def test_log_only_window(self, tmp_path):
        out = tmp_path / "out"
        logs = out / "logs"
        logs.mkdir(parents=True)
        log = logs / "build.log"
        log.write_text("x\n")
        os.utime(log, (500.0, 500.0))
        started, finished = build_window(out)
        assert started == finished  # single artifact: window collapses


class TestUtcIso:
    def test_fixed_clock(self):
        assert utc_iso(lambda: FIXED_NOW) == "2026-09-13T12:00:00+00:00"

    def test_naive_clock_assumed_utc(self):
        assert utc_iso(lambda: datetime(2026, 1, 2, 3, 4, 5)) == (
            "2026-01-02T03:04:05+00:00"
        )


class TestBuildManifest:
    def test_full_manifest_shape(self, tree):
        target = _target(
            tree["external"], board="pengyos/qemu-x86_64"
        )
        m = build_manifest(
            target,
            tree["root"],
            clock=lambda: FIXED_NOW,
            git_runner=_fake_git("v9-gdeadbeef")[0],
        )
        assert m["schema_version"] == SCHEMA_VERSION
        assert m["target"] == "demo"
        assert m["pengyos_build_version"] == __version__
        assert m["generated_at"] == FIXED_NOW.isoformat()

        inputs = m["inputs"]
        assert inputs["br_version"] == "2026.02.3"
        assert inputs["defconfig"] == "demo_defconfig"
        assert "defconfig_sha256" in inputs
        assert inputs["external_tree"] == str(tree["external"])
        assert inputs["external_tree_describe"] == "v9-gdeadbeef"
        assert inputs["board"] == "pengyos/qemu-x86_64"

        build = m["build"]
        assert build["stage"] == "done"
        assert build["images"] == collect_images(tree["output"])
        assert build["started_at"] <= build["finished_at"]

    def test_board_absent_when_empty(self, tree):
        target = _target(tree["external"])
        m = build_manifest(
            target, tree["root"], clock=lambda: FIXED_NOW,
            git_runner=_fake_git()[0],
        )
        assert "board" not in m["inputs"]

    def test_unbuilt_target_has_no_window_or_images(self, tree):
        target = _target(tree["external"])
        empty_root = tree["root"] / "emptyroot"
        (empty_root / "output" / "demo").mkdir(parents=True)
        m = build_manifest(
            target, empty_root, clock=lambda: FIXED_NOW, git_runner=_fake_git()[0]
        )
        assert m["build"]["started_at"] is None
        assert m["build"]["finished_at"] is None
        assert m["build"]["images"] == {}
        assert m["build"]["stage"] == "not_started"

    def test_missing_external_tree_is_error(self, tmp_path):
        target = _target(tmp_path / "nope")
        with pytest.raises(ManifestError) as exc:
            build_manifest(target, tmp_path / "root", clock=lambda: FIXED_NOW)
        assert "external tree not found" in str(exc.value)

    def test_missing_defconfig_is_error(self, tree):
        target = _target(tree["external"], defconfig="does_not_exist")
        with pytest.raises(ManifestError) as exc:
            build_manifest(target, tree["root"], clock=lambda: FIXED_NOW)
        assert "defconfig not found" in str(exc.value)


class TestWriteManifest:
    def test_default_path_is_inside_output_dir(self, tree):
        target = _target(tree["external"])
        path = write_manifest(
            target, tree["root"], clock=lambda: FIXED_NOW, git_runner=_fake_git()[0]
        )
        assert path == manifest_default_path(tree["output"])
        assert path == tree["output"] / "build-manifest.json"
        assert path.is_file()

    def test_custom_out_path_created_with_parents(self, tree, tmp_path):
        target = _target(tree["external"])
        dest = tmp_path / "deep" / "nest" / "m.json"
        path = write_manifest(
            target,
            tree["root"],
            out_path=dest,
            clock=lambda: FIXED_NOW,
            git_runner=_fake_git()[0],
        )
        assert path == dest
        assert dest.is_file()

    def test_written_json_round_trips(self, tree):
        target = _target(tree["external"])
        path = write_manifest(
            target, tree["root"], clock=lambda: FIXED_NOW, git_runner=_fake_git()[0]
        )
        data = json.loads(path.read_text())
        assert data["target"] == "demo"
        assert data["inputs"]["defconfig_sha256"]

    def test_deterministic_field_ordering(self, tree):
        target = _target(tree["external"], board="b/q")
        a = write_manifest(
            target, tree["root"], clock=lambda: FIXED_NOW, git_runner=_fake_git("x")[0]
        )
        b = write_manifest(
            target,
            tree["root"],
            out_path=a.parent / "again.json",
            clock=lambda: FIXED_NOW,
            git_runner=_fake_git("x")[0],
        )
        # Identical artifacts + clock -> byte-identical files.
        assert a.read_bytes() == b.read_bytes()
        # Top-level keys are sorted (deterministic ordering).
        data = json.loads(a.read_text())
        assert list(data) == sorted(data)
        assert list(data["inputs"]) == sorted(data["inputs"])
        assert list(data["build"]) == sorted(data["build"])

    def test_manifest_changes_when_image_bytes_change(self, tree):
        target = _target(tree["external"])
        first = write_manifest(
            target, tree["root"], clock=lambda: FIXED_NOW, git_runner=_fake_git()[0]
        )
        first_images = json.loads(first.read_text())["build"]["images"]

        # Rebuild an image with different bytes; keep its mtime fixed.
        img = tree["images"] / "rootfs.ext4"
        img.write_bytes(b"EXT4DATA-CHANGED")
        os.utime(img, (1_000_005.0, 1_000_005.0))
        second = write_manifest(
            target,
            tree["root"],
            out_path=first.parent / "second.json",
            clock=lambda: FIXED_NOW,
            git_runner=_fake_git()[0],
        )
        second_images = json.loads(second.read_text())["build"]["images"]
        assert second_images["rootfs.ext4"] != first_images["rootfs.ext4"]
        # The unchanged image is byte-stable across runs.
        assert second_images["sub/Image.gz"] == first_images["sub/Image.gz"]
