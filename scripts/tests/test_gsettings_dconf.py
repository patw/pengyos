"""The Desktop's GSettings backend is dconf, and old keyfile settings move into it.

WHY
---
Without a GSettings backend module GLib stores settings in
~/.config/glib-2.0/settings/keyfile, and each writer rewrites the whole file.
cosmic-settings-daemon writes its GNOME keys as concurrent `gsettings set`
processes (six at login; color-scheme + gtk-theme on every dark/light switch),
so writes were lost: a fresh 26.10.32 install lost button-layout and Firefox
and Chrome had a close button only (beholaptop). dconf-service applies writes
in order. The gschema override (test_gsettings_defaults.py) covers the
defaults; dconf covers every later write.

Turning dconf on makes GLib ignore the old keyfile, so a user unit loads it
into dconf once. These tests pin the wiring, run the migration against a real
dconf on a private session bus, and run the daemon's write pattern against
dconf.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXT = ROOT / "buildroot-external"
PKG = EXT / "package" / "dconf"
OVERLAY = EXT / "board" / "desktop" / "rootfs-overlay"
SCRIPT = OVERLAY / "usr/libexec/pengyos/gsettings-keyfile-migrate"
UNIT = "pengyos-gsettings-keyfile-migrate.service"

needs_dconf = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("dconf", "gsettings", "dbus-run-session")),
    reason="needs the host's dconf, gsettings and dbus-run-session",
)


def test_recipe_is_wired():
    assert 'source "$BR2_EXTERNAL_PENGYOS_PATH/package/dconf/Config.in"' in \
        (EXT / "Config.in").read_text(encoding="utf-8")
    mk = (PKG / "dconf.mk").read_text(encoding="utf-8")
    # dconf.service is what D-Bus activation resolves (SystemdService=); see
    # board/common/check-dbus-units.py.
    assert "-Dsystemduserunitdir=/usr/lib/systemd/user" in mk
    assert "$(eval $(meson-package))" in mk


def test_desktop_editions_ship_dconf():
    layer = (EXT / "configs/layers/desktop.config").read_text(encoding="utf-8")
    assert "\nBR2_PACKAGE_DCONF=y\n" in layer
    for edition in ("pengyos_desktop_defconfig", "pengyos_desktop_nvidia_defconfig"):
        assert "\nBR2_PACKAGE_DCONF=y\n" in (EXT / "configs" / edition).read_text(encoding="utf-8")


def test_migration_unit_is_enabled():
    unit = (OVERLAY / "usr/lib/systemd/user" / UNIT).read_text(encoding="utf-8")
    assert "ExecStart=/usr/libexec/pengyos/gsettings-keyfile-migrate" in unit
    assert "ConditionPathExists=%h/.config/glib-2.0/settings/keyfile" in unit
    link = OVERLAY / "etc/systemd/user/default.target.wants" / UNIT
    assert os.readlink(link) == f"/usr/lib/systemd/user/{UNIT}"
    assert os.access(SCRIPT, os.X_OK)


def _session(tmp_path: pathlib.Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GSETTINGS", "DBUS_SESSION", "XDG_CONFIG"))}
    env.update(HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / ".config"),
               XDG_RUNTIME_DIR=str(tmp_path / "run"))
    (tmp_path / "run").mkdir(mode=0o700)
    return env


def _in_session(env: dict[str, str], script: str) -> str:
    return subprocess.run(["dbus-run-session", "--", "sh", "-c", script], env=env,
                          check=True, capture_output=True, text=True, timeout=60).stdout


@needs_dconf
def test_migration_loads_the_keyfile_into_dconf(tmp_path):
    env = _session(tmp_path)
    keyfile = tmp_path / ".config/glib-2.0/settings/keyfile"
    keyfile.parent.mkdir(parents=True)
    # As GLib's keyfile backend writes it (copied from beholaptop, plus a key
    # a user set by hand).
    keyfile.write_text(
        "[org/gnome/desktop/interface]\n"
        "font-name='Open Sans'\n"
        "monospace-font-name='Noto Sans Mono'\n"
        "\n"
        "[org/gnome/system/location]\n"
        "enabled=false\n", encoding="utf-8")
    out = _in_session(env, f"{SCRIPT} && "
                           "dconf read /org/gnome/desktop/interface/font-name && "
                           "dconf read /org/gnome/system/location/enabled")
    assert out.splitlines()[-2:] == ["'Open Sans'", "false"]
    assert not keyfile.exists()
    assert keyfile.with_name("keyfile.migrated-to-dconf").exists()
    # Second login: nothing to do.
    _in_session(env, str(SCRIPT))


@needs_dconf
def test_a_bad_keyfile_is_kept_for_the_next_login(tmp_path):
    env = _session(tmp_path)
    keyfile = tmp_path / ".config/glib-2.0/settings/keyfile"
    keyfile.parent.mkdir(parents=True)
    keyfile.write_text("[org/gnome/desktop/interface]\nfont-name=not a gvariant\n",
                       encoding="utf-8")
    r = subprocess.run(["dbus-run-session", "--", str(SCRIPT)], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    assert keyfile.exists()


@needs_dconf
def test_concurrent_writes_all_land_in_dconf(tmp_path):
    """cosmic-settings-daemon's login pattern: six `gsettings set` at once."""
    env = _session(tmp_path)
    env["GSETTINGS_BACKEND"] = "dconf"
    sets = {
        ("org.gnome.desktop.wm.preferences", "button-layout"): "':minimize,close'",
        ("org.gnome.desktop.interface", "icon-theme"): "'Pop'",
        ("org.gnome.desktop.interface", "font-name"): "'Fira Sans 11'",
        ("org.gnome.desktop.interface", "monospace-font-name"): "'Fira Mono 11'",
        ("org.gnome.desktop.interface", "color-scheme"): "'prefer-dark'",
        ("org.gnome.desktop.interface", "gtk-theme"): "'adw-gtk3-dark'",
    }
    spawn = " ".join(f'gsettings set {s} {k} "{v}" &' for (s, k), v in sets.items())
    reads = " && ".join(f"gsettings get {s} {k}" for (s, k) in sets)
    for _ in range(10):
        _in_session(env, "dconf reset -f /org/gnome/")
        got = _in_session(env, f"{spawn} wait; {reads}").split("\n")
        assert got[:len(sets)] == list(sets.values())
