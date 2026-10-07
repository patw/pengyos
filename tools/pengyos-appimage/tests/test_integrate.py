"""Tests for :mod:`pengyos_appimage.integrate` and the ``integrate`` CLI.

The central invariant under test: integration only *reads* the already-extracted
files and *copies* them into the destination tree. It never runs the embedded
``AppRun`` hook. We prove this two ways:

* Structurally — the extracted prefix carries a *poison* ``AppRun``; we spy on
  ``subprocess.run`` so any attempt to shell out (or to run the AppImage/AppRun)
  raises. Integration must succeed without shelling out.
* End-to-end (integration, opt-in) — the committed fixture's real poison
  ``AppRun`` sits in the extracted prefix; we assert the poison marker is never
  created.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from pengyos_appimage import cli
from pengyos_appimage import desktop as desktop_mod
from pengyos_appimage import integrate as integrator


# The poison marker the embedded AppRun would create if ever executed.
POISON_PATH = "/tmp/PENGYOS_APPIMAGE_POISON"

DESKTOP_CONTENT = (
    "[Desktop Entry]\n"
    "Version=1.0\n"
    "Type=Application\n"
    "Name=Sample App\n"
    "GenericName=Sample\n"
    "Exec=sample %F\n"
    "Icon=sample\n"
    "Terminal=false\n"
    "Categories=Utility;\n"
)

# A desktop whose Exec accepts URIs (%U) -> the mime entry must be generated.
DESKTOP_URI_CONTENT = (
    "[Desktop Entry]\n"
    "Version=1.0\n"
    "Type=Application\n"
    "Name=URI App\n"
    "Exec=uriapp %U\n"
    "Icon=uriapp\n"
    "Terminal=false\n"
)

# A desktop missing the required ``Exec`` key for Type=Application.
DESKTOP_NO_EXEC = (
    "[Desktop Entry]\n"
    "Version=1.0\n"
    "Type=Application\n"
    "Name=Broken App\n"
    "Icon=broken\n"
)

POISON_APPRUN = (
    "#!/bin/sh\n"
    f'echo "poisoned" > {repr(POISON_PATH)}\n'
    "exec ./usr/bin/sample \"$@\"\n"
)


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _write_appdir(root: Path, desktop_content: str = DESKTOP_CONTENT) -> Path:
    """Materialise a canonical extracted AppImage prefix under *root*."""
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps").mkdir(
        parents=True
    )

    # Poison AppRun at the prefix root — executable, exactly as extraction
    # produces it. Integration must never run it.
    poison = root / "AppRun"
    poison.write_text(POISON_APPRUN, encoding="utf-8")
    poison.chmod(0o755)

    (root / "usr" / "bin" / "sample").write_text("#!/bin/sh\necho sample\n")
    (root / "sample.desktop").write_text(desktop_content, encoding="utf-8")
    (
        root / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
        / "sample.png"
    ).write_bytes(_png_bytes())
    return root


@pytest.fixture
def appdir(tmp_path) -> Path:
    return _write_appdir(tmp_path / "app")


@pytest.fixture
def dest_root(tmp_path) -> Path:
    return tmp_path / "xdg"


def test_integrate_writes_desktop_file(appdir, dest_root):
    result = integrator.integrate_app(appdir, dest_root)
    assert result.desktop_path is not None
    assert result.desktop_path.parent.name == "applications"
    assert result.desktop_path.name == "Sample App.desktop"
    text = result.desktop_path.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in text
    assert "Name=Sample App" in text
    assert "Exec=sample %F" in text


def test_integrate_desktop_is_valid_freedesktop_document(appdir, dest_root):
    """The generated .desktop round-trips through the parser."""
    result = integrator.integrate_app(appdir, dest_root)
    doc = desktop_mod.parse_desktop_file(result.desktop_path)
    entry = doc.main
    assert entry is not None
    assert entry.entry_type == "Application"
    assert entry.name == "Sample App"
    assert entry.exec == "sample %F"


def test_integrate_desktop_passes_desktop_file_validate(appdir, dest_root):
    """Opt-in: the generated .desktop passes `desktop-file-validate`."""
    import shutil

    if shutil.which("desktop-file-validate") is None:
        pytest.skip("desktop-file-validate not on PATH")
    result = integrator.integrate_app(appdir, dest_root)
    proc = subprocess.run(
        ["desktop-file-validate", str(result.desktop_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_integrate_adds_provenance_marker(appdir, dest_root):
    result = integrator.integrate_app(appdir, dest_root)
    text = result.desktop_path.read_text(encoding="utf-8")
    assert integrator.GENERATED_MARKER_KEY in text


def test_integrate_installs_icon(appdir, dest_root):
    result = integrator.integrate_app(appdir, dest_root)
    assert result.icon_path is not None
    # Installed under a hicolor theme size dir, named after the icon id.
    assert result.icon_path.parent.name == "apps"
    assert "hicolor" in [p.name for p in result.icon_path.parents]
    assert result.icon_path.name == "sample.png"
    assert result.icon_path.read_bytes() == _png_bytes()


def test_integrate_uses_theme_size_from_path(appdir, dest_root):
    result = integrator.integrate_app(appdir, dest_root)
    # The icon sits under .../hicolor/256x256/apps/.
    assert "/256x256/" in str(result.icon_path)


def test_integrate_generates_mime_for_uri_exec(appdir, dest_root):
    # Swap in a desktop whose Exec accepts URIs, then integrate the same prefix.
    (appdir / "sample.desktop").write_text(DESKTOP_URI_CONTENT, encoding="utf-8")
    result = integrator.integrate_app(appdir, dest_root)
    assert result.mime_xml_path is not None
    assert "text/uri-list" in result.mime_types
    xml = result.mime_xml_path.read_text(encoding="utf-8")
    assert '<mime-type type="text/uri-list"/>' in xml
    # The mime packages live under mime/packages/.
    assert result.mime_xml_path.parent.name == "packages"


def test_integrate_skips_mime_when_no_uri_exec(appdir, dest_root):
    # The default fixture uses ``Exec=sample %F`` (file names, not URIs).
    result = integrator.integrate_app(appdir, dest_root)
    assert result.mime_xml_path is None
    assert result.mime_types == []


def test_integrate_missing_desktop_raises(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(integrator.IntegrateError):
        integrator.integrate_app(empty, tmp_path / "xdg")


def test_integrate_missing_required_key_raises(appdir, dest_root):
    (appdir / "sample.desktop").write_text(DESKTOP_NO_EXEC, encoding="utf-8")
    with pytest.raises(integrator.IntegrateError):
        integrator.integrate_app(appdir, dest_root)


def test_integrate_not_a_directory_raises(tmp_path):
    with pytest.raises(integrator.IntegrateError):
        integrator.integrate_app(tmp_path / "nope", tmp_path / "xdg")


def test_integrate_no_icon_warns(appdir, dest_root):
    # Remove the icon so integration must warn but still succeed.
    (
        appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
        / "sample.png"
    ).unlink()
    result = integrator.integrate_app(appdir, dest_root)
    assert result.icon_path is None
    assert any("icon" in w.lower() for w in result.warnings)
    # The .desktop is still written.
    assert result.desktop_path is not None


def test_integrate_result_json_serialisable(appdir, dest_root):
    import json

    result = integrator.integrate_app(appdir, dest_root)
    payload = json.dumps(result.to_dict())
    parsed = json.loads(payload)
    assert parsed["desktop_path"].endswith("Sample App.desktop")
    assert parsed["mime_types"] == []


def test_integrate_does_not_execute_poison_hook(
    appdir, dest_root, monkeypatch
):
    """The poison invariant: integration never shells out / runs AppRun."""
    if Path(POISON_PATH).exists():
        Path(POISON_PATH).unlink()

    def spy_run(cmd, *args, **kwargs):
        joined = " ".join(str(c) for c in cmd)
        if "AppRun" in joined or POISON_PATH in joined or "sample-appimage" in joined:
            raise AssertionError(f"integrate executed the AppImage/AppRun: {cmd}")
        raise AssertionError(f"integrate must not shell out: {cmd}")

    monkeypatch.setattr(subprocess, "run", spy_run)

    try:
        result = integrator.integrate_app(appdir, dest_root)
    finally:
        if Path(POISON_PATH).exists():
            Path(POISON_PATH).unlink()
    assert result.desktop_path is not None
    assert not Path(POISON_PATH).exists()


def test_integrate_resolves_default_dest(monkeypatch, appdir, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    result = integrator.integrate_app(appdir)
    assert result.dest == tmp_path / "share"
    assert result.desktop_path.parent.name == "applications"


def test_render_entry_round_trips():
    text = (
        "[Desktop Entry]\n"
        "Name=English\n"
        "Name[de]=Deutsch\n"
        "Type=Application\n"
        "Exec=sample %F\n"
    )
    entry = desktop_mod.parse_desktop(text).main
    assert entry is not None
    rendered = desktop_mod.render_entry(entry)
    reparsed = desktop_mod.parse_desktop(rendered).main
    assert reparsed is not None
    assert reparsed.keys == entry.keys


def test_render_entry_preserves_key_order():
    text = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Ordered\n"
        "Exec=a\n"
        "Icon=i\n"
    )
    entry = desktop_mod.parse_desktop(text).main
    rendered = desktop_mod.render_entry(entry)
    lines = [ln for ln in rendered.splitlines() if "=" in ln]
    assert lines == [
        "Type=Application",
        "Name=Ordered",
        "Exec=a",
        "Icon=i",
    ]


# --- CLI -------------------------------------------------------------------


def test_cli_integrate_human(appdir, dest_root, capsys):
    rc = cli.main(["integrate", str(appdir), "--dest", str(dest_root)])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Integrated" in out
    assert "Desktop:" in out
    assert "Icon:" in out
    assert "MIME:" in out


def test_cli_integrate_missing_file_errors(capsys, tmp_path):
    rc = cli.main(["integrate", str(tmp_path / "nope"), "--dest", str(tmp_path / "d")])
    assert rc == cli.EXIT_ERROR
    assert capsys.readouterr().err.strip()


def test_cli_integrate_missing_required_key_errors(appdir, dest_root, capsys):
    (appdir / "sample.desktop").write_text(DESKTOP_NO_EXEC, encoding="utf-8")
    rc = cli.main(["integrate", str(appdir), "--dest", str(dest_root)])
    assert rc == cli.EXIT_ERROR
    assert "missing required key" in capsys.readouterr().err


@pytest.mark.skipif(
    not os.environ.get("PENGYOS_IT"),
    reason="PENGYOS_IT=1 required for the real end-to-end run",
)
def test_integration_real_extracted_prefix_runs_no_poison(tmp_path):
    """A real extracted prefix with a poison AppRun: integrate must not run it."""
    assert Path(POISON_PATH).exists() is False
    appdir = _write_appdir(tmp_path / "app")
    result = integrator.integrate_app(appdir, tmp_path / "xdg")
    assert result.desktop_path is not None
    assert not Path(POISON_PATH).exists()
