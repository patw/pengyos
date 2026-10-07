"""Tests for the ``revert`` command: restore tracked files from a snapshot."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pengyos_mutations import attest
from pengyos_mutations.apply import apply_change
from pengyos_mutations.cli import main
from pengyos_mutations.revert import (
    RevertError,
    find_change,
    revert_change,
)


def _py(code: str) -> "list[str]":
    """A portable argv that runs ``code`` under the current interpreter."""
    return [sys.executable, "-c", code]


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# find_change
# --------------------------------------------------------------------------- #
def test_find_change_returns_the_apply_record(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "a.conf", "v1")
    store = tmp_path / "store"
    result = apply_change(desc="edit", store=str(store), command=[], root=str(root))

    rec = find_change(str(store), result.change_id)
    assert rec is not None
    assert rec["data"]["change_id"] == result.change_id
    assert rec["data"]["kind"] == "apply"


def test_find_change_missing_returns_none(tmp_path) -> None:
    store = tmp_path / "store"
    (store).mkdir(parents=True, exist_ok=True)
    assert find_change(str(store), "no-such-id") is None


# --------------------------------------------------------------------------- #
# revert_change
# --------------------------------------------------------------------------- #
def test_revert_restores_modified_file(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "a.conf", "v1")
    store = tmp_path / "store"

    result = apply_change(
        desc="edit a.conf", store=str(store),
        command=_py("open('a.conf','w').write('v2')"), root=str(root),
    )
    assert (root / "a.conf").read_text() == "v2"

    rev = revert_change(result.change_id, store=str(store))
    assert rev.ok
    assert rev.restored == ["a.conf"]
    assert rev.removed == []
    # The live file is back to its pre-change content.
    assert (root / "a.conf").read_text() == "v1"


def test_revert_removes_created_file(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "a.conf", "v1")
    store = tmp_path / "store"

    result = apply_change(
        desc="add b.conf", store=str(store),
        command=_py("open('b.conf','w').write('new')"), root=str(root),
    )
    assert (root / "b.conf").read_text() == "new"

    rev = revert_change(result.change_id, store=str(store))
    assert rev.ok
    assert rev.removed == ["b.conf"]
    assert rev.restored == []
    assert not (root / "b.conf").exists()
    # The untouched file survives.
    assert (root / "a.conf").read_text() == "v1"


def test_revert_restores_deleted_file(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "a.conf", "v1")
    store = tmp_path / "store"

    result = apply_change(
        desc="rm a.conf", store=str(store),
        command=_py("import os; os.remove('a.conf')"), root=str(root),
    )
    assert not (root / "a.conf").exists()

    rev = revert_change(result.change_id, store=str(store))
    assert rev.ok
    assert rev.restored == ["a.conf"]
    assert rev.removed == []
    assert (root / "a.conf").read_text() == "v1"


def test_revert_restores_nested_directory(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "sub" / "deep.txt", "deep")
    store = tmp_path / "store"

    result = apply_change(
        desc="edit deep", store=str(store),
        command=_py("open('sub/deep.txt','w').write('changed')"), root=str(root),
    )
    assert (root / "sub" / "deep.txt").read_text() == "changed"

    rev = revert_change(result.change_id, store=str(store))
    assert rev.restored == ["sub/deep.txt"]
    assert (root / "sub" / "deep.txt").read_text() == "deep"


def test_revert_reports_both_restored_and_removed(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "keep.txt", "v1")
    _write(root / "mod.txt", "old")
    store = tmp_path / "store"

    result = apply_change(
        desc="mixed", store=str(store),
        command=_py(
            "open('mod.txt','w').write('new'); "
            "open('new.txt','w').write('n'); "
            "import os; os.remove('keep.txt')"
        ),
        root=str(root),
    )

    rev = revert_change(result.change_id, store=str(store))
    assert rev.ok
    assert set(rev.restored) == {"mod.txt", "keep.txt"}
    assert rev.removed == ["new.txt"]
    # Restored to pre-change state: keep.txt (deleted by the change) is back,
    # mod.txt (modified) is back, new.txt (created) is gone.
    assert (root / "keep.txt").read_text() == "v1"
    assert (root / "mod.txt").read_text() == "old"
    assert not (root / "new.txt").exists()


def test_revert_noop_when_change_made_no_tracked_change(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "a.conf", "v1")
    store = tmp_path / "store"
    # A command that writes outside root leaves the tracked tree unchanged.
    result = apply_change(
        desc="noop", store=str(store),
        command=_py("open('/dev/null','w').close()"), root=str(root),
    )
    rev = revert_change(result.change_id, store=str(store))
    assert rev.ok
    assert rev.restored == []
    assert rev.removed == []
    assert "no tracked paths" in rev.summary()


def test_revert_missing_change_raises(tmp_path) -> None:
    store = tmp_path / "store"
    store.mkdir(parents=True, exist_ok=True)
    with pytest.raises(RevertError, match="no recorded change"):
        revert_change("does-not-exist", store=str(store))


def test_revert_unknown_backend_raises(tmp_path) -> None:
    """A record that names a backend which cannot be loaded fails cleanly."""
    root = tmp_path / "root"
    _write(root / "a.conf", "v1")
    store = tmp_path / "store"
    log = store / "events.jsonl"

    # Seed a well-formed apply record whose backend cannot be resolved.
    attest.append_event(
        log, "apply",
        {
            "kind": "apply",
            "change_id": "ghost-change",
            "backend": "ghost",
            "root": root.as_posix(),
        },
    )

    with pytest.raises(RevertError, match="cannot load backend"):
        revert_change("ghost-change", store=str(store))


def test_revert_appends_revert_event_and_chain_still_valid(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "a.conf", "v1")
    store = tmp_path / "store"

    result = apply_change(
        desc="edit", store=str(store),
        command=_py("open('a.conf','w').write('v2')"), root=str(root),
    )
    assert attest.chain_length(store / "events.jsonl") == 1

    revert_change(result.change_id, store=str(store))

    # The reversal is recorded, and the chain still verifies end to end.
    assert attest.chain_length(store / "events.jsonl") == 2
    report = attest.verify_chain(store / "events.jsonl")
    assert report.ok
    rev = attest.last_record(store / "events.jsonl")
    assert rev["type"] == "revert"
    assert rev["data"]["reverted_change"] == result.change_id
    assert rev["data"]["restored"] == ["a.conf"]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_revert_via_cli_restores_and_returns_zero(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "a.conf", "v1")
    store = tmp_path / "store"

    result = apply_change(
        desc="cli edit", store=str(store),
        command=_py("open('a.conf','w').write('v2')"), root=str(root),
    )

    rc = main(["revert", result.change_id, "--store", str(store)])
    assert rc == 0
    assert (root / "a.conf").read_text() == "v1"


def test_revert_via_cli_missing_change_returns_one(tmp_path, capsys) -> None:
    store = tmp_path / "store"
    store.mkdir(parents=True, exist_ok=True)
    rc = main(["revert", "nope", "--store", str(store)])
    assert rc == 1
    assert "revert" in capsys.readouterr().err


def test_revert_via_cli_records_event_and_chain_valid(tmp_path) -> None:
    root = tmp_path / "root"
    _write(root / "a.conf", "v1")
    store = tmp_path / "store"

    result = apply_change(
        desc="cli", store=str(store),
        command=_py("open('a.conf','w').write('v2')"), root=str(root),
    )
    main(["revert", result.change_id, "--store", str(store)])
    assert attest.verify_chain(store / "events.jsonl").ok
