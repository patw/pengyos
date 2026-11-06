"""Trial boots: what was tried, what failed, and whether the new system is healthy.

``apply`` records the release it staged (``trial`` in the state file). The trial
boot keeps it only if the health checks pass (``confirm --boot``); otherwise,
or if the trial never confirms (a panic, a hang, emergency mode: the boot
deadline and the watchdog reboot it), the version is remembered as failed, so
neither ``apply`` nor the unattended run installs it again by itself.

Health checks are executables in ``/usr/lib/pengyos/update-health.d/`` (the
image's) and ``/etc/pengyos/update-health.d/`` (the machine's), merged by name:
a file in /etc replaces the image's file of the same name, and one that is a
symlink to /dev/null (or not executable) switches it off.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from .state import load_state, now_iso, save_state

HEALTH_DIRS = ("/usr/lib/pengyos/update-health.d", "/etc/pengyos/update-health.d")
CONFIRMED = "/run/pengyos-update/confirmed"
CHECK_TIMEOUT = 60      # seconds per check
RETRY_INTERVAL = 10     # seconds between rounds


# --------------------------------------------------------------------------
# The state file's trial and failure records
# --------------------------------------------------------------------------

def record_trial(state_path: str, version: str, from_version: str, slot: str) -> None:
    save_state(state_path, trial={"version": version, "from": from_version,
                                  "slot": slot, "at": now_iso()})


def clear_trial(state_path: str) -> None:
    if load_state(state_path).get("trial") is not None:
        save_state(state_path, trial=None)


def mark_failed(state_path: str, version: str, reason: str) -> None:
    """Remember ``version`` as failed on this machine; drop the trial record.

    It is no longer staged either: GRUB consumed the one-shot try, so a restart
    boots the old system, and the login message must not say otherwise."""
    state = load_state(state_path)
    failed = dict(state.get("failed") or {})
    failed[version] = {"at": now_iso(), "reason": reason}
    fields = {"failed": failed, "trial": None}
    if state.get("staged") == version:
        fields["staged"] = None
    save_state(state_path, **fields)


def forget_failed(state_path: str, version: str) -> None:
    state = load_state(state_path)
    failed = dict(state.get("failed") or {})
    if failed.pop(version, None) is not None:
        save_state(state_path, failed=failed)


def failure(state: dict, version: str) -> dict | None:
    """``{"at", "reason"}`` if ``version`` failed here, else None."""
    f = (state.get("failed") or {}).get(version)
    return f if isinstance(f, dict) else None


def settle_trial(state_path: str, booted: str, committed: str, pending_try: str) -> str | None:
    """At boot on a committed slot: turn a trial that never confirmed into a failure.

    A recorded trial whose slot is not the one running, with grubenv
    ``pengyos_try`` empty, was booted (GRUB clears the variable when it boots
    the trial) and did not keep itself: it failed. Returns that version.
    A trial still waiting for its boot (``pengyos_try`` set) is left alone, and
    a trial of the running, committed slot was kept (by an updater that did not
    clear the record) and is just dropped."""
    trial = load_state(state_path).get("trial")
    if not isinstance(trial, dict) or booted != committed:
        return None
    if trial.get("slot") == booted:
        clear_trial(state_path)
        return None
    if pending_try:
        return None
    version = str(trial.get("version") or "?")
    mark_failed(state_path, version, "it did not finish starting (no confirmation from the new "
                                     "system; it was restarted into the previous one)")
    return version


def write_confirmed(path: str = CONFIRMED) -> None:
    """Tell the trial's boot deadline (pengyos-update-deadline.service) to stand down."""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(now_iso() + "\n", encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------
# Health checks
# --------------------------------------------------------------------------

def health_checks(dirs: tuple[str, ...] = HEALTH_DIRS) -> list[Path]:
    """The checks to run, by name; a later directory replaces an earlier one's file."""
    by_name: dict[str, Path] = {}
    for d in dirs:
        try:
            entries = sorted(Path(d).iterdir())
        except OSError:
            continue
        for p in entries:
            if p.name.startswith(".") or p.name.endswith(("~", ".md")) or p.name == "README":
                continue
            if p.is_dir():
                continue
            by_name[p.name] = p
    out = []
    for name in sorted(by_name):
        p = by_name[name]
        if os.path.realpath(p) == "/dev/null" or not p.is_file() or not os.access(p, os.X_OK):
            continue  # switched off
        out.append(p)
    return out


def run_check(check: Path, timeout: float = CHECK_TIMEOUT) -> str | None:
    """None if it passed, else the reason (its last output line)."""
    try:
        r = subprocess.run([str(check)], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"{check.name}: no answer after {int(timeout)} s"
    except OSError as exc:
        return f"{check.name}: could not run it ({exc.strerror or exc})"
    if r.returncode == 0:
        return None
    lines = (r.stdout + r.stderr).strip().splitlines()
    return f"{check.name}: " + (lines[-1].strip() if lines else f"exit {r.returncode}")


def wait_healthy(checks: list[Path], total: float, interval: float = RETRY_INTERVAL,
                 runner: Callable[[Path], str | None] = run_check,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 say: Callable[[str], None] = print) -> list[str]:
    """Run the checks until all pass or ``total`` seconds have gone by.

    Returns the reasons of the last round's failures ([] = healthy). Every
    check runs at least once, however small ``total`` is."""
    start = clock()
    while True:
        failures = [r for r in (runner(c) for c in checks) if r]
        if not failures:
            return []
        left = total - (clock() - start)
        if left <= 0:
            return failures
        say(f"  not healthy yet ({'; '.join(failures)}); trying again for {int(left)} s")
        sleep(min(interval, left))
