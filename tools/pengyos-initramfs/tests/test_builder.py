"""Tests for ``build_archive``: spec + root dir -> deterministic gzip cpio."""

from __future__ import annotations

import gzip
import os
from pathlib import Path

import pytest

from pengyos_initramfs.builder import (
    BuildError,
    build_archive,
    gzip_compress,
)
from pengyos_initramfs.spec import parse_spec
from cpio_reader import read_archive, regular_entries

BUSYBOX = b"\x7fELF-busybox-stub\n"
OS_RELEASE = b"ID=pengyos\nNAME=\"PengyOS\"\n"
FSTAB = b"# /etc/fstab\n"
LOOP_KO = bytes(range(256)) * 4


@pytest.fixture
def root_tree(tmp_path):
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    (root / "bin/busybox").write_bytes(BUSYBOX)
    (root / "etc").mkdir()
    (root / "etc/os-release").write_bytes(OS_RELEASE)
    (root / "etc/fstab").write_bytes(FSTAB)
    (root / "lib/modules/6.1.0/extra").mkdir(parents=True)
    (root / "lib/modules/6.1.0/extra/loop.ko").write_bytes(LOOP_KO)
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/bin/sh").symlink_to("../bin/busybox")
    return root


def make_spec(**overrides) -> "object":
    data = {
        "binaries": ["bin/busybox"],
        "files": ["etc/os-release", "etc/fstab"],
        "modules": ["lib/modules/6.1.0/extra/loop.ko"],
    }
    data.update(overrides)
    return parse_spec(data)


def test_archive_contains_exactly_the_spec_paths_plus_init_sorted(root_tree):
    spec = make_spec()
    entries = regular_entries(gzip.decompress(build_archive(spec, root_tree)))

    assert [e["name"] for e in entries] == [
        "bin/busybox",
        "etc/fstab",
        "etc/os-release",
        "init",  # generated /init entry, in its sorted position
        "lib/modules/6.1.0/extra/loop.ko",
    ]
    # Data is preserved byte-for-byte.
    by_name = {e["name"]: e for e in entries}
    assert by_name["bin/busybox"]["data"] == BUSYBOX
    assert by_name["etc/os-release"]["data"] == OS_RELEASE
    assert by_name["etc/fstab"]["data"] == FSTAB
    assert by_name["lib/modules/6.1.0/extra/loop.ko"]["data"] == LOOP_KO


def test_metadata_normalized_from_spec(root_tree):
    spec = make_spec(
        **{
            "build": {
                "mtime": "1970-01-02T01:04:05Z",  # epoch 90245
                "uid": 1000,
                "gid": 2001,
                "user": "pengy",
                "group": "pengy",
            }
        }
    )
    entries = regular_entries(gzip.decompress(build_archive(spec, root_tree)))
    for i, e in enumerate(entries, start=1):
        assert e["ino"] == i  # 1-based position, never from the filesystem
        assert e["uid"] == 1000 and e["gid"] == 2001
        assert e["mtime"] == 90245
        assert e["nlink"] == 1
        assert e["check"] == 0


def test_modes_normalized_per_kind_ignoring_disk_modes(root_tree):
    # Mess with on-disk modes: the archive must not depend on them.
    os.chmod(root_tree / "bin/busybox", 0o600)
    os.chmod(root_tree / "etc/os-release", 0o777)
    os.chmod(root_tree / "lib/modules/6.1.0/extra/loop.ko", 0o400)

    spec = make_spec(**{"files": ["etc/os-release", "etc/fstab", "usr/bin/sh"]})
    entries = {e["name"]: e for e in regular_entries(gzip.decompress(build_archive(spec, root_tree)))}

    assert entries["bin/busybox"]["mode"] == 0o100755
    assert entries["etc/os-release"]["mode"] == 0o100644
    assert entries["etc/fstab"]["mode"] == 0o100644
    assert entries["lib/modules/6.1.0/extra/loop.ko"]["mode"] == 0o100644
    assert entries["usr/bin/sh"]["mode"] == 0o120777
    assert entries["usr/bin/sh"]["data"] == b"../bin/busybox"  # symlink target


def test_duplicate_path_across_kinds_single_entry_binaries_wins(root_tree):
    spec = make_spec(
        **{
            "binaries": ["etc/os-release", "bin/busybox"],
            "files": ["etc/os-release", "etc/fstab"],
        }
    )
    entries = regular_entries(gzip.decompress(build_archive(spec, root_tree)))
    assert [e["name"] for e in entries] == [
        "bin/busybox",
        "etc/fstab",
        "etc/os-release",
        "init",
        "lib/modules/6.1.0/extra/loop.ko",
    ]
    by_name = {e["name"]: e for e in entries}
    assert by_name["etc/os-release"]["mode"] == 0o100755  # binaries listed first


def test_missing_path_is_a_build_error(root_tree):
    with pytest.raises(BuildError, match="not found"):
        build_archive(make_spec(**{"files": ["etc/missing"]}), root_tree)


def test_directory_path_in_file_kind_is_a_build_error(root_tree):
    with pytest.raises(BuildError, match="directory"):
        build_archive(make_spec(**{"files": ["etc"]}), root_tree)


# --- explicit directory entries -------------------------------------------


def test_directory_entries_packed_with_trailing_slash_and_mode(root_tree):
    spec = make_spec(**{"directories": ["etc", "lib/modules/6.1.0/extra"]})
    entries = regular_entries(gzip.decompress(build_archive(spec, root_tree)))

    names = [e["name"] for e in entries]
    # Directory entries carry a trailing '/' and sort before their children.
    assert names == [
        "bin/busybox",
        "etc/",            # explicit directory, before its children
        "etc/fstab",
        "etc/os-release",
        "init",
        "lib/modules/6.1.0/extra/",  # explicit directory, before its children
        "lib/modules/6.1.0/extra/loop.ko",
    ]
    by_name = {e["name"]: e for e in entries}
    for d in ("etc/", "lib/modules/6.1.0/extra/"):
        assert by_name[d]["mode"] == 0o040755
        assert by_name[d]["data"] == b""  # directory entries have no data


def test_directory_entries_get_normalized_metadata(root_tree):
    spec = make_spec(
        **{
            "directories": ["etc"],
            "build": {"mtime": "1970-01-02T01:04:05Z", "uid": 42, "gid": 43},
        }
    )
    entries = {e["name"]: e for e in regular_entries(gzip.decompress(build_archive(spec, root_tree)))}
    d = entries["etc/"]
    assert d["uid"] == 42 and d["gid"] == 43
    assert d["mtime"] == 90245
    assert d["nlink"] == 1 and d["check"] == 0


def test_directories_kind_wins_over_file_kind(root_tree):
    # 'etc' listed in BOTH files and directories: it packs as a directory.
    spec = make_spec(**{"files": ["etc/os-release", "etc"], "directories": ["etc"]})
    entries = regular_entries(gzip.decompress(build_archive(spec, root_tree)))
    by_name = {e["name"]: e for e in entries}
    assert "etc" not in by_name
    assert by_name["etc/"]["mode"] == 0o040755
    assert by_name["etc/os-release"]["mode"] == 0o100644


def test_directory_entry_ignores_disk_mode(root_tree):
    os.chmod(root_tree / "etc", 0o700)
    spec = make_spec(**{"files": ["etc/os-release"], "directories": ["etc"]})
    entries = {e["name"]: e for e in regular_entries(gzip.decompress(build_archive(spec, root_tree)))}
    assert entries["etc/"]["mode"] == 0o040755  # normalized, not 0o700


def test_directories_are_deterministic(root_tree):
    spec = make_spec(**{"directories": ["etc", "usr", "bin"]})
    assert build_archive(spec, root_tree) == build_archive(spec, root_tree)


def test_directory_entry_missing_under_root_is_build_error(root_tree):
    with pytest.raises(BuildError, match="not a directory"):
        build_archive(make_spec(**{"directories": ["nope/never"]}), root_tree)


def test_directory_entry_where_a_file_lives_is_build_error(root_tree):
    with pytest.raises(BuildError, match="not a directory"):
        build_archive(make_spec(**{"directories": ["etc/os-release"]}), root_tree)


def test_symlink_to_directory_in_directories_is_packed_as_plain_dir(tmp_path):
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc/os-release").write_bytes(b"ID=pengyos\n")
    (root / "alias").symlink_to("etc")  # symlink to a directory
    spec = parse_spec({"files": ["etc/os-release"], "directories": ["alias"]})
    entries = regular_entries(gzip.decompress(build_archive(spec, root)))
    by_name = {e["name"]: e for e in entries}
    # Packed as a plain directory entry (the kernel only needs a dir here),
    # not as a symlink entry.
    assert by_name["alias/"]["mode"] == 0o040755
    assert by_name["alias/"]["data"] == b""


def test_root_must_be_a_directory(tmp_path):
    with pytest.raises(BuildError, match="root is not a directory"):
        build_archive(make_spec(), tmp_path / "nope")


def test_empty_spec_yields_only_the_generated_init():
    entries = read_archive(gzip.decompress(build_archive(make_spec(binaries=[], files=[], modules=[]), Path("."))))
    assert [e["name"] for e in entries] == ["init", "TRAILER!!!"]


def test_output_is_gzip_with_zeroed_mtime_and_no_fname():
    spec = make_spec(binaries=[], files=[], modules=[])
    blob = build_archive(spec, Path("."))
    assert blob[:2] == b"\x1f\x8b"
    assert blob[2] == 8  # deflate
    assert blob[3] & 0x08 == 0  # no FNAME
    assert blob[4:8] == b"\x00\x00\x00\x00"  # MTIME == 0
    # Deterministic at the gzip level too.
    assert blob == build_archive(spec, Path("."))


def test_two_builds_are_byte_identical(root_tree):
    spec = make_spec()
    assert build_archive(spec, root_tree) == build_archive(spec, root_tree)


def test_gzip_compress_matches_stable_reference():
    """gzip_compress is a pure function of its input + settings."""
    payload = b"hello pengyos\n" * 100
    assert gzip_compress(payload) == gzip.compress(payload, compresslevel=9, mtime=0)
    # A nonzero gzip mtime would make it non-deterministic; ours must not drift.
    assert gzip_compress(payload, mtime=0) == gzip_compress(payload, mtime=0)
    assert gzip_compress(payload, mtime=1) != gzip_compress(payload, mtime=0)
