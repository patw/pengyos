"""Module entry point: ``python -m pengyos_fleet_rollout``."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
