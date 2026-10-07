"""pengyos-upstream-watch — detect new upstream releases for PengyOS pins.

The PengyOS maintenance loop watches upstream and proposes what matters, while a
human ratifies the boundary (SPEC §3, OBSERVE stage).

This package is the *detect* half of that loop: given a pins manifest (the
pinned ``(name, version)`` pairs that make a build reproducible) plus a
``Fetcher`` abstraction that knows how to ask an upstream for its latest
release, it decides whether a pin has drifted out of date and emits change
proposals.

The fetcher is the only thing that touches the outside world, so the detection
logic stays fully offline-testable: a mock fetcher stands in for the real HTTP
one in tests.

See ``spec.md`` for the full contract.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
