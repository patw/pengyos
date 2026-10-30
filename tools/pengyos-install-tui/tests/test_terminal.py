"""The curses driver for real: the full-screen installer in a pseudo-terminal,
typed at like a keyboard, with a fake disk list and a fake install.

curses repaints only the cells that changed, so the pty's byte stream is no
place to look for text. The child logs every frame it paints to a file instead
and the test waits on that; keys still go through the pty and curses."""

from __future__ import annotations

import fcntl
import os
import pty
import select
import struct
import sys
import termios
import textwrap
import time
from pathlib import Path

import pytest
from conftest import ENGINE_SRC

ROOT = Path(__file__).resolve().parent.parent

CHILD = textwrap.dedent("""
    import sys
    sys.path[:0] = [{root!r}, {engine!r}]
    from pengyos_install.image import Disk, Manifest
    from pengyos_install_tui.screen import App, run_screen
    from pengyos_install_tui.session import Session

    def install(target, answers, **kw):
        kw["report"]("write", 0.5, "writing the system")
        with open({marker!r}, "w") as f:
            f.write(f"{{target}} {{answers.hostname}} {{answers.username}} {{answers.password}}")
        kw["report"]("done", 1.0, "done")

    disks = [Disk("sda", "/dev/sda", 8 * 10**9, "USB", is_boot=True),
             Disk("vda", "/dev/vda", 20 * 10**9, "QEMU")]
    render = App.render

    def logged(self, h, w):
        frame = render(self, h, w)
        with open({frames!r}, "a") as f:
            f.write("\\n".join(frame.text()) + "\\n\\f\\n")
        return frame

    App.render = logged
    s = Session(Manifest.load({payload!r}), disks, fetch_keys=lambda u: "")
    sys.exit(run_screen(App(s, install=install)))
""")


def _curses_term() -> str | None:
    import curses

    for term in ("xterm", "linux", "vt100"):
        try:
            curses.setupterm(term, sys.__stdout__.fileno())
            return term
        except Exception:
            continue
    return None


class Term:
    def __init__(self, script: str, term: str, frames: Path):
        self.frames, self.seen, self.raw = frames, 0, b""
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # child
            os.environ["TERM"] = term
            os.execv(sys.executable, [sys.executable, "-c", script])
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))

    def _drain(self, wait: float) -> None:
        if select.select([self.fd], [], [], wait)[0]:
            try:
                self.raw = (self.raw + os.read(self.fd, 65536))[-4000:]
            except OSError:
                pass

    def read_until(self, needle: str, timeout: float = 10.0) -> None:
        """Wait for a painted frame showing *needle* (newer than the last match)."""
        end = time.monotonic() + timeout
        while True:
            text = self.frames.read_text() if self.frames.exists() else ""
            at = text.find(needle, self.seen)
            if at >= 0:
                self.seen = at + len(needle)
                return
            if time.monotonic() > end:
                raise AssertionError(f"{needle!r} never painted; last frame: {text[-800:]}\n"
                                     f"terminal: {self.raw[-1500:]!r}")
            self._drain(0.05)

    def send(self, keys: str) -> None:
        for k in keys:
            os.write(self.fd, k.encode())
            time.sleep(0.02)

    def wait(self) -> int:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                return os.waitstatus_to_exitcode(status)
            self._drain(0.1)
        os.kill(self.pid, 9)
        raise AssertionError("the installer did not exit")


@pytest.fixture
def term():
    t = _curses_term()
    if t is None:
        pytest.skip("no terminfo entry to draw with")
    return t


def _start(tmp_path, payload, term):
    marker, frames = tmp_path / "installed", tmp_path / "frames"
    script = CHILD.format(root=str(ROOT), engine=str(ENGINE_SRC), marker=str(marker),
                          payload=payload, frames=str(frames))
    return Term(script, term, frames), marker


def test_install_through_the_real_terminal(tmp_path, payload, term):
    t, marker = _start(tmp_path, payload, term)
    t.read_until("Choose a disk")
    t.send("\r")
    t.read_until("Name this computer")
    t.send("\x15box\r")                       # Ctrl-U clears the default
    t.read_until("Create your account")
    t.send("pat\rhunter22\rhunter22\r")
    t.read_until("SSH access")
    t.send("\t\r")
    t.read_until("own keyboard")
    t.send("\r")
    t.read_until("Set up Pengy")
    t.send("\r\r\r")
    t.read_until("Ready to install")
    t.send("erase\r")
    t.read_until("PengyOS is installed")
    t.send("\r")
    assert t.wait() == 0
    assert marker.read_text() == "/dev/vda box pat hunter22"


def test_quit_changes_nothing(tmp_path, payload, term):
    t, marker = _start(tmp_path, payload, term)
    t.read_until("Choose a disk")
    t.send("\x03")
    t.read_until("Quit the installer?")
    t.send("y")
    assert t.wait() == 1
    assert not marker.exists()


def test_escape_goes_back(tmp_path, payload, term):
    t, marker = _start(tmp_path, payload, term)
    t.read_until("Choose a disk")
    t.send("\r")
    t.read_until("Name this computer")
    t.send("\x1b")
    t.read_until("Choose a disk")
    t.send("\x03y")
    assert t.wait() == 1
