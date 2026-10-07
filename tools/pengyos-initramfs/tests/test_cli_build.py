"""End-to-end CLI tests for ``pengyos-initramfs build`` (spec + root -> .cpio.gz)."""

from __future__ import annotations

import contextlib
import gzip
import io
from pathlib import Path

import pytest

from pengyos_initramfs.cli import (
    EXIT_BUILD_FAILED,
    EXIT_INVALID_SPEC,
    EXIT_OK,
    EXIT_USAGE,
    main,
)
from cpio_reader import regular_entries

SPEC = """\
binaries = ["bin/busybox"]
files = ["etc/os-release"]
modules = ["lib/modules/6.1.0/extra/loop.ko"]
"""


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 2)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    (root / "bin/busybox").write_bytes(b"busybox\n")
    (root / "etc").mkdir()
    (root / "etc/os-release").write_bytes(b"ID=pengyos\n")
    (root / "lib/modules/6.1.0/extra").mkdir(parents=True)
    (root / "lib/modules/6.1.0/extra/loop.ko").write_bytes(b"\x00" * 16)
    spec = tmp_path / "initramfs.toml"
    spec.write_text(SPEC, encoding="utf-8")
    return tmp_path, root, spec


def test_build_writes_valid_gzip_cpio(fixture):
    tmp_path, root, spec = fixture
    out = tmp_path / "out" / "init.cpio.gz"
    code, _, err = _run(["build", "--root", str(root), "--spec", str(spec), "--out", str(out)])

    assert code == EXIT_OK, err
    assert out.is_file()
    blob = out.read_bytes()
    assert blob[:2] == b"\x1f\x8b"
    entries = regular_entries(gzip.decompress(blob))
    assert [e["name"] for e in entries] == [
        "bin/busybox",
        "etc/os-release",
        "init",
        "lib/modules/6.1.0/extra/loop.ko",
    ]


def test_build_creates_out_parent_directories(fixture):
    tmp_path, root, spec = fixture
    out = tmp_path / "deep" / "nested" / "dir" / "init.cpio.gz"
    code, _, err = _run(["build", "--root", str(root), "--spec", str(spec), "--out", str(out)])
    assert code == EXIT_OK, err
    assert out.is_file()


def test_build_rerun_is_byte_identical(fixture):
    tmp_path, root, spec = fixture
    out1, out2 = tmp_path / "a.cpio.gz", tmp_path / "b.cpio.gz"
    code1, _, err1 = _run(["build", "--root", str(root), "--spec", str(spec), "--out", str(out1)])
    code2, _, err2 = _run(["build", "--root", str(root), "--spec", str(spec), "--out", str(out2)])
    assert code1 == code2 == EXIT_OK, (err1, err2)
    assert out1.read_bytes() == out2.read_bytes()


def test_build_spec_path_missing_in_root_is_exit_5(fixture):
    tmp_path, root, spec = fixture
    spec.write_text(
        'binaries = ["bin/busybox"]\nfiles = ["etc/os-release", "etc/nope"]\n',
        encoding="utf-8",
    )
    code, _, err = _run(["build", "--root", str(root), "--spec", str(spec), "--out", str(tmp_path / "o.gz")])
    assert code == EXIT_BUILD_FAILED
    assert "build failed" in err
    assert "etc/nope" in err


def test_build_unwritable_out_is_exit_5(fixture):
    tmp_path, root, spec = fixture
    blocker = tmp_path / "file"  # a file, not a dir
    blocker.write_text("x", encoding="utf-8")
    code, _, err = _run(
        ["build", "--root", str(root), "--spec", str(spec), "--out", str(tmp_path / "file" / "o.gz")]
    )
    assert code == EXIT_BUILD_FAILED
    assert "cannot write" in err


def test_build_root_not_a_dir_is_exit_2(fixture):
    tmp_path, _, spec = fixture
    code, _, err = _run(
        ["build", "--root", str(tmp_path / "no-such-dir"), "--spec", str(spec), "--out", "o.gz"]
    )
    assert code == EXIT_USAGE
    assert "root directory not found" in err


def test_build_directories_are_packed(fixture):
    tmp_path, root, spec = fixture
    spec.write_text(
        'files = ["etc/os-release"]\ndirectories = ["etc"]\n',
        encoding="utf-8",
    )
    out = tmp_path / "o.cpio.gz"
    code, _, err = _run(["build", "--root", str(root), "--spec", str(spec), "--out", str(out)])
    assert code == EXIT_OK, err
    entries = regular_entries(gzip.decompress(out.read_bytes()))
    assert [e["name"] for e in entries] == ["etc/", "etc/os-release", "init"]
    by_name = {e["name"]: e for e in entries}
    assert by_name["etc/"]["mode"] == 0o040755


def test_build_directory_missing_in_root_is_exit_5(fixture):
    tmp_path, root, spec = fixture
    spec.write_text(
        'files = ["etc/os-release"]\ndirectories = ["etc/no-such-dir"]\n',
        encoding="utf-8",
    )
    code, _, err = _run(["build", "--root", str(root), "--spec", str(spec), "--out", str(tmp_path / "o.gz")])
    assert code == EXIT_BUILD_FAILED
    assert "build failed" in err
    assert "not a directory" in err


def test_build_invalid_spec_is_exit_4(fixture):
    tmp_path, root, spec = fixture
    spec.write_text("binaries = [\"/etc/passwd\"]\n", encoding="utf-8")
    code, _, err = _run(["build", "--root", str(root), "--spec", str(spec), "--out", "o.gz"])
    assert code == EXIT_INVALID_SPEC
    assert "invalid spec" in err
