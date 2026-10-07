"""The deterministic COSMIC-session emitter.

Given a :class:`~pengyos_desktop_session.model.CosmicSession`, render each of
the five session-definition artifacts as a deterministic byte-for-byte
reproducible string:

======================  ============================================  ===============
Artifact                File                                        Renderer
======================  ============================================  ===============
greetd config           ``greetd.toml``                             :func:`render_greetd_config`
cosmic-greeter entry    ``cosmic.desktop``                          :func:`render_cosmic_greeter_entry`
cosmic-session unit     ``cosmic-session@.service``                 :func:`render_cosmic_session_unit`
cosmic-session launcher ``cosmic-session-launch.sh``                :func:`render_launcher`
session environment     ``cosmic-session.env``                      :func:`render_env`
======================  ============================================  ===============

:func:`render_artifacts` returns an ordered ``dict`` mapping *filename ->
content* in the canonical order above, so the caller (the ``emit`` CLI, item 3)
can write them out deterministically. Nothing here does I/O — it only produces
strings — so it is pure and unit-testable.

Determinism rules (so the output is byte-for-byte reproducible across runs and
machines):

* keys / env vars are emitted in sorted order;
* every file ends with exactly one trailing newline;
* a header comment identifies the tool and points at ``emit``;
* no timestamps, no host names, no absolute uid leaks into the committed
  content (the wayland socket keeps the ``{uid}`` placeholder).
"""

from __future__ import annotations

from typing import Callable

from .model import CosmicSession

__all__ = [
    "ARTIFACT_ORDER",
    "ARTIFACT_RENDERERS",
    "render_artifacts",
    "render_greetd_config",
    "render_cosmic_greeter_entry",
    "render_cosmic_session_unit",
    "render_launcher",
    "render_env",
]

HEADER = (
    "# Emitted by `pengyos-desktop-session` (US-DSK-1). Do not edit by hand;\n"
    "# re-emit with:  pengyos-desktop-session emit --out /path/to/overlay\n"
)


def _finish(text: str) -> str:
    """Normalise emitted content: exactly one trailing newline, LF line ends."""
    return text.replace("\r\n", "\n").rstrip("\n") + "\n"


def _toml_string(value: str) -> str:
    """Quote a value as a TOML basic string, escaping backslash and quotes."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_greetd_config(session: "CosmicSession | None" = None) -> str:
    """Render the greetd ``config.toml`` ``[default_session]`` block.

    greetd runs ``command`` as the default session for ``user`` and passes the
    wayland socket to it through ``greetd_socket_var``. The environment is
    emitted in sorted key order for determinism.
    """
    session = session or CosmicSession.default()
    lines = [HEADER, "[default_session]"]
    lines.append(f"command = {_toml_string(session.greetd_command)}")
    lines.append(f"user = {_toml_string(session.greetd_user)}")
    env = session.env
    if env:
        pairs = ", ".join(
            f"{key} = {_toml_string(str(env[key]))}" for key in sorted(env)
        )
        lines.append(f"env = {{ {pairs} }}")
    else:
        lines.append("env = {}")
    return _finish("\n".join(lines))


def render_cosmic_greeter_entry(session: "CosmicSession | None" = None) -> str:
    """Render the cosmic-greeter session entry (a ``[Desktop Entry]``).

    This is what makes COSMIC appear in cosmic-greeter's session list: the
    decided compositor (``Compositor = cosmic-comp``) and the session ``exec``
    (``Exec = cosmic-session``).
    """
    session = session or CosmicSession.default()
    lines = [
        HEADER,
        "[Desktop Entry]",
        f"Name = {_toml_string(session.cosmic_greeter_session_name)}",
        f"Comment = {_toml_string(session.cosmic_greeter_session_comment)}",
        f"Exec = {_toml_string(session.cosmic_greeter_exec)}",
        f"Compositor = {_toml_string(session.compositor)}",
        f"Type = {_toml_string(session.session_type)}",
        f"Categories = {_toml_string(session.session_categories)}",
    ]
    return _finish("\n".join(lines))


def render_cosmic_session_unit(session: "CosmicSession | None" = None) -> str:
    """Render the cosmic-session systemd unit (a ``[Service]`` template unit).

    ``ExecStart`` runs the launcher under ``/usr/local/bin``; the session
    environment is sourced from ``cosmic-session.env`` (written alongside). The
    unit name is ``cosmic-session@.service`` (a template unit).
    """
    session = session or CosmicSession.default()
    lines = [
        HEADER,
        "[Unit]",
        f"Description=PengyOS COSMIC session ({session.unit_name})",
        "After=graphical-login.service greetd.service",
        "",
        "[Service]",
        "ExecStart=/usr/local/bin/cosmic-session-launch.sh",
        "Restart=on-failure",
        "RestartSec=3",
    ]
    return _finish("\n".join(lines))


def render_launcher(session: "CosmicSession | None" = None) -> str:
    """Render the cosmic-session launcher script.

    The launcher sets up the wayland display (from the environment) and execs
    the decided compositor's session (``cosmic-session``). It is the ``ExecStart``
    target of the systemd unit above.
    """
    session = session or CosmicSession.default()
    exec_command = session.cosmic_greeter_exec
    # The shebang must be line 1: this script is ExecStart'd directly by the
    # systemd unit, so it has to be a valid executable. The tool-identification
    # lives in the comment right after the shebang.
    lines = [
        "#!/bin/sh",
        "# Launcher for the PengyOS COSMIC session (emitted by "
        "pengyos-desktop-session, US-DSK-1). Do not edit by hand;",
        "# re-emit with:  pengyos-desktop-session emit --out /path/to/overlay",
        'set -eu',
        "",
        "# Ensure a wayland display exists for the compositor.",
        'if [ -z "${WAYLAND_DISPLAY:-}" ]; then',
        '  export WAYLAND_DISPLAY="wayland-0"',
        "fi",
        "",
        f'exec {exec_command}',
    ]
    return _finish("\n".join(lines))


def render_env(session: "CosmicSession | None" = None) -> str:
    """Render the session environment file (``KEY=value`` lines, sorted)."""
    session = session or CosmicSession.default()
    lines = [HEADER]
    for key in sorted(session.env):
        lines.append(f"{key}={session.env[key]}")
    return _finish("\n".join(lines))


# Canonical, stable ordering of the emitted artifacts (filename -> renderer).
# Defined *after* the renderers above so the names resolve at import time.
ARTIFACT_ORDER: tuple[str, ...] = (
    "greetd.toml",
    "cosmic.desktop",
    "cosmic-session@.service",
    "cosmic-session-launch.sh",
    "cosmic-session.env",
)

ARTIFACT_RENDERERS: dict[str, Callable[[CosmicSession], str]] = {
    "greetd.toml": render_greetd_config,
    "cosmic.desktop": render_cosmic_greeter_entry,
    "cosmic-session@.service": render_cosmic_session_unit,
    "cosmic-session-launch.sh": render_launcher,
    "cosmic-session.env": render_env,
}


def render_artifacts(
    session: CosmicSession | None = None,
) -> "dict[str, str]":
    """Render every COSMIC-session artifact in canonical order.

    Returns an ordered ``dict`` mapping *filename* to *byte content* for all
    five artifacts, in :data:`ARTIFACT_ORDER`. The ``emit`` CLI (item 3) writes
    these under ``--out``; this function does no I/O, so the output is pure and
    deterministic.
    """
    session = session or CosmicSession.default()
    return {name: ARTIFACT_RENDERERS[name](session) for name in ARTIFACT_ORDER}
