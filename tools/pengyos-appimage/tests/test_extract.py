"""Tests for :mod:`pengyos_appimage.extract`.

The extraction tools shell out to external binaries. These unit tests replace
the :meth:`SquashfsTool.run` seam with a *materializing* fake that writes a
canned AppDir into the destination prefix — but crucially also drops a poison
``AppRun`` into that prefix. If extraction ever tried to execute it, the poison
marker would be created; its absence proves the invariant that extraction only
reads files out of the payload.

An opt-in integration test exercises the real ``unsquashfs`` against the
committed fixture when ``PENGYOS_IT=1``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from pengyos_appimage import cli, extract as extractor
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


class MaterializingTool(squashfs.SquashfsTool):
    """A fake tool that writes a canned AppDir to the prefix and lists it.

    It materialises *every* declared file into the destination (including a
    poison ``AppRun``), then returns the on-disk regular files — exactly the
    shape a real extraction tool would return. It never shells out.
    """

    name = "materializing"

    def __init__(self, contents: dict[str, str | bytes]):
        self.contents = contents

    def available(self) -> bool:
        return True

    def list_files(self, appimage, payload_offset) -> list[str]:
        return sorted(self.contents)

    def extract_to_dir(self, appimage, payload_offset, dest) -> list[str]:
        root = Path(dest)
        for rel, content in self.contents.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")
        return sorted(
            str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
        )

    def run(self, args, input=None):  # pragma: no cover - never shells out
        raise AssertionError("materializing fake must not shell out")


def _valid_contents() -> dict[str, str]:
    return {
        "AppRun": "#!/bin/sh\necho sample\n",
        "sample.desktop": DESKTOP_CONTENT,
        "usr/bin/sample": "#!/bin/sh\necho sample\n",
    }


def _extract(sample_appimage, contents, tool=None):
    with tempfile.TemporaryDirectory() as to:
        tool = tool or MaterializingTool(contents)
        return extractor.extract_appimage(sample_appimage, to, tool=tool)


def test_extract_reports_structure(sample_appimage):
    result = _extract(sample_appimage, _valid_contents())
    assert result.app_entry == "AppRun"
    assert result.desktop == "sample.desktop"
    assert result.root_directory is None
    assert result.ok
    assert result.file_count == 3
    assert "usr/bin/sample" in result.files


def test_extract_returns_files_relative_to_prefix(sample_appimage):
    with tempfile.TemporaryDirectory() as to:
        result = extractor.extract_appimage(
            sample_appimage, to, tool=MaterializingTool(_valid_contents())
        )
        # The prefix is a real directory...
        assert result.prefix.is_dir()
    # ...and the returned paths are relative to it (forward slashes, no root).
    assert all(not p.startswith("/") for p in result.files)


def test_extract_validates_missing_apprun(sample_appimage):
    contents = {
        "sample.desktop": DESKTOP_CONTENT,
        "usr/bin/sample": "#!/bin/sh\necho sample\n",
    }
    result = _extract(sample_appimage, contents)
    assert result.app_entry is None
    assert not result.ok
    assert any("AppRun" in w for w in result.warnings)


def test_extract_validates_missing_desktop(sample_appimage):
    contents = {
        "AppRun": "#!/bin/sh\necho sample\n",
        "usr/bin/sample": "#!/bin/sh\necho sample\n",
    }
    result = _extract(sample_appimage, contents)
    assert result.desktop is None
    assert not result.ok
    assert any(".desktop" in w for w in result.warnings)


def test_validate_warns_when_root_directory_missing():
    # When the trailer declares a root directory that is absent from the
    # payload, validation notes it as a warning.
    from pengyos_appimage import trailer

    meta = trailer.TrailerMetadata(format="2", root_directory="opt/app")
    result = extractor._validate(Path("/x"), meta, ["AppRun", "sample.desktop"])
    assert any("opt/app" in w for w in result.warnings)


def test_validate_ok_when_root_directory_present():
    # When the declared root directory exists and the entry point + .desktop
    # are present, no warning is raised.
    from pengyos_appimage import trailer

    meta = trailer.TrailerMetadata(format="2", root_directory="usr")
    result = extractor._validate(
        Path("/x"), meta, ["AppRun", "usr/bin/sample", "sample.desktop"]
    )
    assert result.root_directory == "usr"
    assert result.ok


def test_extract_missing_file_raises():
    with pytest.raises(extractor.ExtractError):
        with tempfile.TemporaryDirectory() as to:
            extractor.extract_appimage("/no/such/appimage", to)


def test_extract_bad_trailer_raises(tmp_path):
    bad = tmp_path / "bad.AppImage"
    bad.write_bytes(b"not an appimage")
    with pytest.raises(extractor.ExtractError):
        with tempfile.TemporaryDirectory() as to:
            extractor.extract_appimage(bad, to)


def test_extract_no_files_raises(sample_appimage):
    class EmptyTool(squashfs.SquashfsTool):
        name = "empty"

        def available(self) -> bool:
            return True

        def extract_to_dir(self, appimage, payload_offset, dest):
            return []

    with pytest.raises(extractor.ExtractError):
        with tempfile.TemporaryDirectory() as to:
            extractor.extract_appimage(sample_appimage, to, tool=EmptyTool())


def test_extract_destination_must_be_directory(sample_appimage, tmp_path):
    # A destination that exists but is a regular file is rejected.
    not_a_dir = tmp_path / "a_file"
    not_a_dir.write_text("x")
    with pytest.raises(extractor.ExtractError):
        with tempfile.TemporaryDirectory() as to:
            extractor.extract_appimage(sample_appimage, not_a_dir)


def test_extract_result_json_serialisable(sample_appimage):
    import json

    result = _extract(sample_appimage, _valid_contents())
    payload = json.dumps(result.to_dict())
    parsed = json.loads(payload)
    assert parsed["app_entry"] == "AppRun"
    assert parsed["file_count"] == 3


def test_extract_does_not_execute_poison_hook(sample_appimage, monkeypatch):
    """The poison invariant: extraction only reads files, never runs AppRun.

    A poison ``AppRun`` is materialised into the prefix; ``subprocess.run`` is
    spied so that *any* attempt to execute it (or the AppImage) raises. Its
    absence proves extraction never touches the embedded hook.
    """
    if Path(POISON_PATH).exists():
        Path(POISON_PATH).unlink()

    def spy_run(cmd, *args, **kwargs):
        joined = " ".join(str(c) for c in cmd)
        if "AppRun" in joined or POISON_PATH in joined:
            raise AssertionError(f"extract executed the AppImage/AppRun: {cmd}")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", spy_run)

    contents = {
        "AppRun": "#!/bin/sh\necho poisoned > " + repr(POISON_PATH) + "\n",
        "sample.desktop": DESKTOP_CONTENT,
        "usr/bin/sample": "#!/bin/sh\necho sample\n",
    }
    try:
        result = _extract(sample_appimage, contents)
    finally:
        if Path(POISON_PATH).exists():
            Path(POISON_PATH).unlink()
    assert result.ok
    assert not Path(POISON_PATH).exists()


def test_cli_extract_human(sample_appimage, capsys):
    with tempfile.TemporaryDirectory() as to:
        rc = cli.main(["extract", str(sample_appimage), "--to", to])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Extracted to" in out
    assert "App entry:" in out
    assert "Structure OK." in out


def test_cli_extract_missing_file_errors(capsys):
    with tempfile.TemporaryDirectory() as to:
        rc = cli.main(["extract", "/no/such/file", "--to", to])
    assert rc == cli.EXIT_ERROR
    assert capsys.readouterr().err.strip()


@pytest.mark.skipif(
    not os.environ.get("PENGYOS_IT"),
    reason="PENGYOS_IT=1 required for the real end-to-end run",
)
def test_integration_real_unsquashfs(sample_appimage):
    """Real unsquashfs extracts the poison AppRun; validation must pass."""
    assert shutil.which("unsquashfs") is not None
    with tempfile.TemporaryDirectory() as to:
        result = extractor.extract_appimage(sample_appimage, to)
    assert result.app_entry == "AppRun"
    assert result.desktop == "sample.desktop"
    assert result.ok
    assert not Path(POISON_PATH).exists()
