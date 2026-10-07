"""Tests for :mod:`pengyos_debx.scripts`.

Covers: Implement list-scripts to SHOW maintainer scripts and explicitly refuse to run
them.

The fixtures are built *in-test* with the standard library only — the package
builders live in :mod:`test_formats` and are reused here, so the listing is
exercised on real byte-for-byte ``.deb``/``.rpm`` layouts.

The security invariants under test:

* maintainer scripts are **listed, never executed** (a poison ``postinst`` that
  would create a marker file proves it did not run), and
* reading scripts never shells out to an external process.
"""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from pengyos_debx import cli
from pengyos_debx.formats import PackageFormatError, ScriptEntry
from pengyos_debx.scripts import ScriptListing, format_listing, list_scripts
from pengyos_debx.scripts import list_scripts_from_path

# Reuse the byte-exact package builders from test_formats.
from test_formats import ELF_SO, make_deb, make_rpm  # noqa: E402


# RPM maintainer-script tag numbers (rpm.org ``rpmtag.h``).
_RPM_PREIN, _RPM_POSTIN, _RPM_PREUN, _RPM_POSTUN = 1023, 1024, 1025, 1026


# --- helpers -----------------------------------------------------------------


def _deb_with_scripts(
    tmp_path: Path,
    *,
    scripts: dict[str, bytes] | None = None,
    data: dict | None = None,
    control: dict | None = None,
    name: str = "pkg.deb",
) -> Path:
    """A gzip ``.deb`` with the given maintainer scripts in ``control.tar.*``.

    ``scripts`` are the maintainer scripts (``postinst``/…); ``control`` lets a
    test supply a full control set (falling back to ``scripts``).
    """
    control = control if control is not None else scripts
    blob = make_deb(
        data=data or {"/usr/lib/libfoo.so.1": ELF_SO},
        control=control or {"control": b"Package: x\n"},
    )
    p = tmp_path / name
    p.write_bytes(blob)
    return p


def _rpm_with_scripts(
    tmp_path: Path, *, scripts: dict[int, bytes], name: str = "pkg.rpm"
) -> Path:
    """A gzip ``.rpm`` carrying the given maintainer-script header tags."""
    p = tmp_path / name
    p.write_bytes(
        make_rpm(
            data={"/usr/bin/foo": b"#!/bin/sh\necho bin\n"},
            header={"name": "testpkg", "version": "1.0", "release": "1"},
            scripts=scripts,
        )
    )
    return p


# --- deb listing -------------------------------------------------------------


def test_list_scripts_deb_lists_pre_and_post(tmp_path: Path):
    p = _deb_with_scripts(
        tmp_path,
        scripts={
            "preinst": b"#!/bin/sh\necho pre\n",
            "postinst": b"#!/bin/sh\necho post\n",
            "postrm": b"#!/bin/sh\necho gone\n",
        },
    )
    listing = list_scripts_from_path(str(p))
    assert listing.names == ["./preinst", "./postinst", "./postrm"]


def test_list_scripts_deb_reads_body_and_interpreter(tmp_path: Path):
    p = _deb_with_scripts(
        tmp_path,
        scripts={"postinst": b"#!/bin/sh\necho hi\n"},
    )
    listing = list_scripts_from_path(str(p))
    (entry,) = listing
    assert entry.name == "./postinst"
    assert entry.body == b"#!/bin/sh\necho hi\n"
    assert entry.interpreter == "bin/sh"
    assert entry.size == len(b"#!/bin/sh\necho hi\n")


def test_list_scripts_deb_interpreter_with_args_and_whitespace(tmp_path: Path):
    p = _deb_with_scripts(
        tmp_path,
        scripts={"postinst": b"#! /usr/bin/python3 -u\nimport os\n"},
    )
    entry = list_scripts_from_path(str(p))[0]
    # Leading space after ``#!`` and the ``-u`` argument are both handled.
    assert entry.interpreter == "usr/bin/python3"


def test_list_scripts_deb_no_scripts_is_empty(tmp_path: Path):
    p = _deb_with_scripts(tmp_path, data={"/a": b"b"}, name="noscripts.deb")
    listing = list_scripts_from_path(str(p))
    assert listing.has_scripts is False
    assert listing.names == []


def test_list_scripts_deb_body_matches_sha256(tmp_path: Path):
    body = b"#!/bin/bash\necho x\n"
    p = _deb_with_scripts(tmp_path, scripts={"postinst": body})
    entry = list_scripts_from_path(str(p))[0]
    assert entry.sha256 == hashlib.sha256(body).hexdigest()


# --- rpm listing -------------------------------------------------------------


def test_list_scripts_rpm_lists_install_lifecycle(tmp_path: Path):
    p = _rpm_with_scripts(
        tmp_path,
        scripts={
            _RPM_PREIN: b"#!/bin/sh\necho pre\n",
            _RPM_POSTIN: b"#!/bin/sh\necho post\n",
            _RPM_PREUN: b"#!/bin/sh\necho preun\n",
        },
    )
    listing = list_scripts_from_path(str(p))
    # Sorted by tag number: PREIN(1023) < POSTIN(1024) < PREUN(1025).
    assert listing.names == ["preinst", "postinst", "prerm"]


def test_list_scripts_rpm_reads_body_and_interpreter(tmp_path: Path):
    p = _rpm_with_scripts(
        tmp_path,
        scripts={_RPM_POSTIN: b"#!/bin/sh\necho post\n"},
    )
    entry = list_scripts_from_path(str(p))[0]
    assert entry.name == "postinst"
    assert entry.body == b"#!/bin/sh\necho post\n"
    assert entry.interpreter == "bin/sh"


def test_list_scripts_rpm_no_scripts_is_empty(tmp_path: Path):
    p = tmp_path / "nosc.rpm"
    p.write_bytes(
        make_rpm(data={"/a": b"b"}, header={"name": "x", "version": "1"})
    )
    listing = list_scripts_from_path(str(p))
    assert listing.has_scripts is False


# --- ScriptListing helpers ---------------------------------------------------


def test_script_listing_accessors(tmp_path: Path):
    p = _deb_with_scripts(
        tmp_path,
        scripts={"postinst": b"#!/bin/sh\necho\n"},
    )
    listing = list_scripts_from_path(str(p))
    assert len(listing) == 1
    assert listing.names == ["./postinst"]
    assert listing.by_name("./postinst") is not None
    assert listing.by_name("./missing") is None
    assert list(listing)[0].name == "./postinst"


def test_script_listing_is_frozen_copy():
    entries = [ScriptEntry("./pre", b"#!/bin/sh\n")]
    listing = ScriptListing("pkg.deb", entries)
    # Mutating the source list does not affect the listing's own copy.
    entries.clear()
    assert len(listing) == 1


# --- formatting --------------------------------------------------------------


def test_format_listing_shows_interpreter_and_size():
    listing = ScriptListing(
        "pkg.deb",
        [ScriptEntry("./postinst", b"#!/bin/sh\necho\n", interpreter="bin/sh")],
    )
    out = format_listing(listing)
    assert "pkg.deb" in out
    assert "./postinst" in out
    assert "bin/sh" in out
    assert "15 bytes" in out  # len(b"#!/bin/sh\necho\n")


def test_format_listing_no_scripts_message():
    out = format_listing(ScriptListing("pkg.deb", []))
    assert "no maintainer scripts" in out


def test_format_listing_asserts_never_executed():
    out = format_listing(ScriptListing("pkg.deb", []))
    assert "NOT executed" in out or "never" in out.lower()


# --- CLI ---------------------------------------------------------------------


def test_cli_list_scripts_prints_listing(tmp_path: Path, capsys):
    p = _deb_with_scripts(
        tmp_path,
        scripts={"postinst": b"#!/bin/sh\necho hi\n"},
    )
    rc = cli.main(["list-scripts", str(p)])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "./postinst" in out
    assert "bin/sh" in out


def test_cli_list_scripts_rpm(tmp_path: Path, capsys):
    p = _rpm_with_scripts(
        tmp_path,
        scripts={_RPM_POSTIN: b"#!/bin/sh\necho post\n"},
    )
    rc = cli.main(["list-scripts", str(p)])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "postinst" in out


def test_cli_list_scripts_poison_never_runs(tmp_path: Path):
    """A poison ``postinst`` that would ``touch MARKER`` must leave no marker:
    the script is listed, never executed."""
    marker = tmp_path / "MARKER"
    p = _deb_with_scripts(
        tmp_path,
        data={"/usr/lib/libfoo.so.1": ELF_SO},
        control={
            "control": b"Package: foo\n",
            "postinst": f"#!/bin/sh\ntouch {marker}\n".encode(),
        },
        name="poison.deb",
    )
    rc = cli.main(["list-scripts", str(p)])
    assert rc == cli.EXIT_OK
    assert not marker.exists(), "maintainer script was executed during listing"


def test_cli_list_scripts_bad_magic_raises(tmp_path: Path):
    p = tmp_path / "not-a-pkg"
    p.write_bytes(b"this is certainly not a deb or rpm archive")
    with pytest.raises(PackageFormatError):
        list_scripts_from_path(str(p))


# --- never shells out --------------------------------------------------------


def test_list_scripts_never_shells_out(tmp_path: Path, monkeypatch):
    """Reading scripts must never invoke an external process (no dpkg-deb, no
    rpm, no zstd CLI)."""

    def _raise(*args, **kwargs):
        raise AssertionError("list-scripts must not invoke an external process")

    monkeypatch.setattr(subprocess, "run", _raise)
    p = _deb_with_scripts(
        tmp_path,
        scripts={"postinst": b"#!/bin/sh\necho\n"},
    )
    # Must not raise: reading is pure stdlib.
    listing = list_scripts_from_path(str(p))
    assert listing.names == ["./postinst"]
