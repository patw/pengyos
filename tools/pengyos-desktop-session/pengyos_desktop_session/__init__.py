"""pengyos-desktop-session — the COSMIC session + a VM-testable smoke matrix (US-DSK-1).

A declarative COSMIC-session manager for the PengyOS Desktop edition:
greetd/cosmic-greeter -> cosmic-session startup as deterministic systemd +
session files, plus a check engine for the session facts QEMU+OVMF can actually
verify (cosmic-comp started, a wayland socket exists, cosmic-session/panel up,
pipewire running, portals responding) — with GPU/audio/BT explicitly out of
scope.

:mod:`.cli` defines the command-line surface (``emit``, ``check``).

See ``spec.md`` and ``README.md`` for the full interface and behaviour
contract.
"""

__version__ = "0.7.0"

# ``cli`` is imported last: later modules may import ``__version__`` back out of
# this package, so defining it above avoids a circular-import error.
from . import cli  # noqa: E402,F401

__all__ = ["__version__", "cli"]
