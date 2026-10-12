"""``pengyos-install-tui install`` — the interactive console installer.

Runs on the live medium's console (or over serial / SSH) and installs through
the same engine as the GUI, :mod:`pengyos_install.image`:

    disk  ->  computer name  ->  user  ->  password  ->  SSH key
          ->  Pengy endpoint (optional; Enter skips it)
          ->  automatic login (Desktop only; default no)  ->  confirm ("erase")  ->  install

Unlike ``run`` / ``auto`` (which only produce an answer set), this erases the
chosen disk. Every prompt re-asks with the reason on bad input, and Ctrl-C /
EOF before the confirmation leaves the disks untouched.
"""

from __future__ import annotations

import getpass
import sys
from typing import Callable, TextIO

try:
    from pengyos_install import image as engine
except ImportError:  # pragma: no cover - exercised only on a broken install
    engine = None  # type: ignore[assignment]

__all__ = ["run_install"]


class Aborted(Exception):
    """The user left the installer before confirming."""


def _ask(prompt: str, inp: Callable[[str], str], out: TextIO, *, default: str = "",
         check: Callable[[str], str | None] = lambda v: None, optional: bool = False) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        try:
            value = inp(f"{prompt}{suffix}: ").strip() or default
        except EOFError:
            raise Aborted() from None
        if not value and optional:
            return ""
        problem = "a value is required" if not value else check(value)
        if problem is None:
            return value
        out.write(f"  {problem}\n")


def _answers_problem(**kw) -> str | None:
    base = dict(hostname="pengyos", username="user", password="xxxxxx")
    base.update(kw)
    try:
        engine.ProvisionAnswers(**base).validate()
    except engine.ImageInstallError as exc:
        return str(exc)
    return None


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


def run_install(
    *,
    inp: Callable[[str], str] = input,
    secret: Callable[[str], str] = getpass.getpass,
    out: TextIO = sys.stdout,
    payload: str | None = None,
    disks: list | None = None,
    install: Callable | None = None,
) -> int:
    if engine is None:
        out.write("pengyos-install-tui: the pengyos-install engine is not installed\n")
        return 1
    install = install or engine.install_image
    try:
        try:
            manifest = engine.Manifest.load(engine.find_payload(payload))
        except engine.ImageInstallError as exc:
            out.write(f"pengyos-install-tui: {exc}\n")
            return 1
        out.write(f"\nPengyOS {manifest.edition} installer (image {manifest.version})\n\n")

        disks = engine.list_disks() if disks is None else disks
        choices = [d for d in disks if not d.is_boot and not d.mounted]
        out.write("Disks:\n")
        for d in disks:
            if d in choices:
                tag = f"  {choices.index(d) + 1})"
                note = " (removable)" if d.removable else ""
            else:
                tag = "    "
                note = " (running system)" if d.is_boot else " (in use)"
            out.write(f"{tag} {d.path:<14} {d.size_label:>9}  {d.model or '-'}{note}\n")
        if not choices:
            out.write("\nNo disk to install to was found.\n")
            return 1
        out.write("\n")

        def pick(v: str) -> str | None:
            if v.isdigit() and 1 <= int(v) <= len(choices):
                if choices[int(v) - 1].size_bytes < engine.MIN_DISK:
                    return "that disk is too small for PengyOS"
                return None
            return f"enter a number from 1 to {len(choices)}"

        disk = choices[int(_ask("Install to disk number", inp, out,
                                default="1" if len(choices) == 1 else "", check=pick)) - 1]
        hostname = _ask("Computer name", inp, out, default="pengyos",
                        check=lambda v: _answers_problem(hostname=v))
        username = _ask("Username", inp, out, check=lambda v: _answers_problem(username=v))
        while True:
            try:
                password = secret("Password (also used for root): ")
                again = secret("Repeat the password: ")
            except EOFError:
                raise Aborted() from None
            problem = _answers_problem(password=password)
            if problem:
                out.write(f"  {problem}\n")
            elif password != again:
                out.write("  the passwords do not match\n")
            else:
                break
        # sshd accepts keys only (password logins over SSH are off), so without
        # a key this user can log in at the console but not over SSH.
        key = _ask(f"SSH public key for {username} (paste one line; Enter to skip)", inp, out,
                   check=lambda v: _answers_problem(authorized_keys=v), optional=True)
        # The assistant ships unconfigured (its defaults are the local endpoint,
        # no key, no model). This page is optional on purpose: plenty of people
        # have no endpoint to point at during an install, and an empty base URL
        # means "leave Pengy exactly as the image ships it".
        base_url = _ask(
            "Pengy base URL (OpenAI-compatible, e.g. http://127.0.0.1:11434/v1; Enter to skip)",
            inp, out, check=lambda v: _answers_problem(pengy_base_url=v), optional=True,
        )
        api_key = model = ""
        if base_url:
            api_key = _ask(
                "Pengy API key (Enter for none — local endpoints need none)",
                secret, out, check=lambda v: _answers_problem(
                    pengy_base_url=base_url, pengy_api_key=v), optional=True,
            )
            model = _ask(
                "Pengy model name (Enter to choose later)", inp, out,
                check=lambda v: _answers_problem(
                    pengy_base_url=base_url, pengy_api_key=api_key, pengy_model=v),
                optional=True,
            )
        # A Desktop starts at its login screen; skipping it is opt-in (Ubuntu's
        # "Log in automatically").
        autologin = False
        if manifest.edition == "desktop":
            autologin = _ask("Log in automatically, skipping the login screen? [y/N]", inp, out,
                             optional=True).strip().lower() in ("y", "yes")

        out.write(
            f"\n  disk:      {disk.path}  {disk.size_label}  {disk.model or ''}\n"
            f"  computer:  {hostname}\n  user:      {username}\n"
            f"  ssh key:   {key.split()[0] + ' ...' + key[-24:] if key else 'none (console login only)'}\n"
            f"  pengy:     {base_url + ('  model ' + model if model else '') if base_url else 'not configured (Pengy keeps its defaults)'}\n"
            + (f"  login:     {'automatic' if autologin else 'password required'}\n"
               if manifest.edition == "desktop" else "")
            + "\n"
            f"EVERYTHING on {disk.path} will be erased.\n"
        )
        if _ask("Type 'erase' to install, anything else to quit", inp, out).lower() != "erase":
            out.write("Nothing was changed.\n")
            return 1
    except (Aborted, KeyboardInterrupt):
        out.write("\nInstaller quit; nothing was changed.\n")
        return 1

    answers = engine.ProvisionAnswers(hostname=hostname, username=username, password=password,
                                      authorized_keys=key, autologin=autologin,
                                      pengy_base_url=base_url, pengy_api_key=api_key,
                                      pengy_model=model)
    try:
        install(disk.path, answers, payload_dir=payload, confirm=True, report=_progress(out))
    except engine.ImageInstallError as exc:
        out.write(f"\nThe install failed: {exc}\n")
        return 1
    out.write("\nPengyOS is installed. Remove the USB stick and reboot.\n")
    return 0
