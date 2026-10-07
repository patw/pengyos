"""Tests for :mod:`pengyos_debx.formats`.

Covers: Implement deb reading (ar + tar) and rpm reading (cpio/zstd) using
stdlib-ish parsing; never invoke dpkg/rpm.

The fixtures are built *in-test* with the Python standard library only
(``struct`` / ``io`` / ``tarfile`` / ``gzip``) — no external ``ar``/``cpio``/
``dpkg-deb``/``rpm`` binaries are invoked, and the code under test is never
allowed to shell out to them either (see ``test_does_not_shell_out``).

A ``.deb`` is an ``ar`` archive holding ``debian-binary``, ``control.tar.*`` and
``data.tar.*``. A ``.rpm`` is a 96-byte lead followed by a ``cpio newc`` archive
holding an RPM header blob plus a ``payload.tar.*`` member. Both containers are
reproduced byte-for-byte here so the pure-stdlib parsers are exercised on real
byte layouts, not synthetic ones.
"""

import gzip
import io
import struct
import tarfile
from pathlib import Path
from typing import Optional

import pytest

from pengyos_debx import formats
from pengyos_debx.formats import (
    ArReader,
    CpioReader,
    DebPackage,
    PackageFormatError,
    RpmPackage,
    classify,
    decompress,
    detect_compression,
)

# A minimal, recognisable ELF header (the 7-byte DT_NEEDED marker is irrelevant
# to the container tests; we only need non-trivial binary payload bytes).
ELF_SO = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 60
PLAIN_TEXT = b"key = value\n"


# --- ar (deb) builders -------------------------------------------------------


def _ar_pad(field: bytes, width: int) -> bytes:
    assert len(field) <= width
    return field + b" " * (width - len(field))


def _ar_member(name: str, data: bytes, *, gnu_terminate: bool = False) -> bytes:
    """Build one ``ar`` member.

    ``gnu_terminate`` reproduces the GNU ``ar`` variant (name terminated by a
    trailing ``/``); the default is the BSD variant that ``.deb`` files use
    (name space-padded). The parser must accept both.
    """
    raw = name.encode("ascii")
    if gnu_terminate:
        raw = raw + b"/"
    header = struct.pack(
        "<16s12s6s6s8s10s2s",
        _ar_pad(raw, 16),
        _ar_pad(b"0", 12),
        _ar_pad(b"0", 6),
        _ar_pad(b"0", 6),
        _ar_pad(b"0", 8),
        _ar_pad(str(len(data)).encode("ascii"), 10),
        b"`\n",
    )
    return header + data + (b"\n" if len(data) & 1 else b"")


def _tar_gz_bytes(members: dict[str, bytes]) -> bytes:
    """Return a ``gzip``-compressed ``tar`` blob for ``{path: bytes}``."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for path, data in members.items():
                info = tarfile.TarInfo(path)
                info.size = len(data)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def make_deb(
    *,
    data: dict[str, bytes],
    control: dict[str, bytes],
    compression: str = "gz",
    gnu_terminate: bool = False,
) -> bytes:
    """Assemble a minimal but valid ``.deb`` byte sequence."""
    ext = {"gz": "gz", "bz2": "bz2", "xz": "xz", "raw": "", "zstd": "zstd"}[compression]
    data_member = f"data.tar.{ext}" if ext else "data.tar"
    control_member = f"control.tar.{ext}" if ext else "control.tar"

    data_blob = _tar_bytes(data, compression)
    control_blob = _tar_bytes(control, compression)

    ar = b"!<arch>\n"
    ar += _ar_member("debian-binary", b"2.0\n", gnu_terminate=gnu_terminate)
    ar += _ar_member(data_member, data_blob, gnu_terminate=gnu_terminate)
    ar += _ar_member(control_member, control_blob, gnu_terminate=gnu_terminate)
    return ar


def _tar_plain_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path, data in members.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _zstd_cli() -> Optional[str]:
    """Locate the ``zstd`` CLI on ``PATH`` (used only for zstd fixtures)."""
    import os

    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = os.path.join(directory, "zstd")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _tar_bytes(members: dict[str, bytes], compression: str = "gz") -> bytes:
    """Build a tar blob compressed with ``compression`` (gz/bz2/xz/raw/zstd).

    gzip/bz2/xz/raw use the standard library; zstd shells out to the ``zstd``
    CLI (skipped if unavailable). This mirrors what real debs/rpms ship.
    """
    import bz2
    import lzma
    import subprocess

    raw = _tar_plain_bytes(members)
    if compression == "gz":
        return gzip.compress(raw)
    if compression == "bz2":
        return bz2.compress(raw)
    if compression == "xz":
        return lzma.compress(raw)
    if compression == "raw":
        return raw
    if compression == "zstd":
        zstd_bin = _zstd_cli()
        if zstd_bin is None:
            pytest.skip("zstd CLI not available for fixture")
        proc = subprocess.run(
            [zstd_bin, "-q", "-f", "-c"],
            input=raw,
            stdout=subprocess.PIPE,
            check=True,
        )
        return proc.stdout
    raise AssertionError(compression)


# --- cpio (rpm) builders -----------------------------------------------------


def _cpio_newc_member(name: bytes, data: bytes, kind: int = 0x81FF) -> bytes:
    """Build one ``cpio newc`` member.

    The newc header is a fixed **110 bytes**: a 6-byte ``070701`` magic followed
    by fourteen **8**-byte hex fields, with the name at offset 110. This matches
    busybox's ``cpio.c`` printf format and GNU ``cpio -H newc`` output (verified
    by round-tripping through the real ``cpio`` tool). The fields are contiguous
    hex digits with **no** NUL separators. ``kind`` is the ``st_mode`` file-type
    nibble; the mode field is ``kind | 0644``. ``namesize`` is ``strlen(name)+1``
    (it counts the trailing NUL), and the checksum is always ``00000000``.
    """
    name_b = name if isinstance(name, bytes) else name.encode("ascii")
    mode = kind | 0o644
    header = (
        b"070701"
        + b"%08x" % 0               # ino
        + b"%08x" % mode            # mode
        + b"%08x" % 0               # uid
        + b"%08x" % 0               # gid
        + b"%08x" % 1               # nlink
        + b"%08x" % 0               # mtime
        + b"%08x" % len(data)       # filesize
        + b"%08x" % 0               # devmajor
        + b"%08x" % 0               # devminor
        + b"%08x" % 0               # rdevmajor
        + b"%08x" % 0               # rdevminor
        + b"%08x" % (len(name_b) + 1)  # namesize (strlen+1, incl. trailing NUL)
        + b"00000000"               # checksum (always 0 for newc)
        + name_b
        + b"\x00"
    )
    body = header + data
    pad = (-len(body)) % 4
    return body + b"\x00" * pad


def _rpm_header_blob(
    members: dict[str, str],
    scripts: dict[int, bytes] | None = None,
) -> bytes:
    """Build a minimal RPM header blob.

    Layout (little-endian, matching ``formats.RpmPackage._read_header``): a
    32-byte lead, then three parallel arrays of ``count`` int32s — the tag
    numbers, their data offsets, and their data sizes — followed by the
    concatenated tag data. String tags are NUL-terminated; script tags are raw
    ``<interpreter><sep><body>`` blobs of the given ``size``.

    ``scripts`` maps an RPM script tag number (see rpm.org ``rpmtag.h``:
    PREIN=1023 POSTIN=1024 PREUN=1025 POSTUN=1026) to its raw data. Only the
    tags present in ``members``/``scripts`` are emitted, so every array has
    exactly ``count`` entries.
    """
    scripts = scripts or {}
    # Only the tags present in ``members`` are emitted, so the header's
    # ``count`` equals the number of tag entries actually written.
    present = [(key, tag) for key, tag in
               [("name", 1005), ("version", 1006), ("release", 1002), ("summary", 1003)]
               if key in members]
    # Script tags follow the string tags; sorted so the layout is stable.
    for tag in sorted(scripts):
        present.append((f"_script_{tag}", tag))
    count = len(present)
    strings = b""
    offsets = []
    sizes = []
    for key, tag in present:
        data = scripts[tag] if tag in scripts else members[key].encode("utf-8") + b"\x00"
        # string/script data starts after the 32-byte lead + tag array +
        # offset array + size array (each ``count`` int32s).
        offsets.append(32 + 4 * count + 4 * count + 4 * count + len(strings))
        sizes.append(len(data))
        strings += data
    blob = b"\x00\x00\x00\x00"              # 4-byte magic (unchecked by parser)
    blob += struct.pack("<i", count)         # count
    blob += b"\x00" * 24                     # reserved (skipped -> off 32)
    for _key, tag in present:                # tag number array (count entries)
        blob += struct.pack("<i", tag)
    for off in offsets:                      # data offset array (count entries)
        blob += struct.pack("<i", off)
    for sz in sizes:                         # data size array (count entries)
        blob += struct.pack("<i", sz)
    blob += strings
    return blob


def make_rpm(
    *,
    data: dict[str, bytes],
    header: dict[str, str] | None = None,
    scripts: dict[int, bytes] | None = None,
    compression: str = "gz",
) -> bytes:
    """Assemble a minimal but valid ``.rpm`` byte sequence.

    The payload member is named ``payload.tar.<ext>`` and compressed with
    ``compression`` (gz/bz2/xz/raw/zstd). Real rpms ship gzip/xz/zstd payloads.

    ``scripts`` maps RPM maintainer-script tag numbers (see rpm.org
    ``rpmtag.h``: PREIN=1023 POSTIN=1024 PREUN=1025 POSTUN=1026) to raw script
    data, so the header carries maintainer scripts for listing.
    """
    ext = {"gz": "gz", "bz2": "bz2", "xz": "xz", "raw": "gz", "zstd": "zstd"}[compression]
    payload = _tar_bytes(data, compression)
    cpio = b""
    if header is not None:
        cpio += _cpio_newc_member(b"\x00", _rpm_header_blob(header, scripts=scripts))
    cpio += _cpio_newc_member(b"payload.tar." + ext.encode(), payload)
    cpio += _cpio_newc_member(b"TRAILER!!!", b"")
    lead = bytearray(96)
    lead[0:2] = b"\xab\xcd"
    lead[2:4] = b"\x04\x00"  # version 4.0
    lead[4:8] = b"\xab\xcd\xef\x96"  # little-endian 0x96efcdab
    lead[8:10] = b"\x00\x00"  # arch: none
    lead[10:12] = b"\x01\x00"  # os: linux
    lead[88:92] = struct.pack("<i", len(cpio))  # header size
    return bytes(lead) + cpio


# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def deb_path(tmp_path: Path) -> Path:
    p = tmp_path / "testpkg_1.0_amd64.deb"
    p.write_bytes(
        make_deb(
            data={
                "/usr/lib/libfoo.so.1": ELF_SO,
                "/usr/share/doc/foo/README": PLAIN_TEXT,
                "/etc/foo.conf": b"enabled = 1\n",
            },
            control={
                "control": b"Package: foo\nVersion: 1.0\nArchitecture: amd64\n",
                "postinst": b"#!/bin/sh\necho hi\n",
                "postrm": b"#!/bin/sh\necho bye\n",
            },
        )
    )
    return p


@pytest.fixture
def gnu_terminated_deb(tmp_path: Path) -> Path:
    """A deb whose ar names use the GNU ``/`` terminator variant."""
    p = tmp_path / "gnu.deb"
    p.write_bytes(
        make_deb(
            data={"/usr/lib/libfoo.so.1": ELF_SO},
            control={"control": b"Package: foo\n"},
            gnu_terminate=True,
        )
    )
    return p


@pytest.fixture
def rpm_path(tmp_path: Path) -> Path:
    p = tmp_path / "testpkg-1.0-1.x86_64.rpm"
    p.write_bytes(
        make_rpm(
            data={
                "/usr/lib/libbar.so.2": ELF_SO,
                "/usr/bin/foo": b"#!/bin/sh\n",
            },
            header={"name": "testpkg", "version": "1.0", "release": "1", "summary": "A test package"},
        )
    )
    return p


# --- classify ----------------------------------------------------------------


def test_classify_deb(deb_path):
    assert classify(str(deb_path)) == "deb"


def test_classify_rpm(rpm_path):
    assert classify(str(rpm_path)) == "rpm"


def test_classify_none_for_garbage(tmp_path):
    p = tmp_path / "not-a-pkg"
    p.write_bytes(b"this is certainly not a deb or rpm archive")
    assert classify(str(p)) is None


def test_classify_missing_file_returns_none():
    assert classify("/nope/does/not/exist.deb") is None


def test_classify_uses_only_leading_bytes(tmp_path):
    """classify must not raise on a truncated file: it reads a 1 KiB head."""
    p = tmp_path / "short.deb"
    p.write_bytes(b"!<arch>\npartial")  # valid ar magic, truncated body
    assert classify(str(p)) == "deb"


# --- ar reader ---------------------------------------------------------------


def test_ar_reader_strips_space_padded_names(deb_path):
    """Real debs space-pad member names; the parser must strip them."""
    ar = ArReader(deb_path.read_bytes())
    names = {m.name for m in ar.members}
    assert "debian-binary" in names
    assert "data.tar.gz" in names
    assert "control.tar.gz" in names


def test_ar_reader_gnu_terminator(gnu_terminated_deb):
    ar = ArReader(gnu_terminated_deb.read_bytes())
    names = {m.name for m in ar.members}
    assert names == {"debian-binary", "data.tar.gz", "control.tar.gz"}


def test_ar_reader_get_by_name(deb_path):
    ar = ArReader(deb_path.read_bytes())
    debian = ar.get("debian-binary")
    assert debian is not None
    assert debian.data == b"2.0\n"


def test_ar_reader_bad_magic():
    with pytest.raises(PackageFormatError):
        ArReader(b"NOTARarchive\x00\x00")


def test_ar_reader_truncated(tmp_path):
    p = tmp_path / "trunc.deb"
    p.write_bytes(b"!<arch>\n\x00" * 3)  # magic + partial header
    with pytest.raises(PackageFormatError):
        ArReader(p.read_bytes())


# --- deb package -------------------------------------------------------------


def test_deb_payload_extracted(deb_path):
    pkg = DebPackage(str(deb_path))
    files = dict(pkg.payload.files())
    assert "/usr/lib/libfoo.so.1" in files
    assert files["/usr/lib/libfoo.so.1"] == ELF_SO
    assert files["/etc/foo.conf"] == b"enabled = 1\n"


def test_deb_control_parsed(deb_path):
    pkg = DebPackage(str(deb_path))
    assert pkg.control["Package"] == "foo"
    assert pkg.control["Version"] == "1.0"
    assert pkg.control["Architecture"] == "amd64"


def test_deb_scripts_listed_not_run(deb_path):
    """Maintainer scripts are listed, never executed."""
    pkg = DebPackage(str(deb_path))
    scripts = sorted(pkg.control["_scripts"])
    assert scripts == ["./postinst", "./postrm"]


def test_deb_gnu_terminated_payload(gnu_terminated_deb):
    pkg = DebPackage(str(gnu_terminated_deb))
    files = dict(pkg.payload.files())
    assert files["/usr/lib/libfoo.so.1"] == ELF_SO


def test_deb_missing_debian_binary(tmp_path):
    # ar archive without a debian-binary member.
    ar = b"!<arch>\n"
    ar += _ar_member("data.tar.gz", _tar_gz_bytes({"/a": b"b"}))
    p = tmp_path / "nodeb.deb"
    p.write_bytes(ar)
    with pytest.raises(PackageFormatError, match="debian-binary"):
        DebPackage(str(p))


def test_deb_missing_data_member(tmp_path):
    ar = b"!<arch>\n"
    ar += _ar_member("debian-binary", b"2.0\n")
    p = tmp_path / "nodata.deb"
    p.write_bytes(ar)
    with pytest.raises(PackageFormatError, match="data payload"):
        DebPackage(str(p))


def test_deb_rejects_non_deb(tmp_path):
    p = tmp_path / "fake.deb"
    p.write_bytes(b"definitely not an ar archive")
    with pytest.raises(PackageFormatError):
        DebPackage(str(p))


def test_deb_control_handles_slash_prefix(tmp_path):
    """Real debs carry ``./control`` / ``./postinst`` (leading ``./``) inside
    the control tar. The parser must read control fields and list scripts for
    that layout, not just the bare ``control`` name."""
    control = {
        "./control": b"Package: foo\nVersion: 1.0\nArchitecture: amd64\n",
        "./postinst": b"#!/bin/sh\necho hi\n",
        "./postrm": b"#!/bin/sh\necho bye\n",
    }
    p = tmp_path / "prefix.deb"
    p.write_bytes(make_deb(data={"/usr/lib/libfoo.so.1": ELF_SO}, control=control))
    pkg = DebPackage(str(p))
    assert pkg.control["Package"] == "foo"
    assert pkg.control["Version"] == "1.0"
    assert sorted(pkg.control["_scripts"]) == ["./postinst", "./postrm"]


def test_deb_control_nested_path_ignored(tmp_path):
    """A control member nested in a subdirectory (e.g. ``./scripts/foo``) must
    not be mistaken for a top-level control file or maintainer script."""
    control = {
        "./control": b"Package: foo\n",
        "./sub/dir/nested": b"not a script\n",
    }
    p = tmp_path / "nested.deb"
    p.write_bytes(make_deb(data={"/a": b"b"}, control=control))
    pkg = DebPackage(str(p))
    assert pkg.control["Package"] == "foo"
    assert pkg.control["_scripts"] == []


# --- cpio reader -------------------------------------------------------------


def test_cpio_reader_parses_members(rpm_path):
    body = rpm_path.read_bytes()[96:]
    members = {m.name for m in CpioReader(body).members}
    assert "payload.tar.gz" in members
    assert "TRAILER!!!" in members


# --- rpm package -------------------------------------------------------------


def test_rpm_payload_extracted(rpm_path):
    pkg = RpmPackage(str(rpm_path))
    files = dict(pkg.payload.files())
    assert "/usr/lib/libbar.so.2" in files
    assert files["/usr/lib/libbar.so.2"] == ELF_SO
    assert files["/usr/bin/foo"] == b"#!/bin/sh\n"


def test_rpm_header_parsed(rpm_path):
    pkg = RpmPackage(str(rpm_path))
    assert pkg.header["name"] == "testpkg"
    assert pkg.header["version"] == "1.0"
    assert pkg.header["release"] == "1"
    assert pkg.header["summary"] == "A test package"


def test_rpm_bad_lead_magic(tmp_path):
    p = tmp_path / "bad.rpm"
    p.write_bytes(b"\x00" * 96 + b"garbage")
    with pytest.raises(PackageFormatError, match="lead magic"):
        RpmPackage(str(p))


def test_rpm_too_small(tmp_path):
    p = tmp_path / "tiny.rpm"
    p.write_bytes(b"\xab\xcd")
    with pytest.raises(PackageFormatError, match="too small"):
        RpmPackage(str(p))


def test_rpm_missing_payload(tmp_path):
    lead = bytearray(96)
    lead[4:8] = b"\xab\xcd\xef\x96"
    # cpio with only a header, no payload.tar.*
    cpio = _cpio_newc_member(b"\x00", b"\x00" * 40)
    cpio += _cpio_newc_member(b"TRAILER!!!", b"")
    p = tmp_path / "norpayload.rpm"
    p.write_bytes(bytes(lead) + cpio)
    with pytest.raises(PackageFormatError, match="payload"):
        RpmPackage(str(p))


# --- compression helpers -----------------------------------------------------


def test_detect_compression():
    assert detect_compression(gzip.compress(b"hi")) == "gzip"
    assert detect_compression(b"no compression here") == "raw"


def test_decompress_gzip_roundtrip():
    blob = gzip.compress(b"round trip")
    assert decompress(blob, "gzip") == b"round trip"


def test_decompress_unrecognised_raises():
    with pytest.raises(formats.UnsupportedCompression):
        decompress(b"\x00\x00\x00\x00", "raw")


# --- zstd --------------------------------------------------------------------


def test_zstd_magic_is_little_endian_0xfd2fb528():
    """The on-disk zstd frame magic is ``0xFD2FB528`` little-endian, i.e.
    ``28 B5 2F FD`` (see ``zstd.h`` ``ZSTD_MAGICNUMBER``). Real debs/rpms on
    modern Debian/Ubuntu ship zstd payloads with exactly this magic."""
    assert formats._ZSTD_MAGIC == b"\x28\xb5\x2f\xfd"
    assert detect_compression(b"\x28\xb5\x2f\xfd" + b"\x00" * 16) == "zstd"


def test_decompress_zstd_roundtrip():
    """A round-trip through the real ``zstd`` CLI (skipped if the CLI is not
    installed). This pins the decompressor to a genuine zstd frame, not a
    hand-faked magic."""
    zstd_bin = _zstd_cli()
    if zstd_bin is None:
        pytest.skip("zstd CLI not available")
    import subprocess

    original = b"zstd round trip payload " * 8
    proc = subprocess.run(
        [zstd_bin, "-q", "-f", "-c"], input=original, stdout=subprocess.PIPE, check=True
    )
    assert decompress(proc.stdout, "zstd") == original


def test_deb_zstd_payload_roundtrip(tmp_path):
    """A real zstd-compressed ``.deb`` (built via the zstd CLI) extracts its
    payload. This is the bug the on-disk zstd magic fix exists to cover."""
    p = tmp_path / "zstd.deb"
    p.write_bytes(
        make_deb(
            data={"/usr/lib/libz.so.1": ELF_SO},
            control={"./control": b"Package: z\nVersion: 1\n"},
            compression="zstd",
        )
    )
    pkg = DebPackage(str(p))
    files = dict(pkg.payload.files())
    assert files["/usr/lib/libz.so.1"] == ELF_SO
    assert pkg.control["Package"] == "z"


def test_rpm_zstd_payload_roundtrip(tmp_path):
    """A real zstd-compressed ``.rpm`` (built via the zstd CLI) extracts its
    payload."""
    p = tmp_path / "zstd.rpm"
    p.write_bytes(
        make_rpm(
            data={"/usr/lib/libz.so.1": ELF_SO},
            header={"name": "z", "version": "1"},
            compression="zstd",
        )
    )
    pkg = RpmPackage(str(p))
    files = dict(pkg.payload.files())
    assert files["/usr/lib/libz.so.1"] == ELF_SO


# --- never shells out --------------------------------------------------------


def test_does_not_shell_out(monkeypatch, deb_path, rpm_path):
    """Reading a deb/rpm must never invoke an external process (no dpkg-deb,
    no rpm, no zstd CLI). We stub ``subprocess.run`` and assert it is never
    called on the gzip paths."""
    import subprocess as _subprocess

    calls = []

    def _fake_run(*args, **kwargs):
        calls.append(args)
        raise AssertionError("subprocess.run must not be called for gzip payloads")

    monkeypatch.setattr(_subprocess, "run", _fake_run)
    # Also guard the module-level reference the parser uses.
    monkeypatch.setattr(formats, "subprocess", _subprocess)

    DebPackage(str(deb_path))
    RpmPackage(str(rpm_path))
    assert calls == []


def test_classify_does_not_shell_out(monkeypatch, tmp_path):
    import subprocess as _subprocess

    calls = []
    monkeypatch.setattr(
        _subprocess,
        "run",
        lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError()),
    )
    p = tmp_path / "x.deb"
    p.write_bytes(b"!<arch>\nshort")
    classify(str(p))
    assert calls == []
