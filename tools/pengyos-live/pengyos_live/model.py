"""The live-overlay data model.

This module *models* the PengyOS live/installer session overlay as data — the
autologin target, the session type, the installer unit/entry and the live marker
— before :mod:`pengyos_live.cli` knows how to *emit* it (item 3) or *lint* it
(item 4). Keeping the model in its own module means ``emit`` and ``lint`` both
reason over the *same* structured representation of the overlay, so a lint rule
can never check something ``emit`` never writes.

The overlay is a set of files that live at absolute paths inside the target
rootfs (the edition's install medium). Each file is an :class:`OverlayFile`
(a path plus its exact contents). A :class:`LiveOverlay` is the whole set for a
single edition + session, produced by :func:`build_overlay`.

The content here is the real, hand-authored overlay for PengyOS:

* **autologin** — a systemd ``getty@.service`` drop-in that auto-logs the
  ``live`` user into the console getty (the graphical/console getty, per
  spec.md).
* **installer autostart** — either a ``pengyos-installer.service`` systemd unit
  (console) or a ``pengyos-installer.desktop`` autostart entry (cosmic) that
  runs the ``pengyos-install`` binary on the live session.
* **live-user sudo policy** — a sudoers drop-in at
  ``/etc/sudoers.d/pengyos-live`` letting the ``live`` user run
  ``pengyos-install`` without a password, so the installer can autostart and
  complete the install on the live session.
* **live marker** — ``/etc/pengyos/live`` identifying the running system as a
  live medium.

Naming (shared with the ``pengyos-installer`` tool): the
installer binary is ``pengyos-install`` and the live user is ``live``.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from typing import Any

# --- shared naming (do not drift from the pengyos-installer tool) ------------

#: The auto-login live user. This is the standard Ubuntu/PengyOS live user.
LIVE_USER = "live"

#: The installer binary the live session autostarts. Shared with the
#: ``pengyos-installer`` tool (``tools/pengyos-installer``).
INSTALLER_BIN = "pengyos-install"

#: Absolute path of the live marker inside the target rootfs.
LIVE_MARKER_PATH = "/etc/pengyos/live"

#: Absolute path of the getty autologin drop-in (the graphical/console getty).
AUTOLOGIN_PATH = "/etc/systemd/system/getty@.service.d/autologin.conf"

#: Absolute path of the console installer systemd unit.
INSTALLER_UNIT_PATH = "/etc/systemd/system/pengyos-installer.service"

#: Absolute path of the cosmic installer autostart entry.
INSTALLER_SESSION_PATH = "/usr/share/autostart/pengyos-installer.desktop"

#: Absolute path of the live-user sudo policy (a sudoers drop-in letting the
#: live user run the installer without a password, so it can autostart and
#: complete the install on the live session).
SUDOERS_PATH = "/etc/sudoers.d/pengyos-live"


class Edition(enum.Enum):
    """A PengyOS edition the install medium ships for.

    Mirrors the ``--edition`` choices in spec.md. ``value`` is the canonical
    string used in the emitted overlay and in the JSON schema.
    """

    DESKTOP = "desktop"
    CORE = "core"
    SERVER = "server"

    @property
    def value_str(self) -> str:
        """The canonical string form (``Edition.DESKTOP.value_str == "desktop"``)."""
        return self.value

    @classmethod
    def all_values(cls) -> tuple[str, ...]:
        """Every edition value, in declaration order."""
        return tuple(e.value for e in cls)


class Session(enum.Enum):
    """The session the installer autostarts into.

    Mirrors the ``--session`` choices in spec.md. ``cosmic`` is the graphical
    COSMIC desktop (the installer autostarts as a session entry); ``console`` is
    the plain console (the installer autostarts as a systemd unit).
    """

    COSMIC = "cosmic"
    CONSOLE = "console"

    @property
    def value_str(self) -> str:
        """The canonical string form (``Session.COSMIC.value_str == "cosmic"``)."""
        return self.value

    @classmethod
    def all_values(cls) -> tuple[str, ...]:
        """Every session value, in declaration order."""
        return tuple(s.value for s in cls)

    @property
    def is_graphical(self) -> bool:
        """Whether this session is a graphical (COSMIC) session."""
        return self is Session.COSMIC

    @property
    def is_console(self) -> bool:
        """Whether this session is the plain console fallback."""
        return self is Session.CONSOLE


# --- overlay files -----------------------------------------------------------


@dataclass(frozen=True)
class OverlayFile:
    """A single file in the emitted overlay.

    ``path`` is the absolute path inside the target rootfs (e.g.
    ``/etc/pengyos/live``); ``content`` is the exact bytes-to-be-written text.
    Frozen so an emitted overlay is an immutable, hashable value.
    """

    path: str
    content: str

    def as_dict(self) -> dict[str, str]:
        """Serialise to a ``{"path": ..., "content": ...}`` mapping."""
        return {"path": self.path, "content": self.content}


# --- component builders ------------------------------------------------------


def _autologin_content() -> str:
    """The getty autologin drop-in (graphical/console getty)."""
    return (
        "# This file is managed by pengyos-live. Do not edit by hand.\n"
        "#\n"
        f"# Auto-login the live user ({LIVE_USER}) on the console getty for the\n"
        "# live session. The [Service] ExecStart= line is cleared and replaced so\n"
        "# getty logs the live user in directly.\n"
        "[Service]\n"
        "ExecStart=\n"
        f"ExecStart=-/sbin/getty --autologin {LIVE_USER} --noclear %I $BAUD\n"
        "TTYVTXDiscard=1\n"
        "TimeoutStartSec=0\n"
    )


def _console_installer_unit_content() -> str:
    """The console installer systemd unit (console fallback session)."""
    return (
        "# This file is managed by pengyos-live. Do not edit by hand.\n"
        "#\n"
        f"# Run the PengyOS installer on the live console session. The live path\n"
        "# never writes the target disk before the user confirms inside\n"
        f"# {INSTALLER_BIN}.\n"
        "[Unit]\n"
        "Description=PengyOS live installer\n"
        f"Documentation=file://{LIVE_MARKER_PATH}\n"
        "After=multi-user.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart=/usr/bin/{INSTALLER_BIN}\n"
        "RemainAfterExit=no\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _cosmic_installer_session_content() -> str:
    """The cosmic installer autostart entry (graphical session)."""
    return (
        "# This file is managed by pengyos-live. Do not edit by hand.\n"
        "#\n"
        "# Autostart the PengyOS installer when the COSMIC live session starts.\n"
        f"# The live path never writes the target disk before the user confirms\n"
        f"# inside {INSTALLER_BIN}.\n"
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=PengyOS Installer\n"
        "Comment=Install PengyOS to disk\n"
        f"Exec=/usr/bin/{INSTALLER_BIN}\n"
        "Icon=drive-install\n"
        "Terminal=false\n"
        "Categories=System;\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _sudoers_content() -> str:
    """The live-user sudo policy (a sudoers drop-in).

    Lets the ``live`` user run ``pengyos-install`` without a password, so the
    installer can autostart on the live session and complete the install
    (partition, format, mount, copy) without a human typing a password. The
    grant is intentionally scoped to the installer binary only.
    """
    return (
        "# This file is managed by pengyos-live. Do not edit by hand.\n"
        "#\n"
        "# Let the live user (live) run the PengyOS installer without a\n"
        "# password, so it can autostart on the live session and complete the\n"
        f"# install without a human typing a password. Scoped to {INSTALLER_BIN}.\n"
        f"{LIVE_USER} ALL=(ALL) NOPASSWD: /usr/bin/{INSTALLER_BIN}\n"
    )


def _live_marker_content(edition: Edition) -> str:
    """The live marker identifying the running system as a live medium."""
    return (
        "# This file is managed by pengyos-live. Do not edit by hand.\n"
        "#\n"
        "# Present on a PengyOS live/installer medium. Its presence is what tells\n"
        "# the installer (and the rest of the system) it is running from a live\n"
        "# medium, not from an installed target.\n"
        f"edition={edition.value_str}\n"
        "live=true\n"
    )


def _files_for(edition: Edition, session: Session) -> tuple[OverlayFile, ...]:
    """Build the overlay files for a single edition + session."""
    files: list[OverlayFile] = [
        OverlayFile(AUTOLOGIN_PATH, _autologin_content()),
        OverlayFile(LIVE_MARKER_PATH, _live_marker_content(edition)),
    ]
    if session.is_console:
        files.append(
            OverlayFile(INSTALLER_UNIT_PATH, _console_installer_unit_content())
        )
    else:
        files.append(
            OverlayFile(INSTALLER_SESSION_PATH, _cosmic_installer_session_content())
        )
    # The live-user sudo policy is session-independent: both the console unit
    # and the cosmic entry launch the installer as the live user, so both need
    # it to complete the install unattended.
    files.append(OverlayFile(SUDOERS_PATH, _sudoers_content()))
    return tuple(files)


# --- the overlay -------------------------------------------------------------


@dataclass(frozen=True)
class LiveOverlay:
    """The complete live overlay for one edition + session.

    ``files`` is the ordered set of :class:`OverlayFile`s that make up the
    overlay. The order is deterministic (autologin, live marker, installer), so
    the emitted overlay is byte-for-byte stable across runs.
    """

    edition: Edition
    session: Session
    files: tuple[OverlayFile, ...] = field(default_factory=tuple)

    @property
    def installer_files(self) -> tuple[OverlayFile, ...]:
        """The overlay files that autostart the installer (unit or session)."""
        return tuple(
            f for f in self.files if f.path in (INSTALLER_UNIT_PATH, INSTALLER_SESSION_PATH)
        )

    @property
    def autologin_file(self) -> OverlayFile | None:
        """The autologin drop-in, if present."""
        for f in self.files:
            if f.path == AUTOLOGIN_PATH:
                return f
        return None

    @property
    def marker_file(self) -> OverlayFile | None:
        """The live marker, if present."""
        for f in self.files:
            if f.path == LIVE_MARKER_PATH:
                return f
        return None

    @property
    def sudoers_file(self) -> OverlayFile | None:
        """The live-user sudo policy, if present."""
        for f in self.files:
            if f.path == SUDOERS_PATH:
                return f
        return None

    def paths(self) -> tuple[str, ...]:
        """The sorted absolute paths of every file in the overlay."""
        return tuple(sorted(f.path for f in self.files))


def build_overlay(edition: Edition, session: Session) -> LiveOverlay:
    """Build the live overlay for ``edition`` + ``session``.

    This is the single source of truth for what an emitted overlay contains.
    ``emit`` writes :meth:`LiveOverlay.files` to disk; ``lint`` reads them back.
    """
    return LiveOverlay(
        edition=edition,
        session=session,
        files=_files_for(edition, session),
    )


def build_overlay_from_strings(
    edition: str, session: str
) -> LiveOverlay:
    """Build an overlay from canonical edition/session strings.

    Raises :class:`ValueError` for an unknown edition or session. Used by the
    JSON path and tests, where the strings arrive unparsed.
    """
    return build_overlay(Edition(edition), Session(session))


# --- JSON --------------------------------------------------------------------

#: The JSON representation of a live overlay. Kept as a module-level constant so
#: ``lint`` (and any consumer) can validate an incoming document against it.
OVERLAY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["edition", "session", "files"],
    "properties": {
        "edition": {
            "type": "string",
            "enum": list(Edition.all_values()),
        },
        "session": {
            "type": "string",
            "enum": list(Session.all_values()),
        },
        "files": {
            "type": "array",
            "minItems": 4,
            "items": {
                "type": "object",
                "required": ["path", "content"],
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
    },
}


def overlay_to_dict(overlay: LiveOverlay) -> dict[str, Any]:
    """Serialise a :class:`LiveOverlay` to a JSON-ready dict."""
    return {
        "edition": overlay.edition.value_str,
        "session": overlay.session.value_str,
        "files": [f.as_dict() for f in overlay.files],
    }


def overlay_to_json(overlay: LiveOverlay, *, indent: int | None = 2) -> str:
    """Serialise a :class:`LiveOverlay` to a JSON string."""
    return json.dumps(overlay_to_dict(overlay), indent=indent, sort_keys=True)
