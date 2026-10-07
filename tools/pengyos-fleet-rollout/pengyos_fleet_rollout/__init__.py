"""PengyOS fleet-rollout — plan, stage and roll back an image rollout.

Turn a built PengyOS image (or a pending update set) plus a
``pengyos-fleet-inventory`` into a staged rollout plan (ordered batches, each
with an explicit health gate, and a per-host rollback record), drive it through
an injectable transport (no-op / dry-run by default), and prove ``rollback``
restores the recorded prior artifact — as declarative data.

The package is standard-library only at runtime. ``pytest`` is an optional dev
dependency (see ``pyproject.toml``).
"""

__version__ = "0.1.0"

from .errors import (
    RolloutError,
    RolloutPlanError,
    RolloutTransportError,
)
from .model import (
    GATE_ALL_HEALTHY,
    UNKNOWN_ARTIFACT,
    Batch,
    HealthGate,
    InventoryHost,
    JournalEntry,
    ModelError,
    RollbackEntry,
    RolloutJournal,
    RolloutPlan,
    STATE_APPLIED,
    STATE_FAILED,
    STATE_PENDING,
    STATE_ROLLED_BACK,
    load_inventory,
    validate,
)

__all__ = [
    "__version__",
    "GATE_ALL_HEALTHY",
    "Batch",
    "HealthGate",
    "InventoryHost",
    "JournalEntry",
    "ModelError",
    "RollbackEntry",
    "RolloutError",
    "RolloutJournal",
    "RolloutPlan",
    "RolloutPlanError",
    "RolloutTransportError",
    "STATE_APPLIED",
    "STATE_FAILED",
    "STATE_PENDING",
    "STATE_ROLLED_BACK",
    "UNKNOWN_ARTIFACT",
    "load_inventory",
    "validate",
]
