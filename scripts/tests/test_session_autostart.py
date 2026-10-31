"""session-autostart starts XDG autostart entries once the desktop has a display.

It used to start them as soon as the user manager had WAYLAND_DISPLAY, about a
second before cosmic-session exported Xwayland's DISPLAY. The Steam flatpak,
added to the startup apps on behotv, refused to start in that gap ("requires
... the DISPLAY environment variable", 2026-10-31). Run here against a fake
`systemctl --user show-environment` whose answer changes as the test goes.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import threading
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = (REPO / "buildroot-external" / "board" / "desktop" / "rootfs-overlay"
          / "usr" / "libexec" / "pengyos" / "session-autostart")

FAKE_SYSTEMCTL = """#!/bin/sh
[ "$1 $2" = "--user show-environment" ] || exit 1
cat "$FAKE_ENV" 2>/dev/null
"""


def session(tmp: pathlib.Path, script: pathlib.Path = SCRIPT):
    bindir, runtime, config, out = (tmp / "bin", tmp / "run", tmp / "config", tmp / "out")
    for d in (bindir, runtime, config / "autostart", out):
        d.mkdir(parents=True)
    (bindir / "systemctl").write_text(FAKE_SYSTEMCTL)
    (bindir / "systemctl").chmod(0o755)
    (config / "autostart" / "game.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Game\n"
        f"Exec=sh -c 'echo \"$DISPLAY|$WAYLAND_DISPLAY\" > {out}/game'\n")
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", XDG_RUNTIME_DIR=str(runtime),
               XDG_CONFIG_HOME=str(config), XDG_CONFIG_DIRS=str(tmp / "none"),
               FAKE_ENV=str(tmp / "env"))
    return env, runtime, out


def compositor_up(tmp: pathlib.Path, runtime: pathlib.Path, *lines: str) -> None:
    (runtime / "wayland-1").touch()
    (tmp / "env").write_text("".join(l + "\n" for l in lines))


def wait_for(path: pathlib.Path, secs: float = 10) -> str:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if path.exists() and path.read_text().strip():
            return path.read_text().strip()
        time.sleep(0.1)
    raise AssertionError(f"{path} never written")


def test_entries_wait_for_xwaylands_display(tmp_path):
    env, runtime, out = session(tmp_path)
    p = subprocess.Popen([sys.executable, str(SCRIPT)], env=env, stderr=subprocess.PIPE, text=True)
    time.sleep(0.3)
    compositor_up(tmp_path, runtime, "WAYLAND_DISPLAY=wayland-1")
    # The race on behotv: Wayland is up, DISPLAY arrives ~1 s later.
    timer = threading.Timer(1.5, compositor_up,
                            (tmp_path, runtime, "WAYLAND_DISPLAY=wayland-1", "DISPLAY=:0"))
    timer.start()
    try:
        assert p.wait(timeout=20) == 0, p.stderr.read()
        assert wait_for(out / "game") == ":0|wayland-1"
    finally:
        timer.cancel()


def test_without_xwayland_entries_still_start(tmp_path):
    fast = tmp_path / "session-autostart"
    fast.write_text(SCRIPT.read_text().replace("XWAYLAND_WAIT = 15", "XWAYLAND_WAIT = 1"))
    env, runtime, out = session(tmp_path)
    compositor_up(tmp_path, runtime, "WAYLAND_DISPLAY=wayland-1")
    cp = subprocess.run([sys.executable, str(fast)], env=env, capture_output=True, text=True, timeout=20)
    assert cp.returncode == 0, cp.stderr
    assert "no Xwayland DISPLAY after 1s" in cp.stderr
    assert wait_for(out / "game") == "|wayland-1"


def test_the_grace_period_is_short():
    # A desktop with Xwayland turned off must not sit without its startup apps.
    assert "XWAYLAND_WAIT = 15" in SCRIPT.read_text()
