"""Lint an emitted live overlay.

This module *checks* a live overlay that :mod:`pengyos_live.emit` wrote to disk.
It reasons over the *same* structured representation as ``emit`` (the model's
absolute target paths, installer binary name, and the console/cosmic split), so
a rule can never pass something ``emit`` never writes.

The overlay is read back from the flattened tree ``emit`` produced: absolute
target paths such as ``/etc/pengyos/live`` and
``/etc/systemd/system/getty@.service.d/autologin.conf`` are flattened relative
to the output root by stripping the leading ``/`` (the mirror image of
:func:`pengyos_live.emit.emit_path`).

Five rules, every one with teeth (each can fail a clean overlay only if a real
artifact is missing or wrong):

1. **Installer present AND wired to autostart for the chosen session.** The
   overlay carries exactly one installer autostart artifact — the
   ``pengyos-installer.service`` systemd unit for ``console`` or the
   ``pengyos-installer.desktop`` autostart entry for ``cosmic`` — it references
   the ``pengyos-install`` binary, and it is actually wired to autostart
   (``WantedBy=multi-user.target`` for the unit, ``X-GNOME-Autostart-enabled=true``
   for the desktop entry). The session is *inferred* from which artifact is
   present, so an overlay that ships the wrong artifact (or both/neither) fails.
2. **Autologin present.** The getty autologin drop-in exists and auto-logs the
   ``live`` user in.
3. **Live marker present.** ``/etc/pengyos/live`` exists and carries
   ``live=true``.
4. **No pre-emptive disk write.** The installer autostart launches
   ``pengyos-install`` without a flag/command that writes the target disk before
   the user confirms inside the installer (no ``dd``/``mkfs``/partitioning, no
   ``--non-interactive``/``--yes``/``--force`` bypass).
5. **Live-user sudo policy present.** The sudoers drop-in at
   ``/etc/sudoers.d/pengyos-live`` exists and lets the ``live`` user run
   ``pengyos-install`` without a password (naming the live user, ``NOPASSWD``,
   and the installer binary) — without it the installer cannot autostart and
   complete the install on the live session.
"""

from __future__ import annotations

import enum
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import (
    AUTOLOGIN_PATH,
    INSTALLER_BIN,
    INSTALLER_SESSION_PATH,
    INSTALLER_UNIT_PATH,
    LIVE_MARKER_PATH,
    LIVE_USER,
    SUDOERS_PATH,
    Session,
)

__all__ = [
    "LintRule",
    "LintFinding",
    "LintReport",
    "read_overlay",
    "lint_overlay",
    "lint_directory",
    "report_to_dict",
    "report_to_json",
]


# --- findings ----------------------------------------------------------------


class LintRule(enum.Enum):
    """The lint rule a :class:`LintFinding` reports a violation of."""

    INSTALLER_PRESENT = "installer_present"
    INSTALLER_WIRED = "installer_autostarted"
    AUTOLOGIN_PRESENT = "autologin_present"
    MARKER_PRESENT = "live_marker_present"
    NO_PREEMPTIVE_DISK_WRITE = "no_preemptive_disk_write"
    SUDO_POLICY_PRESENT = "sudo_policy_present"


@dataclass(frozen=True)
class LintFinding:
    """A single lint violation: which rule and what is wrong."""

    rule: LintRule
    detail: str

    def as_dict(self) -> dict[str, str]:
        """Serialise to a ``{"rule": ..., "detail": ...}`` mapping."""
        return {"rule": self.rule.value, "detail": self.detail}


@dataclass
class LintReport:
    """The result of linting an overlay: the inferred session + findings."""

    session: Session | None
    findings: list[LintFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether every lint rule passed (no findings)."""
        return not self.findings

    def add(self, rule: LintRule, detail: str) -> None:
        """Record a finding for ``rule`` with a human ``detail``."""
        self.findings.append(LintFinding(rule, detail))


# --- reading the flattened overlay back --------------------------------------


def read_overlay(overlay_dir: os.PathLike[str] | str) -> dict[str, str]:
    """Read an emitted overlay directory into a ``{absolute_path: content}`` map.

    ``emit`` flattens absolute target paths (``/etc/pengyos/live``) relative to
    the output root by stripping the leading ``/``; :func:`read_overlay` does the
    mirror image, turning ``<out>/etc/pengyos/live`` back into
    ``/etc/pengyos/live`` so lint can look artifacts up by their absolute target
    path.
    """
    root = Path(overlay_dir)
    if not root.is_dir():
        raise NotADirectoryError(f"overlay directory not found: {overlay_dir}")

    overlay: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        # Reconstruct the absolute target path by re-adding the leading '/'.
        overlay["/" + str(rel)] = path.read_text(encoding="utf-8")
    return overlay


# --- rule 4: pre-emptive disk-write patterns ---------------------------------

#: Substrings that, inside the installer autostart, mean the live path writes the
#: target disk before the user confirms inside ``pengyos-install``. Each pairs a
#: pattern with the reason it is forbidden.
PREEMPTIVE_DISK_WRITE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("dd ", "direct disk image write (dd)"),
    ("dd=", "direct disk image write (dd)"),
    ("mkfs", "disk format (mkfs)"),
    ("parted", "disk partitioning (parted)"),
    ("fdisk", "disk partitioning (fdisk)"),
    ("gdisk", "disk partitioning (gdisk)"),
    ("of=/dev/", "write to a block device"),
    ("--non-interactive", "bypasses the installer's interactive confirmation"),
    ("--noninteractive", "bypasses the installer's interactive confirmation"),
    ("--yes", "bypasses the installer's interactive confirmation"),
    ("--force", "bypasses the installer's interactive confirmation"),
)


def _sudo_policy_ok(content: str) -> bool:
    """Whether a sudoers drop-in lets the live user run the installer passwordless.

    The grant must name the live user, be ``NOPASSWD`` (so the installer can
    autostart and complete the install without a password), and be scoped to the
    ``pengyos-install`` binary.
    """
    installer_ref = f"/usr/bin/{INSTALLER_BIN}"
    return LIVE_USER in content and "NOPASSWD" in content and installer_ref in content


# --- the rules ---------------------------------------------------------------


def lint_overlay(overlay: dict[str, str]) -> LintReport:
    """Lint a flattened overlay (``{absolute_path: content}``) against all rules.

    Returns a :class:`LintReport`. ``session`` is inferred from which installer
    artifact is present (unit → ``console``, desktop entry → ``cosmic``); it is
    ``None`` when no session can be determined.
    """
    report = LintReport(session=None)

    # --- Rule 1a: installer present ---
    installer_paths = [p for p in (INSTALLER_UNIT_PATH, INSTALLER_SESSION_PATH) if p in overlay]
    if not installer_paths:
        report.add(
            LintRule.INSTALLER_PRESENT,
            "no installer autostart present (expected a systemd unit for console "
            "or a desktop entry for cosmic)",
        )
        return report  # nothing else can be checked without the installer

    if len(installer_paths) > 1:
        report.add(
            LintRule.INSTALLER_PRESENT,
            "installer autostart present for multiple sessions "
            f"({', '.join(installer_paths)}) — the overlay must ship exactly one",
        )
        return report

    installer_path = installer_paths[0]
    installer_content = overlay[installer_path]

    # Infer the session from which artifact is present.
    report.session = Session.CONSOLE if installer_path == INSTALLER_UNIT_PATH else Session.COSMIC

    # --- Rule 1b: installer references the binary ---
    binary_ref = f"/usr/bin/{INSTALLER_BIN}"
    if binary_ref not in installer_content:
        report.add(
            LintRule.INSTALLER_PRESENT,
            f"installer autostart does not reference the {INSTALLER_BIN} binary "
            f"({binary_ref})",
        )

    # --- Rule 1c: installer wired to autostart for the inferred session ---
    if report.session is Session.CONSOLE:
        if "WantedBy=multi-user.target" not in installer_content:
            report.add(
                LintRule.INSTALLER_WIRED,
                "console installer unit is not wired to autostart "
                "(missing WantedBy=multi-user.target)",
            )
    else:
        if "X-GNOME-Autostart-enabled=true" not in installer_content:
            report.add(
                LintRule.INSTALLER_WIRED,
                "cosmic installer entry is not wired to autostart "
                "(missing X-GNOME-Autostart-enabled=true)",
            )

    # --- Rule 4: no pre-emptive disk write ---
    for pattern, reason in PREEMPTIVE_DISK_WRITE_PATTERNS:
        if pattern in installer_content:
            report.add(
                LintRule.NO_PREEMPTIVE_DISK_WRITE,
                f"installer autostart {reason}: {pattern.strip()!r}",
            )

    # --- Rule 2: autologin present ---
    if AUTOLOGIN_PATH not in overlay:
        report.add(
            LintRule.AUTOLOGIN_PRESENT,
            f"autologin drop-in missing at {AUTOLOGIN_PATH}",
        )
    else:
        autologin = overlay[AUTOLOGIN_PATH]
        if f"--autologin {LIVE_USER}" not in autologin:
            report.add(
                LintRule.AUTOLOGIN_PRESENT,
                "autologin drop-in present but does not auto-login the live user",
            )

    # --- Rule 3: live marker present ---
    if LIVE_MARKER_PATH not in overlay:
        report.add(
            LintRule.MARKER_PRESENT,
            f"live marker missing at {LIVE_MARKER_PATH}",
        )
    else:
        marker = overlay[LIVE_MARKER_PATH]
        if "live=true" not in marker:
            report.add(
                LintRule.MARKER_PRESENT,
                "live marker present but does not set live=true",
            )

    # --- Rule 5: live-user sudo policy present ---
    if SUDOERS_PATH not in overlay:
        report.add(
            LintRule.SUDO_POLICY_PRESENT,
            f"sudo policy missing at {SUDOERS_PATH}",
        )
    elif not _sudo_policy_ok(overlay[SUDOERS_PATH]):
        report.add(
            LintRule.SUDO_POLICY_PRESENT,
            "sudo policy does not let the live user run the installer "
            "passwordless (missing the live user, NOPASSWD, or the installer "
            "binary)",
        )

    return report


def lint_directory(overlay_dir: os.PathLike[str] | str) -> LintReport:
    """Read an overlay directory from disk and lint it."""
    return lint_overlay(read_overlay(overlay_dir))


# --- serialisation -----------------------------------------------------------


def report_to_dict(report: LintReport) -> dict[str, Any]:
    """Serialise a :class:`LintReport` to a JSON-ready dict."""
    return {
        "ok": report.ok,
        "session": report.session.value_str if report.session is not None else None,
        "errors": [f.as_dict() for f in report.findings],
    }


def report_to_json(report: LintReport, *, indent: int | None = 2) -> str:
    """Serialise a :class:`LintReport` to a JSON string (stable, sorted keys)."""
    return json.dumps(report_to_dict(report), indent=indent, sort_keys=True)
