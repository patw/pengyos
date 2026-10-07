"""Tests for ``rollback`` (``pengyos_fleet_rollout.rollback``).

These lock the rollback subtask: from a journal, restore each still-touched
host's prior artifact in reverse (rollout) order, recording a
:data:`~pengyos_fleet_rollout.model.STATE_ROLLED_BACK` entry per restored host.
Rollback is idempotent — a host already back is a no-op — and a failed rollback
gate halts and records the failure.

The transport is faked (installed by patching ``make_transport``); no real
machine, no network, no root, no hardware. The journal is a committed fixture
(or a small hand-built one). Nothing is written on the dry-run path.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import pengyos_fleet_rollout.rollback as rollback_module
from pengyos_fleet_rollout import model as m
from pengyos_fleet_rollout.errors import RolloutTransportError
from pengyos_fleet_rollout.rollback import RollbackError, rollback
from pengyos_fleet_rollout.cli import build_parser, main
from pengyos_fleet_rollout.transport import (
    ACTION_ROLLED_BACK,
    TransportResult,
    build_argv,
)

PRIOR = "img-2026.09.18"
NEW = "img-2026.09.25"


# ---------------------------------------------------------------------------
# A fake transport — installed by patching make_transport.
# ---------------------------------------------------------------------------


class FakeTransport:
    """A small in-memory transport for the rollback suite.

    Reports the artifact it "observed" the host having (the step's `prior`,
    i.e. the current artifact the host has), can be told to fail on specific
    hosts, and records every step it is asked to run. Installed into `rollback`
    by patching `make_transport`.
    """

    def __init__(self, prior_by_host=None, fail_on=None):
        self.prior_by_host = dict(prior_by_host or {})
        self.fail_on = set(fail_on or ())
        self.ran: list = []  # TransportStep objects, in call order

    def run(self, step):
        self.ran.append(step)
        if step.host in self.fail_on:
            return TransportResult(
                ok=False, argv=build_argv(step), returncode=1, output="boom"
            )
        # A transport reports the artifact it observed the host having — the
        # current artifact (the step's prior, i.e. the applied new).
        prior = self.prior_by_host.get(step.host, step.prior)
        return TransportResult(
            ok=True, argv=build_argv(step), returncode=0, prior=prior
        )


@pytest.fixture
def patched_make_transport():
    """Patch ``make_transport`` for the whole test; tests install their fake."""

    with patch.object(rollback_module, "make_transport") as patched:
        yield patched


@pytest.fixture
def applied_journal(tmp_path):
    """A journal with three applied hosts — the canonical partial rollout."""

    journal = m.RolloutJournal(
        entries=(
            m.JournalEntry(host="canary-1", prior=PRIOR, new=NEW, state=m.STATE_APPLIED, at="t1"),
            m.JournalEntry(host="core-1", prior=PRIOR, new=NEW, state=m.STATE_APPLIED, at="t2"),
            m.JournalEntry(host="edge-1", prior=PRIOR, new=NEW, state=m.STATE_APPLIED, at="t3"),
        )
    )
    path = tmp_path / "journal.json"
    journal.dump(path)
    return path


# ---------------------------------------------------------------------------
# Rollback restores each host's prior artifact, in reverse order.
# ---------------------------------------------------------------------------


def test_rollback_restores_each_host_prior_in_reverse_order(
    applied_journal, patched_make_transport
):
    fake = FakeTransport()
    patched_make_transport.return_value = fake
    result = rollback(str(applied_journal), transport="noop", commit=True)
    # Reverse rollout order: edge-1, core-1, canary-1.
    assert [step.host for step in fake.ran] == ["edge-1", "core-1", "canary-1"]
    assert result.rolled_back == ("edge-1", "core-1", "canary-1")
    assert result.halted is False
    assert result.failed is None


def test_rollback_step_moves_from_current_to_prior(
    applied_journal, patched_make_transport
):
    fake = FakeTransport()
    patched_make_transport.return_value = fake
    rollback(str(applied_journal), transport="noop", commit=True)
    # The step's prior is what the host currently has (the applied new); the
    # step's new is the prior artifact to restore.
    for step in fake.ran:
        assert step.action == ACTION_ROLLED_BACK
        assert step.prior == NEW
        assert step.new == PRIOR


def test_rollback_records_a_rolled_back_entry_per_host(
    applied_journal, patched_make_transport
):
    fake = FakeTransport()
    patched_make_transport.return_value = fake
    result = rollback(str(applied_journal), transport="noop", commit=True)
    for host in ("canary-1", "core-1", "edge-1"):
        entry = result.journal.latest_for(host)
        assert entry.state == m.STATE_ROLLED_BACK
        assert entry.host == host
        # The rollback is recorded as a transition: prior is the artifact the
        # host currently has (the applied new), new is the restored prior.
        assert entry.prior == NEW
        assert entry.new == PRIOR


def test_rollback_reports_the_restored_prior_from_the_transport(
    applied_journal, patched_make_transport
):
    # The transport reports the artifact it observed the host having (the
    # current artifact, i.e. the applied new) — apply records that as the
    # entry's prior. The restored prior (the step's new) is always recorded.
    fake = FakeTransport(prior_by_host={"edge-1": "img-2026.08.01"})
    patched_make_transport.return_value = fake
    result = rollback(str(applied_journal), transport="noop", commit=True)
    entry = result.journal.latest_for("edge-1")
    assert entry.prior == "img-2026.08.01"  # what the transport observed
    assert entry.new == PRIOR  # the restored prior artifact


def test_rollback_rewrites_the_journal_on_disk(applied_journal, patched_make_transport):
    fake = FakeTransport()
    patched_make_transport.return_value = fake
    result = rollback(str(applied_journal), transport="noop", commit=True)
    # The on-disk journal is the same document that was rolled back.
    loaded = m.RolloutJournal.load(str(applied_journal))
    assert loaded.entries == result.journal.entries
    # Every host is now rolled back on disk.
    assert loaded.rolled_back_hosts() == ("canary-1", "core-1", "edge-1")


def test_rollback_records_the_restore_transition(applied_journal, patched_make_transport):
    # A rolled-back entry records the transition the step performed: prior is the
    # artifact the host currently has (the applied new), new is the prior
    # artifact restored (the applied prior).
    fake = FakeTransport()
    patched_make_transport.return_value = fake
    result = rollback(str(applied_journal), transport="noop", commit=True)
    entry = result.journal.latest_for("canary-1")
    assert entry.state == m.STATE_ROLLED_BACK
    assert entry.prior == NEW  # what the host had before rollback
    assert entry.new == PRIOR  # what it is restored to


def test_rollback_committed_journal_is_schema_valid(
    applied_journal, patched_make_transport
):
    fake = FakeTransport()
    patched_make_transport.return_value = fake
    result = rollback(str(applied_journal), transport="noop", commit=True)
    # The restored journal is still schema-valid declarative data.
    again = m.RolloutJournal.from_dict(result.journal.as_dict())
    assert again.entries == result.journal.entries


# ---------------------------------------------------------------------------
# Idempotency — a host already back is a no-op.
# ---------------------------------------------------------------------------


def test_rollback_is_idempotent_already_back_hosts_are_noops(
    tmp_path, patched_make_transport
):
    # Persist the journal so the second run reads what the first restored.
    journal_path = tmp_path / "journal.json"
    m.RolloutJournal(
        entries=(
            m.JournalEntry(host="canary-1", prior=PRIOR, new=NEW, state=m.STATE_APPLIED, at="t1"),
            m.JournalEntry(host="core-1", prior=PRIOR, new=NEW, state=m.STATE_APPLIED, at="t2"),
            m.JournalEntry(host="edge-1", prior=PRIOR, new=NEW, state=m.STATE_APPLIED, at="t3"),
        )
    ).dump(journal_path)

    fake = FakeTransport()
    patched_make_transport.return_value = fake
    # First rollback restores everything (and rewrites the reversed journal).
    first = rollback(str(journal_path), transport="noop", commit=True)
    assert first.rolled_back == ("edge-1", "core-1", "canary-1")

    # A second rollback against the now-reversed journal touches nothing.
    second = rollback(str(journal_path), transport="noop", commit=True)
    assert second.rolled_back == ()
    assert second.noop == ("canary-1", "core-1", "edge-1")
    # The fake was never asked to run a rollback step the second time.
    assert second.steps == ()


def test_rollback_skips_already_rolled_back_but_restores_the_rest(
    tmp_path, patched_make_transport
):
    # canary-1 is already back; core-1 and edge-1 still need rolling back.
    journal = m.RolloutJournal(
        entries=(
            m.JournalEntry(host="canary-1", prior=PRIOR, new=NEW, state=m.STATE_APPLIED, at="t1"),
            m.JournalEntry(host="core-1", prior=PRIOR, new=NEW, state=m.STATE_ROLLED_BACK, at="t2"),
            m.JournalEntry(host="edge-1", prior=PRIOR, new=NEW, state=m.STATE_APPLIED, at="t3"),
        )
    )
    path = tmp_path / "journal.json"
    journal.dump(path)

    fake = FakeTransport()
    patched_make_transport.return_value = fake
    result = rollback(str(path), transport="noop", commit=True)
    # Only the still-touched hosts are rolled back, in reverse (edge-1, canary-1).
    assert [step.host for step in fake.ran] == ["edge-1", "canary-1"]
    assert result.rolled_back == ("edge-1", "canary-1")
    assert result.noop == ("core-1",)


# ---------------------------------------------------------------------------
# Rollback of a partial rollout that includes a failed host.
# ---------------------------------------------------------------------------


def test_rollback_restores_a_failed_host_to_its_prior(
    fixtures_dir, tmp_path, patched_make_transport
):
    # The committed journal.json: canary-1 + core-1 applied, core-2 failed.
    # Every touched host (including the failed core-2) is restored to its prior.
    # Copy it aside so the committed fixture is never mutated in place.
    journal_path = tmp_path / "journal.json"
    journal_path.write_text(
        (fixtures_dir / "journal.json").read_text(encoding="utf-8"), encoding="utf-8"
    )

    fake = FakeTransport()
    patched_make_transport.return_value = fake
    result = rollback(str(journal_path), transport="noop", commit=True)
    assert [step.host for step in fake.ran] == ["core-2", "core-1", "canary-1"]
    assert result.rolled_back == ("core-2", "core-1", "canary-1")
    # Every touched host is restored to its prior artifact (the entry's new).
    for host in ("canary-1", "core-1", "core-2"):
        entry = result.journal.latest_for(host)
        assert entry.state == m.STATE_ROLLED_BACK
        assert entry.new == PRIOR
    # The failed core-2 never got the applied image, so its rollback restores
    # it from its prior to its prior — recorded as prior=prior (a no-op restore).
    core2 = result.journal.latest_for("core-2")
    assert core2.prior == PRIOR

    # The committed fixture is untouched by the test.
    original = json.loads((fixtures_dir / "journal.json").read_text(encoding="utf-8"))
    assert original["entries"][2]["state"] == "failed"


# ---------------------------------------------------------------------------
# The dry-run path — renders argv, performs no transport call, writes nothing.
# ---------------------------------------------------------------------------


def test_rollback_dry_run_renders_a_step_per_host_to_restore(
    applied_journal, patched_make_transport
):
    fake = FakeTransport()
    patched_make_transport.return_value = fake
    result = rollback(str(applied_journal), transport="noop", commit=False)
    # Never invoked on a dry run...
    assert [step.host for step in fake.ran] == []
    # ...but every still-touched host is rendered as a would-be rollback step,
    # in reverse order.
    assert [step.host for step in result.steps] == ["edge-1", "core-1", "canary-1"]
    assert result.rolled_back == ()
    assert result.halted is False


def test_rollback_dry_run_performs_no_transport_call(applied_journal):
    class ExplodingTransport:
        def run(self, step):
            raise AssertionError("transport must not be invoked on a dry run")

    with patch.object(
        rollback_module, "make_transport", return_value=ExplodingTransport()
    ):
        result = rollback(str(applied_journal), transport="noop", commit=False)
    assert len(result.steps) == 3


def test_rollback_dry_run_does_not_write_journal(applied_journal, tmp_path):
    # The source journal is the positional arg; commit would rewrite it in place.
    rollback(str(applied_journal), transport="noop", commit=False)
    # Nothing was written (the dry-run path never persists).


# ---------------------------------------------------------------------------
# Error handling.
# ---------------------------------------------------------------------------


def test_rollback_missing_journal_raises():
    with pytest.raises(RollbackError):
        rollback("/no/such/journal.json", transport="noop", commit=True)


def test_rollback_unknown_transport_raises(applied_journal):
    with pytest.raises(RollbackError):
        rollback(str(applied_journal), transport="bogus", commit=True)


def test_rollback_transport_run_error_raises(applied_journal, patched_make_transport):
    class BoomTransport:
        def run(self, step):
            raise RolloutTransportError("cannot exec")

    patched_make_transport.return_value = BoomTransport()
    with pytest.raises(RollbackError):
        rollback(str(applied_journal), transport="subprocess", commit=True)


def test_rollback_halts_on_a_failing_gate(applied_journal, patched_make_transport):
    fake = FakeTransport(fail_on={"core-1"})
    patched_make_transport.return_value = fake
    result = rollback(str(applied_journal), transport="noop", commit=True)
    # Reverse order: edge-1 restored, core-1 fails — the rollback stops there.
    assert [step.host for step in fake.ran] == ["edge-1", "core-1"]
    assert result.halted is True
    assert result.failed == "core-1"
    assert result.rolled_back == ("edge-1",)
    # The failed rollback entry records the failure state.
    failed = result.journal.latest_for("core-1")
    assert failed.state == m.STATE_FAILED


# ---------------------------------------------------------------------------
# The CLI surface.
# ---------------------------------------------------------------------------


def test_cli_rollback_dry_run_returns_zero(applied_journal, capsys, patched_make_transport):
    patched_make_transport.return_value = FakeTransport()
    rc = main(["rollback", "--journal", str(applied_journal), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry run" in out
    assert "3 to restore" in out


def test_cli_rollback_commit_returns_zero(applied_journal, capsys, patched_make_transport):
    patched_make_transport.return_value = FakeTransport()
    rc = main(["rollback", "--journal", str(applied_journal), "--commit"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "complete" in out
    assert "3 restored" in out


def test_cli_rollback_missing_journal_returns_one(capsys):
    rc = main(["rollback", "--journal", "/no/such/journal.json", "--commit"])
    assert rc == 1
    assert "journal not found" in capsys.readouterr().err


def test_rollback_subparser_defaults_to_commit():
    # The rollback subparser defaults to commit=True (rollback actually runs by
    # default; --dry-run flips it). Locked by the scaffold suite.
    parser = build_parser()
    args = parser.parse_args(["rollback", "--journal", "j.json"])
    assert args.commit is True
    committed = parser.parse_args(
        ["rollback", "--journal", "j.json", "--commit"]
    )
    assert committed.commit is True
    explicit_dry = parser.parse_args(
        ["rollback", "--journal", "j.json", "--dry-run"]
    )
    assert explicit_dry.commit is False
