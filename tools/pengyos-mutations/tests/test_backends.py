"""Tests for the snapshot / restore backend layer."""

from __future__ import annotations

import shutil

import pytest

from pengyos_mutations.backends import (
    Backend,
    BackendError,
    BtrfsBackend,
    MockBackend,
    Snapshot,
    get_backend,
)


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
def test_backend_is_abstract() -> None:
    """The base class cannot be instantiated (it declares abstract methods)."""
    with pytest.raises(TypeError):
        Backend(store=".")  # type: ignore[abstract]


def test_mock_backend_is_a_backend() -> None:
    assert issubclass(MockBackend, Backend)
    assert issubclass(BtrfsBackend, Backend)


def test_backend_creates_store_on_init(tmp_path) -> None:
    store = tmp_path / "nested" / "store"
    MockBackend(store)
    assert store.is_dir()


# --------------------------------------------------------------------------- #
# Snapshot / restore of files
# --------------------------------------------------------------------------- #
def _write(path, text="hello"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_snapshot_copies_existing_file(tmp_path) -> None:
    store = tmp_path / "store"
    src = _write(tmp_path / "foo.conf", "v1")

    backend = MockBackend(store)
    snap = backend.snapshot("c1", {"foo.conf": src})

    assert isinstance(snap, Snapshot)
    assert snap.change_id == "c1"
    assert snap.backend == "mock"
    assert snap.keys == ["foo.conf"]
    assert snap.entries["foo.conf"] is not None

    stored = store / "mock" / "snapshots" / "c1" / "foo.conf"
    assert stored.exists()
    assert stored.read_text(encoding="utf-8") == "v1"


def test_snapshot_records_missing_paths_as_none(tmp_path) -> None:
    store = tmp_path / "store"
    backend = MockBackend(store)

    snap = backend.snapshot("c1", {"created.conf": tmp_path / "missing.conf"})
    assert snap.entries["created.conf"] is None


def test_snapshot_of_directory(tmp_path) -> None:
    store = tmp_path / "store"
    d = tmp_path / "dir"
    _write(d / "a.txt", "aaa")
    nested = _write(d / "sub" / "b.txt", "bbb")
    assert nested.exists()

    backend = MockBackend(store)
    snap = backend.snapshot("c1", {"dir": d})

    assert snap.entries["dir"] is not None
    stored = store / "mock" / "snapshots" / "c1" / "dir"
    assert (stored / "a.txt").read_text(encoding="utf-8") == "aaa"
    assert (stored / "sub" / "b.txt").read_text(encoding="utf-8") == "bbb"


def test_re_snapshot_overwrites(tmp_path) -> None:
    store = tmp_path / "store"
    src = _write(tmp_path / "foo.conf", "v1")

    backend = MockBackend(store)
    backend.snapshot("c1", {"foo.conf": src})

    # Mutate the live file and re-snapshot.
    _write(tmp_path / "foo.conf", "v2")
    snap = backend.snapshot("c1", {"foo.conf": src})
    assert snap.entries["foo.conf"] is not None

    stored = store / "mock" / "snapshots" / "c1" / "foo.conf"
    assert stored.read_text(encoding="utf-8") == "v2"


# --------------------------------------------------------------------------- #
# Restore
# --------------------------------------------------------------------------- #
def test_restore_restores_modified_file(tmp_path) -> None:
    store = tmp_path / "store"
    src = _write(tmp_path / "foo.conf", "v1")

    backend = MockBackend(store)
    backend.snapshot("c1", {"foo.conf": src})

    _write(tmp_path / "foo.conf", "tampered")
    backend.restore("c1", {"foo.conf": src})

    assert src.read_text(encoding="utf-8") == "v1"


def test_restore_removes_created_file(tmp_path) -> None:
    store = tmp_path / "store"
    created = tmp_path / "created.conf"

    backend = MockBackend(store)
    # The path did not exist at snapshot time.
    backend.snapshot("c1", {"created.conf": created})
    assert not created.exists()

    _write(created, "live")
    backend.restore("c1", {"created.conf": created})
    assert not created.exists()


def test_restore_recreates_deleted_file(tmp_path) -> None:
    store = tmp_path / "store"
    src = _write(tmp_path / "foo.conf", "v1")

    backend = MockBackend(store)
    backend.snapshot("c1", {"foo.conf": src})
    src.unlink()
    assert not src.exists()

    backend.restore("c1", {"foo.conf": src})
    assert src.read_text(encoding="utf-8") == "v1"


def test_restore_removes_dir_created_by_change(tmp_path) -> None:
    store = tmp_path / "store"
    created = tmp_path / "created_dir"
    # Absent at snapshot time (created by the change).
    backend = MockBackend(store)
    backend.snapshot("c1", {"created_dir": created})
    assert not created.exists()

    created.mkdir()
    _write(created / "inner.txt", "x")
    assert created.is_dir()

    backend.restore("c1", {"created_dir": created})
    assert not created.exists()


def test_restore_missing_snapshot_raises(tmp_path) -> None:
    store = tmp_path / "store"
    src = _write(tmp_path / "foo.conf", "v1")
    backend = MockBackend(store)
    with pytest.raises(BackendError):
        backend.restore("missing", {"foo.conf": src})


def test_restore_replaces_dir_with_file(tmp_path) -> None:
    """A path that was a file at snapshot time is restored as a file."""
    store = tmp_path / "store"
    target = tmp_path / "thing"
    _write(target, "file")

    backend = MockBackend(store)
    backend.snapshot("c1", {"thing": target})

    # Change it into a directory on disk.
    target.unlink()
    target.mkdir()
    _write(target / "old.txt", "stale")

    backend.restore("c1", {"thing": target})
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "file"


def test_restore_restores_directory_as_directory(tmp_path) -> None:
    """A dir captured at snapshot time is restored as a dir, even if it became
    a file on disk in the meantime."""
    store = tmp_path / "store"
    target = tmp_path / "thing"
    _write(target / "a.txt", "aaa")

    backend = MockBackend(store)
    backend.snapshot("c1", {"thing": target})

    shutil.rmtree(target)
    _write(target, "now a file")

    backend.restore("c1", {"thing": target})
    assert target.is_dir()
    assert (target / "a.txt").read_text(encoding="utf-8") == "aaa"


# --------------------------------------------------------------------------- #
# Registry / btrfs stub
# --------------------------------------------------------------------------- #
def test_get_backend_known_name(tmp_path) -> None:
    backend = get_backend("mock", tmp_path / "store")
    assert isinstance(backend, MockBackend)


def test_get_backend_unknown_name(tmp_path) -> None:
    with pytest.raises(BackendError):
        get_backend("does-not-exist", tmp_path)


def test_btrfs_backend_snapshot_raises(tmp_path) -> None:
    backend = BtrfsBackend(tmp_path / "store")
    with pytest.raises(BackendError):
        backend.snapshot("c1", {})


def test_btrfs_backend_restore_raises(tmp_path) -> None:
    backend = BtrfsBackend(tmp_path / "store")
    with pytest.raises(BackendError):
        backend.restore("c1", {})


def test_btrfs_backend_reports_why(tmp_path) -> None:
    backend = BtrfsBackend(tmp_path / "store")
    with pytest.raises(BackendError, match="root privileges"):
        backend.snapshot("c1", {})
