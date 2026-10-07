"""Schema model for a declarative container ``service.yaml``.

The schema is deliberately small (see spec.md). Every field
maps directly onto a flag passed to a rootless ``podman run`` invocation that the
generated ``systemd --user`` unit executes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Service:
    """A parsed ``service.yaml``.

    Attributes:
        name: Service identity. Required. Used as the container name and the
            generated unit filename (<name>.service).
        image: Container image reference. Required.
        user: Optional UID/GID the container runs as (rootless podman user).
        restart: systemd ``Restart=`` value. Defaults to ``on-failure``.
        ports: Published ports, each a ``"<host>:<container>"`` (host side
            optional) string, e.g. ``"8080:80"`` or ``"80"``.
        volumes: Bind mounts, each a ``"<host>:<container>"`` pair string.
        env: Environment variables exported into the container.
        reachable: Whether the service is expected to be reachable on the LAN
            (US-SRV-2's core purpose). Defaults to ``True``: a reachable service
            must publish at least one port. Set to ``False`` for non-network
            services (workers, cron, batch jobs) that legitimately publish no
            ports — the reachability policy is then skipped (the "escape hatch").
    """

    name: str
    image: str
    user: Optional[str] = None
    restart: str = "on-failure"
    ports: list[str] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    reachable: bool = True

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict of the service."""

        return {
            "name": self.name,
            "image": self.image,
            "user": self.user,
            "restart": self.restart,
            "ports": list(self.ports),
            "volumes": list(self.volumes),
            "env": dict(self.env),
            "reachable": self.reachable,
        }
