"""``rollback`` — restore each rolled host's prior artifact, in reverse.

This is the rollback subtask. It owns *how a rolled-out fleet is reversed*: read
the :class:`~pengyos_fleet_rollout.model.RolloutJournal` :func:`pengyos_fleet_rollout.apply.apply`
wrote, and drive a :class:`~pengyos_fleet_rollout.transport.Transport` per host
that still needs to be restored — each host's prior artifact, in reverse
(rollout) order.

The journal is the record of what was applied. A host whose latest entry is
:data:`~pengyos_fleet_rollout.model.STATE_ROLLED_BACK` is already back — a
no-op. Every other touched host (``applied`` **or** ``failed``) is rolled back:
the transport is asked to move it from the artifact it currently has back to
the artifact it had before (the applied ``prior``), and a
:data:`STATE_ROLLED_BACK` entry is appended recording the restore transition
(``prior`` = the current artifact, ``new`` = the restored prior). A failed
rollback gate records the failure and halts.

Rollback walks hosts in reverse so the *last* host rolled out is the *first*
restored — the exact inverse of the apply walk. It is idempotent: a host already
back is left alone, so re-running rollback against a fully-reversed journal is a
clean no-op.

Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import RolloutError
from .model import (
    JournalEntry,
    ModelError,
    RolloutJournal,
    STATE_APPLIED,
    STATE_FAILED,
    STATE_ROLLED_BACK,
)
from .transport import ACTION_ROLLED_BACK, TransportStep, make_transport

#: The transport action ``rollback`` runs per host: restore the prior artifact.
_ROLLBACK_ACTION = ACTION_ROLLED_BACK


class RollbackError(RolloutError):
    """The journal could not be rolled back (a problem with the journal)."""


@dataclass
class RollbackResult:
    """The outcome of a :func:`rollback` run.

    Attributes:
        journal: The journal after this run — the loaded journal plus every
            entry this run appended (in write order).
        rolled_back: The hosts this run rolled back, in the order they were
            restored (reverse rollout order).
        noop: The hosts already back before this run (already
            :data:`STATE_ROLLED_BACK`) — the idempotent no-ops.
        halted: Whether the rollout-back halted on a failing gate.
        failed: The host that failed the rollback gate (the run halted on it),
            or ``None`` when the rollback ran to completion.
        steps: The rendered steps (dry-run) or the steps that were actually run
            (commit), in order.
        journal_path: Where the journal was persisted, or ``None`` when the
            journal lived in memory.
    """

    journal: RolloutJournal
    rolled_back: tuple[str, ...] = field(default_factory=tuple)
    noop: tuple[str, ...] = field(default_factory=tuple)
    halted: bool = False
    failed: str | None = None
    steps: tuple[TransportStep, ...] = field(default_factory=tuple)
    journal_path: str | None = None

    def as_dict(self) -> dict:
        """Return the result as a plain dict (for a future ``--json`` path)."""

        return {
            "rolled_back": list(self.rolled_back),
            "noop": list(self.noop),
            "halted": self.halted,
            "failed": self.failed,
        }


def _rollback_step_for(journal: RolloutJournal, host: str) -> TransportStep:
    """The rollback step for ``host``: move it from its current to its prior.

    The prior artifact to restore is always the entry's ``prior``. The current
    artifact is what the host physically has: an :data:`STATE_APPLIED` host has
    its ``new`` (the roll-forward succeeded); a :data:`STATE_FAILED` host never
    got ``new`` and still has its ``prior`` (so its rollback is a no-op restore).
    """

    entry = journal.latest_for(host)
    current = entry.new if entry.state == STATE_APPLIED else entry.prior
    return TransportStep(
        action=_ROLLBACK_ACTION,
        host=host,
        prior=current,
        new=entry.prior,
    )


def _needs_rollback(journal: RolloutJournal, host: str) -> bool:
    """Whether ``host`` still needs to be rolled back.

    A host already back (:data:`STATE_ROLLED_BACK`) does not. A host whose latest
    entry is ``applied`` or ``failed`` does — both are touched and not yet
    restored.
    """

    latest = journal.latest_for(host)
    if latest is None:
        return False
    return latest.state != STATE_ROLLED_BACK


def rollback(
    journal_path: str,
    *,
    transport: str = "noop",
    commit: bool = False,
) -> RollbackResult:
    """Roll back ``journal_path``: restore each still-touched host's prior.

    Loads the journal the :func:`pengyos_fleet_rollout.apply.apply` wrote, then
    walks every touched host in **reverse** rollout order. A host whose latest
    entry is :data:`~pengyos_fleet_rollout.model.STATE_ROLLED_BACK` is already
    back — a no-op. Every other touched host is rolled back: its prior artifact
    is restored through the transport and a :data:`STATE_ROLLED_BACK` entry is
    appended.

    ``commit=False`` (the default, ``--dry-run``) renders each host's rollback
    step's argv through the side-effect-free transport and performs no transport
    call: nothing is written. ``commit=True`` (``--commit``) runs the transport,
    records each restored host, and rewrites ``journal_path`` with the updated
    journal.

    Args:
        journal_path: Path to the rollout journal written by ``apply``. A
            missing journal is an error (there is nothing to roll back). It is
            also where the updated journal is rewritten on commit.
        transport: The transport name to drive per host (``noop`` /
            ``subprocess``); defaults to the side-effect-free ``noop``.
        commit: Whether to actually run the transport (``--commit``) or just
            dry-run (the default).

    Returns:
        A :class:`RollbackResult` describing what was rolled back, what was a
        no-op, whether the rollback halted, and on which host.

    Raises:
        :class:`RollbackError`: the journal is missing/malformed, the transport
            is unknown, or a transport call cannot run.
    """

    try:
        journal = RolloutJournal.load(journal_path)
    except (FileNotFoundError, ModelError) as exc:
        raise RollbackError(str(exc)) from exc

    try:
        t = make_transport(transport)
    except RolloutError as exc:
        raise RollbackError(str(exc)) from exc

    # Reverse rollout order: the last host rolled out is the first restored.
    to_rollback = [host for host in reversed(journal.hosts()) if _needs_rollback(journal, host)]
    # The noop hosts: touched but already back — in canonical (journal) order.
    already_back = tuple(
        host for host in journal.hosts() if host not in set(to_rollback)
    )

    result = RollbackResult(journal=journal, noop=already_back)

    if not commit:
        # The dry-run path: render each rollback step's argv through the
        # (side-effect-free) transport and stop — nothing is written.
        for host in to_rollback:
            result = RollbackResult(
                journal=result.journal,
                rolled_back=result.rolled_back,
                noop=result.noop,
                halted=result.halted,
                failed=result.failed,
                steps=result.steps + (_rollback_step_for(journal, host),),
                journal_path=result.journal_path,
            )
        return result

    # The committed path: walk the hosts in reverse, restoring each.
    for host in to_rollback:
        step = _rollback_step_for(journal, host)
        try:
            outcome = t.run(step)
        except RolloutError as exc:
            raise RollbackError(str(exc)) from exc

        if not outcome.ok:
            # The rollback gate failed: record it and halt. The host is left
            # where it was (the observed current artifact) with a failed state.
            failed_entry = JournalEntry(
                host=host,
                prior=outcome.prior or step.prior,
                new=step.new,
                state=STATE_FAILED,
            )
            journal = journal.append(failed_entry)
            result = RollbackResult(
                journal=journal,
                rolled_back=result.rolled_back,
                noop=result.noop,
                halted=True,
                failed=host,
                steps=result.steps + (step,),
                journal_path=journal_path,
            )
            journal.dump(journal_path)
            return result

        # The host is now back at its prior artifact. Record the restored state
        # as a transition: prior is the artifact the host had before rollback
        # (the observed current artifact), new is the restored prior.
        restored_entry = JournalEntry(
            host=host,
            prior=outcome.prior or step.prior,
            new=step.new,
            state=STATE_ROLLED_BACK,
        )
        journal = journal.append(restored_entry)
        result = RollbackResult(
            journal=journal,
            rolled_back=result.rolled_back + (host,),
            noop=result.noop,
            halted=False,
            failed=None,
            steps=result.steps + (step,),
            journal_path=journal_path,
        )

    # Nothing needed rolling back — every touched host was already back.
    journal.dump(journal_path)
    result.journal_path = journal_path
    return result
