"""Tests for :mod:`pengyos_appimage.check` and the ``check`` CLI.

The sample AppImage carries a PIE that needs a bundled library (shipped behind
a symlink), a non-PIE that needs libm, a binary that needs a library shipped
nowhere, and a stand-in ``libQt6XcbQpa.so.6`` that triggers the dlopen table.
Host libraries are faked with ``--libdirs`` pointing at a temp directory, so
the answer does not depend on the machine running the tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pengyos_appimage import check, cli


@pytest.fixture
def host(tmp_path):
    """A fake host library directory with only libc and libm."""
    d = tmp_path / "lib"
    d.mkdir()
    for name in ("libc.so.6", "libm.so.6"):
        (d / name).write_bytes(b"")
    return d


def test_reports_unshipped_and_dlopen_libraries(sample_appimage, host):
    r = check.check_appimage(sample_appimage, [str(host)])
    assert r.arch == "x86_64"
    assert r.elf_count == 5
    assert not r.ok
    by_name = {m.soname: m for m in r.missing}
    assert set(by_name) == {"libpengyos-absent.so.3", "libxcb-cursor.so.0"}
    absent = by_name["libpengyos-absent.so.3"]
    assert absent.needed_by == ["usr/bin/needs-missing"]
    assert absent.dlopen is False
    assert absent.hint == check.GENERIC_HINT
    cursor = by_name["libxcb-cursor.so.0"]
    assert cursor.dlopen is True
    assert cursor.needed_by == ["libQt6XcbQpa.so.6"]


def test_bundled_library_behind_symlink_resolves(sample_appimage, host):
    r = check.check_appimage(sample_appimage, [str(host)])
    assert "libbundled.so.1" not in {m.soname for m in r.missing}


def test_host_library_resolves(sample_appimage, host):
    (host / "libxcb-cursor.so.0").write_bytes(b"")
    (host / "libpengyos-absent.so.3").write_bytes(b"")
    assert check.check_appimage(sample_appimage, [str(host)]).ok


def test_empty_host_reports_libc(sample_appimage, tmp_path):
    r = check.check_appimage(sample_appimage, [str(tmp_path)])
    assert "libc.so.6" in {m.soname for m in r.missing}


def test_hint_table():
    assert "GTK 2" in check._hint("libgtk-x11-2.0.so.0")
    assert check._hint("libfoo.so.1") == check.GENERIC_HINT


def test_ld_so_conf_includes(tmp_path):
    (tmp_path / "conf.d").mkdir()
    (tmp_path / "conf.d" / "a.conf").write_text("/opt/a/lib\n# comment\n")
    conf = tmp_path / "ld.so.conf"
    conf.write_text("include conf.d/*.conf\n/opt/b/lib\n")
    assert check._ld_so_conf_dirs(conf) == ["/opt/a/lib", "/opt/b/lib"]


def test_not_an_appimage(tmp_path):
    bad = tmp_path / "bad.AppImage"
    bad.write_bytes(b"nope")
    with pytest.raises(check.CheckError):
        check.check_appimage(bad)


def test_cli_exit_status_and_text(sample_appimage, host, capsys):
    rc = cli.main(["check", str(sample_appimage), "--libdirs", str(host)])
    assert rc == cli.EXIT_MISSING
    out = capsys.readouterr().out
    assert "Read 5 x86_64 ELF objects." in out
    assert "libpengyos-absent.so.3" in out
    assert "loaded at run time by: libQt6XcbQpa.so.6" in out


def test_cli_clean_exit(sample_appimage, host, capsys):
    (host / "libxcb-cursor.so.0").write_bytes(b"")
    (host / "libpengyos-absent.so.3").write_bytes(b"")
    assert cli.main(["check", str(sample_appimage), "--libdirs", str(host)]) == cli.EXIT_OK
    assert "Every library" in capsys.readouterr().out


def test_cli_json(sample_appimage, host, capsys):
    rc = cli.main(["check", str(sample_appimage), "--libdirs", str(host), "--json"])
    assert rc == cli.EXIT_MISSING
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert {m["soname"] for m in payload["missing"]} == {
        "libpengyos-absent.so.3", "libxcb-cursor.so.0"
    }


def test_cli_error(capsys):
    assert cli.main(["check", "/no/such/file"]) == cli.EXIT_ERROR
    assert capsys.readouterr().err.startswith("error:")
