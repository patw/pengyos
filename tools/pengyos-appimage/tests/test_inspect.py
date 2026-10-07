"""Tests for :mod:`pengyos_appimage.inspect` and the ``inspect`` CLI.

The central invariant under test: inspection never executes an AppImage's
embedded hooks. We prove this two ways:

* Structurally — a fake extraction tool writes a *poison* ``AppRun`` to the
  temp prefix; we patch ``subprocess.run`` so that invoking it (or the AppImage)
  would raise. Inspection must succeed without invoking anything.
* End-to-end (integration) — the committed fixture's real poison ``AppRun`` is
  extracted by the real ``unsquashfs``; we assert the poison marker is never
  created.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from pengyos_appimage import cli, desktop, inspect as inspector
from pengyos_appimage import squashfs

# The poison marker the fixture's embedded AppRun would create if ever run.
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


class ExtractingTool(squashfs.SquashfsTool):
    """A fake tool that materialises a canned AppDir in the temp prefix.

    Crucially it also writes a poison ``AppRun`` (executable) into the prefix,
    exactly as a real extraction would. If inspection ever tried to run it, the
    poison marker would be created — so its absence proves the invariant.
    """

    name = "extracting"

    def __init__(self, files: list[str], desktop_content: str = DESKTOP_CONTENT):
        self.files = files
        self.desktop_content = desktop_content
        self.run_calls: list[list[str]] = []

    def available(self) -> bool:
        return True

    def list_files(self, appimage, payload_offset) -> list[str]:
        return self.files

    def extract_to_dir(self, appimage, payload_offset, dest) -> list[str]:
        root = Path(dest)
        (root / "sample.desktop").write_text(self.desktop_content, encoding="utf-8")
        icon = root / "usr" / "share" / "icons" / "sample.png"
        icon.parent.mkdir(parents=True, exist_ok=True)
        (icon).write_bytes(b"\x89PNG\r\n\x1a\n")  # tiny stand-in icon
        # A poison AppRun, executable, exactly as real extraction would produce.
        poison = root / "AppRun"
        poison.write_text(
            "#!/bin/sh\necho poisoned > " + repr(POISON_PATH) + "\n",
            encoding="utf-8",
        )
        poison.chmod(0o755)
        return self.files

    def run(self, args, input=None):  # pragma: no cover - not used here
        self.run_calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _tool(sample_appimage: Path) -> ExtractingTool:
    return ExtractingTool(
        files=[
            "AppRun",
            "sample.desktop",
            "usr/share/icons/sample.png",
        ]
    )


def test_inspect_reports_type_and_desktop(sample_appimage):
    tool = _tool(sample_appimage)
    result = inspector.inspect_appimage(sample_appimage, tool=tool)
    assert result.format == "2"
    assert result.type == "Bundle"
    assert result.arch == "x86_64"
    assert result.payload_offset == 4096
    assert result.desktop.name == "Sample App"
    assert result.desktop.icon == "sample"
    assert result.desktop.exec == "sample %F"
    assert result.desktop.type == "Application"


def test_inspect_finds_icon(sample_appimage):
    tool = _tool(sample_appimage)
    result = inspector.inspect_appimage(sample_appimage, tool=tool)
    assert result.icon.found is True
    assert result.icon.path.endswith("sample.png")
    assert isinstance(result.icon.size, int)


def test_inspect_json_serialisable(sample_appimage):
    tool = _tool(sample_appimage)
    result = inspector.inspect_appimage(sample_appimage, tool=tool)
    # Must round-trip through JSON (used by the CLI --json output).
    payload = json.dumps(result.to_dict())
    assert json.loads(payload)["desktop"]["name"] == "Sample App"


def test_inspect_does_not_execute_poison_hook(
    sample_appimage, monkeypatch
):
    """The poison invariant: the embedded AppRun is never executed.

    The fake tool writes a poison ``AppRun`` into the prefix; ``subprocess.run``
    is spied so that *any* attempt to run it (or the AppImage) raises.
    """
    if Path(POISON_PATH).exists():
        Path(POISON_PATH).unlink()

    def spy_run(cmd, *args, **kwargs):
        joined = " ".join(str(c) for c in cmd)
        if "AppRun" in joined or "sample-appimage" in joined:
            raise AssertionError(f"inspect executed the AppImage/AppRun: {cmd}")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", spy_run)

    tool = _tool(sample_appimage)
    try:
        result = inspector.inspect_appimage(sample_appimage, tool=tool)
    finally:
        if Path(POISON_PATH).exists():
            Path(POISON_PATH).unlink()
    assert result.desktop.name == "Sample App"
    assert not Path(POISON_PATH).exists()


def test_inspect_missing_file_raises():
    with pytest.raises(inspector.InspectionError):
        inspector.inspect_appimage("/no/such/appimage", tool=_tool(Path(".")))


def test_inspect_bad_trailer_raises(tmp_path):
    bad = tmp_path / "bad.AppImage"
    bad.write_bytes(b"not an appimage")
    with pytest.raises(inspector.InspectionError):
        inspector.inspect_appimage(bad, tool=_tool(tmp_path))


def test_cli_inspect_human(sample_appimage, capsys):
    rc = cli.main(["inspect", str(sample_appimage)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Sample App" in out
    assert "Format:" in out
    assert "found" in out


def test_cli_inspect_json(sample_appimage, capsys):
    rc = cli.main(["inspect", str(sample_appimage), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["desktop"]["name"] == "Sample App"
    assert payload["icon"]["found"] is True


def test_cli_inspect_missing_file_errors(capsys):
    rc = cli.main(["inspect", "/no/such/file"])
    assert rc == cli.EXIT_ERROR
    assert capsys.readouterr().err.strip()


@pytest.mark.skipif(
    not os.environ.get("PENGYOS_IT"),
    reason="PENGYOS_IT=1 required for the real end-to-end run",
)
def test_integration_real_unsquashfs_runs_no_poison(sample_appimage):
    """Real unsquashfs extracts the poison AppRun; inspect must not run it."""
    result = inspector.inspect_appimage(sample_appimage)
    assert result.desktop.name == "Sample App"
    assert not Path(POISON_PATH).exists()
