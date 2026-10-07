"""Opt-in integration tests: validate archives against the system's real ``cpio``.

These are skipped unless ``PENGYOS_IT=1`` is set AND a ``cpio`` binary is
available. They prove the hand-rolled ``newc`` writer is accepted by a real,
independent implementation of the format (listing + extraction round-trip).
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pengyos_initramfs.builder import build_archive
from pengyos_initramfs.spec import parse_spec

SPEC_DATA = {
    "binaries": ["bin/busybox"],
    "files": ["etc/os-release"],
    "modules": ["lib/modules/6.1.0/extra/loop.ko"],
}

EXPECTED = [
    "bin/busybox",
    "etc/os-release",
    "init",
    "lib/modules/6.1.0/extra/loop.ko",
]

requires_it = pytest.mark.skipif(
    os.environ.get("PENGYOS_IT") != "1",
    reason="integration tests are opt-in: set PENGYOS_IT=1",
)
requires_cpio = pytest.mark.skipif(
    shutil.which("cpio") is None, reason="system cpio binary not installed"
)


@pytest.fixture(scope="module")
def built_blob(tmp_path_factory):
    root = tmp_path_factory.mktemp("it-root")
    (root / "bin").mkdir(parents=True)
    (root / "bin/busybox").write_bytes(b"busybox\n")
    (root / "etc").mkdir()
    (root / "etc/os-release").write_bytes(b"ID=pengyos\n")
    (root / "lib/modules/6.1.0/extra").mkdir(parents=True)
    (root / "lib/modules/6.1.0/extra/loop.ko").write_bytes(b"\x00" * 16)
    return build_archive(parse_spec(SPEC_DATA), root)


@requires_it
@requires_cpio
def test_system_cpio_lists_exactly_the_spec_paths(built_blob, tmp_path):
    cpio_file = tmp_path / "init.cpio"
    cpio_file.write_bytes(gzip.decompress(built_blob))
    proc = subprocess.run(
        ["cpio", "-t"],
        cwd=tmp_path,
        input=cpio_file.read_bytes(),
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    listed = [line for line in proc.stdout.decode().splitlines() if line]
    assert sorted(listed) == sorted(EXPECTED)


@requires_it
@requires_cpio
def test_system_cpio_lists_explicit_directory_entries(tmp_path):
    """Real GNU cpio sees spec'd 'directories' as directory entries."""
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc/os-release").write_bytes(b"ID=pengyos\n")
    blob = build_archive(
        parse_spec({"files": ["etc/os-release"], "directories": ["etc"]}), root
    )
    cpio_file = tmp_path / "with-dirs.cpio"
    cpio_file.write_bytes(gzip.decompress(blob))
    proc = subprocess.run(
        ["cpio", "-t"],
        cwd=tmp_path,
        input=cpio_file.read_bytes(),
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    listed = [line for line in proc.stdout.decode().splitlines() if line]
    # GNU cpio prints directory entries with a trailing slash.
    assert sorted(listed) == ["etc/", "etc/os-release", "init"]


@requires_it
@requires_cpio
def test_system_cpio_extract_round_trip(built_blob, tmp_path):
    proc = subprocess.run(
        ["cpio", "-idm"],
        cwd=tmp_path,
        input=gzip.decompress(built_blob),
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert (tmp_path / "bin/busybox").read_bytes() == b"busybox\n"
    assert (tmp_path / "etc/os-release").read_bytes() == b"ID=pengyos\n"
    # Modes were normalized by the builder.
    assert os.stat(tmp_path / "bin/busybox").st_mode & 0o7777 == 0o755
    assert os.stat(tmp_path / "etc/os-release").st_mode & 0o7777 == 0o644
    # The generated /init round-trips too, executable.
    from pengyos_initramfs.initgen import INIT_SCRIPT

    assert (tmp_path / "init").read_bytes() == INIT_SCRIPT.encode("ascii")
    assert os.stat(tmp_path / "init").st_mode & 0o7777 == 0o755
