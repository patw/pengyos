"""pengyos_hardening — a declarative PengyOS hardening baseline + linter.

PengyOS is built with Buildroot using an external (``BR2_EXTERNAL``) tree. Each edition is a
Buildroot *defconfig* plus a rootfs-overlay, and today the editions hand-write
an ``sshd_config``, an ``nftables`` ruleset and defconfigs with nothing that
asserts the posture is actually hardened — a reopened ``PermitRootLogin`` or a
policy-accept firewall would ship silently.

This package turns the hardening posture into **data the build is checked
against**, so drift fails a test instead of reaching a machine:

* :mod:`pengyos_hardening.baseline` — load + structurally validate the
  committed baseline (rules with a stable id, severity, domain, matcher).
* :mod:`pengyos_hardening.check` — audit a repo tree (every
  ``BR2_EXTERNAL */rootfs-overlay/`` and the ``*_defconfig``s) into per-rule
  findings with ``file:line`` evidence.
* :mod:`pengyos_hardening.report` — render the audit as stable text or JSON.

The code is the reader; the baseline data is the contract (see spec.md).
"""

from __future__ import annotations

__version__ = "0.7.4"

# The three submodules that make up the tool. They are imported lazily by the
# CLI (see ``pengyos_hardening.cli``) so the dispatch surface works even while a
# submodule is still being authored; the names here document the package's
# public surface.
__all__ = ["__version__", "baseline", "check", "report"]
