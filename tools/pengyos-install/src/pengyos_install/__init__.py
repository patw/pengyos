"""PengyOS install — the edition-agnostic install engine.

``install = first update``: one engine that lays a signed release onto a blank
disk, turning ``(release, target, layout)`` into an ordered plan and executing
that plan through an injectable runner, writing the same artifact the updater
consumes.

This package depends only on the Python standard library.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
