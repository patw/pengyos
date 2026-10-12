"""COSMIC's icon theme inherits Pop; the image must ship Pop's symbolic icons.

WHY
---
cosmic-icons draws most of COSMIC's icons and names Pop as its parent for the
rest. PengyOS installed only Pop's cursors (`pop-cursor-theme`), with a Pop
`index.theme` listing no icon directories, so every icon COSMIC leaves to Pop
was missing and its button rendered as an empty square, silently: the Store's
release-notes button (help-info-symbolic), Files' grid/list toggle, the VPN
icon, Player's repeat/subtitle buttons, About pages' links, the Displays and
Date & Time page icons, the "touchpad disabled" OSD (found on behoweather,
26.10.25). The recipe now installs Pop/scalable (symbolic SVGs only, 2.3 MB),
and the Desktop post-build fails if any of the audited names is in no theme.
This test pins both without a build.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = ROOT / "buildroot-external" / "package" / "pop-cursor-theme"
POST_BUILD = ROOT / "buildroot-external" / "board" / "desktop" / "post-build.sh"

# Every icon the pinned COSMIC sources request by literal name that no app
# embeds and only Pop provides (audited 2026-10-12).
NEEDED_FROM_POP = {
    "help-info-symbolic", "link-symbolic", "view-grid-symbolic", "view-list-symbolic",
    "network-vpn-symbolic", "media-playlist-no-repeat-symbolic",
    "media-playlist-repeat-song-symbolic", "media-view-subtitles-symbolic",
    "preferences-desktop-display-symbolic", "preferences-system-time-symbolic",
    "touchpad-disabled-symbolic",
}


def test_recipe_installs_pops_symbolic_icons():
    mk = (PKG / "pop-cursor-theme.mk").read_text(encoding="utf-8")
    assert "cp -a $(@D)/Pop/scalable $(TARGET_DIR)/usr/share/icons/Pop/scalable" in mk
    # The 68 MB of fixed-size PNGs stay out: only scalable/ is copied.
    assert not re.search(r"Pop/(16x16|24x24|32x32|48x48|64x64|128x128|256x256)", mk)


def test_pop_index_lists_the_scalable_directories():
    idx = (PKG / "Pop-index.theme").read_text(encoding="utf-8")
    dirs = re.search(r"^Directories=(.*)$", idx, re.M)
    assert dirs, "Pop's index.theme must list its icon directories"
    listed = dirs.group(1).split(",")
    for d in ("actions", "apps", "devices", "places", "status"):
        assert f"scalable/{d}" in listed
    for d in listed:
        section = re.search(rf"^\[{re.escape(d)}\]\n((?:[A-Za-z]+=.*\n)+)", idx, re.M)
        assert section, f"{d} is listed but has no section"
        assert "Type=Scalable" in section.group(1)
    # Pop is COSMIC's parent, and must not send lookups back into Cosmic.
    assert re.search(r"^Inherits=hicolor$", idx, re.M)


def test_post_build_refuses_an_image_without_them():
    post = POST_BUILD.read_text(encoding="utf-8")
    block = post[post.index("# --- Icons COSMIC asks the theme for"):]
    block = block[: block.index("echo \"post-build: COSMIC's theme icons are all present\"")]
    checked = set(re.findall(r"[a-z-]+-symbolic", block))
    assert NEEDED_FROM_POP <= checked, NEEDED_FROM_POP - checked
    assert "exit 1" in block
