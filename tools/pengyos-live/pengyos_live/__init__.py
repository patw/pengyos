"""pengyos-live — the PengyOS live/installer session overlay.

Boot a PengyOS install medium into a live desktop with the installer running.
This package emits the live-session overlay and lints it:

* :mod:`.cli` defines the command-line surface — ``emit`` (render the live
  overlay for an edition + session) and ``lint`` (check an emitted overlay).
* :mod:`.emit` writes the overlay; :mod:`.lint`
  checks it. Both reason over :mod:`.model`, the
  single source of truth.

See ``spec.md`` and ``README.md`` for the full interface and behaviour
contract.
"""

__version__ = "0.5.0"

# ``cli`` is imported last: it imports ``__version__`` back out of this package,
# so defining it above avoids a circular-import error.
from . import cli  # noqa: E402,F401

__all__ = ["__version__", "cli"]
