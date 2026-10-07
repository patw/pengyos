"""Declarative guest configuration.

A ``guests.toml`` file is a table of guest definitions under a top-level
``[guests]`` table. Each guest is identified by its key and declares:

    [[guests.wolfi]]
    image = "quay.io/aliyunos/wolfi:latest"
    packages = ["git", "curl"]
    exports = { "git-editor" = "/var/lib/distrobox/bin/git-editor" }
    mounts = ["/home/alex/projects:/projects:rw"]

``packages`` are provisioned inside the guest on ``apply``; ``setup`` shell
commands then run inside it as the user (toolchains that are not distro
packages: rustup, uv). ``nvidia = true`` shares the host's NVIDIA driver with
the guest (CUDA). ``description`` is shown by ``list``.
``exports`` map a launcher name to the guest-side executable path that the
host-side launcher (``export``) will invoke.
``mounts`` are ``host:guest`` (optional ``:mode``) bind-mount specs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from pengyos_guest._toml_compat import loads


@dataclass(frozen=True)
class Guest:
    """A single declarative guest definition."""

    name: str
    image: str
    packages: list[str] = field(default_factory=list)
    exports: dict[str, str] = field(default_factory=dict)
    mounts: list[str] = field(default_factory=list)
    setup: list[str] = field(default_factory=list)
    nvidia: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise back to the on-disk shape (minus the ``name`` key)."""
        return {
            "image": self.image,
            "packages": list(self.packages),
            "exports": dict(self.exports),
            "mounts": list(self.mounts),
            "setup": list(self.setup),
            "nvidia": self.nvidia,
            "description": self.description,
        }


@dataclass
class Guestbook:
    """All guests declared in a ``guests.toml`` file, in declaration order."""

    guests: list[Guest] = field(default_factory=list)

    def names(self) -> list[str]:
        return [g.name for g in self.guests]

    def get(self, name: str) -> Guest | None:
        for g in self.guests:
            if g.name == name:
                return g
        return None

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.guests)


def parse(text: str) -> Guestbook:
    """Parse the TOML *text* into a :class:`Guestbook`."""
    data = loads(text)
    raw = data.get("guests")
    if raw is None:
        raise ValueError("guests.toml must contain a top-level [guests] table")
    if not isinstance(raw, dict):
        raise ValueError("[guests] table must map a guest name to its definition")

    guests: list[Guest] = []
    for name, body in raw.items():
        # Accept both idioms:
        #   [[guests.wolfi]]  -> array of tables  (body is a list)
        #   [guests.wolfi]    -> nested table     (body is a dict)
        if isinstance(body, list):
            if not body:
                raise ValueError(f"guest {name!r} must be a table")
            body = body[-1]  # last definition wins
        if not isinstance(body, dict):
            raise ValueError(f"guest {name!r} must be a table")
        guests.append(
            Guest(
                name=name,
                image=str(body["image"]),
                packages=[str(p) for p in body.get("packages", [])],
                exports={str(k): str(v) for k, v in body.get("exports", {}).items()},
                mounts=[str(m) for m in body.get("mounts", [])],
                setup=[str(c) for c in body.get("setup", [])],
                nvidia=bool(body.get("nvidia", False)),
                description=str(body.get("description", "")),
            )
        )
    return Guestbook(guests=guests)


def load(path: str | os.PathLike[str]) -> Guestbook:
    """Load a ``guests.toml`` file from *path*."""
    with open(path, "r", encoding="utf-8") as fh:
        return parse(fh.read())


#: Guest definitions shipped in the image, one or more per file (the Server
#: layer ships ``dev``; the NVIDIA layer adds ``cuda``).
SYSTEM_GUESTS_DIR = "/usr/share/pengyos/guests.d"


def user_guests_file() -> str:
    """The user's own definitions: ``$XDG_CONFIG_HOME/pengyos/guests.toml``."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "pengyos", "guests.toml")


def load_default(
    system_dir: str | os.PathLike[str] | None = None,
    user_file: str | os.PathLike[str] | None = None,
) -> Guestbook:
    """Every guest this machine knows: the image's ``guests.d/*.toml`` (sorted),
    then the user's ``guests.toml`` — a user guest replaces a system guest of
    the same name. Missing files are skipped."""
    system_dir = os.fspath(system_dir if system_dir is not None
                           else os.environ.get("PENGYOS_GUESTS_DIR", SYSTEM_GUESTS_DIR))
    user_file = os.fspath(user_file) if user_file is not None else user_guests_file()
    merged: dict[str, Guest] = {}
    paths = []
    if os.path.isdir(system_dir):
        paths += sorted(os.path.join(system_dir, f) for f in os.listdir(system_dir)
                        if f.endswith(".toml"))
    if os.path.isfile(user_file):
        paths.append(user_file)
    for path in paths:
        for guest in load(path).guests:
            merged[guest.name] = guest
    return Guestbook(guests=list(merged.values()))
