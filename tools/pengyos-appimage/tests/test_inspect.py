"""Tests for :mod:`pengyos_appimage.inspect` and the ``inspect`` CLI."""

from __future__ import annotations

import json

import pytest

from pengyos_appimage import cli, inspect as inspector, runtime, squashfs


def test_inspect_reports_runtime_and_desktop(sample_appimage):
    r = inspector.inspect_appimage(sample_appimage)
    assert r.type == 2
    assert r.arch == "x86_64"
    assert r.payload_offset == 944632
    assert r.compression == "xz"
    assert r.update_info.startswith("zsync|")
    assert r.signed is False
    assert r.desktop.path == "sample.desktop"
    assert r.desktop.name == "Sample App"
    assert r.desktop.icon == "sample"
    assert r.desktop.exec == "sample %F"
    assert r.desktop.type == "Application"


def test_inspect_finds_root_icon(sample_appimage):
    r = inspector.inspect_appimage(sample_appimage)
    assert r.icon.found is True
    assert r.icon.path == "sample.png"
    assert r.icon.size > 0


def test_find_root_desktop_ignores_nested():
    assert inspector.find_root_desktop(["usr", "b.desktop", "a.desktop"]) == "a.desktop"
    assert inspector.find_root_desktop(["usr/share/applications/x.desktop"]) is None
    assert inspector.find_root_desktop([]) is None


@pytest.fixture
def payload(sample_appimage):
    meta = runtime.read_metadata_file(sample_appimage)
    with squashfs.SquashFS.open(sample_appimage, meta.payload_offset) as fs:
        yield fs, [n for n, _ in fs.listdir(fs.root())]


def test_read_icon_falls_back_to_diricon(payload):
    fs, names = payload
    name, data = inspector.read_icon(fs, "no-such-icon", names)
    assert name == ".DirIcon.png"
    assert data.startswith(b"\x89PNG")


def test_read_icon_with_extension_in_name(payload):
    fs, names = payload
    assert inspector.read_icon(fs, "sample.png", names)[0] == "sample.png"


def test_inspect_not_an_appimage(tmp_path):
    bad = tmp_path / "bad.AppImage"
    bad.write_bytes(b"not an appimage")
    with pytest.raises(inspector.InspectionError):
        inspector.inspect_appimage(bad)


def test_inspect_missing_file():
    with pytest.raises(inspector.InspectionError):
        inspector.inspect_appimage("/no/such/appimage")


def test_cli_inspect_human(sample_appimage, capsys):
    assert cli.main(["inspect", str(sample_appimage)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Type:     2" in out
    assert "squashfs (xz) at offset 944632" in out
    assert "Sample App" in out
    assert "sample.png" in out


def test_cli_inspect_json(sample_appimage, capsys):
    assert cli.main(["inspect", str(sample_appimage), "--json"]) == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["desktop"]["name"] == "Sample App"
    assert payload["icon"]["found"] is True
    assert payload["compression"] == "xz"


def test_cli_inspect_error(capsys):
    assert cli.main(["inspect", "/no/such/file"]) == cli.EXIT_ERROR
    assert capsys.readouterr().err.strip()
