#!/usr/bin/env python3
"""Fail the Desktop build if COSMIC Files would draw a common file type with a blank icon.

COSMIC Files asks shared-mime-info for a file's icon names and draws the first one
the icon theme has (``cosmic-files/src/mime_icon.rs``, xdg-mime ``lookup_icon_names``):

  1. the type's ``icons`` entry, if the MIME database has one;
  2. ``<type>-<subtype>`` (``application/x-sharedlib`` -> ``application-x-sharedlib``);
  3. its ``generic-icons`` entry, else ``<type>-x-generic``.

It looks them up as SVG only (``prefer_svg`` -> ``force_svg``) in the theme COSMIC
uses and the themes it inherits. Until 2026-11-22 the image had no
``application-x-generic`` anywhere, so 113 of 793 types drew a blank tile, among
them ``application/x-sharedlib``, which is what every PIE binary sniffs as.

cosmic-files patch 0003 now ends every chain with ``text-x-generic``, so nothing
draws blank any more. That is a floor, not an icon. This check replays the lookup
*without* it, so a theme change that drops a common type's icon still fails here.

It checks every theme a user gets without asking: Cosmic (COSMIC's own, always)
and, when the image has them, the default Papirus-Dark / Papirus-Light
(papirus-icon-theme, the Glacier defaults since 2026-11-22).

    check-mime-icons.py TARGET_DIR    exit 1 and name every REQUIRED type that is blank
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: The theme COSMIC asks first (libcosmic's icon_theme::COSMIC); it must exist.
THEME = "Cosmic"
#: The image's default icon themes, checked when present.
DEFAULT_THEMES = ("Papirus-Dark", "Papirus-Light")

#: Types a user meets in an ordinary home directory or /usr: each must draw a real
#: icon of its own or its generic one. The PIE/shared-library pair is the one that
#: went blank (1075 of the 2026-11-22 Desktop's 1280 /usr/bin + /usr/lib/pengyos files).
REQUIRED = (
    "application/x-sharedlib", "application/x-executable", "application/x-pie-executable",
    "application/octet-stream", "application/x-shellscript", "text/x-python3",
    "text/plain", "text/html", "application/json", "application/x-desktop",
    "application/pdf", "application/zip", "application/x-compressed-tar",
    "application/gzip", "application/x-iso9660-image", "application/x-raw-disk-image",
    "application/vnd.sqlite3", "application/x-core",
    "image/png", "image/jpeg", "audio/mpeg", "audio/flac", "video/mp4",
    "inode/directory",
    "model/stl",  # 3D printing; Pop's "model" icon, linked as model-x-generic
)


def _kv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        if ":" in line and not line.startswith("#"):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def icon_names(mime: str, icons: dict[str, str], generic: dict[str, str]) -> list[str]:
    """xdg-mime's lookup_icon_names, the list COSMIC Files tries in order."""
    names = [icons[mime]] if mime in icons else []
    names.append(mime.replace("/", "-"))
    names.append(generic.get(mime, mime.split("/", 1)[0] + "-x-generic"))
    return names


def _inherits(theme_dir: Path) -> list[str]:
    try:
        for line in (theme_dir / "index.theme").read_text(encoding="utf-8").splitlines():
            if line.startswith("Inherits="):
                return [t.strip() for t in line.split("=", 1)[1].split(",") if t.strip()]
    except OSError:
        pass
    return []


def svg_names(icons_root: Path, theme: str) -> set[str]:
    """Every SVG icon name reachable from ``theme`` through Inherits (plus hicolor)."""
    names: set[str] = set()
    seen: set[str] = set()
    todo = [theme, "hicolor"]
    while todo:
        t = todo.pop(0)
        if t in seen:
            continue
        seen.add(t)
        d = icons_root / t
        for dirpath, _, files in os.walk(d, followlinks=True):
            for f in files:
                p = Path(dirpath) / f
                # A dangling link draws nothing either.
                if f.endswith(".svg") and p.exists():
                    names.add(f[:-4])
        todo.extend(_inherits(d))
    return names


def audit(target: Path, theme: str = THEME) -> tuple[list[str], list[str], int]:
    """(required types that are blank, all blank types, number of types in the database)."""
    mime_dir = target / "usr/share/mime"
    types = [l.strip() for l in (mime_dir / "types").read_text(encoding="utf-8").splitlines()
             if l.strip()]
    icons, generic = _kv(mime_dir / "icons"), _kv(mime_dir / "generic-icons")
    have = svg_names(target / "usr/share/icons", theme)
    blank = [m for m in types if not any(n in have for n in icon_names(m, icons, generic))]
    known = set(types)
    req_blank = [m for m in REQUIRED
                 if m in known and not any(n in have for n in icon_names(m, icons, generic))]
    return req_blank, blank, len(types)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    target = Path(argv[1])
    if not (target / "usr/share/mime/types").is_file():
        print(f"check-mime-icons: ERROR: {target}/usr/share/mime/types is missing "
              "(shared-mime-info not installed?)", file=sys.stderr)
        return 1
    icons = target / "usr/share/icons"
    themes = [THEME] + [t for t in DEFAULT_THEMES if (icons / t / "index.theme").is_file()]
    failed = False
    for theme in themes:
        req_blank, blank, total = audit(target, theme)
        for m in req_blank:
            print(f"check-mime-icons: ERROR: with {theme} icons, COSMIC Files draws {m} "
                  "with no icon of its own or its generic one", file=sys.stderr)
            failed = True
        if not req_blank:
            print(f"check-mime-icons: {theme}: every common file type has an icon "
                  f"({total - len(blank)} of {total} types; the other {len(blank)} fall "
                  "back to text-x-generic)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
