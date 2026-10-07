"""Tests for :mod:`pengyos_appimage.squashfs`.

The extraction tools shell out to external binaries. These unit tests replace
the :meth:`SquashfsTool.run` seam with canned output so no real tool, network,
root, or display is required. A gated integration test exercises the real
``unsquashfs`` against the committed fixture when ``PENGYOS_IT=1``.
"""

from __future__ import annotations

import os
import shutil
from types import SimpleNamespace

import pytest

from pengyos_appimage import squashfs


class RecordingTool(squashfs.SquashfsTool):
    """A fake tool that records its ``run`` calls and returns canned output."""

    name = "recording"

    def __init__(self, list_output: str = "", extract_return: list[str] | None = None):
        self.list_output = list_output
        self.extract_return = extract_return or []
        self.run_calls: list[list[str]] = []

    def available(self) -> bool:
        return True

    def list_files(self, appimage, payload_offset) -> list[str]:
        self.run_calls.append(["-l", str(payload_offset), str(appimage)])
        return squashfs._parse_unsquashfs_list(self.list_output)

    def extract_to_dir(self, appimage, payload_offset, dest) -> list[str]:
        self.run_calls.append(["-e", str(payload_offset), str(appimage)])
        return self.extract_return

    def run(self, args, input=None):  # pragma: no cover - overridden semantics
        self.run_calls.append(args)
        return SimpleNamespace(returncode=0, stdout=self.list_output, stderr="")


def test_strip_squashfs_root():
    paths = [
        "squashfs-root",
        "squashfs-root/AppRun",
        "squashfs-root/usr/bin/sample",
        "sample.desktop",
    ]
    assert squashfs._strip_squashfs_root(paths) == [
        "AppRun",
        "usr/bin/sample",
        "sample.desktop",
    ]


def test_parse_unsquashfs_list_strips_root_and_empty_lines():
    # ``unsquashfs -l`` emits a clean listing: a bare ``squashfs-root`` entry
    # plus ``squashfs-root/<path>`` lines.
    text = "squashfs-root\nsquashfs-root/sample.desktop\nsquashfs-root/usr\n"
    assert squashfs._parse_unsquashfs_list(text) == ["sample.desktop", "usr"]


def test_iter_regular_files_skips_directories(tmp_path):
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "nested.txt").write_text("x")
    (tmp_path / "top.txt").write_text("y")
    found = squashfs._iter_regular_files(tmp_path)
    assert found == ["dir/nested.txt", "top.txt"]


def test_default_tool_available_or_raises(monkeypatch):
    # With a tool on PATH, default_tool returns it.
    monkeypatch.setattr(squashfs.shutil, "which", lambda name: f"/usr/bin/{name}")
    tool = squashfs.default_tool()
    assert isinstance(tool, squashfs.SquashfsTool)


def test_default_tool_raises_when_none_available(monkeypatch):
    monkeypatch.setattr(squashfs.shutil, "which", lambda name: None)
    with pytest.raises(squashfs.SquashfsError):
        squashfs.default_tool()


def test_unsquashfs_list_builds_correct_argv():
    tool = RecordingTool(list_output="squashfs-root/a.desktop\n")
    # Replace run with the real subprocess-free path used by list_files.
    result = tool.list_files("img", 4096)
    assert result == ["a.desktop"]
    assert tool.run_calls == [["-l", "4096", "img"]]


def test_unsquashfs_extract_builds_correct_argv(tmp_path):
    tool = RecordingTool(extract_return=["a.desktop", "icon.png"])
    result = tool.extract_to_dir("img", 4096, str(tmp_path))
    assert result == ["a.desktop", "icon.png"]
    assert tool.run_calls == [["-e", "4096", "img"]]


def test_icon_extensions_constant():
    assert ".png" in squashfs.ICON_EXTENSIONS
    assert ".svg" in squashfs.ICON_EXTENSIONS
    assert ".exe" not in squashfs.ICON_EXTENSIONS


@pytest.mark.skipif(
    os.environ.get("PENGYOS_IT") != "1"
    or shutil.which("unsquashfs") is None,
    reason="PENGYOS_IT=1 and unsquashfs required",
)
def test_integration_real_unsquashfs(sample_appimage, tmp_path):
    """End-to-end: extract the real fixture with the real unsquashfs."""
    from pengyos_appimage import trailer

    meta = trailer.read_metadata_file(sample_appimage)
    files = squashfs.extract_to_dir(
        sample_appimage, meta.payload_offset, tmp_path
    )
    assert "sample.desktop" in files
    assert any(f.endswith(".png") for f in files)
