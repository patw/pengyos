"""``pengyos-install-tui install`` — the console installer.

Runs on the live medium and installs its image through the same engine as the
GUI (:mod:`pengyos_install.image`). The pages and rules live in
:mod:`~pengyos_install_tui.session`; this module loads the medium, picks a
front-end and runs the install:

* full screen (:mod:`~pengyos_install_tui.screen`) on any terminal of at least
  80x24 — the Linux console the stick boots to, or an SSH session;
* line by line (:mod:`~pengyos_install_tui.plain`) otherwise, or with ``--plain``.

Nothing is written before the user types the confirmation word; quitting or
EOF before that leaves every disk untouched.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
import time
from typing import Callable, TextIO

try:
    from pengyos_install import image as engine
except ImportError:  # pragma: no cover - exercised only on a broken install
    engine = None  # type: ignore[assignment]

__all__ = ["Restart", "run_install"]

SYSRQ = "/proc/sysrq-trigger"


class Restart:
    """Restart the machine once the install is done, stick or no stick.

    The last screen asks for the USB stick to be pulled before Enter, and with
    it goes every file of the live system: ``systemctl reboot`` failed with EIO
    on real hardware, and even when it runs from cache a clean shutdown needs
    the stick at every step. So the restart comes from the kernel alone: the
    sysrq trigger is opened here, before the prompt, and Enter syncs, remounts
    everything read-only and reboots. The engine has already unmounted and
    synced the new disk, and the live session has nothing else worth saving.
    ``systemctl reboot`` is only the fallback when the trigger cannot be opened.
    """

    def __init__(self, sysrq: str = SYSRQ, run: Callable = subprocess.run,
                 pause: Callable[[float], None] = time.sleep) -> None:
        self.run, self.pause = run, pause
        try:
            self.trigger: TextIO | None = open(sysrq, "w")
        except OSError:
            self.trigger = None

    def __call__(self) -> None:
        if self.trigger is None:
            self.run(["systemctl", "reboot"], check=False)
            return
        os.sync()
        for key, wait in (("u", 1.0), ("b", 0.0)):  # u: remount read-only, b: reboot
            self.trigger.write(key)
            self.trigger.flush()
            self.pause(wait)


def _progress(out: TextIO) -> Callable[[str, float, str], None]:
    state = {"line": ""}

    def report(phase: str, fraction: float, message: str) -> None:
        fraction = engine.overall_fraction(phase, fraction)
        filled = int(fraction * 30)
        line = f"\r  [{'#' * filled}{'.' * (30 - filled)}] {fraction * 100:5.1f}%  {message:<40}"
        if line != state["line"]:
            state["line"] = line
            out.write(line)
            out.flush()
        if phase == "done":
            out.write("\n")
            state["line"] = ""

    return report


def _run_plain(session, *, inp, secret, out, payload, install, reboot, restart) -> int:
    from pengyos_install_tui.plain import Aborted, ask_plain

    if not session.selectable():
        for d in session.disks:
            out.write(f"  {d.path:<14} {d.size_label:>9}  {d.model or '-'}"
                      f"  ({session.disk_note(d) or 'not usable'})\n")
        out.write("\nNo disk to install to was found.\n")
        return 1
    try:
        confirmed = ask_plain(session, inp=inp, secret=secret, out=out)
    except (Aborted, KeyboardInterrupt):
        out.write("\nInstaller quit; nothing was changed.\n")
        return 1
    if not confirmed:
        out.write("Nothing was changed.\n")
        return 1
    try:
        install(session.disk.path, session.answers(), payload_dir=payload, confirm=True,
                report=_progress(out))
    except engine.ImageInstallError as exc:
        out.write(f"\nThe install failed: {exc}\n")
        return 1
    if not reboot:
        out.write("\nPengyOS is installed. Remove the USB stick and reboot.\n")
        return 0
    restart = restart or Restart()
    try:
        inp("\nPengyOS is installed. Remove the USB stick, then press Enter to restart. ")
    except (EOFError, KeyboardInterrupt):
        pass
    restart()
    return 0


def run_install(
    *,
    inp: Callable[[str], str] = input,
    secret: Callable[[str], str] = getpass.getpass,
    out: TextIO = sys.stdout,
    payload: str | None = None,
    disks: list | None = None,
    install: Callable | None = None,
    fetch_keys: Callable[[str], str] | None = None,
    plain: bool | None = None,
    reboot: bool = False,
    wifi=None,
    restart: Callable[[], None] | None = None,
) -> int:
    """Run the installer; *plain* None picks the front-end from the terminal.

    *reboot*: restart the machine once the install is done and the user has
    pressed Enter (the install stick's launcher passes ``--reboot``), through
    *restart* (default :class:`Restart`).

    *wifi*: the Wi-Fi backend (:class:`pengyos_install_tui.wifi.Wifi`). By default
    the live system's own, only when installing from the medium itself (no
    *payload* given): the Wi-Fi pages then appear on machines with a Wi-Fi card.
    """
    if engine is None:
        out.write("pengyos-install-tui: the pengyos-install engine is not installed\n")
        return 1
    from pengyos_install_tui.session import Session

    install = install or engine.install_image
    try:
        manifest = engine.Manifest.load(engine.find_payload(payload))
    except engine.ImageInstallError as exc:
        out.write(f"pengyos-install-tui: {exc}\n")
        return 1
    disks = engine.list_disks() if disks is None else disks
    if wifi is None and payload is None:
        from pengyos_install_tui.wifi import Wifi

        wifi = Wifi()
    kw = {"fetch_keys": fetch_keys} if fetch_keys else {}
    session = Session(manifest, disks, wifi=wifi, **kw)

    if plain is None:
        from pengyos_install_tui.screen import can_run_screen

        plain = not can_run_screen(sys.stdin, out, os.environ.get("TERM"))
    if plain:
        return _run_plain(session, inp=inp, secret=secret, out=out, payload=payload,
                          install=install, reboot=reboot, restart=restart)

    from pengyos_install_tui.screen import App, run_screen

    if reboot:
        restart = restart or Restart()
    app = App(session, install=install, payload=payload, reboot=reboot)
    rc = run_screen(app)
    if rc == 0:
        if reboot:
            restart()
    elif app.mode == "failed" or app._before_quit == "failed":
        out.write(f"The install failed: {app.failure}\n")
    else:
        out.write("Installer quit; nothing was changed.\n")
    return rc
