"""The Desktop's App Library folders.

COSMIC's built-in folders are Office, System and Utilities. PengyOS ships no
office software, so Office was an empty folder, while the eight pengy-* games
(Categories=Game) had none and sat loose on the home page. The overlay's
system default replaces the folder list: Games first, then System and
Utilities exactly as COSMIC defines them (their cosmic-* names keep the
translations). App Library writes the file into ~/.config only when someone
edits a folder, so this default is what every user sees until they do.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
GROUPS = (ROOT / "buildroot-external" / "board" / "desktop" / "rootfs-overlay" / "usr" / "share"
          / "cosmic" / "com.system76.CosmicAppLibrary" / "v1" / "groups")
APPS = ROOT / "buildroot-external" / "board" / "desktop" / "rootfs-overlay" / "usr" / "share" / "applications"


def test_games_folder_and_no_empty_office():
    text = GROUPS.read_text()
    names = re.findall(r'name: "([^"]+)"', text)
    assert names == ["Games", "cosmic-system", "cosmic-utilities"], names
    assert 'categories: ["Game"]' in text
    assert "cosmic-office" not in text and '"Office"' not in text


def test_the_ron_is_balanced():
    text = GROUPS.read_text()
    for a, b in ("()", "[]"):
        assert text.count(a) == text.count(b), f"unbalanced {a}{b}"


def test_the_games_are_what_the_folder_collects():
    games = [p.name for p in APPS.glob("*.desktop")
             if re.search(r"^Categories=.*\bGame;", p.read_text(), re.M)]
    assert len(games) >= 8, games
