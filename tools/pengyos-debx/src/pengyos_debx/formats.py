"""Reading of deb (``ar`` + tar) and rpm (``cpio``/zstd) package formats.

This module parses package containers with the Python standard library only
(``struct`` / ``io`` for the container framing, ``tarfile`` + ``gzip`` / ``bz2``
/ ``lzma`` for the common payload compressions). It **never** shells out to
``dpkg-deb`` or ``rpm`` and never executes anything it reads.

Formats
-------

A ``.deb`` is an ``ar`` archive holding three members::

    debian-binary   text, e.g. ``"2.0\\n"``
    control.tar.gz  control metadata + maintainer scripts
    data.tar.gz     the payload to extract

An ``.rpm`` begins with a 96-byte *lead* and is then a ``cpio`` (newc) archive
that contains an RPM header blob plus a ``payload.tar.*`` member. The payload
tarball is compressed with gzip, bzip2, xz or (modern) zstd.

The public surface is small on purpose:

* :func:`classify`        -- ``"deb"`` / ``"rpm"`` / ``None``
* :class:`ArReader`       -- parse an ``ar`` archive into members
* :class:`CpioReader`     -- parse a ``cpio newc`` stream into members
* :func:`decompress`      -- decompress a blob (gzip/bz2/xz/zstd)
* :class:`DebPackage`     -- read a ``.deb`` and expose its payload files + maintainer scripts
* :class:`RpmPackage`     -- read an ``.rpm`` and expose its payload files + maintainer scripts
* :class:`PayloadMember`  -- a payload entry with its tar member ``kind``
* :class:`ScriptEntry`    -- one maintainer script, read but never executed
"""

from __future__ import annotations

import bz2
import gzip
import hashlib
import io
import lzma
import struct
import subprocess
import tarfile
from dataclasses import dataclass
from typing import BinaryIO, Iterator, Optional

# Exit code used by the CLI for a hard, actionable failure (e.g. an unsupported
# payload compression). Kept in sync with ``cli.py``.
EXIT_BAD_PAYLOAD = 4


class PackageFormatError(Exception):
    """Raised when a file is not a recognised/valid deb/rpm container."""


class UnsupportedCompression(PackageFormatError):
    """Raised when a payload uses a compression this build cannot decode."""


# --- ar (deb) ----------------------------------------------------------------

# ``ar`` member header: name[16] mtime[12] uid[6] gid[6] mode[8] size[10] end[2]
_AR_HEADER = struct.Struct("<16s12s6s6s8s10s2s")
_AR_MAGIC = b"!<arch>\n"  # 8 bytes: `!<arch>` + newline


def _ar_uint(field: bytes) -> int:
    """Decode a right-justified, space-padded unsigned decimal ``ar`` field."""
    field = field.strip()
    if not field:
        return 0
    return int(field, 10)


class ArReader:
    """Parse an ``ar`` archive (as used by ``.deb``) into its members.

    Each member is exposed as an :class:`ArMember` with a decoded ``name``,
    ``size`` and a seekable ``data`` buffer.
    """

    def __init__(self, data: bytes):
        if data[:8] != _AR_MAGIC:
            raise PackageFormatError("not an ar archive (bad magic)")
        self.data = data
        # Validate eagerly: a truncated/malformed archive raises here rather
        # than lazily on first access, so ``ArReader(data)`` alone is sufficient
        # to prove the archive is well-formed.
        self._members = list(self._parse())

    @property
    def members(self) -> list["ArMember"]:
        return self._members

    def _parse(self) -> Iterator["ArMember"]:
        pos = len(_AR_MAGIC)
        buf = self.data
        n = len(buf)
        while pos < n:
            # A partial member header (the archive was truncated mid-way) is
            # malformed: raise rather than silently reporting zero members.
            # An empty archive (magic only) never enters this loop.
            if pos + _AR_HEADER.size > n:
                raise PackageFormatError("ar archive truncated mid-header")
            (
                raw_name,
                _mtime,
                _uid,
                _gid,
                _mode,
                raw_size,
                end,
            ) = _AR_HEADER.unpack_from(buf, pos)
            if end != b"`\n":
                raise PackageFormatError("malformed ar member header")
            # ``ar`` member names are space-padded (the BSD variant, which
            # ``.deb`` files use). The GNU variant instead terminates the name
            # with a trailing ``/`` and space-pads the remainder. Trim the
            # padding *first* (it follows the ``/``), then drop the ``/`` so
            # exact member lookups (``get("debian-binary")``) work for both
            # variants.
            raw = raw_name.split(b"\x00", 1)[0].rstrip(b" ")
            if raw.endswith(b"/"):
                raw = raw[:-1]
            name = raw.decode("utf-8", "surrogateescape")
            size = _ar_uint(raw_size)
            data_start = pos + _AR_HEADER.size
            data = buf[data_start : data_start + size]
            if len(data) < size:
                raise PackageFormatError("ar member truncated")
            if name == "//":
                # GNU extended-name table: skip the member's data.
                pos = data_start + size + (size & 1)
                continue
            yield ArMember(name, size, data)
            # Members are padded to an even byte boundary.
            pos = data_start + size + (size & 1)

    def get(self, name: str) -> Optional["ArMember"]:
        for m in self.members:
            if m.name == name:
                return m
        return None


@dataclass
class ArMember:
    """A single member of an ``ar`` archive."""

    name: str
    size: int
    data: bytes


# --- cpio (rpm) --------------------------------------------------------------

# ``cpio newc`` fixed header, all fields hex, big-endian, 8 chars each.
_CPIO_NEWC = struct.Struct(">15s")
_CPIO_MAGIC = b"070701"


class CpioMember:
    """A single file/dir/symlink member of a ``cpio newc`` archive."""

    def __init__(self, name: str, size: int, kind: str, data: bytes):
        self.name = name
        self.size = size
        self.kind = kind  # "file" | "dir" | "symlink"
        self.data = data

    @property
    def symlink_target(self) -> Optional[str]:
        if self.kind == "symlink":
            # For symlinks the "data" holds the target path (padded to 4B).
            return self.data.rstrip(b"\x00").decode("utf-8", "surrogateescape")
        return None


class CpioReader:
    """Parse a ``cpio newc`` archive (as used by ``.rpm``) into members."""

    def __init__(self, data: bytes):
        self.data = data
        self._members: list[CpioMember] | None = None

    @property
    def members(self) -> list[CpioMember]:
        if self._members is None:
            self._members = list(self._parse())
        return self._members

    def _parse(self) -> Iterator[CpioMember]:
        buf = self.data
        pos = 0
        n = len(buf)
        while pos < n:
            if buf[pos : pos + 6] != _CPIO_MAGIC:
                if buf[pos : pos + 6] == b"070702":
                    raise PackageFormatError(
                        "cpo cpio 'old' (binary) format is unsupported; "
                        "rpm uses newc"
                    )
                raise PackageFormatError("malformed cpio (bad magic)")
            # The newc header is a fixed 110 bytes: a 6-byte magic followed by
            # fourteen 8-byte hex fields; the name follows at offset 110. The
            # fields are contiguous hex digits with NO NUL separators, so they
            # are parsed at fixed byte offsets (not split on NUL). Field widths
            # and layout are verified against busybox's cpio.c printf format and
            # the get_header_cpio() sscanf string.
            header = buf[pos : pos + 110]
            if len(header) < 110:
                raise PackageFormatError("truncated cpio header")

            def _hex_field(start: int, width: int = 8) -> int:
                raw = header[start : start + width]
                try:
                    return int(raw.decode("ascii", "replace"), 16)
                except ValueError as exc:
                    raise PackageFormatError(
                        f"malformed cpio header field at offset {start}"
                    ) from exc

            mode = _hex_field(14)
            size = _hex_field(54)
            # ``namesize`` is ``strlen(name) + 1`` (it counts the trailing NUL).
            namesize = _hex_field(94)
            if namesize < 1:
                raise PackageFormatError("cpio member has zero-length name")
            name_len = namesize - 1  # bytes excluding the trailing NUL
            name_start = pos + 110
            name = buf[name_start : name_start + name_len].decode(
                "utf-8", "surrogateescape"
            )
            data_start = name_start + namesize
            data = buf[data_start : data_start + size]
            if len(data) < size:
                raise PackageFormatError("cpio member truncated")
            kind = _cpio_mode_kind(mode)
            yield CpioMember(name, size, kind, data)
            # Both name and data are padded up to the next 4-byte boundary.
            pos = data_start + size
            pos += (4 - (pos % 4)) % 4
            # A trailing zero-size member (the cpio terminator) ends the stream.
            if size == 0 and name == "TRAILER!!!" and kind == "file":
                break

    def get(self, name: str) -> Optional[CpioMember]:
        for m in self.members:
            if m.name == name:
                return m
        return None


def _cpio_mode_kind(mode: int) -> str:
    """Map a ``cpio`` mode's file-type nibble to a kind string."""
    st_mode = mode & 0xF000
    if st_mode == 0x8000:
        return "file"
    if st_mode == 0x4000:
        return "dir"
    if st_mode == 0xA000:
        return "symlink"
    return "file"


# --- compression -------------------------------------------------------------

_GZIP_MAGIC = b"\x1f\x8b"
_BZ2_MAGIC = b"BZ"
_XZ_MAGIC = b"\xfd7zXZ\x00"
# zstd frame magic. ``zstd.h`` defines ``ZSTD_MAGICNUMBER 0xFD2FB528`` (valid
# since v0.8.0); written little-endian on disk this is ``28 B5 2F FD``.
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def _find_external_binary(name: str) -> Optional[str]:
    """Locate an external CLI on ``PATH`` without importing ``shutil``."""
    import os

    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def decompress(blob: bytes, fmt: str) -> bytes:
    """Decompress ``blob`` according to ``fmt`` (``"gzip"``/``"bz2"``/``"xz"``/``"zstd"``).

    Uses the standard library for gzip/bz2/xz. For zstd the ``zstandard``
    Python module is used when importable; otherwise the external ``zstd``
    CLI is invoked as a fallback. Neither path executes the package payload.
    """
    if blob[:2] == _GZIP_MAGIC:
        return gzip.decompress(blob)
    if blob[:2] == _BZ2_MAGIC:
        return bz2.decompress(blob)
    if blob[:4] == _XZ_MAGIC:
        return lzma.decompress(blob)
    if blob[:4] == _ZSTD_MAGIC:
        return _decompress_zstd(blob)
    raise UnsupportedCompression(
        f"unrecognised compression magic {blob[:4]!r}; not gzip/bz2/xz/zstd"
    )


def _decompress_zstd(blob: bytes) -> bytes:
    """Decompress a zstd frame, preferring ``zstandard`` then the ``zstd`` CLI."""
    try:
        import zstandard  # type: ignore

        dctx = zstandard.ZstdDecompressor()
        return dctx.decompress(blob)
    except ImportError:
        pass
    zstd_bin = _find_external_binary("zstd")
    if zstd_bin is None:
        raise UnsupportedCompression(
            "zstd payload with no 'zstandard' module and no 'zstd' binary "
            "available"
        )
    try:
        proc = subprocess.run(
            [zstd_bin, "-d", "-c"],
            input=blob,
            stdout=subprocess.PIPE,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise UnsupportedCompression(f"zstd decompression failed: {exc}") from exc
    return proc.stdout


def detect_compression(blob: bytes) -> str:
    """Return the compression scheme of ``blob`` (or ``"raw"`` for none)."""
    if blob[:2] == _GZIP_MAGIC:
        return "gzip"
    if blob[:2] == _BZ2_MAGIC:
        return "bz2"
    if blob[:4] == _XZ_MAGIC:
        return "xz"
    if blob[:4] == _ZSTD_MAGIC:
        return "zstd"
    return "raw"


# --- deb ---------------------------------------------------------------------

_DEB_DATA_MEMBER = "data.tar"  # prefix; may be data.tar.gz/.bz2/.xz/.zstd
_DEB_CONTROL_MEMBER = "control.tar"  # prefix; .gz/.bz2/.xz/.zstd


# The three payload member kinds we care about when extracting. ``dir`` entries
# are implicit (their parents are created on demand) and never written to disk.
PAYLOAD_FILE = "file"
PAYLOAD_SYMLINK = "symlink"
PAYLOAD_DIR = "dir"


@dataclass(frozen=True)
class PayloadMember:
    """A single payload entry with its tar member *kind* exposed.

    Unlike :meth:`DebPayload.files`, which hides the tar member type, this
    carries the ``kind`` so the extractor can recreate symlinks on disk rather
    than writing their targets out as regular files.
    """

    name: str
    data: bytes
    kind: str


@dataclass(frozen=True)
class ScriptEntry:
    """A single maintainer script read from a package.

    ``name`` is the script's path inside the package (for a deb this is the
    ``./postinst``-style path inside ``control.tar.*``; for an rpm this is the
    conventional script label such as ``postinst``). ``interpreter`` is the
    shebang interpreter (leading ``/`` dropped, e.g. ``bin/sh``) if the script
    has one. ``body`` is the raw script bytes; ``size``/``sha256`` describe it.

    Reading a script is purely *read* — the bytes are never executed. See
    :mod:`pengyos_debx.scripts`, which owns the "list but never run" policy.
    """

    name: str
    body: bytes
    interpreter: Optional[str] = None

    @property
    def size(self) -> int:
        return len(self.body)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


def _script_interpreter(body: bytes) -> Optional[str]:
    """Return the interpreter named by a script's shebang, else ``None``.

    Recognises ``#! /bin/sh`` (whitespace after ``#!``) and
    ``#!/bin/sh -e`` (arguments after the interpreter). The interpreter is
    returned with any leading ``/`` dropped so callers get a bare name
    (``bin/sh``), matching ``which``-style lookups.
    """
    if not body:
        return None
    line = body.split(b"\n", 1)[0]
    if not line.startswith(b"#!"):
        return None
    rest = line[2:].strip()
    if not rest:
        return None
    # Drop an argument appended to the interpreter (``/bin/sh -e``).
    return rest.split(None, 1)[0].decode("utf-8", "surrogateescape").lstrip("/")


class DebPayload:
    """The extracted payload of a ``.deb``: a tar stream of files."""

    def __init__(self, tar_stream: BinaryIO, data_member: str):
        self._tar = tarfile.open(fileobj=tar_stream, mode="r|")
        self.data_member = data_member

    def files(self) -> Iterator[tuple[str, bytes]]:
        """Yield ``(name, data)`` for every file in the payload tar.

        Symlinks are exposed as a zero-byte entry whose ``data`` carries the
        link target, so the caller gets a uniform ``(path -> bytes)`` mapping
        (e.g. ``dict(payload.files())``).
        """
        for member in self._tar:
            if member.isfile():
                yield member.name, self._tar.extractfile(member).read()
            elif member.issym():
                # Expose symlinks as a zero-byte entry carrying the target.
                yield member.name, member.linkname.encode("utf-8", "surrogateescape")

    def iter_members(self) -> Iterator[PayloadMember]:
        """Yield :class:`PayloadMember` for every entry in the payload tar.

        Unlike :meth:`files`, the tar member *kind* is exposed so the extractor
        can recreate symlinks on disk. Directory members are yielded with
        ``kind=PAYLOAD_DIR`` and zero-length ``data``; the extractor turns them
        into real directories. Nothing here is executed — it is a purely
        read-only walk of the payload.
        """
        for member in self._tar:
            if member.isfile():
                # Read the whole member now: this is a streaming (``r|``) tar,
                # so its bytes must be consumed before advancing.
                data = self._tar.extractfile(member).read()
                yield PayloadMember(member.name, data, PAYLOAD_FILE)
            elif member.issym():
                yield PayloadMember(
                    member.name,
                    member.linkname.encode("utf-8", "surrogateescape"),
                    PAYLOAD_SYMLINK,
                )
            elif member.isdir():
                yield PayloadMember(member.name, b"", PAYLOAD_DIR)


class DebPackage:
    """Read a ``.deb`` and expose its payload plus control metadata."""

    def __init__(self, path: str):
        with open(path, "rb") as fh:
            data = fh.read()
        self._init_from_bytes(data, path)

    def _init_from_bytes(self, data: bytes, source: str) -> None:
        self.source = source
        ar = ArReader(data)
        self.debian_binary = ar.get("debian-binary")
        if self.debian_binary is None:
            raise PackageFormatError("deb missing debian-binary member")

        data_member = self._find_member(ar, _DEB_DATA_MEMBER)
        if data_member is None:
            raise PackageFormatError("deb missing data payload member")
        self.data_member = data_member.name
        payload = decompress(data_member.data, detect_compression(data_member.data))

        control_member = self._find_member(ar, _DEB_CONTROL_MEMBER)
        control, scripts = self._read_control(control_member)
        self.control = control
        self._control_scripts = scripts or []

        self.payload = DebPayload(io.BytesIO(payload), self.data_member)

    @property
    def scripts(self) -> list[ScriptEntry]:
        """The maintainer scripts in this package, read but never executed.

        These are the files under ``./`` in ``control.tar.*`` (``preinst``,
        ``postinst``, ``prerm``, ``postrm`` …). Reading them is read-only; the
        bytes are never run. See :mod:`pengyos_debx.scripts`.
        """
        return list(getattr(self, "_control_scripts", []))

    @staticmethod
    def _find_member(ar: ArReader, prefix: str) -> Optional[ArMember]:
        for member in ar.members:
            if member.name.startswith(prefix):
                return member
        return None

    @staticmethod
    def _read_control(control_member: Optional[ArMember]) -> Optional[dict]:
        """Parse the ``control`` file inside ``control.tar.*`` into a dict.

        The control file is ``key: value`` pairs. Maintainer scripts (the files
        under ``./`` in the control tarball) are intentionally *not* run; they
        are read (not executed) so :attr:`DebPackage.scripts` can list their
        interpreter and contents for inspection.

        Returns ``(info, scripts)``: the control dict (with a ``"_scripts"``
        list of script names) and the list of :class:`ScriptEntry` objects.
        """
        if control_member is None:
            return None
        try:
            control_tar = tarfile.open(
                fileobj=io.BytesIO(decompress(control_member.data, detect_compression(control_member.data))),
                mode="r",
            )
        except (PackageFormatError, tarfile.TarError):
            return None
        info = {}
        scripts: list[ScriptEntry] = []
        for member in control_tar:
            if not member.isfile():
                continue
            # Control members sit at the top level of the control tar, either as
            # ``control``/``postinst`` … or with a leading ``./`` (as real debs
            # emit). Normalise the leading ``./`` away to find the top-level
            # name, then require no further ``/`` so nested paths are skipped.
            top = member.name
            if top.startswith("./"):
                top = top[2:]
            if "/" in top:
                continue
            raw = control_tar.extractfile(member).read()
            text = raw.decode("utf-8", "replace")
            # Re-add the ``./`` prefix so script names match the paths as they
            # appear inside the package (``./postinst`` etc.).
            display = member.name if member.name.startswith("./") else "./" + member.name
            if top == "control":
                for line in text.splitlines():
                    if ": " in line:
                        key, _, value = line.partition(": ")
                        info[key] = value
            else:
                # A maintainer script. It is read (not run) so the listing can
                # show its interpreter and contents for inspection.
                scripts.append(
                    ScriptEntry(
                        name=display,
                        body=raw,
                        interpreter=_script_interpreter(raw),
                    )
                )
        info["_scripts"] = [s.name for s in scripts]
        return info, scripts


# --- rpm ---------------------------------------------------------------------

_RPM_LEAD_SIZE = 96
_RPM_LEAD_MAGIC = b"\xab\xcd\xef\x96"  # little-endian 0x96efcdab
_RPM_PAYLOAD_PREFIX = "payload.tar"


class RpmPayload:
    """The extracted payload of an ``.rpm``: a tar stream of files."""

    def __init__(self, tar_stream: BinaryIO):
        self._tar = tarfile.open(fileobj=tar_stream, mode="r|")

    def files(self) -> Iterator[tuple[str, bytes]]:
        for member in self._tar:
            if member.isfile():
                yield member.name, self._tar.extractfile(member).read()
            elif member.issym():
                yield member.name, member.linkname.encode("utf-8", "surrogateescape")

    def iter_members(self) -> Iterator[PayloadMember]:
        """Yield :class:`PayloadMember` for every entry in the payload tar.

        See :meth:`DebPayload.iter_members` — the same read-only, kind-exposing
        walk, used by the extractor to recreate symlinks on disk.
        """
        for member in self._tar:
            if member.isfile():
                data = self._tar.extractfile(member).read()
                yield PayloadMember(member.name, data, PAYLOAD_FILE)
            elif member.issym():
                yield PayloadMember(
                    member.name,
                    member.linkname.encode("utf-8", "surrogateescape"),
                    PAYLOAD_SYMLINK,
                )
            elif member.isdir():
                yield PayloadMember(member.name, b"", PAYLOAD_DIR)


class RpmPackage:
    """Read an ``.rpm`` and expose its payload plus header metadata."""

    def __init__(self, path: str):
        with open(path, "rb") as fh:
            data = fh.read()
        self._init_from_bytes(data, path)

    def _init_from_bytes(self, data: bytes, source: str) -> None:
        self.source = source
        if len(data) < _RPM_LEAD_SIZE:
            raise PackageFormatError("rpm file too small to have a lead")
        if data[4:8] != _RPM_LEAD_MAGIC:
            raise PackageFormatError("not an rpm (bad lead magic)")

        body = data[_RPM_LEAD_SIZE:]
        cpio = CpioReader(body)
        payload_member = self._find_payload(cpio)
        if payload_member is None:
            raise PackageFormatError("rpm missing payload.tar member")
        self.payload_member = payload_member.name
        payload = decompress(
            payload_member.data, detect_compression(payload_member.data)
        )
        self.payload = RpmPayload(io.BytesIO(payload))
        header, scripts = self._read_header(cpio)
        self.header = header
        self._rpm_scripts = scripts

    @property
    def scripts(self) -> list[ScriptEntry]:
        """The maintainer scripts in this package, read but never executed.

        These are the RPM installation-lifecycle scripts (``preinst``,
        ``postinst``, ``prerm``, ``postrm``) stored in the header. Reading them
        is read-only; the bytes are never run. See :mod:`pengyos_debx.scripts`.
        """
        return list(getattr(self, "_rpm_scripts", []))

    @staticmethod
    def _find_payload(cpio: CpioReader) -> Optional[CpioMember]:
        for member in cpio.members:
            if member.name.startswith(_RPM_PAYLOAD_PREFIX):
                return member
        return None

    # RPM maintainer-script tags (rpm.org ``rpmtag.h``). The four
    # installation-lifecycle scripts run during install/remove; the two
    # transaction scripts (PRETRANS/POSTTRANS) run around the whole
    # transaction and are handled by a follow-up.
    _SCRIPT_TAGS = {
        1023: "preinst",
        1024: "postinst",
        1025: "prerm",
        1026: "postrm",
    }

    @staticmethod
    def _read_header(cpio: CpioReader) -> dict:
        """Read RPM header tags from the cpio header blob.

        The RPM *new-style* header (format 100) is a blob that begins with a
        32-byte lead, followed by three parallel arrays of ``count``
        little-endian int32s: the tag numbers, their data offsets, and their
        data sizes. We decode the common string tags (NAME/VERSION/RELEASE/
        SUMMARY) and the maintainer-script tags (PREIN/POSTIN/PREUN/POSTUN).
        This is read-only metadata; nothing here is executed.
        """
        header_member = None
        for member in cpio.members:
            if member.name == "\x00":
                header_member = member
                break
        if header_member is None:
            return {}
        blob = header_member.data
        if len(blob) < 32:
            return {}
        (count,) = struct.unpack_from("<i", blob, 4)
        tag_array_off = 32
        offset_array_off = tag_array_off + 4 * count
        size_array_off = offset_array_off + 4 * count
        result: dict = {}
        scripts: list[ScriptEntry] = []
        for i in range(count):
            (tag,) = struct.unpack_from("<i", blob, tag_array_off + 4 * i)
            (data_off,) = struct.unpack_from("<i", blob, offset_array_off + 4 * i)
            (size,) = struct.unpack_from("<i", blob, size_array_off + 4 * i)
            chunk = blob[data_off : data_off + size]
            if tag in RpmPackage._SCRIPT_TAGS:
                # RPM stores the full script text (including its ``#!`` shebang)
                # as the script body; the interpreter is derived from that
                # shebang, exactly as for a deb script.
                scripts.append(
                    ScriptEntry(
                        name=RpmPackage._SCRIPT_TAGS[tag],
                        body=chunk,
                        interpreter=_script_interpreter(chunk),
                    )
                )
            elif tag in (1005, 1006, 1002, 1003):
                names = {1005: "name", 1006: "version", 1002: "release", 1003: "summary"}
                result[names[tag]] = chunk.rstrip(b"\x00").decode("utf-8", "surrogateescape")
        return result, scripts


def classify(path: str) -> Optional[str]:
    """Return ``"deb"`` / ``"rpm"`` for a package file, else ``None``.

    Uses only the leading magic bytes; the file is never opened fully or
    executed.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(1024)
    except OSError:
        return None
    if head[:8] == _AR_MAGIC:
        return "deb"
    if len(head) >= _RPM_LEAD_SIZE and head[4:8] == _RPM_LEAD_MAGIC:
        return "rpm"
    return None


def read_package(path: str) -> object:
    """Dispatch on the file type and return a :class:`DebPackage`/``RpmPackage``."""
    fmt = classify(path)
    if fmt == "deb":
        return DebPackage(path)
    if fmt == "rpm":
        return RpmPackage(path)
    raise PackageFormatError(f"{path!r}: not a recognised .deb/.rpm file")
