"""Safe-by-default confirmation and dry-run gate for destructive plans.

This module is the *safety net* the project spec demands: a destructive plan
(notably ``rollback``, which can delete a live subvolume) must **never** be
executed without an explicit confirmation flag (``--yes``,
:data:`pengyos_btrfs.constants.ROLLBACK_CONFIRM_FLAG`), and every plan can be
*previewed* without touching a runner via ``--dry-run``.

It is pure control flow over a :class:`~pengyos_btrfs.commands.Plan` and a
mockable :class:`~pengyos_btrfs.commands.BtrfsRunner`; nothing here shells out
or inspects the filesystem, so the whole contract is golden-testable without
root or a real btrfs volume.

Semantics
---------
- **Destructive plans.** A plan *kind* is *destructive* when it belongs to
  :data:`DESTRUCTIVE_KINDS` (currently ``{"rollback"}``). Only destructive
  plans are gated.
- **The gate** (:func:`require_confirmation`). A destructive plan may be
  *executed* only when the caller explicitly confirmed it (``confirmed=True``,
  i.e. passed ``--yes``). In ``--dry-run`` mode nothing is executed, so no
  confirmation is required — you can preview a rollback without agreeing to
  make the change. The gate raises
  :class:`pengyos_btrfs.errors.RollbackError` (never a traceback) when a
  destructive plan is about to be executed without confirmation.
- **Dry-run vs execute** (:func:`execute_or_preview`). In ``--dry-run`` mode
  the runner is **never called** and an :class:`Execution` is returned with
  ``dry_run=True`` and no results. Otherwise the gate is enforced and the plan
  is executed via :func:`pengyos_btrfs.commands.execute_plan` (fail-fast),
  returning an :class:`Execution` with the recorded :class:`RunResult`s.

The CLI wiring (the ``snapshot``/``rollback`` handlers) consumes this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from . import constants as c
from .commands import BtrfsRunner, Plan, RunResult, execute_plan
from .errors import RollbackError

__all__ = [
    "DESTRUCTIVE_KINDS",
    "is_destructive",
    "requires_confirmation",
    "require_confirmation",
    "Execution",
    "execute_or_preview",
]

# Plan kinds that are destructive and therefore gated behind --yes.
# ``rollback`` deletes live subvolumes; ``create`` and ``snapshot`` are not.
DESTRUCTIVE_KINDS = frozenset({"rollback"})


def is_destructive(kind: str) -> bool:
    """Return ``True`` if plan *kind* is destructive (needs confirmation)."""
    return kind in DESTRUCTIVE_KINDS


def requires_confirmation(kind: str) -> bool:
    """Return ``True`` if executing a plan of *kind* needs explicit ``--yes``.

    Alias for :func:`is_destructive`, named for the CLI's question: *"does
    running this kind require the confirm flag?"*
    """
    return is_destructive(kind)


def require_confirmation(
    kind: str, *, confirmed: bool, dry_run: bool = False
) -> None:
    """Enforce the safe-by-default gate for a plan of *kind*.

    Raises :class:`RollbackError` when the plan is **destructive**, is **not a
    dry-run** (i.e. it is about to be executed), and *confirmed* is ``False``.
    Returns ``None`` (the plan may proceed) in every other case: non-
    destructive kinds are never gated, a dry-run never executes, and an
    explicitly confirmed destructive plan is allowed through.

    Args:
        kind: the plan kind (``"create"`` / ``"snapshot"`` / ``"rollback"``).
        confirmed: whether the caller passed the explicit confirm flag
            (``--yes``).
        dry_run: whether the call is a preview (nothing will be executed).

    Raises:
        RollbackError: if a destructive, non-dry-run plan is not confirmed.
    """
    if not is_destructive(kind):
        return
    if dry_run:
        return
    if confirmed:
        return
    raise RollbackError(
        f"refusing to run destructive {kind!r} plan without explicit "
        f"confirmation — re-run with {c.ROLLBACK_CONFIRM_FLAG} to make the "
        "change, or use --dry-run to preview it without touching anything"
    )


@dataclass(frozen=True)
class Execution:
    """The outcome of an :func:`execute_or_preview` call.

    Attributes:
        dry_run: ``True`` if the plan was only previewed (runner not called).
        results: the :class:`RunResult`s, in plan order. Always empty for a
            dry-run; otherwise the results of every executed step.
    """

    dry_run: bool
    results: tuple  # tuple[RunResult, ...]

    @property
    def argvs(self) -> "list":
        """The argvs that were actually run (empty for a dry-run)."""
        return [list(r.argv) for r in self.results]


def execute_or_preview(
    runner: BtrfsRunner,
    plan: Plan,
    *,
    confirmed: bool = False,
    dry_run: bool = False,
) -> Execution:
    """Execute *plan* through *runner*, or preview it in ``--dry-run`` mode.

    In ``--dry-run`` mode the runner is **never called**; an
    :class:`Execution` with ``dry_run=True`` and no results is returned so the
    caller can report the plan to the user. Otherwise the
    :func:`require_confirmation` gate is enforced first (a destructive plan
    without ``confirmed=True`` raises :class:`RollbackError` before any runner
    call), then the plan is executed via :func:`execute_plan` (fail-fast on a
    non-zero exit).

    Args:
        runner: the mockable :class:`BtrfsRunner` to execute steps with.
        plan: the immutable :class:`Plan` to run or preview.
        confirmed: whether the caller passed the explicit confirm flag.
        dry_run: whether to preview instead of execute.

    Returns:
        An :class:`Execution` describing what was (or would be) done.

    Raises:
        RollbackError: if a destructive plan is executed without confirmation.
        BtrfsCommandError: if an executed step exits non-zero (via
            :func:`execute_plan`), never in dry-run mode.
    """
    if dry_run:
        return Execution(dry_run=True, results=())
    require_confirmation(plan.kind, confirmed=confirmed, dry_run=False)
    results: Sequence[RunResult] = execute_plan(runner, plan)
    return Execution(dry_run=False, results=tuple(results))
