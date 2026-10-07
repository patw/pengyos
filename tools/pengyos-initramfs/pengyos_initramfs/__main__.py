"""Allow ``python -m pengyos_initramfs`` to invoke the CLI."""

from .cli import main

if __name__ == "__main__":  # pragma: no cover - thin wrapper
    raise SystemExit(main())
