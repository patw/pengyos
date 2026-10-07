"""``apply`` — drive a plan through a transport, host by host, into a journal.

This is the apply subtask. It owns *how a plan is rolled out*: read the
:class:`~pengyos_fleet_rollout.model.RolloutPlan` the ``plan`` subtask produced,
and drive it through an injectable :class:`~pengyos_fleet_rollout.transport.Transport`
one batch at a time, in order.

The rollout is batch-by-batch: within a batch each host is applied in order; the
batch's health gate (:class:`~pengyos_fleet_rollout.model.HealthGate`) is checked
after every host. A host whose apply step reports failure (``ok=False``) fails
the gate, halts the rollout, and is recorded as :data:`STATE_FAILED` in the
journal. Hosts that never get a step — the rest of the failed batch and every
later batch — stay untouched (no journal entry).

The journal is the durable record. ``apply`` appends an entry for each host it
touches *before moving on*, so a journal on disk reflects exactly the hosts
applied so far. Re-running ``apply`` against that journal resumes: hosts
already :data:`STATE_APPLIED` are skipped, everything else is retried in order,
and the rollout halts again on the next failing gate.

Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import RolloutError
from .model import (
    JournalEntry,
    ModelError,
    RolloutJournal,
    RolloutPlan,
    STATE_APPLIED,
    STATE_FAILED,
    load_inventory,
)
from .transport import (
    ACTION_APPLY,
    TransportStep,
    make_transport,
)

#: The transport action ``apply`` runs per host.
_APPLY_ACTION = ACTION_APPLY


class ApplyError(RolloutError):
    """The plan could not be applied (a problem with the plan or journal)."""


@dataclass
class ApplyResult:
    """The outcome of an :func:`apply` run.

    Attributes:
        journal: The journal after this run — the loaded journal plus every
            entry this run appended (in write order).
        applied: The hosts this run applied successfully, in the order they were
            applied.
        failed: The host that failed the gate (the run halted on it), or
            ``None`` when the rollout ran to completion.
        halted: Whether the rollout halted on a failing gate (as opposed to
            running to completion).
        untouched: The hosts in the plan that never received a step — the rest
            of the failing batch (if halted) and every later batch. Empty when
            the rollout ran to completion.
    """

    journal: RolloutJournal
    applied: tuple[str, ...] = field(default_factory=tuple)
    failed: str | None = None
    halted: bool = False
    untouched: tuple[str, ...] = field(default_factory=tuple)
    json_path: str | None = None
    #: The rendered steps (dry-run) or the steps that were actually run (commit),
    #: in order. The dry-run path renders these without invoking the transport.
    steps: tuple[TransportStep, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        """Return the result as a plain dict (for a future ``--json`` path)."""

        return {
            "applied": list(self.applied),
            "failed": self.failed,
            "halted": self.halted,
            "untouched": list(self.untouched),
        }


def _prior_for(
    plan: RolloutPlan,
    journal: RolloutJournal,
    host: str,
) -> str:
    """The artifact ``host`` currently has, for the apply step.

    * An :data:`STATE_APPLIED` host has its latest ``new`` — the prior for the
      next roll-forward.
    * A :data:`STATE_FAILED` host still has its ``prior`` (the roll-forward
      failed, so nothing changed); a :data:`STATE_ROLLED_BACK` host has its
      ``prior`` too (rollback restored it).
    * Otherwise the plan's rollback record stands in: its ``prior`` (the
      ``UNKNOWN`` placeholder on a fresh plan, or a real prior on a re-apply).
    """

    latest = journal.latest_for(host)
    if latest is not None:
        if latest.state == STATE_APPLIED:
            return latest.new
        # A failed or rolled-back host still has its prior artifact.
        return latest.prior
    entry = plan.rollback_by_host().get(host)
    return entry.prior if entry is not None else ""


def _step_for(
    plan: RolloutPlan,
    hosts_by_name: dict[str, str],
    journal: RolloutJournal,
    host: str,
) -> TransportStep:
    """The apply step for ``host``: prior (current artifact), new, and address."""

    prior = _prior_for(plan, journal, host)
    return TransportStep(
        action=_APPLY_ACTION,
        host=host,
        prior=prior,
        new=plan.image,
        address=hosts_by_name.get(host),
    )


def _hosts_by_name(plan: RolloutPlan) -> dict[str, str]:
    """Map host name -> address from the plan's source inventory (cached)."""

    if not plan.inventory:
        return {}
    try:
        hosts = load_inventory(plan.inventory)
    except RolloutError:
        return {}
    return {host.name: host.address for host in hosts}


def _untouched(plan: RolloutPlan, journal: RolloutJournal) -> tuple[str, ...]:
    """Hosts in the plan with no journal entry (never touched by an apply)."""

    touched = set(journal.hosts())
    return tuple(h for h in plan.hosts() if h not in touched)


def apply(
    plan_path: str,
    *,
    transport: str = "noop",
    journal_path: str | None = None,
    commit: bool = False,
) -> ApplyResult:
    """Apply ``plan_path`` through the transport, into ``journal_path``.

    Reads the plan, then walks it batch-by-batch, host-by-host, in order. For
    each host it builds an :class:`~pengyos_fleet_rollout.transport.TransportStep`
    and runs the transport:

    * On success the host is recorded :data:`STATE_APPLIED` (its real prior is
      the artifact the transport reports back, falling back to the plan's
      record) and the rollout continues to the next host.
    * On failure the host is recorded :data:`STATE_FAILED`, the rollout halts,
      and the remaining hosts of the batch plus every later batch are untouched.

    ``commit=False`` (the default, ``--dry-run``) renders the argv it would run
    via the side-effect-free transport and performs no transport call: no entry
    is written and the result reports the would-be steps instead.

    Args:
        plan_path: Path to the plan JSON produced by ``plan``.
        transport: The transport name to drive per host (``noop`` /
            ``subprocess``); defaults to the side-effect-free ``noop``.
        journal_path: Where to load/append the journal. When ``None`` the journal
            lives in memory (nothing is written to disk).
        commit: Whether to actually run the transport (``--commit``) or just
            dry-run (the default).

    Returns:
        An :class:`ApplyResult` describing what was applied, whether the rollout
        halted, on which host, and which hosts were left untouched.

    Raises:
        :class:`ApplyError`: the plan is missing/malformed, the transport is
            unknown, or a transport call cannot run.
    """

    try:
        plan = RolloutPlan.load(plan_path)
    except ModelError as exc:
        raise ApplyError(str(exc)) from exc

    try:
        t = make_transport(transport)
    except RolloutError as exc:
        raise ApplyError(str(exc)) from exc

    hosts_by_name = _hosts_by_name(plan)

    journal = RolloutJournal()
    if journal_path is not None:
        try:
            journal = RolloutJournal.load(journal_path)
        except (FileNotFoundError, ModelError):
            journal = RolloutJournal()

    result = ApplyResult(journal=journal)

    # The dry-run path: render every step's argv through the (side-effect-free)
    # transport and stop — no entry is written, nothing is invoked. The rendered
    # steps are captured on the result so the argv is inspectable.
    if not commit:
        for batch in plan.batches:
            for host in batch.hosts:
                step = _step_for(plan, hosts_by_name, journal, host)
                result = ApplyResult(
                    journal=journal,
                    applied=result.applied,
                    failed=result.failed,
                    halted=result.halted,
                    untouched=result.untouched,
                    json_path=result.json_path,
                    steps=result.steps + (step,),
                )
        # Nothing was touched on a dry run, so every plan host is untouched.
        result = ApplyResult(
            journal=result.journal,
            applied=result.applied,
            failed=result.failed,
            halted=result.halted,
            untouched=_untouched(plan, journal),
            json_path=result.json_path,
            steps=result.steps,
        )
        return result

    # The committed path: walk the rollout, appending a journal entry for each
    # host as it is touched (so the on-disk journal always reflects progress).
    result = _drive(plan, t, journal, hosts_by_name)

    # Persist the journal on commit (the dry-run path above returns before here).
    if journal_path is not None:
        result.json_path = journal_path
        result.journal.dump(journal_path)

    return result


def _drive(plan, t, journal, hosts_by_name):
    """Drive ``plan`` through ``t``, one batch then host, in order.

    Resumes from ``journal`` (already-applied hosts are skipped), records a
    journal entry for each host as it is touched, and halts on the first failing
    gate (returning an :class:`ApplyResult` with ``halted`` set).
    """

    result = ApplyResult(journal=journal)
    for batch in plan.batches:
        for host in batch.hosts:
            # Resume: a host already applied is left alone.
            if (
                journal.latest_for(host) is not None
                and journal.latest_for(host).state == STATE_APPLIED
            ):
                continue

            step = _step_for(plan, hosts_by_name, journal, host)

            try:
                outcome = t.run(step)
            except RolloutError as exc:
                raise ApplyError(str(exc)) from exc

            if not outcome.ok:
                # The gate failed: record the failure and halt. The rest of this
                # batch and every later batch stay untouched.
                failed_entry = JournalEntry(
                    host=host,
                    prior=outcome.prior or step.prior,
                    new=plan.image,
                    state=STATE_FAILED,
                )
                journal = journal.append(failed_entry)
                return ApplyResult(
                    journal=journal,
                    applied=result.applied,
                    failed=host,
                    halted=True,
                    untouched=_untouched(plan, journal),
                )

            applied_entry = JournalEntry(
                host=host,
                prior=outcome.prior or step.prior,
                new=plan.image,
                state=STATE_APPLIED,
            )
            journal = journal.append(applied_entry)
            result = ApplyResult(
                journal=journal,
                applied=result.applied + (host,),
                failed=None,
                halted=False,
                untouched=_untouched(plan, journal),
            )

    # Ran to completion.
    return ApplyResult(
        journal=journal,
        applied=result.applied,
        failed=None,
        halted=False,
        untouched=_untouched(plan, journal),
    )
