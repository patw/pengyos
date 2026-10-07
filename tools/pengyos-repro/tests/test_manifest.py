"""Tests for the two-tree manifest:

Walk two trees; build a manifest of relative paths -> size + sha256
(streaming, large-file safe).
"""

import builtins
import hashlib
import os
from pathlib import Path

import pytest

from pengyos_repro import manifest as mod
from pengyos_repro.manifest import (
    DEFAULT_CHUNK_SIZE,
    FileEntry,
    ManifestError,
    iter_manifest,
    manifest,
)


def _write(root: Path, rel: str, data: bytes) -> Path:
    """Write ``data`` to ``root/rel``, creating parent dirs, and return path."""
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# --- basics ---------------------------------------------------------------


def test_manifest_empty_dir(tmp_path):
    assert manifest(str(tmp_path)) == {}


def test_manifest_single_file_size_and_sha256(tmp_path):
    _write(tmp_path, "a.txt", b"hello\n")
    m = manifest(str(tmp_path))
    assert set(m) == {"a.txt"}
    entry = m["a.txt"]
    assert isinstance(entry, FileEntry)
    assert entry.size == len(b"hello\n")
    assert entry.sha256 == hashlib.sha256(b"hello\n").hexdigest()


def test_manifest_known_vector(tmp_path):
    # sha256 of the empty string, a well-known constant.
    _write(tmp_path, "empty", b"")
    entry = manifest(str(tmp_path))["empty"]
    assert entry.size == 0
    assert entry.sha256 == hashlib.sha256(b"").hexdigest()


def test_manifest_nested_dirs_use_posix_separators(tmp_path):
    _write(tmp_path, "sub/deep/file.bin", b"\x00\x01\x02" * 1000)
    m = manifest(str(tmp_path))
    assert set(m) == {"sub/deep/file.bin"}
    assert m["sub/deep/file.bin"].size == 3000


def test_manifest_multiple_files(tmp_path):
    _write(tmp_path, "a", b"a")
    _write(tmp_path, "b", b"bb")
    _write(tmp_path, "sub/c", b"ccc")
    m = manifest(str(tmp_path))
    assert set(m) == {"a", "b", "sub/c"}
    assert m["a"].sha256 == hashlib.sha256(b"a").hexdigest()
    assert m["b"].sha256 == hashlib.sha256(b"bb").hexdigest()
    assert m["sub/c"].sha256 == hashlib.sha256(b"ccc").hexdigest()


def test_manifest_entrypoint_matches_iter(tmp_path):
    _write(tmp_path, "a", b"a")
    _write(tmp_path, "sub/b", b"bb")
    expected = dict(iter_manifest(str(tmp_path)))
    assert manifest(str(tmp_path)) == expected


# --- what gets skipped ----------------------------------------------------


def test_manifest_skips_symlinks(tmp_path):
    target = _write(tmp_path, "real.txt", b"data")
    os.symlink(target, tmp_path / "link.txt")
    m = manifest(str(tmp_path))
    assert set(m) == {"real.txt"}


def test_manifest_skips_directories(tmp_path):
    _write(tmp_path, "dir/inside.txt", b"x")
    m = manifest(str(tmp_path))
    # Only the file is an entry; the directory is not keyed.
    assert set(m) == {"dir/inside.txt"}


# --- streaming / large-file safety ----------------------------------------


def test_iter_manifest_is_lazy(tmp_path, monkeypatch):
    calls = []

    def spy_hash_stream(path, chunk_size):
        calls.append(path)
        return FileEntry(size=1, sha256="0" * 64)

    monkeypatch.setattr(mod, "_hash_stream", spy_hash_stream)
    _write(tmp_path, "a", b"x")
    _write(tmp_path, "b", b"y")

    gen = iter_manifest(str(tmp_path))
    # Nothing hashed until the generator is advanced.
    assert calls == []
    next(gen)
    assert len(calls) == 1
    # And advancing again hashes only the next file.
    next(gen)
    assert len(calls) == 2


def test_streaming_reads_in_bounded_chunks(tmp_path, monkeypatch):
    """A large file must be hashed in chunks no larger than chunk_size."""
    big = 5 * DEFAULT_CHUNK_SIZE + 123  # just over 5 chunks
    _write(tmp_path, "big.bin", b"\xab" * big)

    real_open = builtins.open
    stats = {"n_reads": 0, "max_chunk": 0}

    class RecordingFile:
        def __init__(self, fh):
            self._fh = fh

        def read(self, *args):
            chunk = self._fh.read(*args)
            stats["n_reads"] += 1
            if chunk:
                stats["max_chunk"] = max(stats["max_chunk"], len(chunk))
            return chunk

        def __enter__(self):
            self._fh.__enter__()
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

    def fake_open(path, mode, *args, **kwargs):
        if str(path).endswith("big.bin"):
            return RecordingFile(real_open(path, mode, *args, **kwargs))
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)

    entry = manifest(str(tmp_path))["big.bin"]
    # Correct digest proves streaming produced the same result as a full read.
    assert entry.sha256 == hashlib.sha256(b"\xab" * big).hexdigest()
    assert entry.size == big
    # Proved it chunked: more than one read, each bounded by chunk_size.
    assert stats["n_reads"] >= 6
    assert stats["max_chunk"] <= DEFAULT_CHUNK_SIZE


# --- error handling -------------------------------------------------------


def test_manifest_raises_on_unreadable_file(tmp_path):
    p = _write(tmp_path, "secret.txt", b"top secret")
    # Remove all permissions so the open() inside _hash_stream fails.
    # (Runs as a non-root user, so 0o000 blocks even the owner.)
    original = p.stat().st_mode
    try:
        os.chmod(p, 0o000)
        with pytest.raises(ManifestError):
            manifest(str(tmp_path))
    finally:
        os.chmod(p, original)


def test_manifest_error_message_mentions_path(tmp_path):
    p = _write(tmp_path, "nope.txt", b"x")
    original = p.stat().st_mode
    try:
        os.chmod(p, 0o000)
        with pytest.raises(ManifestError) as excinfo:
            manifest(str(tmp_path))
    finally:
        os.chmod(p, original)
    assert "nope.txt" in str(excinfo.value)
