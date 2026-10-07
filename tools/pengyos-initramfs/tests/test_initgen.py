"""Tests for the minimal /init generator (``pengyos_initramfs.initgen``).

Covers:
* the generated script is POSIX sh (shebang) and syntactically valid (`sh -n`);
* the script is deterministic (pure function of the spec — no timestamps);
* the generated entry is packed at ``spec.init_path`` with mode
  ``0o100000 | spec.init_mode`` (default ``0o100755``) in its sorted
  position;
* an operator path explicitly listed in an include list wins over the
  generated script.

Uses the independent ``tests/cpio_reader.py`` parser. `sh -n` runs the
host's POSIX shell in parse-only mode: no root, no network, no execution.
"""

from __future__ import annotations

import gzip
import shutil
import subprocess

import pytest

from pengyos_initramfs.builder import build_archive
from pengyos_initramfs.initgen import (
    INIT_SCRIPT,
    generate_init_script,
    init_entry_mode,
)
from pengyos_initramfs.spec import parse_spec
from cpio_reader import regular_entries

sh_path = shutil.which("sh")
skip_no_sh = pytest.mark.skipif(sh_path is None, reason="no POSIX sh on this host")

ROOT_MARKER = b"root-provided-init\n"


@pytest.fixture
def root_tree(tmp_path):
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    (root / "bin/busybox").write_bytes(b"\x7fELF-busybox-stub\n")
    (root / "etc").mkdir()
    (root / "etc/os-release").write_bytes(b"ID=pengyos\n")
    return root


def make_spec(**overrides) -> "object":
    data = {
        "binaries": ["bin/busybox"],
        "files": ["etc/os-release"],
    }
    data.update(overrides)
    return parse_spec(data)


def entry_data(spec, root, name: str) -> bytes:
    entries = {
        e["name"]: e for e in regular_entries(gzip.decompress(build_archive(spec, root)))
    }
    assert name in entries
    return entries[name]["data"]


# -- the script itself -------------------------------------------------------


def test_script_starts_with_sh_shebang_and_uses_set_eu():
    assert INIT_SCRIPT.startswith("#!/bin/sh\n")
    assert "set -eu" in INIT_SCRIPT


def test_script_mounts_early_virtual_filesystems():
    for fs in ("proc", "sysfs", "devtmpfs"):
        assert f"mount -t {fs}" in INIT_SCRIPT


def test_script_ends_with_exec_sh():
    assert INIT_SCRIPT.rstrip().endswith("exec /bin/sh")


@skip_no_sh
def test_script_is_syntactically_valid_sh():
    proc = subprocess.run([sh_path, "-n", "-c", INIT_SCRIPT], capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


def test_generate_is_pure_and_stable():
    a = make_spec()
    b = make_spec(binaries=[], files=[], modules=["whatever/else.ko"])
    assert generate_init_script(a) == generate_init_script(a)
    assert generate_init_script(a) == generate_init_script(b)
    # No wall-clock / host-dependent content.
    for leak in ("20", "localhost", "home/", "beholder"):
        assert leak not in generate_init_script(a)


def test_init_entry_mode_default_is_0755_regular_file():
    assert init_entry_mode(make_spec()) == 0o100755


# -- the packed archive entry -------------------------------------------------


def test_init_entry_packed_at_default_path_with_mode_0755(root_tree):
    spec = make_spec()
    entries = regular_entries(gzip.decompress(build_archive(spec, root_tree)))
    assert [e["name"] for e in entries] == [
        "bin/busybox",
        "etc/os-release",
        "init",  # sorted position between etc/* and ...
    ]
    init = next(e for e in entries if e["name"] == "init")
    assert init["mode"] == 0o100755
    assert init["data"].decode("ascii") == INIT_SCRIPT


def test_init_entry_gets_spec_normalized_metadata(root_tree):
    spec = make_spec(
        **{
            "build": {"mtime": "1970-01-02T01:04:05Z", "uid": 42, "gid": 43},
            "init": {"mode": "0700"},
        }
    )
    entries = {
        e["name"]: e for e in regular_entries(gzip.decompress(build_archive(spec, root_tree)))
    }
    init = entries["init"]
    assert init["mode"] == 0o100700
    assert init["uid"] == 42 and init["gid"] == 43
    assert init["mtime"] == 90245


def test_custom_init_path(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    spec = make_spec(binaries=[], files=[], **{"init": {"path": "sbin/init", "mode": 0o750}})
    entries = {
        e["name"]: e
        for e in regular_entries(gzip.decompress(build_archive(spec, root)))
    }
    assert "init" not in entries
    assert entries["sbin/init"]["mode"] == 0o100750
    assert entries["sbin/init"]["data"].decode("ascii") == INIT_SCRIPT


def test_explicit_include_of_init_path_wins_over_generated_script(root_tree):
    # The operator provides their own init: the real file from the root is
    # packed (binaries kind -> 0o100755), not the generated script.
    (root_tree / "init").write_bytes(ROOT_MARKER)
    spec = make_spec(binaries=["bin/busybox", "init"])
    entries_list = regular_entries(gzip.decompress(build_archive(spec, root_tree)))
    assert [e["name"] for e in entries_list] == [
        "bin/busybox",
        "etc/os-release",
        "init",
    ]
    entries = {e["name"]: e for e in entries_list}
    assert entries["init"]["data"] == ROOT_MARKER
    assert entries["init"]["data"] != INIT_SCRIPT.encode("ascii")
    assert entries["init"]["mode"] == 0o100755  # binaries kind wins, not [init].mode


def test_generated_init_does_not_require_root_contents(root_tree):
    # init.path need not exist under --root; the script is generated.
    spec = make_spec(binaries=[], files=[], **{"init": {"path": "init"}})
    assert entry_data(spec, root_tree, "init") == INIT_SCRIPT.encode("ascii")


def test_two_builds_with_init_are_byte_identical(root_tree):
    spec = make_spec()
    assert build_archive(spec, root_tree) == build_archive(spec, root_tree)


@skip_no_sh
def test_extracted_init_is_syntactically_valid_sh(root_tree):
    """The init as packed in the real archive parses as sh (spec verification)."""
    spec = make_spec()
    data = entry_data(spec, root_tree, "init")
    proc = subprocess.run([sh_path, "-n", "-c", data.decode("ascii")], capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
