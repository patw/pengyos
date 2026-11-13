"""scripts/check-applied-patches.py: release.sh refuses output trees patched from a
worktree (26.11.15's legal-info failure)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_applied_patches", REPO / "scripts/check-applied-patches.py")
cap = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cap
spec.loader.exec_module(cap)


def _tree(tmp_path: Path, entries: dict[str, list[Path]]) -> Path:
    out = tmp_path / "output" / "pengyos-desktop"
    for build_dir, patches in entries.items():
        d = out / "build" / build_dir
        d.mkdir(parents=True)
        (d / ".applied_patches_list").write_text("".join(f"{p}\n" for p in patches))
    return out


def _patch(root: Path, rel: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("--- a\n+++ b\n")
    return p


def test_patches_from_the_repo_and_buildroot_pass(tmp_path):
    repo = tmp_path / "PengyOS"
    br = tmp_path / "buildroot"
    out = _tree(tmp_path, {
        "host-go-src-1.27.2": [_patch(repo, "buildroot-external/patches/go-src/1.27.2/0001-x.patch")],
        "busybox-1.37.0": [_patch(br, "package/busybox/0001-y.patch")],
        "zlib-1.3": [],
    })
    assert cap.problems(out, repo) == []
    assert cap.main(["--repo", str(repo), str(out)]) == 0


def test_a_removed_worktree_is_named_with_the_build_dir(tmp_path, capsys):
    repo = tmp_path / "PengyOS"
    gone = tmp_path / "secfix" / "buildroot-external/patches/go-src/1.27.2/0001-x.patch"
    out = _tree(tmp_path, {"host-go-src-1.27.2": [gone]})
    found = cap.problems(out, repo)
    assert [(d, why) for d, _, why in found] == [("host-go-src-1.27.2", "not from this repo")]
    assert cap.main(["--repo", str(repo), str(out)]) == 1
    printed = capsys.readouterr().out
    assert "build/host-go-src-1.27.2" in printed and "dirclean" in printed


def test_a_worktree_that_still_exists_is_refused_too(tmp_path):
    """Otherwise the release silently ships the branch's patches."""
    repo = tmp_path / "PengyOS"
    _patch(repo, "buildroot-external/patches/podman/0001-x.patch")
    branch = _patch(tmp_path / "tmp/branch", "buildroot-external/patches/podman/0001-x.patch")
    out = _tree(tmp_path, {"podman-v5.7.0": [branch]})
    assert [why for _, _, why in cap.problems(out, repo)] == ["not from this repo"]


def test_a_deleted_buildroot_patch_is_caught(tmp_path):
    repo = tmp_path / "PengyOS"
    out = _tree(tmp_path, {"busybox-1.37.0": [tmp_path / "buildroot/package/busybox/0009-gone.patch"]})
    assert [why for _, _, why in cap.problems(out, repo)] == ["no longer exists"]


def test_a_tree_not_built_yet_is_skipped(tmp_path):
    assert cap.main(["--repo", str(tmp_path), str(tmp_path / "output/pengyos-core")]) == 0


def test_release_sh_runs_it_before_building():
    text = (REPO / "scripts/release.sh").read_text()
    check = text.index("check-applied-patches.py")
    assert check < text.index('say "${t}: acquire + configure + build"')
