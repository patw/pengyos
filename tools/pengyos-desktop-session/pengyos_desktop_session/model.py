"""The COSMIC session modelled as data.

A single frozen dataclass, :class:`CosmicSession`, captures every fact the
deterministic emitter needs to render the COSMIC session definition:

* the **greetd config** — the user greetd runs the session as, the session
  command, and the wayland-socket env var greetd hands the greeter;
* the **cosmic-greeter session entry** — the ``[Desktop Entry]`` fields that make
  COSMIC appear in cosmic-greeter's session list, most notably the decided
  compositor (``compositor = "cosmic-comp"``) and the session ``exec``
  (``cosmic-session``);
* the **systemd unit name** and the **wayland socket path**;
* the **session environment** (``XDG_CURRENT_DESKTOP=COSMIC`` and friends).

The emitter that turns this model into deterministic file contents lives in
:mod:`pengyos_desktop_session.emit`. Nothing here does any I/O.

The values below are the operator-decided COSMIC facts (operator 2026-09-19;
the Compositor Decision Record in ``pengyos-desktop-defconfig`` records
COSMIC as the decided compositor/session, with ``cosmic-comp`` /
``cosmic-session``). They are plain defaults on the dataclass, so a caller
may override any of them — but the defaults are the PengyOS Desktop answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

__all__ = ["CosmicSession"]


@dataclass(frozen=True)
class CosmicSession:
    """The COSMIC session as immutable data.

    Frozen: every field has a PengyOS Desktop default, and the emitter renders
    them deterministically. Construct with :meth:`default` for the canonical
    Desktop session, or pass explicit overrides for tests / other distros.
    """

    # --- greetd config ---------------------------------------------------
    # The user greetd runs the default session as.
    greetd_user: str = "alex"
    # The greetd ``[default_session].command``: the greeter greetd launches.
    greetd_command: str = "cosmic-greeter"
    # The env var greetd passes the wayland socket fd/path through to the
    # greeter / session. greetd names it so the greeter can ``exec`` the
    # session with the socket already wired up.
    greetd_socket_var: str = "WAYLAND_SOCKET"

    # --- cosmic-greeter session entry (a [Desktop Entry]) ----------------
    # The session's display name in cosmic-greeter's session list.
    cosmic_greeter_session_name: str = "COSMIC"
    # The session's one-line comment in cosmic-greeter's session list.
    cosmic_greeter_session_comment: str = "COSMIC Desktop Environment"
    # The session's ``Exec`` — the program that starts the session.
    cosmic_greeter_exec: str = "cosmic-session"
    # The decided compositor. This is the operator decision (SPEC 6.9):
    # COSMIC (``cosmic-comp`` / ``cosmic-session``) is the Desktop compositor,
    # with sway/weston/niri recorded as rejected alternatives.
    compositor: str = "cosmic-comp"
    # The Desktop Entry ``Type`` (always ``Application`` for a wayland session).
    session_type: str = "Application"
    # The Desktop Entry ``Categories`` (semicolon-separated).
    session_categories: str = "COSMIC"

    # --- systemd unit + wayland socket ----------------------------------
    # The systemd unit name that launches the cosmic-session (a template unit,
    # hence the ``@.service`` suffix): ``cosmic-session@.service``.
    unit_name: str = "cosmic-session@.service"
    # The wayland socket path the compositor listens on. ``{uid}`` is filled
    # in by the emitter from the runtime user id (see :meth:`wayland_socket`).
    wayland_socket_path: str = "/run/user/{uid}/wayland-0"

    # --- session environment --------------------------------------------
    # The session environment. Keys are emitted sorted by the emitter, so this
    # dict's insertion order is irrelevant; the values are the Desktop answer.
    env: Mapping[str, str] = field(
        default_factory=lambda: {
            "XDG_CURRENT_DESKTOP": "COSMIC",
            "XDG_SESSION_TYPE": "wayland",
            "XDG_SEAT": "seat0",
            "XDG_VIRTUAL_BACKEND": "wayland",
            "WAYLAND_DISPLAY": "wayland-0",
        }
    )

    # --- computed helpers ------------------------------------------------

    def wayland_socket(self, uid: int) -> str:
        """Resolve the wayland socket path for ``uid`` (fills ``{uid}``)."""
        return self.wayland_socket_path.format(uid=uid)

    @classmethod
    def default(cls) -> "CosmicSession":
        """Return the canonical PengyOS Desktop COSMIC session.

        Convenience constructor so callers do not repeat the operator-decided
        defaults (compositor ``cosmic-comp``, exec ``cosmic-session``, ...).
        """
        return cls()
