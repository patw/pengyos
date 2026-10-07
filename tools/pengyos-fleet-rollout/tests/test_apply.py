"""Tests for ``apply`` (``pengyos_fleet_rollout.apply``).

These lock the apply subtask: drive a plan through an injectable transport,
batch-by-batch, host-by-host, recording a journal entry for each host as it is
touched; halt on the first failing gate; and leave the journal resumable.

The transport is faked (a small in-memory fake, installed by patching
``make_transport``) — no real machine, no network, no root, no hardware. The
inventory/plan are committed fixtures (or generated on the fly). No entry is
written and nothing is invoked on the dry-run (``--dry-run``) path.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import pengyos_fleet_rollout.apply as apply_module
from pengyos_fleet_rollout import model as m
from pengyos_fleet_rollout.apply import ApplyError, apply
from pengyos_fleet_rollout.cli import build_parser, main
from pengyos_fleet_rollout.errors import RolloutTransportError
from pengyos_fleet_rollout.plan import plan
from pengyos_fleet_rollout.transport import (
    ACTION_APPLY,
    TransportResult,
    build_argv,
)


# ---------------------------------------------------------------------------
# A fake transport — installed by patching make_transport.
# ---------------------------------------------------------------------------


class FakeTransport:
    """A small in-memory transport for the apply suite.

    Reports a configurable prior per host (falling back to the step's prior),
    can be told to fail on specific hosts, and records every step it is asked to
    run. Installed into ``apply`` by patching ``make_transport``.
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
        prior = self.prior_by_host.get(step.host, step.prior)
        return TransportResult(
            ok=True, argv=build_argv(step), returncode=0, prior=prior
        )


@pytest.fixture
def patched_make_transport():
    """Patch ``make_transport`` for the whole test; tests install their fake."""

    with patch.object(apply_module, "make_transport") as patched:
        yield patched


@pytest.fixture
def plan_path(fixtures_dir, tmp_path):
    """A plan for the committed inventory, written to a temp file."""

    produced = plan("img-2026.09.25", str(fixtures_dir / "inventory.toml"), batch_size=1)
    path = tmp_path / "plan.json"
    produced.dump(path)
    return path


# ---------------------------------------------------------------------------
# The dry-run path — renders argv, performs no transport call, writes nothing.
# ---------------------------------------------------------------------------


def test_apply_dry_run_renders_a_step_per_host(plan_path, patched_make_transport):
    fake = FakeTransport()
    patched_make_transport.return_value = fake
    result = apply(str(plan_path), transport="noop", commit=False)
    # Never invoked on a dry run...
    assert [step.host for step in fake.ran] == []
    # ...but every host is rendered as a would-be step, in rollout order.
    assert len(result.steps) == 5
    # Nothing was applied (dry run), nothing halted.
    assert result.applied == ()
    assert result.halted is False
    assert result.failed is None


def test_apply_dry_run_renders_every_host_in_order(plan_path, patched_make_transport):
    fake = FakeTransport()
    patched_make_transport.return_value = fake
    result = apply(str(plan_path), transport="noop", commit=False)
    assert [step.host for step in result.steps] == [
        "canary-1",
        "core-1",
        "core-2",
        "edge-1",
        "edge-2",
    ]


def test_apply_dry_run_performs_no_transport_call(plan_path):
    # A fake that raises if ever run: the dry run must never invoke it.
    class ExplodingTransport:
        def run(self, step):
            raise AssertionError("transport must not be invoked on a dry run")

    with patch.object(
        apply_module, "make_transport", return_value=ExplodingTransport()
    ):
        result = apply(str(plan_path), transport="noop", commit=False)
    # The dry run still produced the would-be steps without invoking anything.
    assert len(result.steps) == 5


def test_apply_dry_run_does_not_write_journal(plan_path, tmp_path):
    journal_path = tmp_path / "journal.json"
    apply(str(plan_path), transport="noop", journal_path=str(journal_path), commit=False)
    # No journal file was created on a dry run.
    assert not journal_path.exists()


def test_apply_dry_run_reports_untouched_as_all_hosts(plan_path, patched_make_transport):
    patched_make_transport.return_value = FakeTransport()
    result = apply(str(plan_path), transport="noop", commit=False)
    # Nothing was touched on a dry run, so every plan host is untouched.
    assert result.untouched == ("canary-1", "core-1", "core-2", "edge-1", "edge-2")


def test_apply_dry_run_steps_carry_the_plan_image_and_prior(
    plan_path, patched_make_transport
):
    # A fresh plan's rollback record has prior=UNKNOWN, so the first step's prior
    # is the plan's placeholder (rendered, not invoked).
    result = apply(str(plan_path), transport="noop", commit=False)
    first = result.steps[0]
    assert first.action == ACTION_APPLY
    assert first.new == "img-2026.09.25"
    assert first.prior == m.UNKNOWN_ARTIFACT
    # The canary is first.
    assert first.host == "canary-1"


def test_apply_dry_run_step_carry_the_forwarded_address(plan_path, patched_make_transport):
    patched_make_transport.return_value = FakeTransport()
    result = apply(str(plan_path), transport="noop", commit=False)
    first = result.steps[0]
    assert first.host == "canary-1"
    assert first.address == "canary-1.home.lab"


# ---------------------------------------------------------------------------
# The committed path — applies host-by-host, records the journal.
# ---------------------------------------------------------------------------


def test_apply_commit_records_each_host_as_applied(
    plan_path, patched_make_transport
):
    fake = FakeTransport()
    patched_make_transport.return_value = fake
    result = apply(str(plan_path), transport="noop", commit=True)
    # One successful apply per host, in rollout order.
    assert [step.host for step in fake.ran] == [
        "canary-1",
        "core-1",
        "core-2",
        "edge-1",
        "edge-2",
    ]
    assert result.applied == ("canary-1", "core-1", "core-2", "edge-1", "edge-2")
    assert result.halted is False
    assert result.failed is None
    assert result.untouched == ()
    # Every host is recorded applied in the journal.
    assert result.journal.applied_hosts() == (
        "canary-1",
        "core-1",
        "core-2",
        "edge-1",
        "edge-2",
    )


def test_apply_commit_records_the_prior_reported_by_transport(
    plan_path, patched_make_transport
):
    prior = "img-2026.09.18"
    fake = FakeTransport(prior_by_host={"canary-1": prior})
    patched_make_transport.return_value = fake
    result = apply(str(plan_path), transport="noop", commit=True)
    entry = result.journal.latest_for("canary-1")
    assert entry.prior == prior
    assert entry.new == "img-2026.09.25"
    assert entry.state == m.STATE_APPLIED


def test_apply_commit_falls_back_to_step_prior_when_untold(
    plan_path, patched_make_transport
):
    # The fake reports the step's prior when a host is not in prior_by_host.
    fake = FakeTransport()
    patched_make_transport.return_value = fake
    result = apply(str(plan_path), transport="noop", commit=True)
    entry = result.journal.latest_for("canary-1")
    # A fresh plan's rollback prior is the UNKNOWN placeholder.
    assert entry.prior == m.UNKNOWN_ARTIFACT


def test_apply_commit_writes_journal_to_disk(plan_path, tmp_path, patched_make_transport):
    journal_path = tmp_path / "journal.json"
    fake = FakeTransport()
    patched_make_transport.return_value = fake
    result = apply(
        str(plan_path),
        transport="noop",
        journal_path=str(journal_path),
        commit=True,
    )
    assert fake.ran  # applied
    assert journal_path.is_file()
    # The on-disk journal is the same document that was applied.
    loaded = m.RolloutJournal.load(journal_path)
    assert loaded.entries == result.journal.entries


def test_apply_commit_address_forwarded_from_inventory(
    plan_path, patched_make_transport
):
    fake = FakeTransport()
    patched_make_transport.return_value = fake
    apply(str(plan_path), transport="noop", commit=True)
    first = fake.ran[0]
    assert first.host == "canary-1"
    assert first.address == "canary-1.home.lab"


# ---------------------------------------------------------------------------
# Halt on the first failing gate.
# ---------------------------------------------------------------------------


def test_apply_halts_on_first_failing_gate(
    plan_path, patched_make_transport
):
    fake = FakeTransport(fail_on={"core-1"})
    patched_make_transport.return_value = fake
    result = apply(str(plan_path), transport="noop", commit=True)
    # canary-1 applied, core-1 failed — the rollout stops there.
    assert [step.host for step in fake.ran] == ["canary-1", "core-1"]
    assert result.halted is True
    assert result.failed == "core-1"
    assert result.applied == ("canary-1",)


def test_apply_records_the_failed_host_in_the_journal(
    plan_path, patched_make_transport
):
    fake = FakeTransport(fail_on={"core-1"})
    patched_make_transport.return_value = fake
    result = apply(str(plan_path), transport="noop", commit=True)
    failed = result.journal.latest_for("core-1")
    assert failed.state == m.STATE_FAILED
    assert failed.host == "core-1"
    # The failed entry names the artifact it was trying to roll forward to.
    assert failed.new == "img-2026.09.25"


def test_apply_untouched_are_the_rest_of_the_batch_and_later(
    fixtures_dir, tmp_path, patched_make_transport
):
    # Batch size 2: [canary-1, core-1], [core-2, edge-1], [edge-2]. Failing
    # core-1 leaves core-2, edge-1, edge-2 untouched.
    produced = plan(
        "img-2026.09.25", str(fixtures_dir / "inventory.toml"), batch_size=2
    )
    plan_file = tmp_path / "plan_batch2.json"
    produced.dump(plan_file)

    fake = FakeTransport(fail_on={"core-1"})
    patched_make_transport.return_value = fake
    result = apply(str(plan_file), transport="noop", commit=True)
    # canary-1 + core-1 are the first batch; core-1 fails, so core-2/edge-1
    # (rest of the batch) and edge-2 (later batch) are untouched.
    assert result.untouched == ("core-2", "edge-1", "edge-2")
    assert result.applied == ("canary-1",)
    assert result.failed == "core-1"


def test_apply_does_not_touch_hosts_after_a_halt(
    plan_path, patched_make_transport
):
    fake = FakeTransport(fail_on={"core-1"})
    patched_make_transport.return_value = fake
    apply(str(plan_path), transport="noop", commit=True)
    touched = {step.host for step in fake.ran}
    # No step was rendered for the untouched hosts.
    assert "edge-1" not in touched
    assert "edge-2" not in touched


# ---------------------------------------------------------------------------
# Resumability.
# ---------------------------------------------------------------------------


def test_apply_resume_skips_already_applied_hosts(
    plan_path, tmp_path, patched_make_transport
):
    journal_path = tmp_path / "journal.json"
    # First run halts at core-1.
    first = FakeTransport(fail_on={"core-1"})
    patched_make_transport.return_value = first
    first_result = apply(
        str(plan_path),
        transport="noop",
        journal_path=str(journal_path),
        commit=True,
    )
    assert first_result.failed == "core-1"
    assert first_result.applied == ("canary-1",)

    # Second run resumes: canary-1 is skipped, core-1 retried, rest applied.
    second = FakeTransport()
    patched_make_transport.return_value = second
    second_result = apply(
        str(plan_path),
        transport="noop",
        journal_path=str(journal_path),
        commit=True,
    )
    assert second_result.halted is False
    assert second_result.applied == ("core-1", "core-2", "edge-1", "edge-2")
    # canary-1 was skipped on resume (already applied) — the fake only saw the
    # hosts that were actually retried.
    assert [step.host for step in second.ran] == [
        "core-1",
        "core-2",
        "edge-1",
        "edge-2",
    ]


def test_apply_resume_appends_to_the_existing_journal(
    plan_path, tmp_path, patched_make_transport
):
    journal_path = tmp_path / "journal.json"
    # First run fails at core-1.
    first = FakeTransport(fail_on={"core-1"})
    patched_make_transport.return_value = first
    apply(
        str(plan_path),
        transport="noop",
        journal_path=str(journal_path),
        commit=True,
    )
    # Second run: core-1 now succeeds, so it is retried, rest applied.
    second = FakeTransport()
    patched_make_transport.return_value = second
    second_result = apply(
        str(plan_path),
        transport="noop",
        journal_path=str(journal_path),
        commit=True,
    )
    # The resumed journal still has the canary-1 entry from the first run.
    assert second_result.journal.latest_for("canary-1").state == m.STATE_APPLIED
    assert second_result.journal.applied_hosts() == (
        "canary-1",
        "core-1",
        "core-2",
        "edge-1",
        "edge-2",
    )


def test_apply_resume_retries_a_failed_host(
    plan_path, tmp_path, patched_make_transport
):
    journal_path = tmp_path / "journal.json"
    # First run fails at core-1.
    first = FakeTransport(fail_on={"core-1"})
    patched_make_transport.return_value = first
    apply(
        str(plan_path),
        transport="noop",
        journal_path=str(journal_path),
        commit=True,
    )
    # Second run: core-1 now succeeds, so it is retried (its latest entry was
    # failed, not applied) and the rollout completes.
    second = FakeTransport()
    patched_make_transport.return_value = second
    second_result = apply(
        str(plan_path),
        transport="noop",
        journal_path=str(journal_path),
        commit=True,
    )
    assert second_result.halted is False
    assert second.ran[0].host == "core-1"
    assert second_result.journal.latest_for("core-1").state == m.STATE_APPLIED


# ---------------------------------------------------------------------------
# Roll-forward from the journal state.
# ---------------------------------------------------------------------------


def test_apply_rolls_forward_from_journal_state(
    plan_path, tmp_path, patched_make_transport
):
    # A host previously failed at img-25 (prior img-18): on resume its prior is
    # img-18 (it still has the old artifact), not the plan's UNKNOWN placeholder.
    journal_path = tmp_path / "journal.json"
    seed = m.RolloutJournal(
        entries=(
            m.JournalEntry(
                host="canary-1",
                prior="img-2026.09.18",
                new="img-2026.09.25",
                state=m.STATE_FAILED,
                at="2026-09-25T10:00:00+00:00",
            ),
        )
    )
    seed.dump(journal_path)

    fake = FakeTransport()
    patched_make_transport.return_value = fake
    result = apply(
        str(plan_path),
        transport="noop",
        journal_path=str(journal_path),
        commit=True,
    )
    canary_step = next(s for s in fake.ran if s.host == "canary-1")
    assert canary_step.prior == "img-2026.09.18"
    entry = result.journal.latest_for("canary-1")
    # The new applied entry supersedes the failed seed entry.
    assert entry.prior == "img-2026.09.18"
    assert entry.new == "img-2026.09.25"
    assert entry.state == m.STATE_APPLIED


# ---------------------------------------------------------------------------
# Error handling.
# ---------------------------------------------------------------------------


def test_apply_unknown_transport_raises(plan_path):
    with pytest.raises(ApplyError):
        apply(str(plan_path), transport="bogus", commit=True)


def test_apply_missing_plan_raises():
    with pytest.raises(ApplyError):
        apply("/no/such/plan.json", transport="noop", commit=True)


def test_apply_malformed_plan_raises(plan_path, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ApplyError):
        apply(str(bad), transport="noop", commit=True)


def test_apply_transport_run_error_raises(plan_path, patched_make_transport):
    # A transport that raises (e.g. a command that cannot exec) surfaces as an
    # ApplyError, not a swallowed exception.
    class BoomTransport:
        def run(self, step):
            raise RolloutTransportError("cannot exec")

    patched_make_transport.return_value = BoomTransport()
    with pytest.raises(ApplyError):
        apply(str(plan_path), transport="subprocess", commit=True)


# ---------------------------------------------------------------------------
# The CLI surface.
# ---------------------------------------------------------------------------


def test_cli_apply_dry_run_returns_zero(plan_path, tmp_path, capsys, patched_make_transport):
    patched_make_transport.return_value = FakeTransport()
    rc = main(
        ["apply", "--plan", str(plan_path), "--journal", "journal.json", "--dry-run"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry run" in out


def test_cli_apply_commit_returns_zero(plan_path, tmp_path, capsys, patched_make_transport):
    patched_make_transport.return_value = FakeTransport()
    journal_path = tmp_path / "journal.json"
    rc = main(
        [
            "apply",
            "--plan",
            str(plan_path),
            "--journal",
            str(journal_path),
            "--commit",
        ]
    )
    assert rc == 0
    assert capsys.readouterr().out.strip().endswith("applied")


def test_cli_apply_halt_reports_the_failed_host(
    plan_path, tmp_path, capsys, patched_make_transport
):
    patched_make_transport.return_value = FakeTransport(fail_on={"core-1"})
    journal_path = tmp_path / "journal.json"
    rc = main(
        [
            "apply",
            "--plan",
            str(plan_path),
            "--journal",
            str(journal_path),
            "--commit",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "halted" in out
    assert "core-1" in out


def test_cli_apply_unknown_transport_returns_one(plan_path, capsys):
    # argparse rejects an unknown transport choice (exit 2) before the handler.
    with pytest.raises(SystemExit) as excinfo:
        main(
            ["apply", "--plan", str(plan_path), "--journal", "journal.json",
             "--commit", "--transport", "bogus"]
        )
    assert excinfo.value.code == 2


def test_apply_subparser_defaults_to_dry_run():
    parser = build_parser()
    args = parser.parse_args(["apply", "--plan", "p.json", "--journal", "j.json"])
    assert args.commit is False  # dry run by default


def test_apply_subparser_commit_flag():
    parser = build_parser()
    args = parser.parse_args(
        ["apply", "--plan", "p.json", "--journal", "j.json", "--commit"]
    )
    assert args.commit is True
