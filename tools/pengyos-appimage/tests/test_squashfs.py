"""Tests for :mod:`pengyos_appimage.squashfs`, the pure-Python payload reader.

The fixtures are real ``mksquashfs`` output (see
``tests/fixtures/build_squashfs_fixtures.py``), one per compressor and layout,
all packing the same tree. :func:`expected` comes from that builder, so the
tree and the assertions cannot drift apart. An opt-in test compares the reader
with ``unsquashfs`` on any real AppImages named in ``PENGYOS_APPIMAGE_REAL``.
"""

from __future__ import annotations

import importlib.util
import io
import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from pengyos_appimage import runtime, squashfs

FIXTURES = Path(__file__).resolve().parent / "fixtures"
READABLE = ["sq-gzip.sqfs", "sq-xz.sqfs", "sq-zstd.sqfs", "sq-lzma.sqfs",
            "sq-nofrag.sqfs", "sq-raw.sqfs"]


def _builder():
    spec = importlib.util.spec_from_file_location(
        "build_squashfs_fixtures", FIXTURES / "build_squashfs_fixtures.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FILES, LINKS, DIRS = _builder().expected()


def _open(name: str) -> squashfs.SquashFS:
    return squashfs.SquashFS.open(FIXTURES / name, 0)


@pytest.mark.parametrize("name", READABLE)
def test_walk_finds_exactly_the_tree(name):
    with _open(name) as fs:
        got = dict(fs.walk())
    assert set(got) == set(FILES) | set(LINKS) | DIRS


@pytest.mark.parametrize("name", READABLE)
def test_every_file_reads_back_byte_for_byte(name):
    with _open(name) as fs:
        got = dict(fs.walk())
        for path, data in FILES.items():
            assert got[path].is_file
            assert fs.read(got[path]) == data, path


@pytest.mark.parametrize("name", READABLE)
def test_symlink_targets(name):
    with _open(name) as fs:
        got = dict(fs.walk())
        for path, target in LINKS.items():
            assert got[path].is_symlink
            assert got[path].data == target


def test_compression_is_reported():
    for name, comp in [("sq-gzip.sqfs", "gzip"), ("sq-xz.sqfs", "xz"),
                       ("sq-zstd.sqfs", "zstd"), ("sq-lzma.sqfs", "lzma")]:
        with _open(name) as fs:
            assert fs.compression == comp


UNCOMPRESSED = 1 << 24


@pytest.mark.parametrize("name", ["sq-gzip.sqfs", "sq-xz.sqfs", "sq-zstd.sqfs", "sq-lzma.sqfs"])
def test_layout_variants_hit_their_code_paths(name):
    # The fixtures must exercise what they claim: full blocks stored raw and
    # compressed, tail-end fragments, and a sparse block.
    with _open(name) as fs:
        _s, frag, _o, _size, sizes = fs.lookup("big.bin").data
        assert len(sizes) == 3 and frag != 0xFFFFFFFF
        assert all(w & UNCOMPRESSED for w in sizes)
        _s, frag, _o, _size, sizes = fs.lookup("text.bin").data
        assert len(sizes) == 4 and frag != 0xFFFFFFFF
        assert not any(w & UNCOMPRESSED for w in sizes)
        assert 0 in fs.lookup("sparse.bin").data[4], "sparse.bin has no sparse block"


def test_no_fragments_layout():
    with _open("sq-nofrag.sqfs") as fs:
        _s, frag, _o, _size, sizes = fs.lookup("big.bin").data
        assert frag == 0xFFFFFFFF and len(sizes) == 4


def test_big_directory_spans_several_headers():
    with _open("sq-zstd.sqfs") as fs:
        names = [n for n, _ in fs.listdir(fs.lookup("many"))]
    assert sorted(names) == sorted(f"f{i:03d}" for i in range(300))


def test_read_with_limit():
    with _open("sq-xz.sqfs") as fs:
        assert fs.read(fs.lookup("big.bin"), 10) == FILES["big.bin"][:10]


def test_lookup_follows_relative_symlinks():
    with _open("sq-gzip.sqfs") as fs:
        assert fs.read_path("link-chain") == FILES["small.txt"]
        assert fs.read_path("dirlink/c/deep.txt") == FILES["a/b/c/deep.txt"]
        assert fs.read_path("a/b/up") == FILES["small.txt"]
        assert fs.lookup("link-rel", follow=False).is_symlink


def test_absolute_symlink_does_not_escape_to_the_host():
    with _open("sq-gzip.sqfs") as fs:
        assert fs.lookup("link-abs") is None
        assert fs.read_path("link-abs") is None


def test_symlink_loop_is_refused():
    with _open("sq-gzip.sqfs") as fs:
        with pytest.raises(squashfs.SquashfsError, match="symlink"):
            fs.lookup("loop-a")


def test_missing_paths():
    with _open("sq-gzip.sqfs") as fs:
        assert fs.lookup("nope") is None
        assert fs.lookup("small.txt/child") is None
        assert fs.read_path("a") is None  # a directory, not a file


def test_lz4_is_refused_by_name():
    with pytest.raises(squashfs.SquashfsError, match="lz4"):
        _open("sq-lz4.sqfs")


def test_reads_at_an_offset(sample_appimage):
    meta = runtime.read_metadata_file(sample_appimage)
    with squashfs.SquashFS.open(sample_appimage, meta.payload_offset) as fs:
        assert fs.compression == "xz"
        assert b"Name=Sample App" in fs.read_path("sample.desktop")


def _corrupt(name: str, offset: int, data: bytes) -> squashfs.SquashFS:
    raw = bytearray((FIXTURES / name).read_bytes())
    raw[offset:offset + len(data)] = data
    return squashfs.SquashFS(io.BytesIO(bytes(raw)))


def test_rejects_bad_magic():
    with pytest.raises(squashfs.SquashfsError, match="magic"):
        _corrupt("sq-gzip.sqfs", 0, b"XXXX")


def test_rejects_wrong_version():
    with pytest.raises(squashfs.SquashfsError, match="4.0 only"):
        _corrupt("sq-gzip.sqfs", 28, struct.pack("<H", 3))


def test_rejects_inconsistent_tables():
    # Inode table start moved past bytes_used.
    with pytest.raises(squashfs.SquashfsError, match="inconsistent"):
        _corrupt("sq-gzip.sqfs", 64, struct.pack("<Q", 1 << 40))


def test_corrupt_data_block_is_an_error_not_a_crash():
    # text.bin's blocks are compressed, so garbage in one is detectable.
    with _open("sq-gzip.sqfs") as fs:
        start = fs.lookup("text.bin").data[0]
    fs = _corrupt("sq-gzip.sqfs", start, b"\xff" * 64)
    with pytest.raises(squashfs.SquashfsError, match="corrupt"):
        fs.read(fs.lookup("text.bin"))


def test_truncated_image_is_an_error():
    raw = (FIXTURES / "sq-gzip.sqfs").read_bytes()[:200]
    with pytest.raises(squashfs.SquashfsError):
        fs = squashfs.SquashFS(io.BytesIO(raw))
        list(fs.walk())


def _real_appimages() -> list[str]:
    return [p for p in os.environ.get("PENGYOS_APPIMAGE_REAL", "").split(":") if p]


@pytest.mark.skipif(
    not os.environ.get("PENGYOS_IT") or not _real_appimages()
    or shutil.which("unsquashfs") is None,
    reason="PENGYOS_IT=1, PENGYOS_APPIMAGE_REAL and unsquashfs required",
)
@pytest.mark.parametrize("path", _real_appimages())
def test_integration_matches_unsquashfs(path, tmp_path):
    """Every file, symlink and directory equals what unsquashfs extracts."""
    meta = runtime.read_metadata_file(path)
    out = tmp_path / "x"
    subprocess.run(
        ["unsquashfs", "-q", "-n", "-no-xattrs", "-o", str(meta.payload_offset),
         "-d", str(out), path],
        check=True, capture_output=True,
    )
    with squashfs.SquashFS.open(path, meta.payload_offset) as fs:
        seen = set()
        for rel, inode in fs.walk():
            seen.add(rel)
            disk = out / rel
            if inode.is_file:
                assert fs.read(inode) == disk.read_bytes(), rel
            elif inode.is_symlink:
                assert os.readlink(disk) == inode.data, rel
            elif inode.is_dir:
                assert disk.is_dir() and not disk.is_symlink(), rel
    on_disk = {str(p.relative_to(out)) for p in out.rglob("*")}
    assert on_disk == seen
