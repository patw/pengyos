"""The full-screen installer: the session's pages drawn with curses.

Split in two so it tests without a terminal:

* :class:`App` is the whole behaviour: it takes normalised key names
  (``"enter"``, ``"tab"``, ``"a"``, ...) and draws a :class:`Frame` (text at
  positions, with a style name) for any screen size. It never imports curses.
* :func:`run_screen` is the thin curses driver: it paints frames, maps curses
  keys to names, and runs the app's slow jobs (fetching keys, installing)
  between frames so a "fetching" or progress screen is visible meanwhile.

Plain ASCII throughout: it draws on the Linux console's own font.
"""

from __future__ import annotations

import os
import textwrap
from dataclasses import dataclass, field
from typing import Callable

from pengyos_install import image as engine
from pengyos_install_tui.session import CONFIRM_WORD, Page, Session

__all__ = ["App", "Frame", "MIN_W", "MIN_H", "run_screen"]

MIN_W, MIN_H = 80, 24
#: Width of the text column the pages are laid out in.
TEXT_W = 72
INPUT_W = 56


@dataclass
class Frame:
    draws: list[tuple[int, int, str, str]] = field(default_factory=list)
    cursor: tuple[int, int] | None = None

    def put(self, row: int, col: int, text: str, style: str = "normal") -> None:
        self.draws.append((row, col, text, style))

    def text(self) -> list[str]:
        """The frame as plain lines (for tests and debugging)."""
        rows: dict[int, list[str]] = {}
        for row, col, text, _ in self.draws:
            line = rows.setdefault(row, [])
            need = col + len(text) - len(line)
            if need > 0:
                line.extend(" " * need)
            line[col:col + len(text)] = text
        top = max(rows, default=-1)
        return ["".join(rows.get(r, [])).rstrip() for r in range(top + 1)]


class _Editor:
    """One single-line text field: its text and the cursor in it."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.pos = len(text)

    def key(self, k: str) -> bool:
        """Apply an editing key; False if *k* is not one."""
        t, p = self.text, self.pos
        if k == "backspace":
            if p:
                self.text, self.pos = t[:p - 1] + t[p:], p - 1
        elif k == "delete":
            self.text = t[:p] + t[p + 1:]
        elif k == "left":
            self.pos = max(0, p - 1)
        elif k == "right":
            self.pos = min(len(t), p + 1)
        elif k == "home":
            self.pos = 0
        elif k == "end":
            self.pos = len(t)
        elif k == "ctrl-u":
            self.text, self.pos = "", 0
        elif len(k) == 1 and k.isprintable():
            self.text, self.pos = t[:p] + k + t[p:], p + 1
        else:
            return False
        return True

    def view(self, width: int, secret: bool) -> tuple[str, int]:
        """The visible slice of the text (scrolled so the cursor shows) and the
        cursor's column within it."""
        shown = "*" * len(self.text) if secret else self.text
        start = max(0, self.pos - width + 1)
        return shown[start:start + width], self.pos - start


class App:
    """The installer as a key-driven state machine over a :class:`Session`."""

    def __init__(self, session: Session, *, install: Callable, payload: str | None = None,
                 reboot: bool = False) -> None:
        self.s = session
        self.install = install
        self.payload = payload
        self.reboot = reboot
        self.pages = session.pages()
        self.mode = "page" if session.selectable() else "nodisk"
        self.index = 0
        self.focus = 0
        self.row = self._first_row(self.pages[0])
        self.editors: dict[str, _Editor] = {}
        self.errors: dict[str, str] = {}
        self.notice: tuple[str, str] | None = None  # (style, text)
        self.progress = (0.0, "")
        self.failure = ""
        #: A slow job for the driver to run after drawing the current frame.
        self.pending: Callable[[], None] | None = None
        #: Set by the driver: repaint now (used while the install reports progress).
        self.redraw: Callable[[], None] = lambda: None
        self.result: int | None = None
        self._before_quit = "page"

    # ------------------------------------------------------------ helpers
    @property
    def page(self) -> Page:
        return self.pages[self.index]

    def _first_row(self, page: Page) -> int:
        if not page.choice:
            return 0
        chosen = self.s.chosen(page)
        if chosen is not None:
            return chosen
        opts = self.s.options(page)
        return next((i for i, o in enumerate(opts) if o.enabled), 0)

    def editor(self, key: str) -> _Editor:
        if key not in self.editors:
            self.editors[key] = _Editor(self.s.values.get(key, ""))
        return self.editors[key]

    def _go(self, index: int) -> None:
        self.index = index
        self.focus = 0
        self.row = self._first_row(self.page)
        self.notice = None
        self.errors.clear()
        if self.page.key == "review":
            self.editor("confirm").key("ctrl-u")

    def _store(self, i: int) -> str | None:
        f = self.page.fields[i]
        problem = self.s.set(f.key, self.editor(f.key).text)
        if problem:
            self.errors[f.key] = problem
        else:
            self.errors.pop(f.key, None)
        return problem

    # --------------------------------------------------------------- keys
    def key(self, k: str) -> None:
        if self.pending is not None or self.mode == "installing":
            return
        if k == "resize":
            return
        if self.mode == "quit?":
            if k in ("y", "Y"):
                self.result = 1
            else:
                self.mode = self._before_quit
            return
        if k == "ctrl-c" and self.mode != "done":
            self._before_quit, self.mode = self.mode, "quit?"
            return
        if self.mode == "nodisk":
            if k in ("enter", "esc"):
                self.result = 1
        elif self.mode == "done":
            if k == "enter":
                self.result = 0
        elif self.mode == "failed":
            if k in ("enter", "esc"):
                self.mode = "page"
                self._go(self.pages.index(self.page))
        elif self.page.choice:
            self._choice_key(k)
        else:
            self._form_key(k)

    def _back(self) -> None:
        if self.index:
            self._go(self.index - 1)
        else:
            self._before_quit, self.mode = self.mode, "quit?"

    def _choice_key(self, k: str) -> None:
        opts = self.s.options(self.page)
        enabled = [i for i, o in enumerate(opts) if o.enabled]
        if k in ("up", "down", "btab", "tab") and enabled:
            step = -1 if k in ("up", "btab") else 1
            pos = enabled.index(self.row) if self.row in enabled else 0
            self.row = enabled[(pos + step) % len(enabled)]
        elif k.isdigit() and 1 <= int(k) <= len(enabled):
            self.row = enabled[int(k) - 1]
            self.notice = None
        elif k == "enter":
            problem = self.s.choose(self.page, self.row)
            if problem:
                self.notice = ("error", problem)
            else:
                self._go(self.index + 1)
        elif k == "esc":
            self._back()

    def _form_key(self, k: str) -> None:
        fields = self.page.fields
        f = fields[self.focus]
        if k in ("tab", "down"):
            self._store(self.focus)
            self.focus = (self.focus + 1) % len(fields)
        elif k in ("btab", "up"):
            self._store(self.focus)
            self.focus = (self.focus - 1) % len(fields)
        elif k == "esc":
            for i in range(len(fields)):
                self.s.set(fields[i].key, self.editor(fields[i].key).text)
            self._back()
        elif k == "enter":
            if self._store(self.focus):
                return
            if self.focus < len(fields) - 1:
                self.focus += 1
            else:
                self._submit()
        elif self.editor(f.key).key(k):
            self.errors.pop(f.key, None)
            self.notice = None

    def _submit(self) -> None:
        fields = self.page.fields
        for i in range(len(fields)):
            if self._store(i):
                self.focus = i
                return
        if self.page.key == "review":
            self.mode = "installing"
            self.progress = (0.0, "starting")
            self.pending = self._install
            return
        if self.s.needs_network(self.page):
            src = self.s.key_source()
            verb = "Connecting to" if src.kind == "ssh" else "Fetching keys from"
            self.notice = ("ok", f"{verb} {src.label} ...")
            self.pending = self._finish
        else:
            self._finish()

    def _finish(self) -> None:
        bad = self.s.finish(self.page)
        if bad is None:
            self._go(self.index + 1)
            return
        key, problem = bad
        self.notice = None
        keys = [f.key for f in self.page.fields]
        if key in keys:
            self.focus = keys.index(key)
            self.errors[key] = problem
        else:
            self.notice = ("error", problem)

    def _report(self, phase: str, fraction: float, message: str) -> None:
        self.progress = (engine.overall_fraction(phase, fraction), message)
        self.redraw()

    def _install(self) -> None:
        try:
            self.install(self.s.disk.path, self.s.answers(), payload_dir=self.payload,
                         confirm=True, report=self._report)
        except Exception as exc:  # shown on the failure page, never a traceback
            self.failure = str(exc) if isinstance(exc, engine.ImageInstallError) \
                else f"{type(exc).__name__}: {exc}"
            self.mode = "failed"
        else:
            self.mode = "done"

    # ------------------------------------------------------------- render
    def render(self, h: int, w: int) -> Frame:
        fr = Frame()
        if h < MIN_H or w < MIN_W:
            fr.put(0, 0, f"Make the screen at least {MIN_W}x{MIN_H} (it is {w}x{h}).", "error")
            return fr
        left = f" {self.s.title} installer"
        right = (f"Step {self.index + 1} of {len(self.pages)} "
                 if (self._before_quit if self.mode == "quit?" else self.mode) == "page"
                 else "")
        fr.put(0, 0, left + " " * (w - len(left) - len(right)) + right, "bar")
        x = max(2, (w - TEXT_W) // 2)
        width = min(TEXT_W, w - 2 * x)
        mode = self._before_quit if self.mode == "quit?" else self.mode
        draw = getattr(self, f"_draw_{mode}", None) or self._draw_page
        hints = draw(fr, x, width, h)
        if self.mode == "quit?":
            self._draw_quit(fr, h, w)
            hints = "Y Quit   any other key: keep going"
            fr.cursor = None
        fr.put(h - 1, 0, (" " + hints).ljust(w - 1), "bar")
        return fr

    def _heading(self, fr: Frame, x: int, width: int, title: str, intro: str) -> int:
        fr.put(2, x, title, "title")
        row = 4
        for line in textwrap.wrap(intro, width):
            fr.put(row, x, line)
            row += 1
        return row + 1

    def _draw_page(self, fr: Frame, x: int, width: int, h: int) -> str:
        page = self.page
        row = self._heading(fr, x, width, page.title, page.intro)
        if page.choice:
            hints = self._draw_choice(fr, x, width, row)
        else:
            if page.key == "review":
                row = self._draw_review(fr, x, width, row)
            hints = self._draw_fields(fr, x, width, row)
        if self.notice:
            style, text = self.notice
            for i, line in enumerate(textwrap.wrap(text, width)[:2]):
                fr.put(h - 3 + i, x, line, style)
        return hints

    def _draw_choice(self, fr: Frame, x: int, width: int, row: int) -> str:
        """One row per option; only usable ones are numbered (the number keys
        count those), and a long label is cut before its note is."""
        n = 0
        for i, o in enumerate(self.s.options(self.page)):
            if o.enabled:
                n += 1
            note = f"   ({o.note})" if o.note else ""
            label = f"{n}. " if o.enabled else "   "
            label += o.label[:max(0, width - 2 - len(label) - len(note))]
            text = (label + note).ljust(width - 2)
            if i == self.row:
                fr.put(row + i, x, "> " + text, "select")
            else:
                fr.put(row + i, x, "  " + text, "normal" if o.enabled else "dim")
        return "Up/Down Choose   Enter Select   Esc Back   Ctrl-C Quit"

    def _draw_review(self, fr: Frame, x: int, width: int, row: int) -> int:
        for label, value in self.s.review():
            fr.put(row, x + 2, f"{label:<10}", "title")
            fr.put(row, x + 12, value[:width - 12])
            row += 1
        fr.put(row + 1, x, f"EVERYTHING on {self.s.disk.path} will be erased."[:width], "error")
        return row + 3

    def _draw_fields(self, fr: Frame, x: int, width: int, row: int) -> str:
        """Draw the page's fields; return the key hints. A field's error goes
        beside its label, or under its box (moving later fields down) when it
        is too long for that."""
        box = min(INPUT_W, width - 2)
        for i, f in enumerate(self.page.fields):
            focused = i == self.focus
            label, hint = self.s.field_text(f)
            fr.put(row, x, label, "title" if focused else "normal")
            note, style = self.errors.get(f.key), "error"
            if note is None:
                note, style = hint, "hint"
            col = x + len(label) + 2
            below = []
            if note and len(note) > x + width - col and style == "error":
                below, note = textwrap.wrap(note, width), ""
            if note:
                fr.put(row, col, note[:max(0, x + width - col)], style)
            ed = self.editor(f.key)
            text, cur = ed.view(box - 2, f.secret)
            fr.put(row + 1, x, " " + text.ljust(box - 1), "input-focus" if focused else "input")
            if focused:
                fr.cursor = (row + 1, x + 1 + cur)
            for n, line in enumerate(below):
                fr.put(row + 2 + n, x, line, "error")
            row += 3 + len(below)
        if self.page.key == "review":
            return "Enter Install   Esc Back   Ctrl-C Quit"
        return "Enter Next   Tab/Up/Down Field   Esc Back   Ctrl-C Quit"

    def _draw_quit(self, fr: Frame, h: int, w: int) -> None:
        lines = ["Quit the installer?", ""]
        if self._before_quit == "failed":
            lines += [f"{self.s.disk.path} was partly written: install",
                      "again before using it."]
        else:
            lines += ["Nothing has been changed on any disk."]
        lines += ["Press Y to quit, any other key to keep going."]
        bw = max(len(s) for s in lines) + 6
        top, left = h // 2 - 3, (w - bw) // 2
        fr.put(top, left, " " * bw, "bar")
        for i, s in enumerate(lines):
            fr.put(top + 1 + i, left, ("   " + s).ljust(bw), "bar")
        fr.put(top + 1 + len(lines), left, " " * bw, "bar")

    def _draw_nodisk(self, fr: Frame, x: int, width: int, h: int) -> str:
        self._heading(fr, x, width, "No disk to install to",
                      "No disk was found that PengyOS can be installed on. It needs "
                      f"an internal disk of at least "
                      f"{engine.Disk('', '', engine.MIN_DISK).size_label}; the USB "
                      "stick the installer runs from cannot be used.")
        return "Enter Quit"

    def _draw_installing(self, fr: Frame, x: int, width: int, h: int) -> str:
        row = self._heading(fr, x, width, f"Installing on {self.s.disk.path}",
                            "This takes a few minutes. Do not remove the USB stick or "
                            "turn the computer off.")
        frac, msg = self.progress
        bar = width - 8
        filled = int(round(frac * bar))
        fr.put(row + 1, x, " " * filled, "bar-full")
        fr.put(row + 1, x + filled, " " * (bar - filled), "bar-empty")
        fr.put(row + 1, x + bar + 2, f"{frac * 100:3.0f}%", "title")
        fr.put(row + 3, x, msg[:width])
        return "Installing ..."

    def _draw_failed(self, fr: Frame, x: int, width: int, h: int) -> str:
        row = self._heading(fr, x, width, "The install failed",
                            "Your answers are kept. Check the cable or disk and try again.")
        for line in textwrap.wrap(self.failure, width)[:h - row - 3]:
            fr.put(row, x, line, "error")
            row += 1
        return "Enter Back to the summary   Ctrl-C Quit"

    def _draw_done(self, fr: Frame, x: int, width: int, h: int) -> str:
        a = self.s.answers()
        lines = [f"Log in as {a.username} at this keyboard once it restarts."]
        if a.authorized_keys:
            lines += ["", "From another computer on your network:", "",
                      f"    ssh {a.username}@{a.hostname}", "",
                      "If that name does not resolve, run 'ip -brief address' at the "
                      "console to see the machine's address."]
        else:
            lines += ["", "SSH accepts keys only and none was added: put one in "
                      f"/home/{a.username}/.ssh/authorized_keys from the console first."]
        row = self._heading(fr, x, width, "PengyOS is installed", "")
        row -= 1
        for para in lines:
            for line in textwrap.wrap(para, width) or [""]:
                fr.put(row, x, line, "title" if para.startswith("    ") else "normal")
                row += 1
        last = ("Remove the USB stick, then press Enter to restart."
                if self.reboot else "Press Enter to finish.")
        fr.put(h - 3, x, last, "ok")
        return "Enter " + ("Restart" if self.reboot else "Finish")


# ---------------------------------------------------------------- driver
def _styles(curses) -> dict[str, int]:
    names = ("normal", "title", "hint", "error", "ok", "dim", "bar", "input",
             "input-focus", "select", "bar-full", "bar-empty")
    s = dict.fromkeys(names, curses.A_NORMAL)
    s["title"] = curses.A_BOLD
    s["dim"] = curses.A_DIM
    s["error"] = curses.A_BOLD
    s["bar"] = s["select"] = s["input-focus"] = s["bar-full"] = curses.A_REVERSE
    s["input"] = curses.A_UNDERLINE
    if curses.has_colors():
        curses.start_color()
        c = curses
        pairs = {
            "bar": (c.COLOR_WHITE, c.COLOR_BLUE, c.A_BOLD),
            # The Linux console has no dim: unusable rows are bright blue.
            "dim": (c.COLOR_BLUE, c.COLOR_BLACK, c.A_BOLD),
            "hint": (c.COLOR_CYAN, c.COLOR_BLACK, 0),
            "error": (c.COLOR_RED, c.COLOR_BLACK, c.A_BOLD),
            "ok": (c.COLOR_GREEN, c.COLOR_BLACK, c.A_BOLD),
            "input": (c.COLOR_BLACK, c.COLOR_WHITE, 0),
            "input-focus": (c.COLOR_BLACK, c.COLOR_CYAN, 0),
            "select": (c.COLOR_BLACK, c.COLOR_CYAN, 0),
            "bar-full": (c.COLOR_WHITE, c.COLOR_GREEN, 0),
            "bar-empty": (c.COLOR_WHITE, c.COLOR_WHITE, 0),
        }
        for n, (name, (fg, bg, attr)) in enumerate(pairs.items(), start=1):
            curses.init_pair(n, fg, bg)
            s[name] = curses.color_pair(n) | attr
    return s


def _key_name(curses, ch) -> str | None:
    names = {
        curses.KEY_UP: "up", curses.KEY_DOWN: "down", curses.KEY_LEFT: "left",
        curses.KEY_RIGHT: "right", curses.KEY_HOME: "home", curses.KEY_END: "end",
        curses.KEY_BACKSPACE: "backspace", curses.KEY_DC: "delete",
        curses.KEY_ENTER: "enter", curses.KEY_BTAB: "btab", curses.KEY_RESIZE: "resize",
        "\n": "enter", "\r": "enter", "\t": "tab", "\x1b": "esc", "\x7f": "backspace",
        "\x08": "backspace", "\x03": "ctrl-c", "\x15": "ctrl-u", "\x01": "home", "\x05": "end",
    }
    if ch in names:
        return names[ch]
    if isinstance(ch, str) and ch.isprintable():
        return ch
    return None


def run_screen(app: App) -> int:
    """Run *app* full-screen until it finishes; return its exit code."""
    import curses
    import locale

    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")

    def main(stdscr) -> int:
        curses.raw()
        stdscr.keypad(True)
        styles = _styles(curses)

        def paint() -> None:
            h, w = stdscr.getmaxyx()
            frame = app.render(h, w)
            stdscr.erase()
            for row, col, text, style in frame.draws:
                if 0 <= row < h and 0 <= col < w:
                    try:
                        stdscr.addstr(row, col, text[:w - col], styles.get(style, 0))
                    except curses.error:
                        pass  # writing the bottom-right cell raises; it is drawn anyway
            try:
                curses.curs_set(1 if frame.cursor else 0)
                if frame.cursor:
                    stdscr.move(*frame.cursor)
            except curses.error:
                pass
            stdscr.refresh()

        app.redraw = paint
        while app.result is None:
            paint()
            if app.pending is not None:
                job, app.pending = app.pending, None
                job()
                curses.flushinp()  # keys typed meanwhile must not answer the next page
                continue
            try:
                name = _key_name(curses, stdscr.get_wch())
            except curses.error:
                continue
            if name:
                app.key(name)
        return app.result

    return curses.wrapper(main)


def can_run_screen(stdin, stdout, term: str | None) -> bool:
    """True when a full-screen installer can be drawn on this terminal."""
    if not (stdin.isatty() and stdout.isatty()) or term in (None, "", "dumb", "unknown"):
        return False
    try:
        import curses

        curses.setupterm(term, stdout.fileno())
        cols, lines = curses.tigetnum("cols"), curses.tigetnum("lines")
    except Exception:
        return False
    size = os.get_terminal_size(stdout.fileno()) if stdout.isatty() else None
    if size is not None:
        cols, lines = size.columns, size.lines
    return cols >= MIN_W and lines >= MIN_H
