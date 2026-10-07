"""PengyOS install-tui — the guided Core/Server installer + unattended ``--auto``.

A pure, offline-testable wizard state machine (disk/target, edition, layout,
user, network, confirm) that drives ``pengyos-install``, plus a non-interactive
``--auto`` front-end over ``install.toml``.

The package is split into three concerns:

* :mod:`pengyos_install_tui.model` — the wizard state machine: steps,
  validators, back-navigation and derived answers. Pure, no I/O.
* :mod:`pengyos_install_tui.run` — render the model on stdin/stdout.
* :mod:`pengyos_install_tui.auto` — parse ``install.toml`` into the same answer
  set the interactive path produces.

This module is the public surface; the CLI lives in
:mod:`pengyos_install_tui.cli`.
"""

from __future__ import annotations

from pengyos_install_tui.model import (
    Edition,
    Layout,
    NetworkAnswer,
    NetworkMode,
    Plan,
    StepKey,
    StepState,
    UserAnswer,
    WIZARD_ORDER,
    ValidationError,
    Wizard,
    validate_edition,
    validate_layout,
    validate_network,
    validate_target,
    validate_user,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # state machine
    "StepKey",
    "WIZARD_ORDER",
    "Edition",
    "Layout",
    "NetworkMode",
    "ValidationError",
    "Wizard",
    "Plan",
    "StepState",
    "UserAnswer",
    "NetworkAnswer",
    # validators
    "validate_target",
    "validate_edition",
    "validate_layout",
    "validate_user",
    "validate_network",
]
