"""The ``check`` submodule — audit a repo tree against the baseline.

This is the linter core (spec.md): it walks a BR2_EXTERNAL tree, discovers every
``*/rootfs-overlay/``, evaluates each baseline rule against the files it can
see, and returns per-rule :class:`Finding`s with ``file:line`` evidence.

The layering mirrors :mod:`pengyos_hardening.baseline`:

* :mod:`.readers` — thin parsers (the sshd_config reader first).
* :mod:`.matchers` — one evaluator per matcher kind; dispatch on ``kind``.
* :mod:`.audit` — walk the tree, collect locations, evaluate rules.
* :mod:`.report` — render the audit as stable text or JSON.
* :func:`main` — the ``check`` CLI entry point (imported lazily by ``cli``).

Matchers are added one domain at a time: the sshd_config
reader (``key_value``) ships first. A matcher kind with no evaluator is skipped
(reported, not failed) so the tool degrades gracefully.
"""

from __future__ import annotations

from typing import Any

from .audit import AuditLocation, AuditResult, audit, discover_locations

__all__ = [
    "audit",
    "discover_locations",
    "AuditResult",
    "AuditLocation",
    "main",
    "matchers",
    "readers",
    "report",
]


def main(args: Any) -> int:
    """Entry point for ``pengyos-hardening check`` (delegates to ``.report``)."""
    from .report import main as _report_main

    return _report_main(args)
