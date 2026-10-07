"""Tests for spec validation integrated into the ``build`` CLI path."""

from __future__ import annotations

import contextlib
import gzip
import io
from pathlib import Path

from pengyos_initramfs.cli import (
    EXIT_INVALID_SPEC,
    EXIT_OK,
    main,
)
from cpio_reader import regular_entries


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 2)
    return code, out.getvalue(), err.getvalue()


def _build(root: Path, spec: Path, out: Path):
    return _run(["build", "--root", str(root), "--spec", str(spec), "--out", str(out)])


def test_build_valid_spec_builds_archive(tmp_path):
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    (root / "bin/busybox").write_bytes(b"busybox\n")
    (root / "etc").mkdir()
    (root / "etc/os-release").write_bytes(b"ID=pengyos\n")
    spec = tmp_path / "initramfs.toml"
    spec.write_text(
        "binaries = [\"bin/busybox\"]\n" "files = [\"etc/os-release\"]\n",
        encoding="utf-8",
    )
    out = tmp_path / "init.cpio.gz"
    code, _, err = _build(root, spec, out)
    assert code == EXIT_OK, err
    entries = regular_entries(gzip.decompress(out.read_bytes()))
    assert [e["name"] for e in entries] == ["bin/busybox", "etc/os-release", "init"]


def test_build_empty_spec_builds_generated_init_only(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    spec = tmp_path / "initramfs.toml"
    spec.write_text("# just a comment\n", encoding="utf-8")
    out = tmp_path / "init.cpio.gz"
    code, _, err = _build(root, spec, out)
    assert code == EXIT_OK, err
    entries = regular_entries(gzip.decompress(out.read_bytes()))
    assert [e["name"] for e in entries] == ["init"]


def test_build_invalid_spec_path(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    spec = tmp_path / "initramfs.toml"
    spec.write_text("binaries = [\"../etc/passwd\"]\n", encoding="utf-8")
    code, _, err = _build(root, spec, tmp_path / "init.cpio.gz")
    assert code == EXIT_INVALID_SPEC
    assert "invalid spec" in err


def test_build_broken_toml(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    spec = tmp_path / "initramfs.toml"
    spec.write_text("binaries = [", encoding="utf-8")
    code, _, err = _build(root, spec, tmp_path / "init.cpio.gz")
    assert code == EXIT_INVALID_SPEC
    assert "invalid spec" in err
