"""Flatpak GTK apps must get minimize/maximize/close by default.

WHY
---
Firefox, Chrome and VS Code (Flatpaks) draw their own titlebar and read which
buttons to draw from `org.gnome.desktop.wm.preferences button-layout`, served
by xdg-desktop-portal-gtk's Settings portal. The schema default is
`appmenu:close`: a close-only titlebar. This broke twice:

* 26.10.17: the portal was built `-Dsettings=disabled`, so nothing served the
  key (fixed in 364b674a, the portal recipe).
* 26.10.32: the portal served it, but the value was the schema default. The
  image has no dconf, so GSettings uses the keyfile backend, and
  cosmic-settings-daemon writes its four GNOME keys at login as four
  concurrent `gsettings set` processes, each rewriting the whole file. Some
  writes are lost (beholaptop's fresh install kept two of four).

The image now carries COSMIC's values as the schema defaults, so no login race
can leave a close-only titlebar. This test compiles the shipped override
against the real schema and reads the key back, the way the portal does.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXT = ROOT / "buildroot-external"
OVERRIDE = EXT / "board" / "desktop" / "gsettings" / "90-pengyos.gschema.override"
PORTAL_MK = EXT / "package" / "xdg-desktop-portal-gtk" / "xdg-desktop-portal-gtk.mk"
HOST_SCHEMAS = pathlib.Path("/usr/share/glib-2.0/schemas")

# COSMIC's defaults (libcosmic CosmicTk::default) as cosmic-settings-daemon
# writes them.
EXPECTED = {
    ("org.gnome.desktop.wm.preferences", "button-layout"): "':minimize,maximize,close'",
    ("org.gnome.desktop.interface", "icon-theme"): "'Cosmic'",
}


def test_portal_serves_gnome_settings():
    mk = PORTAL_MK.read_text(encoding="utf-8")
    assert "-Dsettings=enabled" in mk
    assert "gsettings-desktop-schemas" in mk


def test_override_is_installed_where_schemas_are_compiled():
    mk = (EXT / "external.mk").read_text(encoding="utf-8")
    assert "board/desktop/gsettings/*.gschema.override" in mk
    assert "GSETTINGS_DESKTOP_SCHEMAS_POST_INSTALL_STAGING_HOOKS += PENGYOS_GSETTINGS_INSTALL_OVERRIDES" in mk


@pytest.mark.skipif(
    not (shutil.which("glib-compile-schemas") and shutil.which("gsettings")),
    reason="needs the host's glib-compile-schemas and gsettings",
)
def test_compiled_defaults_give_three_buttons(tmp_path):
    schemas = re.findall(r"^\[([\w.]+)\]$", OVERRIDE.read_text(encoding="utf-8"), re.M)
    for s in schemas:
        xml = HOST_SCHEMAS / f"{s}.gschema.xml"
        if not xml.is_file():
            pytest.skip(f"host has no {xml.name}")
        shutil.copy(xml, tmp_path)
    for enums in HOST_SCHEMAS.glob("org.gnome.desktop.enums.xml"):
        shutil.copy(enums, tmp_path)
    shutil.copy(OVERRIDE, tmp_path)
    # --strict: an override naming a missing key or a bad value fails here,
    # not silently at image time.
    subprocess.run(["glib-compile-schemas", "--strict", str(tmp_path)], check=True,
                   capture_output=True, text=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GSETTINGS")}
    env.update(GSETTINGS_SCHEMA_DIR=str(tmp_path), GSETTINGS_BACKEND="memory",
               XDG_DATA_DIRS=str(tmp_path))
    for (schema, key), want in EXPECTED.items():
        got = subprocess.run(["gsettings", "get", schema, key], env=env, check=True,
                             capture_output=True, text=True).stdout.strip()
        assert got == want, f"{schema} {key} defaults to {got}, not {want}"
