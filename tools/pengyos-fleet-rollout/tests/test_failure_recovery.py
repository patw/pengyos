"""Grounding the failure/recovery path — apply halts, rollback recovers.

This is the ground-up integration test for the failure/recovery path. It drives
``apply`` and ``rollback`` end to end against the same journal, with a fake
transport that fails on the 2nd host: it proves

* ``apply`` halts on the first failing gate (the 2nd host), recording the
  failed host and leaving the rest untouched, and
* ``rollback`` then returns *every touched host* to its prior artifact — the
  applied host is rolled back to its prior, and the failed host is restored to
  the prior it never lost.

The transport is faked (installed by patching ``make_transport`` in each
module's namespace); no real machine, no network, no root, no hardware. The
inventory is a committed fixture and the plan is generated on the fly. Nothing
is written to the committed fixtures.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import pengyos_fleet_rollout.apply as apply_module
import pengyos_fleet_rollout.rollback as rollback_module
from pengyos_fleet_rollout import model as m
from pengyos_fleet_rollout.apply import apply
from pengyos_fleet_rollout.cli import main
from pengyos_fleet_rollout.plan import plan
from pengyos_fleet_rollout.rollback import rollback
from pengyos_fleet_rollout.transport import TransportResult, build_argv

PRIOR = "img-2026.09.18"
NEW = "img-2026.09.25"

# The committed inventory's rollout order (batch size 1): canary first, then
# priority desc, then name asc.
ORDER = ("canary-1", "core-1", "core-2", "edge-1", "edge-2")


# ---------------------------------------------------------------------------
# A fake transport — reports a real prior per host, fails on the 2nd host.
# ---------------------------------------------------------------------------


class FakeTransport:
    """A small in-memory transport: reports a real prior, can fail on a host."""

    def __init__(self, prior_by_host=None, fail_on=None):
        self.prior_by_host = dict(prior_by_host or {})
        self.fail_on = set(fail_on or ())
        self.ran: list = []  # TransportStep objects, in call order

    def run(self, step):
        self.ran.append(step)
        # A transport observes the artifact the host currently has — the step's
        # prior, which rollback/apply has already resolved to the current
        # artifact (an applied host's new, a failed host's prior). Report it even
        # on failure so the failed entry records the real prior.
        prior = self.prior_by_host.get(step.host, step.prior)
        if step.host in self.fail_on:
            return TransportResult(
                ok=False, argv=build_argv(step), returncode=1, output="boom", prior=prior
            )
        return TransportResult(
            ok=True, argv=build_argv(step), returncode=0, prior=prior
        )


@pytest.fixture
def patched_apply_transport():
    """Patch ``make_transport`` in ``apply``'s namespace for the whole test."""

    with patch.object(apply_module, "make_transport") as patched:
        yield patched


@pytest.fixture
def patched_rollback_transport():
    """Patch ``make_transport`` in ``rollback``'s namespace for the whole test."""

    with patch.object(rollback_module, "make_transport") as patched:
        yield patched


@pytest.fixture
def plan_path(fixtures_dir, tmp_path):
    """A batch-size-1 plan for the committed inventory, written to a temp file."""

    produced = plan("img-2026.09.25", str(fixtures_dir / "inventory.toml"), batch_size=1)
    path = tmp_path / "plan.json"
    produced.dump(path)
    return path


def _prior_by_host() -> dict[str, str]:
    """A real prior artifact for every host (so the journal is not UNKNOWN)."""

    return {host: PRIOR for host in ORDER}


# ---------------------------------------------------------------------------
# apply halts on the 2nd host.
# ---------------------------------------------------------------------------


def test_apply_halts_on_the_second_host(
    plan_path, tmp_path, patched_apply_transport
):
    journal_path = tmp_path / "journal.json"
    fake = FakeTransport(prior_by_host=_prior_by_host(), fail_on={"core-1"})
    patched_apply_transport.return_value = fake

    result = apply(
        str(plan_path),
        transport="noop",
        journal_path=str(journal_path),
        commit=True,
    )

    # The canary (1st host) applied; the 2nd host (core-1) failed the gate.
    assert [step.host for step in fake.ran] == ["canary-1", "core-1"]
    assert result.halted is True
    assert result.failed == "core-1"
    assert result.applied == ("canary-1",)
    # Only the two touched hosts have a journal entry.
    assert set(result.journal.hosts()) == {"canary-1", "core-1"}
    # The rest of the fleet was never touched.
    assert result.untouched == ("core-2", "edge-1", "edge-2")


def test_apply_records_the_failed_host_with_its_prior(
    plan_path, tmp_path, patched_apply_transport
):
    journal_path = tmp_path / "journal.json"
    fake = FakeTransport(prior_by_host=_prior_by_host(), fail_on={"core-1"})
    patched_apply_transport.return_value = fake

    apply(str(plan_path), transport="noop", journal_path=str(journal_path), commit=True)

    failed = result_journal(journal_path).latest_for("core-1")
    assert failed.state == m.STATE_FAILED
    assert failed.prior == PRIOR  # the prior it had before the failed roll-forward
    assert failed.new == NEW


# ---------------------------------------------------------------------------
# rollback returns every touched host to its prior artifact.
# ---------------------------------------------------------------------------


def test_rollback_restores_every_touched_host_to_its_prior(
    plan_path, tmp_path, patched_apply_transport, patched_rollback_transport
):
    journal_path = tmp_path / "journal.json"

    # Apply halts on the 2nd host (core-1): canary-1 applied, core-1 failed.
    apply_fake = FakeTransport(prior_by_host=_prior_by_host(), fail_on={"core-1"})
    patched_apply_transport.return_value = apply_fake
    apply(str(plan_path), transport="noop", journal_path=str(journal_path), commit=True)

    # Roll back the touched hosts.
    rollback_fake = FakeTransport()
    patched_rollback_transport.return_value = rollback_fake
    result = rollback(str(journal_path), transport="noop", commit=True)

    # Every touched host is rolled back, in reverse rollout order.
    assert result.rolled_back == ("core-1", "canary-1")
    assert [step.host for step in rollback_fake.ran] == ["core-1", "canary-1"]
    # The untouched hosts never received a rollback step.
    assert not any(step.host in {"core-2", "edge-1", "edge-2"} for step in rollback_fake.ran)

    # Every touched host is now rolled back and restored to its prior artifact.
    for host in ("canary-1", "core-1"):
        entry = result.journal.latest_for(host)
        assert entry.state == m.STATE_ROLLED_BACK
        # The entry's new is the restored prior artifact (state equality).
        assert entry.new == PRIOR


def test_rollback_restores_the_applied_host_from_its_new(
    plan_path, tmp_path, patched_apply_transport, patched_rollback_transport
):
    journal_path = tmp_path / "journal.json"

    apply_fake = FakeTransport(prior_by_host=_prior_by_host(), fail_on={"core-1"})
    patched_apply_transport.return_value = apply_fake
    apply(str(plan_path), transport="noop", journal_path=str(journal_path), commit=True)

    rollback_fake = FakeTransport()
    patched_rollback_transport.return_value = rollback_fake
    result = rollback(str(journal_path), transport="noop", commit=True)

    # The applied canary-1 is rolled back from its new (the applied image) to
    # its prior — the entry records the transition prior=new, new=prior.
    canary = result.journal.latest_for("canary-1")
    assert canary.state == m.STATE_ROLLED_BACK
    assert canary.prior == NEW  # what it had before rollback (the applied image)
    assert canary.new == PRIOR  # what it is restored to


def test_rollback_restores_the_failed_host_to_what_it_never_lost(
    plan_path, tmp_path, patched_apply_transport, patched_rollback_transport
):
    journal_path = tmp_path / "journal.json"

    apply_fake = FakeTransport(prior_by_host=_prior_by_host(), fail_on={"core-1"})
    patched_apply_transport.return_value = apply_fake
    apply(str(plan_path), transport="noop", journal_path=str(journal_path), commit=True)

    rollback_fake = FakeTransport()
    patched_rollback_transport.return_value = rollback_fake
    result = rollback(str(journal_path), transport="noop", commit=True)

    # The failed core-1 never got the applied image, so its rollback restores it
    # from its prior to its prior — a no-op restore, recorded as prior==new.
    core1 = result.journal.latest_for("core-1")
    assert core1.state == m.STATE_ROLLED_BACK
    assert core1.prior == PRIOR
    assert core1.new == PRIOR


# ---------------------------------------------------------------------------
# The full journal after recovery: every touched host is back.
# ---------------------------------------------------------------------------


def test_after_rollback_every_touched_host_is_rolled_back(
    plan_path, tmp_path, patched_apply_transport, patched_rollback_transport
):
    journal_path = tmp_path / "journal.json"

    apply_fake = FakeTransport(prior_by_host=_prior_by_host(), fail_on={"core-1"})
    patched_apply_transport.return_value = apply_fake
    apply(str(plan_path), transport="noop", journal_path=str(journal_path), commit=True)

    rollback_fake = FakeTransport()
    patched_rollback_transport.return_value = rollback_fake
    rollback(str(journal_path), transport="noop", commit=True)

    journal = result_journal(journal_path)
    # Only the two touched hosts exist in the journal.
    assert set(journal.hosts()) == {"canary-1", "core-1"}
    # Both are rolled back (fully reversed).
    assert set(journal.rolled_back_hosts()) == {"canary-1", "core-1"}
    # Neither is applied anymore.
    assert journal.applied_hosts() == ()


def test_rollback_is_idempotent_after_full_recovery(
    plan_path, tmp_path, patched_apply_transport, patched_rollback_transport
):
    journal_path = tmp_path / "journal.json"

    apply_fake = FakeTransport(prior_by_host=_prior_by_host(), fail_on={"core-1"})
    patched_apply_transport.return_value = apply_fake
    apply(str(plan_path), transport="noop", journal_path=str(journal_path), commit=True)

    rollback_fake = FakeTransport()
    patched_rollback_transport.return_value = rollback_fake
    first = rollback(str(journal_path), transport="noop", commit=True)
    assert first.rolled_back == ("core-1", "canary-1")

    # A second rollback against the fully-reversed journal touches nothing.
    second = rollback(str(journal_path), transport="noop", commit=True)
    assert second.rolled_back == ()
    assert second.noop == ("canary-1", "core-1")
    assert [step.host for step in rollback_fake.ran] == ["core-1", "canary-1"]


# ---------------------------------------------------------------------------
# CLI: the failure path then the recovery path, end to end.
# ---------------------------------------------------------------------------


def test_cli_apply_then_rollback_recovers_every_touched_host(
    plan_path, tmp_path, patched_apply_transport, patched_rollback_transport, capsys
):
    journal_path = tmp_path / "journal.json"

    # apply halts on the 2nd host.
    patched_apply_transport.return_value = FakeTransport(
        prior_by_host=_prior_by_host(), fail_on={"core-1"}
    )
    rc = main(
        ["apply", "--plan", str(plan_path), "--journal", str(journal_path), "--commit"]
    )
    assert rc == 0
    assert "halted" in capsys.readouterr().out

    # rollback recovers every touched host.
    patched_rollback_transport.return_value = FakeTransport()
    rc = main(
        ["rollback", "--journal", str(journal_path), "--commit"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "complete" in out
    assert "2 restored" in out


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def result_journal(journal_path) -> m.RolloutJournal:
    """Load the journal written at ``journal_path``."""

    return m.RolloutJournal.load(str(journal_path))
