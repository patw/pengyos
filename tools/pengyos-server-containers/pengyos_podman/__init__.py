"""PengyOS server-containers: rootless podman + declarative services (US-SRV-2).

A tiny, dependency-light CLI that turns a small ``service.yaml`` into a
reboot-safe rootless ``podman`` ``systemd --user`` unit with published ports and
persistent volumes.

The public surface is intentionally small:

* :func:`pengyos_podman.loader.load_service` parses a ``service.yaml``.
* :func:`pengyos_podman.validate.validate` checks the parsed service.
* :func:`pengyos_podman.render.render_unit` / :func:`render_json` emit output.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
