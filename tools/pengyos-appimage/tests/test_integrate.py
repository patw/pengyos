"""Tests for :mod:`pengyos_appimage.integrate` and the ``integrate`` CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from pengyos_appimage import cli, desktop, integrate


@pytest.fixture
def app(sample_appimage, tmp_path):
    """A copy of the sample AppImage at a path with a space in it."""
    d = tmp_path / "My Apps"
    d.mkdir()
    path = d / "Sample-x86_64.AppImage"
    shutil.copy(sample_appimage, path)
    path.chmod(0o755)
    return path


@pytest.fixture
def dest(tmp_path):
    return tmp_path / "share"


def _entry(result):
    return desktop.parse_desktop_file(result.desktop_path)


def test_writes_entry_pointing_at_the_appimage(app, dest):
    r = integrate.integrate_appimage(app, dest)
    assert r.desktop_path == dest / "applications" / "appimage-sample.desktop"
    main = _entry(r).main
    assert main.get("Exec") == f'"{app}" %F'
    assert main.get("TryExec") == str(app)
    assert main.get("Name") == "Sample App"
    assert main.get("Categories") == "Utility;"
    assert main.get(integrate.MARKER_KEY) == str(app)
    assert r.replaced is False
    assert r.warnings == []


def test_installs_icon_at_a_hicolor_size(app, dest):
    r = integrate.integrate_appimage(app, dest)
    # The fixture icon is 1x1, below the smallest hicolor size.
    assert r.icon_path == dest / "icons/hicolor/16x16/apps/appimage-sample.png"
    assert r.icon_path.read_bytes().startswith(b"\x89PNG")
    assert _entry(r).main.get("Icon") == "appimage-sample"


def test_reintegrating_replaces(app, dest):
    integrate.integrate_appimage(app, dest)
    r = integrate.integrate_appimage(app, dest)
    assert r.replaced is True
    assert len(list((dest / "applications").iterdir())) == 1


def test_warns_when_not_executable(app, dest):
    app.chmod(0o644)
    r = integrate.integrate_appimage(app, dest)
    assert any("chmod +x" in w for w in r.warnings)


def test_relative_path_is_made_absolute(app, dest, monkeypatch):
    monkeypatch.chdir(app.parent)
    r = integrate.integrate_appimage(app.name, dest)
    assert _entry(r).main.get("TryExec") == str(app)


def test_remove_deletes_only_its_own_entries(app, dest):
    integrate.integrate_appimage(app, dest)
    other = dest / "applications" / "appimage-other.desktop"
    other.write_text(f"[Desktop Entry]\nName=O\nExec=o\n{integrate.MARKER_KEY}=/elsewhere\n")
    removed = integrate.remove_integration(app, dest)
    assert {p.name for p in removed} == {"appimage-sample.desktop", "appimage-sample.png"}
    assert other.exists()
    assert not list((dest / "icons").rglob("appimage-sample.*"))


def test_remove_works_after_the_appimage_is_gone(app, dest):
    integrate.integrate_appimage(app, dest)
    app.unlink()
    assert len(integrate.remove_integration(app, dest)) == 2


def test_remove_nothing(app, dest):
    assert integrate.remove_integration(app, dest) == []


@pytest.mark.parametrize("value, args", [
    ("sample %F", " %F"),
    ("sample", ""),
    ('"/opt/my app/run" --flag %u', " --flag %u"),
    ('"/opt/a\\"b" x', " x"),
])
def test_exec_args(value, args):
    assert integrate._exec_args(value) == args


def test_exec_quoting():
    assert integrate._quote_exec_arg("/a/b.AppImage") == "/a/b.AppImage"
    assert integrate._quote_exec_arg("/a b/c") == '"/a b/c"'
    assert integrate._quote_exec_arg("/a/100%") == "/a/100%%"
    # $ is reserved: quoted and escaped, then the backslash escaped again for
    # the desktop-file string level.
    assert integrate._quote_exec_arg("/a/$x") == '"/a/\\\\$x"'


def test_icon_dir():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (300).to_bytes(4, "big") * 2
    assert integrate._icon_dir("x.png", png) == "256x256"
    assert integrate._icon_dir("x.svg", b"<svg/>") == "scalable"
    assert integrate._icon_dir("x.xpm", b"/* XPM */") is None


@pytest.mark.skipif(shutil.which("desktop-file-validate") is None,
                    reason="desktop-file-validate not installed")
def test_entry_passes_desktop_file_validate(app, dest):
    r = integrate.integrate_appimage(app, dest)
    proc = subprocess.run(["desktop-file-validate", str(r.desktop_path)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_default_dest_is_xdg_data_home(app, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    r = integrate.integrate_appimage(app)
    assert r.desktop_path.parent == tmp_path / "xdg" / "applications"


def test_not_an_appimage(tmp_path, dest):
    bad = tmp_path / "bad.AppImage"
    bad.write_bytes(b"nope")
    with pytest.raises(integrate.IntegrateError):
        integrate.integrate_appimage(bad, dest)


def test_cli_integrate_and_remove(app, dest, capsys):
    assert cli.main(["integrate", str(app), "--dest", str(dest)]) == cli.EXIT_OK
    assert "Added" in capsys.readouterr().out
    assert cli.main(["integrate", str(app), "--dest", str(dest), "--json"]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["replaced"] is True
    assert cli.main(["integrate", "--remove", str(app), "--dest", str(dest)]) == cli.EXIT_OK
    assert "Removed" in capsys.readouterr().out
    assert cli.main(["integrate", "--remove", str(app), "--dest", str(dest)]) == cli.EXIT_OK
    assert "Nothing was integrated" in capsys.readouterr().out


def test_cli_integrate_error(capsys, tmp_path):
    assert cli.main(["integrate", "/no/such", "--dest", str(tmp_path)]) == cli.EXIT_ERROR
    assert capsys.readouterr().err.startswith("error:")
