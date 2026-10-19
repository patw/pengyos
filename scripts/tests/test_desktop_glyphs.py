"""Host GTK3 windows can load SVG icons, and the terminal has its icon glyphs.

WHY
---
Both gaps were silent (beholaptop, 26.10.32):

* gdk-pixbuf had no SVG loader (Buildroot builds librsvg with
  --disable-pixbuf-loader, and the image did not build it). The Cosmic theme
  is SVG only, so xdg-desktop-portal-gtk's dialogs could load none of its
  icons. The Desktop post-build now fails the build if loaders.cache lists no
  SVG loader; these tests pin the override and that guard.
* starship's default prompt draws Nerd Fonts icons (U+E0A0, U+E718) and Claude
  Code draws U+23F5; no shipped font had them, so they were boxes. Two font
  recipes cover them through fontconfig's per-glyph fallback, without
  upstream's fontconfig file (it makes the icon font the preferred monospace).
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXT = ROOT / "buildroot-external"


def _defconfigs():
    for edition in ("pengyos_desktop_defconfig", "pengyos_desktop_nvidia_defconfig"):
        yield edition, (EXT / "configs" / edition).read_text(encoding="utf-8")


def test_librsvg_builds_its_pixbuf_loader():
    mk = (EXT / "external.mk").read_text(encoding="utf-8")
    block = mk[mk.index("ifeq ($(BR2_PACKAGE_LIBRSVG),y)"):]
    block = block[:block.index("endif")]
    assert "$(filter-out --disable-pixbuf-loader,$(LIBRSVG_CONF_OPTS)) --enable-pixbuf-loader" in block
    # The target cache is generated from the host loader dir: host-librsvg must
    # be installed first, and the cache rebuilt after the target loader lands.
    assert "$(LIBRSVG_TARGET_CONFIGURE): $(HOST_LIBRSVG_TARGET_INSTALL_HOST)" in block
    assert "LIBRSVG_POST_INSTALL_TARGET_HOOKS += GDK_PIXBUF_UPDATE_CACHE" in block
    # pkg-config's loader dirs carry the staging sysroot; install must name
    # target paths or DESTDIR doubles them (the build failed that way).
    assert "gdk_pixbuf_moduledir=/usr/lib/gdk-pixbuf-2.0/2.10.0/loaders" in block
    for step, dest in (("STAGING", "STAGING_DIR"), ("TARGET", "TARGET_DIR")):
        assert f"LIBRSVG_INSTALL_{step}_OPTS = DESTDIR=$({dest}) $(LIBRSVG_PIXBUF_DIRS) install" in block
    for edition, text in _defconfigs():
        assert "\nBR2_PACKAGE_LIBRSVG=y\n" in text, edition


def test_post_build_refuses_an_image_without_the_svg_loader():
    post = (EXT / "board/desktop/post-build.sh").read_text(encoding="utf-8")
    guard = post[post.index("# --- gdk-pixbuf can load those icons"):]
    guard = guard[:guard.index("\n# --- ", 10)]
    assert "loaders.cache" in guard
    assert re.search(r"grep -q '\^\"svg\" '", guard)
    assert guard.count("exit 1") == 2


def test_terminal_glyph_fonts_are_shipped():
    cfg = (EXT / "Config.in").read_text(encoding="utf-8")
    for pkg in ("font-symbols-nerd", "font-noto-symbols2"):
        assert f'source "$BR2_EXTERNAL_PENGYOS_PATH/package/{pkg}/Config.in"' in cfg
    for edition, text in _defconfigs():
        assert "\nBR2_PACKAGE_FONT_SYMBOLS_NERD=y\n" in text, edition
        assert "\nBR2_PACKAGE_FONT_NOTO_SYMBOLS2=y\n" in text, edition


def test_nerd_font_does_not_take_over_monospace():
    mk = (EXT / "package/font-symbols-nerd/font-symbols-nerd.mk").read_text(encoding="utf-8")
    assert "10-nerd-font-symbols.conf" not in mk
    assert "fontconfig" not in mk.lower().split("define")[1]
