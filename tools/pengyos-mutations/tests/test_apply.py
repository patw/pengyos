"""Tests for the ``apply`` command: snapshot -> run -> record -> capture diff."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

from pengyos_mutations import attest
from pengyos_mutations.apply import (
    ApplyError,
    ApplyResult,
    DiffEntry,
    apply_change,
    change_id_now,
    iter_tree,
    walk_diff,
)


def _py(code: str) -> "list[str]":
    """A portable argv that runs ``code`` under the current interpreter."""
    return [sys.executable, "-c", code]


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def test_change_id_now_format() -> None:
    cid = change_id_now()
    # <UTC ts>-<8 hex chars>, where ts is YYYYMMDDTHHMMSSZ.
    assert re.match(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$", cid), cid


def test_iter_tree_lists_files_relative(tmp_path) -> None:
    _write(tmp_path / "a.txt", "a")
    _write(tmp_path / "sub" / "b.txt", "b")
    found = dict(iter_tree(tmp_path))
    assert set(found) == {"a.txt", "sub/b.txt"}
    # POSIX separators regardless of platform.
    assert "sub/b.txt" in found


def test_iter_tree_ignores_dirs_and_broken_symlinks(tmp_path) -> None:
    _write(tmp_path / "a.txt", "a")
    (tmp_path / "adir").mkdir()
    broken = tmp_path / "broken"
    try:
        os.symlink(tmp_path / "does-not-exist", broken)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unsupported")
    found = dict(iter_tree(tmp_path))
    assert set(found) == {"a.txt"}


def test_walk_diff_created_modified_deleted(tmp_path) -> None:
    before = {"keep.txt": "h1", "gone.txt": "h2"}
    after = {"keep.txt": "changed", "new.txt": "h3"}
    diff = walk_diff(tmp_path, before, after)
    by_path = {e.path: e.kind for e in diff}
    assert by_path == {"gone.txt": "deleted", "keep.txt": "modified", "new.txt": "created"}
    # Deterministic ordering by path.
    assert [e.path for e in diff] == ["gone.txt", "keep.txt", "new.txt"]


def test_walk_diff_empty_when_identical() -> None:
    snapshot = {"a.txt": "h"}
    assert walk_diff(Path("."), snapshot, snapshot) == []


# --------------------------------------------------------------------------- #
# apply_change
# --------------------------------------------------------------------------- #
def test_apply_records_entry_and_snapshot(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "a.conf", "v1")
    store = tmp_path / "store"

    result = apply_change(
        desc="edit a.conf", store=str(store),
        command=_py("open('a.conf','w').write('v2')"), root=str(root),
    )

    assert isinstance(result, ApplyResult)
    assert result.exit_code == 0
    assert result.backend == "mock"
    assert result.change_id
    assert (root / "a.conf").read_text() == "v2"

    # The pre-command snapshot was captured (still 'v1').
    snap = store / "mock" / "snapshots" / result.change_id / "root" / "a.conf"
    assert snap.read_text() == "v1"

    # An apply event was recorded in the attest log.
    assert attest.chain_length(result.log_path) == 1
    rec = attest.last_record(result.log_path)
    assert rec["type"] == "apply"
    assert rec["data"]["desc"] == "edit a.conf"
    assert rec["data"]["exit_code"] == 0


def test_apply_diff_captures_created_modified_deleted(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "mod.txt", "old")
    _write(root / "del.txt", "bye")
    store = tmp_path / "store"

    cmd = _py(
        "open('mod.txt','w').write('new'); "
        "open('created.txt','w').write('c'); "
        "import os; os.remove('del.txt')"
    )
    result = apply_change(desc="diff", store=str(store), command=cmd, root=str(root))

    kinds = {e.path: e.kind for e in result.diff}
    assert kinds == {
        "mod.txt": "modified",
        "created.txt": "created",
        "del.txt": "deleted",
    }
    # The diff is also stored in the attest record.
    rec = attest.last_record(result.log_path)
    stored_kinds = {d["path"]: d["kind"] for d in rec["data"]["diff"]}
    assert stored_kinds == kinds


def test_apply_records_failing_command_and_keeps_snapshot(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "a.conf", "v1")
    store = tmp_path / "store"

    cmd = _py("raise SystemExit(3)")
    result = apply_change(desc="boom", store=str(store), command=cmd, root=str(root))

    assert result.exit_code == 3
    # Still recorded, and the snapshot exists so a revert can restore it.
    assert attest.chain_length(result.log_path) == 1
    snap_root = store / "mock" / "snapshots" / result.change_id / "root"
    assert snap_root.is_dir()
    # The command's mutation was still applied to the live tree (record != revert).
    assert (root / "a.conf").read_text() == "v1"


def test_apply_empty_command_runs_nothing(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "a.conf", "v1")
    store = tmp_path / "store"
    result = apply_change(desc="noop", store=str(store), command=[], root=str(root))
    assert result.exit_code is None
    assert result.diff == []
    assert (root / "a.conf").read_text() == "v1"


def test_apply_runs_command_in_root_by_default(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    store = tmp_path / "store"
    # The command writes a file using a relative path; it must land in root.
    result = apply_change(
        desc="relative write", store=str(store),
        command=_py("open('rel.txt','w').write('x')"), root=str(root),
    )
    assert (root / "rel.txt").read_text() == "x"
    assert not (tmp_path / "rel.txt").exists()


def test_apply_respects_cwd_override(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    store = tmp_path / "store"
    # Command runs in workdir, but root is tracked; nothing in root changes.
    result = apply_change(
        desc="cwd override", store=str(store),
        command=_py("open('outside.txt','w').write('x')"),
        root=str(root), cwd=str(workdir),
    )
    assert (workdir / "outside.txt").read_text() == "x"
    assert result.diff == []


def test_apply_rejects_store_inside_root(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    store = root / "nested-store"  # inside the tracked root
    with pytest.raises(ApplyError, match="must not be inside"):
        apply_change(desc="bad", store=str(store), command=[], root=str(root))


def test_apply_requires_existing_root(tmp_path) -> None:
    store = tmp_path / "store"
    with pytest.raises(ApplyError, match="not a directory"):
        apply_change(desc="bad", store=str(store), command=[], root=str(tmp_path / "nope"))


def test_apply_log_is_a_valid_chain(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "a.conf", "v1")
    store = tmp_path / "store"
    apply_change(desc="one", store=str(store), command=_py("open('a.conf','w').write('v2')"), root=str(root))
    apply_change(desc="two", store=str(store), command=_py("open('b.conf','w').write('v3')"), root=str(root))
    report = attest.verify_chain(store / "events.jsonl")
    assert report.ok
    assert report.record_count == 2


def test_apply_default_store_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PENGYOS_MUTATIONS_STORE", str(tmp_path / "env-store"))
    from pengyos_mutations.apply import default_store

    assert default_store() == tmp_path / "env-store"


def test_apply_default_root_env(tmp_path, monkeypatch) -> None:
    """When --root is unset, $PENGYOS_MUTATIONS_ROOT is used."""
    root = tmp_path / "env-root"
    root.mkdir()
    (root / "a.conf").write_text("v1", encoding="utf-8")
    monkeypatch.setenv("PENGYOS_MUTATIONS_ROOT", str(root))
    store = tmp_path / "store"
    result = apply_change(desc="env root", store=str(store), command=[])
    assert result.change_id
    # The env root was tracked (its snapshot exists).
    assert (store / "mock" / "snapshots" / result.change_id / "root" / "a.conf").exists()


def test_apply_uses_external_attest_when_importable(tmp_path, monkeypatch) -> None:
    """When pengyos-attest is importable, apply records into its chain."""
    pytest.importorskip("pengyos_attest")
    from pengyos_attest import chain as external_chain

    root = tmp_path / "root"
    _write(root / "a.conf", "v1")
    store = tmp_path / "store"
    result = apply_change(
        desc="external", store=str(store),
        command=_py("open('a.conf','w').write('v2')"), root=str(root),
    )
    # The external verifier (schema-compatible) accepts the applied chain.
    assert external_chain.verify_chain(result.log_path).ok
    assert attest.verify_chain(result.log_path).ok


def test_apply_result_diff_entries_are_typed() -> None:
    assert DiffEntry("a", "created").path == "a"
    assert DiffEntry("a", "created").kind == "created"
