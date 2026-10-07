"""``plan`` — a fleet inventory plus a built image id -> a staged rollout plan.

This is the first behaviour subtask. It owns *how the fleet is ordered into a
rollout*: read a :class:`~pengyos_fleet_rollout.model.InventoryHost` list, put the
canary first, order the rest by priority (desc) then name (asc), group them into
deterministic batches, and wrap that in a :class:`~pengyos_fleet_rollout.model.RolloutPlan`
with an explicit health gate per batch and a per-host rollback record.

The ordering and batching are pure functions (:func:`order_hosts` and
:func:`group_into_batches`) so they can be unit-tested in isolation; :func:`plan`
wires them together with the image-id validation and the inventory load.

Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .errors import RolloutPlanError
from .model import (
    Batch,
    HealthGate,
    InventoryHost,
    ModelError,
    RollbackEntry,
    RolloutPlan,
    UNKNOWN_ARTIFACT,
    load_inventory,
)

#: The smallest sensible batch. A batch of zero (or fewer) hosts cannot be
#: rolled out and is rejected before anything is ordered.
MIN_BATCH_SIZE = 1

#: A PengyOS image id is a well-formed artifact identifier: non-empty, with no
#: whitespace, and containing at least one digit (so a bare word like ``latest``
#: or a malformed string like ``img!!!`` is rejected as an unknown image).
#:
#: This tool is hermetic and offline, so there is no build registry to consult;
#: "known" is defined by the id's *shape* — the same sanity check an operator
#: runs before trusting an id to reach the fleet. A real online deployment would
#: substitute a registry lookup here, but keep the same contract: an unknown
#: image is a hard error.
_IMAGE_ID_RE = re.compile(r"^[^\s]+$")


def is_known_image(image: str) -> bool:
    """Whether ``image`` is a well-formed PengyOS image id (known).

    A known image id is non-empty, has no whitespace, and contains at least one
    digit. Anything else — empty, blank, or a malformed string — is *unknown*
    and :func:`plan` rejects it.
    """

    if not image or not _IMAGE_ID_RE.match(image):
        return False
    return any(char.isdigit() for char in image)


def order_hosts(hosts: Iterable[InventoryHost]) -> list[InventoryHost]:
    """Order hosts for rollout: canary first, then priority desc, then name asc.

    * A host whose role is ``canary`` is pulled to the front (the canary batch is
      always the first step).
    * Within that, higher :attr:`~InventoryHost.priority` comes first; a missing
      priority sorts last.
    * Ties break on :attr:`~InventoryHost.name` ascending, so the order is
      deterministic for any inventory.

    Returns a new list; the input order is not mutated.
    """

    ordered = list(hosts)

    def sort_key(host: InventoryHost) -> tuple[int, int, str]:
        canary_first = 0 if (host.role and host.role.casefold() == "canary") else 1
        # A missing priority sorts last; negate so higher priority comes first.
        priority = -1 if host.priority is None else host.priority
        return (canary_first, -priority, host.name)

    return sorted(ordered, key=sort_key)


def group_into_batches(
    ordered: list[InventoryHost], batch_size: int
) -> list[list[InventoryHost]]:
    """Chunk the ordered hosts into batches of at most ``batch_size``.

    Every host is kept — the final batch may be smaller than ``batch_size`` but
    is never empty. The chunking is stable, so the batch order matches the
    rollout order.

    Raises :class:`RolloutPlanError` for a ``batch_size`` below :data:`MIN_BATCH_SIZE`.
    """

    if batch_size < MIN_BATCH_SIZE:
        raise RolloutPlanError(
            f"batch-size must be >= {MIN_BATCH_SIZE}, got {batch_size}"
        )

    return [ordered[i : i + batch_size] for i in range(0, len(ordered), batch_size)]


def _batches_for(order: list[InventoryHost], batch_size: int) -> tuple[Batch, ...]:
    """Build the ordered, canary-first :class:`Batch`es for the plan."""

    groups = group_into_batches(order, batch_size)
    return tuple(
        Batch(index=index, hosts=tuple(host.name for host in group))
        for index, group in enumerate(groups)
    )


def _rollback_for(image: str, order: list[InventoryHost]) -> tuple[RollbackEntry, ...]:
    """Build the per-host rollback record, in rollout order.

    ``prior`` is the :data:`UNKNOWN_ARTIFACT` placeholder until ``apply`` records
    the real prior artifact; ``new`` is the image being rolled out.
    """

    return tuple(
        RollbackEntry(host=host.name, prior=UNKNOWN_ARTIFACT, new=image)
        for host in order
    )


def plan(
    image: str,
    inventory_path: str,
    batch_size: int = 1,
) -> RolloutPlan:
    """Plan a staged rollout from an image id and a fleet inventory.

    Reads the inventory, orders the fleet canary-first (priority desc, then name
    asc), groups it into batches of ``batch_size``, and wraps that in a
    :class:`RolloutPlan` with an explicit health gate per batch and a per-host
    rollback record.

    Raises :class:`~pengyos_fleet_rollout.errors.RolloutPlanError` for a
    ``batch_size`` below :data:`MIN_BATCH_SIZE`, an unknown image id, or a
    missing / empty / malformed inventory (the model-layer `ModelError` from
    :func:`load_inventory` is translated into the same `RolloutPlanError`, so
    every input problem surfaces as one error the CLI maps to exit 2).

    Args:
        image: The built PengyOS image id (or pending update set) being rolled
            out. Must be a known image id.
        inventory_path: Path to the ``pengyos-fleet-inventory`` ``[[hosts]]``
            document.
        batch_size: Maximum number of hosts per batch (default: 1 — the canary).

    Returns:
        The staged :class:`RolloutPlan`, with ``inventory`` set to ``inventory_path``
        for provenance.
    """

    if not is_known_image(image):
        raise RolloutPlanError(f"unknown image id: {image!r}")

    if batch_size < MIN_BATCH_SIZE:
        raise RolloutPlanError(
            f"batch-size must be >= {MIN_BATCH_SIZE}, got {batch_size}"
        )

    # An empty / malformed / missing inventory is an *input* problem — the same
    # class of failure as an unknown image — so translate the model-layer
    # ModelError into a RolloutPlanError (the error the CLI maps to exit 2).
    try:
        hosts = load_inventory(inventory_path)
    except ModelError as exc:
        raise RolloutPlanError(str(exc)) from exc
    order = order_hosts(hosts)

    return RolloutPlan(
        image=image,
        inventory=str(inventory_path),
        batches=_batches_for(order, batch_size),
        rollback=_rollback_for(image, order),
    )
