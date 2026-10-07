"""Tests for :mod:`pengyos_appimage.desktop`."""

from __future__ import annotations

import pytest

from pengyos_appimage import desktop


def test_parses_standard_entry():
    text = (
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=Sample App\n"
        "Icon=sample\n"
        "Exec=sample %F\n"
        "Terminal=false\n"
    )
    doc = desktop.parse_desktop(text)
    entry = doc.main
    assert entry is not None
    assert entry.section == "Desktop Entry"
    assert entry.entry_type == "Application"
    assert entry.name == "Sample App"
    assert entry.icon == "sample"
    assert entry.exec == "sample %F"


def test_preserves_key_case_and_localized_keys():
    text = (
        "[Desktop Entry]\n"
        "Name=English\n"
        "Name[de]=Deutsch\n"
        "Type=Application\n"
    )
    entry = desktop.parse_desktop(text).main
    assert entry.keys["Name"] == "English"
    assert entry.keys["Name[de]"] == "Deutsch"
    # The default (non-localised) name is what we report.
    assert entry.name == "English"


def test_ignores_comments_and_blank_lines():
    text = (
        "# a comment\n"
        "; another comment\n"
        "\n"
        "[Desktop Entry]\n"
        "Type=Link\n"
        "\n"
    )
    entry = desktop.parse_desktop(text).main
    assert entry.entry_type == "Link"


def test_multiple_sections():
    text = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Main\n"
        "[Desktop Action New]\n"
        "Name=New\n"
        "Exec=sample --new\n"
    )
    doc = desktop.parse_desktop(text)
    assert len(doc.entries) == 2
    assert doc.main.name == "Main"


def test_parse_desktop_file(tmp_path):
    path = tmp_path / "foo.desktop"
    path.write_text("[Desktop Entry]\nType=Application\nName=Foo\n", encoding="utf-8")
    doc = desktop.parse_desktop_file(path)
    assert doc.main.name == "Foo"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   \n\t",
        "Type=Application\n",  # key outside any section
        "[Desktop Entry]\nno_equals_here\n",  # missing '='
    ],
)
def test_parse_errors(text):
    with pytest.raises(desktop.DesktopParseError):
        desktop.parse_desktop(text)
