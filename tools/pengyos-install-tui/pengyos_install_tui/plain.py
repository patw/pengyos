"""The line-by-line front-end: one prompt per field on stdin/stdout.

The fallback when there is no screen to draw on (no TTY, ``TERM=dumb``, a
terminal smaller than 80x24) or when asked for with ``--plain``. It asks the
same pages as the full-screen installer (:mod:`~pengyos_install_tui.session`),
re-asking a field with the reason when it is refused.
"""

from __future__ import annotations

import shutil
import textwrap
from typing import Callable, TextIO

from pengyos_install_tui.session import CONFIRM_WORD, DEFAULTS, WIFI, Session

__all__ = ["Aborted", "ask_plain"]


class Aborted(Exception):
    """The user left the installer before confirming (EOF or Ctrl-C)."""


def _read(fn: Callable[[str], str], prompt: str) -> str:
    try:
        return fn(prompt)
    except EOFError:
        raise Aborted() from None


def _choice_page(session: Session, page, inp, out: TextIO) -> None:
    if page.key == "login":
        yes = _read(inp, "Log in automatically, skipping the login screen? [y/N]: ")
        session.choose(page, 1 if yes.strip().lower() in ("y", "yes") else 0)
        return
    opts = session.options(page)
    numbers = [i for i, o in enumerate(opts) if o.enabled]
    for i, o in enumerate(opts):
        tag = f"{numbers.index(i) + 1})" if i in numbers else "  "
        note = f"  ({o.note})" if o.note else ""
        out.write(f"  {tag:>3} {o.label}{note}\n")
    out.write("\n")
    default = "1" if len(numbers) == 1 else ""
    while True:
        raw = _read(inp, f"Install to disk number{f' [{default}]' if default else ''}: ")
        raw = raw.strip() or default
        if raw.isdigit() and 1 <= int(raw) <= len(numbers):
            session.choose(page, numbers[int(raw) - 1])
            return
        out.write(f"  enter a number from 1 to {len(numbers)}\n")


def _form_page(session: Session, page, inp, secret, out: TextIO) -> None:
    fields = list(page.fields)
    i = 0
    while True:
        while i < len(fields):
            f = fields[i]
            if not session.field_active(f.key) or (
                    page.key == "pengy" and f.key != "pengy_base_url"
                    and not session.values.get("pengy_base_url")):
                session.set(f.key, "")
                i += 1
                continue
            # Offer the last answer only if it was accepted, else the default.
            current = session.values.get(f.key, "")
            if session.check(f.key, current):
                current = DEFAULTS.get(f.key, "")
            shown = f" [{current}]" if current and not f.secret else ""
            label, hint = session.field_text(f)
            hint = f" ({hint})" if hint and not shown else ""
            skip = "; Enter to skip" if f.optional and session.field_active(f.key) \
                and f.key != "key_password" else ""
            if f.key == "wifi_password":
                skip = "; Enter to skip Wi-Fi"
            raw = _read(secret if f.secret else inp, f"{label}{hint}{skip}{shown}: ")
            if f.key == "wifi_password" and not raw:
                session.choose(WIFI, 0)  # no Wi-Fi after all
                return
            if not raw.strip() and not f.secret:
                raw = "" if f.optional else current
            problem = session.set(f.key, raw)
            if problem:
                out.write(f"  {problem}\n")
                if f.key == "password2":
                    i -= 1  # ask for both passwords again
                continue
            i += 1
        if session.needs_network(page):
            out.write(f"  {_busy(session, page)}\n")
        bad = session.finish(page)
        if bad is None:
            return
        key, problem = bad
        out.write(f"  {problem}\n")
        i = next((n for n, f in enumerate(fields) if f.key == key), 0)
        if key == "password2":
            i -= 1


def _busy(session: Session, page) -> str:
    if page.key in ("wifi", "wifi_password"):
        return f"Connecting to {session.network.ssid} ..."
    src = session.key_source()
    verb = "Connecting to" if src.kind == "ssh" else "Fetching keys from"
    return f"{verb} {src.label} ..."


def _wifi_page(session: Session, page, inp, out: TextIO) -> None:
    while True:
        if session.networks is None:
            out.write("  Looking for Wi-Fi networks ...\n")
            problem = session.scan()
            if problem:
                out.write(f"  {problem}\n")
        opts = session.options(page)[:-1]  # the last row is "Scan again": r
        for n, o in enumerate(opts):
            note = f"  ({o.note})" if o.note else ""
            out.write(f"  {n:>2}) {o.label}{note}\n")
        out.write("   r) Scan again\n")
        raw = _read(inp, "Wi-Fi network number [0]: ").strip().lower() or "0"
        if raw == "r":
            session.networks = None
            continue
        if not raw.isdigit() or int(raw) >= len(opts):
            out.write(f"  enter a number from 0 to {len(opts) - 1}, or r\n")
            continue
        session.choose(page, int(raw))
        if session.needs_network(page):
            out.write(f"  {_busy(session, page)}\n")
            bad = session.finish(page)
            if bad:
                out.write(f"  {bad[1]}\n")
                continue
        return


def ask_plain(session: Session, *, inp: Callable[[str], str], secret: Callable[[str], str],
              out: TextIO) -> bool:
    """Ask every page. True once the user typed the confirmation word; False if
    they typed anything else. Raises :class:`Aborted` on EOF / Ctrl-C."""
    # Wrap to the terminal (a 1024x768 console with the big font is 64 wide).
    width = max(40, min(76, shutil.get_terminal_size((80, 24)).columns - 1))
    out.write(f"\n{session.title} installer\n")
    i = 0
    while True:
        page = session.pages()[i]  # recomputed: the Wi-Fi choice adds or drops a page
        i += 1
        out.write(f"\n== {page.title} ==\n")
        out.write(textwrap.fill(page.intro, width) + "\n\n")
        if page.key == "review":
            for label, value in session.review():
                out.write(f"  {label + ':':<10} {value}\n")
            out.write(f"\nEVERYTHING on {session.disk.path} will be erased.\n")
            answer = _read(inp, f"Type '{CONFIRM_WORD}' to install, anything else to quit: ")
            return answer.strip().lower() == CONFIRM_WORD
        if page.key == "wifi":
            _wifi_page(session, page, inp, out)
        elif page.choice:
            _choice_page(session, page, inp, out)
        else:
            _form_page(session, page, inp, secret, out)
