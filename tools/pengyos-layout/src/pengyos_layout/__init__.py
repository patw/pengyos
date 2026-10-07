"""pengyos_layout — declarative disk/layout planner for PengyOS installs.

The planner turns a *target probe* (a description of the target disk) plus an
*edition* and a set of *options* into an ordered list of partitions, each with a
role, size, filesystem and mountpoint. The install engine consumes the resulting
plan as data — it never hardcodes the GPT + ESP + A/B + data layout itself.

Layered across iterations:

* ``model`` — the layout as data (partitions, probe, options, plan).
* ``invariants`` — the layout contract (GPT alignment, ESP bounds, A/B symmetry,
  sizes vs disk, no overlap, refuse the live device).
* ``validator`` — check a plan against the invariants.
* ``plan`` — derive a golden layout from a probe + edition + options.
* ``emit`` — the ``sgdisk``/``mkfs``/``mount`` argv for the executor.
* ``cli`` — the ``pengyos-layout`` command-line interface.
"""

from .model import (
    DEFAULT_FS_BY_ROLE,
    Fs,
    LayoutError,
    Options,
    Partition,
    Plan,
    Role,
    TargetProbe,
    ValidationError,
)
from .plan import plan
from .validator import Validator
from .emit import Step, PlanEmitter, emit

__version__ = "0.5.0"

__all__ = [
    "__version__",
    # model
    "Fs",
    "Role",
    "DEFAULT_FS_BY_ROLE",
    "LayoutError",
    "ValidationError",
    "Options",
    "Partition",
    "Plan",
    "TargetProbe",
    # validator
    "Validator",
    # planner
    "plan",
    # emit
    "Step",
    "PlanEmitter",
    "emit",
]
