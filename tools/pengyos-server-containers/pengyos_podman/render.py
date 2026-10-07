"""Render a :class:`Service` into output artifacts.

Two renderers are provided:

* :func:`render_unit` — a rootless ``podman`` **Quadlet** ``.container`` file
  (a ``systemd --user`` unit source). The ``[Container]`` section drives the
  container and, crucially, exposes ``PodmanArgs=``, the one documented key that
  maps straight onto ``podman run`` options (e.g. ``PodmanArgs=--publish ...``).
  ``[Service]`` carries the lifecycle (``ExecStart`` / ``ExecStop`` / ``Restart=``)
  and ``[Install]`` the boot hook.
* :func:`render_json` — the parsed service as pretty-printed JSON.

The Quadlet form is emitted on purpose: ``PodmanArgs=`` is a real, loadable
``podman`` systemd directive *only* inside a ``[Container]`` section of a
``.container`` file (see ``podman-container.unit(5)``). A plain ``.service`` file
cannot hold a valid ``PodmanArgs=`` line, so this is the correct, reboot-safe
rootless user-unit representation.
"""

from __future__ import annotations

import json
from typing import Iterable

from .model import Service

# `podman` is resolved via the user's PATH by the podman systemd generator at
# boot, which is what we want for a rootless user unit.
PODMAN_BIN = "podman"


def _podman_run_args(service: Service) -> list[str]:
    """Build the ``podman run`` argument list (no leading ``podman run``).

    Emitted verbatim onto the ``PodmanArgs=`` line. Order is deterministic:
    ``--rm``, ``--name``, optional ``--user``, sorted ``--publish``, sorted
    ``--volume``, sorted ``--env``.
    """

    args = ["--rm", f"--name {service.name}"]

    if service.user:
        args.append(f"--user {service.user}")

    for port in sorted(service.ports):
        args.append(f"--publish {port}")

    for volume in sorted(service.volumes):
        args.append(f"--volume {volume}")

    for key, value in sorted(service.env.items()):
        args.append(f"--env {key}={value}")

    return args


def render_unit(service: Service) -> str:
    """Render ``service`` as a rootless podman Quadlet ``.container`` body.

    Output ends with a trailing newline and is byte-for-byte deterministic for a
    given service (publish/volume/env are sorted).
    """

    run_args = " ".join(_podman_run_args(service))

    lines: list[str] = []
    lines.append("[Unit]")
    lines.append(f"Description=PengyOS Container Service: {service.name}")
    lines.append("After=network.target")
    lines.append("")
    lines.append("[Container]")
    lines.append(f"Image={service.image}")
    lines.append(f"PodmanArgs={run_args}")
    lines.append("")
    lines.append("[Service]")
    lines.append("Type=simple")
    # ``User=`` is a valid systemd [Service] key. In rootless mode the effective
    # user is whichever logged-in user systemd runs as; see spec.md note.
    if service.user:
        lines.append(f"User={service.user}")
    # Clean up any leftover container before (re)starting; the leading '-' lets
    # the very first start succeed when no container exists yet.
    lines.append(f"ExecStartPre=-{PODMAN_BIN} rm -f {service.name}")
    lines.append(f"ExecStart={PODMAN_BIN} run")
    lines.append(f"ExecStop={PODMAN_BIN} stop -t 10 {service.name}")
    lines.append(f"Restart={service.restart}")
    lines.append("RestartSec=5")
    lines.append("")
    lines.append("[Install]")
    lines.append("WantedBy=default.target")

    return "\n".join(lines) + "\n"


def render_json(service: Service) -> str:
    """Render ``service`` as pretty-printed, key-sorted JSON (trailing newline)."""

    return json.dumps(service.to_dict(), indent=2, sort_keys=True) + "\n"


def unit_filename(service: Service) -> str:
    """Return the ``.quadlet`` filename for ``service`` (a Quadlet source)."""

    return f"{service.name}.quadlet"


def render_units(services: Iterable[Service]) -> dict[str, str]:
    """Render several services to a ``{filename: unit_body}`` map."""

    return {unit_filename(s): render_unit(s) for s in services}
