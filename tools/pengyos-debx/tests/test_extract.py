"""Tests for :mod:`pengyos_debx.extract`.

Covers: Extract payload to an isolated prefix; record file list + hashes.

The fixtures are built *in-test* with the standard library only — the package
builders live in :mod:`test_formats` and are reused here so the extractor is
exercised on real byte-for-byte ``.deb``/``.rpm`` layouts, not synthetic ones.

The security invariants under test:

* maintainer scripts are never executed (a poison ``postinst`` that would create
  a marker file proves it did not run),
* a payload path that escapes the prefix is refused, and
* extraction never shells out to an external process.
"""

import hashlib
import io
import os
import struct
import tarfile
from pathlib import Path

import pytest

from pengyos_debx import cli
from pengyos_debx.extract import (
    ExtractResult,
    ExtractedEntry,
    PathEscapeError,
    extract_package,
    extract_path,
)
from pengyos_debx.formats import DebPackage, classify

# Reuse the byte-exact package builders from test_formats.
from test_formats import ELF_SO, make_deb, make_rpm  # noqa: E402


# --- helpers -----------------------------------------------------------------


def _ar_member(name: str, data: bytes) -> bytes:
    """Build one ``ar`` member (BSD variant, as ``.deb`` files use)."""

    def _pad(field: bytes, width: int) -> bytes:
        return field + b" " * (width - len(field))

    header = struct.pack(
        "<16s12s6s6s8s10s2s",
        _pad(name.encode("ascii"), 16),
        _pad(b"0", 12),
        _pad(b"0", 6),
        _pad(b"0", 6),
        _pad(b"0", 8),
        _pad(str(len(data)).encode("ascii"), 10),
        b"`\n",
    )
    return header + data + (b"\n" if len(data) & 1 else b"")


def _tar_members(
    payload: dict[str, bytes],
    *,
    symlinks: dict[str, str] | None = None,
    dirs: set[str] | None = None,
):
    """Build a ``data.tar`` blob.

    Members are added in three passes so their kinds don't collide: regular
    files from ``payload``, directories from ``dirs``, then symlinks from
    ``symlinks`` (name -> link target). Names are kept absolute (leading
    ``/``), as real debs do.
    """
    symlinks = symlinks or {}
    dirs = dirs or set()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in payload.items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.REGTYPE
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name in dirs:
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tar.addfile(info)
        for name, target in symlinks.items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            info.size = 0
            tar.addfile(info)
    return buf.getvalue()


def _deb_from_payload(
    payload: dict[str, bytes],
    *,
    control: dict[str, bytes] | None = None,
    symlinks: dict[str, str] | None = None,
    dirs: set[str] | None = None,
    tmp_path: Path,
    name: str = "pkg.deb",
) -> Path:
    """Assemble a gzip-compressed ``.deb`` (the common real-world case) from
    ``payload`` into ``tmp_path``."""
    import gzip

    data_blob = gzip.compress(_tar_members(payload, symlinks=symlinks, dirs=dirs))
    control_blob = gzip.compress(_tar_members(control or {"control": b"Package: x\n"}))
    ar = b"!<arch>\n"
    ar += _ar_member("debian-binary", b"2.0\n")
    ar += _ar_member("data.tar", data_blob)
    ar += _ar_member("control.tar", control_blob)
    p = tmp_path / name
    p.write_bytes(ar)
    return p


def _default_control() -> dict[str, bytes]:
    return {
        "control": b"Package: foo\nVersion: 1.0\nArchitecture: amd64\n",
        "postinst": b"#!/bin/sh\ntouch MARKER\n",
        "postrm": b"#!/bin/sh\necho bye\n",
    }


# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def deb_path(tmp_path: Path) -> Path:
    return _deb_from_payload(
        {
            "/usr/lib/libfoo.so.1": ELF_SO,
            "/usr/share/doc/foo/README": b"hello\n",
            "/etc/foo.conf": b"enabled = 1\n",
        },
        control=_default_control(),
        tmp_path=tmp_path,
    )


@pytest.fixture
def rpm_path(tmp_path: Path) -> Path:
    p = tmp_path / "testpkg-1.0-1.x86_64.rpm"
    p.write_bytes(
        make_rpm(
            data={
                "/usr/lib/libbar.so.2": ELF_SO,
                "/usr/bin/foo": b"#!/bin/sh\necho hi\n",
            },
            header={"name": "testpkg", "version": "1.0", "release": "1"},
        )
    )
    return p


# --- extraction: deb ---------------------------------------------------------


def test_extract_deb_writes_expected_tree(deb_path, tmp_path):
    into = tmp_path / "rootfs"
    result = extract_path(str(deb_path), str(into))

    assert (into / "usr/lib/libfoo.so.1").read_bytes() == ELF_SO
    assert (into / "usr/share/doc/foo/README").read_bytes() == b"hello\n"
    assert (into / "etc/foo.conf").read_bytes() == b"enabled = 1\n"
    # The prefix was created from scratch.
    assert into.is_dir()
    assert len(result.files) == 3


def test_extract_creates_prefix_if_missing(deb_path, tmp_path):
    into = tmp_path / "does" / "not" / "exist" / "yet"
    assert not into.exists()
    extract_path(str(deb_path), str(into))
    assert into.is_dir()
    assert (into / "usr/lib/libfoo.so.1").is_file()


def test_extract_deb_manifest_hashes_match_contents(deb_path, tmp_path):
    into = tmp_path / "rootfs"
    result = extract_path(str(deb_path), str(into))

    manifest = result.manifest()
    assert set(manifest) == {
        "/usr/lib/libfoo.so.1",
        "/usr/share/doc/foo/README",
        "/etc/foo.conf",
    }
    # The recorded SHA-256 equals the digest of the bytes on disk.
    for name, digest in manifest.items():
        on_disk = (into / name.lstrip("/")).read_bytes()
        assert digest == hashlib.sha256(on_disk).hexdigest()


def test_extract_records_sizes(deb_path, tmp_path):
    into = tmp_path / "rootfs"
    result = extract_path(str(deb_path), str(into))
    by_name = {e.name: e for e in result.entries}
    assert by_name["/usr/lib/libfoo.so.1"].size == len(ELF_SO)
    assert by_name["/usr/share/doc/foo/README"].size == len(b"hello\n")


def test_extract_result_summary(deb_path, tmp_path):
    into = tmp_path / "rootfs"
    result = extract_path(str(deb_path), str(into))
    summary = result.summary()
    assert "3 file(s)" in summary
    assert "0 symlink(s)" in summary
    assert str(into) in summary


# --- extraction: rpm ---------------------------------------------------------


def test_extract_rpm_writes_expected_tree(rpm_path, tmp_path):
    into = tmp_path / "rootfs"
    result = extract_path(str(rpm_path), str(into))
    assert (into / "usr/lib/libbar.so.2").read_bytes() == ELF_SO
    assert (into / "usr/bin/foo").read_bytes() == b"#!/bin/sh\necho hi\n"
    assert len(result.files) == 2


# --- directories and symlinks ------------------------------------------------


def test_extract_recreates_directories(tmp_path):
    payload = {"/usr/lib/app/bin/program": b"ELF-ish\x00"}
    dirs = {"/usr/lib", "/usr/lib/app/bin"}
    p = _deb_from_payload(
        payload,
        control={"control": b"Package: d\n"},
        dirs=dirs,
        tmp_path=tmp_path,
    )
    into = tmp_path / "rootfs"
    result = extract_path(str(p), str(into))

    assert (into / "usr" / "lib").is_dir()
    assert (into / "usr" / "lib" / "app" / "bin").is_dir()
    assert (into / "usr" / "lib" / "app" / "bin" / "program").is_file()
    kinds = {e.name: e.kind for e in result.entries}
    assert kinds["/usr/lib"] == "dir"
    assert kinds["/usr/lib/app/bin"] == "dir"
    assert kinds["/usr/lib/app/bin/program"] == "file"


def test_extract_recreates_symlinks(tmp_path):
    payload = {"/usr/lib/libfoo.so.1": ELF_SO}
    symlinks = {"/usr/lib/libfoo.so": "libfoo.so.1"}
    p = _deb_from_payload(
        payload, control={"control": b"Package: s\n"}, symlinks=symlinks, tmp_path=tmp_path
    )
    into = tmp_path / "rootfs"
    result = extract_path(str(p), str(into))

    link = into / "usr" / "lib" / "libfoo.so"
    assert link.is_symlink()
    assert os.readlink(link) == "libfoo.so.1"
    entry = next(e for e in result.entries if e.name == "/usr/lib/libfoo.so")
    assert entry.kind == "symlink"
    assert entry.link_target == "libfoo.so.1"
    assert entry.sha256 is None


# --- path traversal guard ----------------------------------------------------


def test_extract_refuses_dotdot_traversal(tmp_path):
    p = _deb_from_payload(
        {"/etc/../evil.txt": b"pwned"},
        control={"control": b"Package: t\n"},
        tmp_path=tmp_path,
    )
    into = tmp_path / "rootfs"
    with pytest.raises(PathEscapeError, match="escapes"):
        extract_path(str(p), str(into))


def test_extract_refuses_deep_traversal(tmp_path):
    p = _deb_from_payload(
        {"/a/b/../../c/../../etc/passwd": b"root:x:0:0"},
        control={"control": b"Package: t\n"},
        tmp_path=tmp_path,
    )
    into = tmp_path / "rootfs"
    with pytest.raises(PathEscapeError, match="escapes"):
        extract_path(str(p), str(into))


def test_extract_accepts_normal_absolute_payload_paths(tmp_path):
    # Payload paths are absolute (leading /); these must extract under the
    # prefix, not be mistaken for escapes.
    p = _deb_from_payload(
        {"/usr/lib/normal.so": b"data"},
        control={"control": b"Package: t\n"},
        tmp_path=tmp_path,
    )
    into = tmp_path / "rootfs"
    result = extract_path(str(p), str(into))
    assert (into / "usr" / "lib" / "normal.so").is_file()
    assert len(result.files) == 1


# --- conflict handling -------------------------------------------------------


def test_extract_conflict_fail(deb_path, tmp_path):
    into = tmp_path / "rootfs"
    (into / "usr" / "lib").mkdir(parents=True)
    # libfoo.so.1 already exists from a prior extraction.
    (into / "usr" / "lib" / "libfoo.so.1").write_bytes(b"pre-existing")

    pkg = DebPackage(str(deb_path))
    with pytest.raises(FileExistsError):
        extract_package(pkg, str(into), on_conflict="fail")


def test_extract_conflict_replace(deb_path, tmp_path):
    p = _deb_from_payload(
        {"/usr/lib/libfoo.so.1": b"fresh-bytes"},
        control={"control": b"Package: r\n"},
        tmp_path=tmp_path,
    )
    pkg = DebPackage(str(p))

    into = tmp_path / "rootfs"
    (into / "usr" / "lib").mkdir(parents=True)
    target = into / "usr" / "lib" / "libfoo.so.1"
    target.write_bytes(b"stale")

    result = extract_package(pkg, str(into), on_conflict="replace")
    assert target.read_bytes() == b"fresh-bytes"
    entry = next(e for e in result.entries if e.name == "/usr/lib/libfoo.so.1")
    assert entry.sha256 == hashlib.sha256(b"fresh-bytes").hexdigest()


def test_extract_conflict_replace_removes_stale_symlink(tmp_path):
    """on_conflict='replace' turns an existing on-disk symlink into the payload's
    regular file (rather than leaving a dangling link)."""
    payload = {"/usr/lib/libfoo.so": b"regular-file-bytes"}
    p = _deb_from_payload(
        payload,
        control={"control": b"Package: r\n"},
        tmp_path=tmp_path,
    )
    pkg = DebPackage(str(p))

    into = tmp_path / "rootfs"
    (into / "usr" / "lib").mkdir(parents=True)
    link = into / "usr" / "lib" / "libfoo.so"
    os.symlink("nowhere", link)
    assert link.is_symlink()

    extract_package(pkg, str(into), on_conflict="replace")
    assert not link.is_symlink()
    assert link.is_file()
    assert link.read_bytes() == b"regular-file-bytes"


# --- never executes maintainer scripts ---------------------------------------


def test_extract_never_executes_postinst(deb_path, tmp_path):
    """The payload carries a poison ``postinst`` that would create MARKER if
    executed; a successful extract must leave MARKER absent."""
    marker = tmp_path / "MARKER"
    control = {
        "control": b"Package: foo\n",
        "postinst": f"#!/bin/sh\ntouch {marker}\n".encode(),
    }
    p = _deb_from_payload(
        {
            "/usr/lib/libfoo.so.1": ELF_SO,
            "/usr/share/doc/foo/README": b"hello\n",
            "/etc/foo.conf": b"enabled = 1\n",
        },
        control=control,
        tmp_path=tmp_path,
        name="poison.deb",
    )

    into = tmp_path / "rootfs"
    extract_path(str(p), str(into))

    assert not marker.exists(), "maintainer script was executed during extract"


# --- never shells out --------------------------------------------------------


def test_extract_does_not_shell_out(monkeypatch, deb_path, tmp_path):
    import subprocess as _subprocess

    def _raise(*a, **k):
        raise AssertionError("extract must not invoke an external process")

    monkeypatch.setattr(_subprocess, "run", _raise)
    # A gzip payload: extract must not call subprocess.run at all.
    extract_path(str(deb_path), str(tmp_path / "rootfs"))


# --- CLI ---------------------------------------------------------------------


def test_cli_extract_writes_files(deb_path, tmp_path, capsys):
    into = tmp_path / "rootfs"
    rc = cli.main(["extract", str(deb_path), "--into", str(into)])
    assert rc == cli.EXIT_OK
    assert (into / "usr/lib/libfoo.so.1").read_bytes() == ELF_SO
    out = capsys.readouterr().out
    assert "3 file(s)" in out


def test_cli_extract_path_escape_exit_code(tmp_path):
    p = _deb_from_payload(
        {"/etc/../evil.txt": b"pwned"},
        control={"control": b"Package: t\n"},
        tmp_path=tmp_path,
    )
    into = tmp_path / "rootfs"
    rc = cli.main(["extract", str(p), "--into", str(into)])
    assert rc == cli.EXIT_PATH_ESCAPE


def test_cli_extract_never_runs_scripts(tmp_path):
    marker = tmp_path / "MARKER"
    control = {
        "control": b"Package: foo\n",
        "postinst": f"#!/bin/sh\ntouch {marker}\n".encode(),
    }
    p = _deb_from_payload(
        {"/usr/lib/libfoo.so.1": ELF_SO},
        control=control,
        tmp_path=tmp_path,
        name="poison.deb",
    )

    into = tmp_path / "rootfs"
    rc = cli.main(["extract", str(p), "--into", str(into)])
    assert rc == cli.EXIT_OK
    assert not marker.exists()


def test_cli_extract_creates_missing_prefix(deb_path, tmp_path):
    into = tmp_path / "fresh" / "prefix"
    assert not into.exists()
    rc = cli.main(["extract", str(deb_path), "--into", str(into)])
    assert rc == cli.EXIT_OK
    assert into.is_dir()


# --- type sanity -------------------------------------------------------------


def test_extracted_entry_fields():
    e = ExtractedEntry("/a", "file", 10, "abc123")
    assert e.name == "/a"
    assert e.kind == "file"
    assert e.size == 10
    assert e.sha256 == "abc123"
    assert e.link_target is None


def test_extract_path_reads_via_classify(deb_path, tmp_path):
    """extract_path dispatches on the file magic (classify) before reading."""
    assert classify(str(deb_path)) == "deb"
    into = tmp_path / "rootfs"
    result = extract_path(str(deb_path), str(into))
    assert isinstance(result, ExtractResult)
    assert all(isinstance(e, ExtractedEntry) for e in result.entries)
