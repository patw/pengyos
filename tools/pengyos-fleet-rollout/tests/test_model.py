"""Tests for the rollout data model (``pengyos_fleet_rollout.model``).

These lock the shape of the two documents the tool moves in and out of: the
:class:`RolloutPlan` (the output of ``plan``) and the :class:`RolloutJournal`
(the output of ``apply``). They assert the schema, the round-trip through JSON,
and the committed fixtures — no network, no root, no hardware.
"""

from __future__ import annotations

import json

import pytest

import pengyos_fleet_rollout
from pengyos_fleet_rollout import model as m


# ---------------------------------------------------------------------------
# The package surface.
# ---------------------------------------------------------------------------


def test_version_is_unchanged():
    # This subtask adds a data model, it does not bump the version.
    assert pengyos_fleet_rollout.__version__ == "0.1.0"


def test_public_api_exports_the_model():
    for name in (
        "RolloutPlan",
        "RolloutJournal",
        "JournalEntry",
        "Batch",
        "HealthGate",
        "RollbackEntry",
        "InventoryHost",
        "ModelError",
        "load_inventory",
        "validate",
    ):
        assert hasattr(pengyos_fleet_rollout, name), f"missing export {name}"


# ---------------------------------------------------------------------------
# The validator.
# ---------------------------------------------------------------------------


def test_validator_accepts_a_document_matching_its_schema():
    schema = {
        "type": "object",
        "required": ["a"],
        "properties": {"a": {"type": "string"}},
    }
    # Must not raise.
    m.validate({"a": "x", "b": None}, schema)


def test_validator_rejects_a_wrong_type():
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    with pytest.raises(m.ModelError):
        m.validate({"a": 123}, schema)


def test_validator_treats_none_as_absent():
    # Optional fields round-trip as None and must validate cleanly.
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    m.validate({"a": None}, schema)


def test_validator_requires_missing_key():
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    with pytest.raises(m.ModelError):
        m.validate({}, schema)


def test_validator_enforces_enum():
    schema = {"type": "object", "properties": {"s": {"enum": ["x", "y"]}}}
    m.validate({"s": "y"}, schema)
    with pytest.raises(m.ModelError):
        m.validate({"s": "z"}, schema)


def test_validator_nests_into_object_and_array():
    schema = {
        "type": "object",
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["k"],
                    "properties": {"k": {"type": "integer"}},
                },
            }
        },
    }
    m.validate({"items": [{"k": 1}, {"k": 2}]}, schema)
    with pytest.raises(m.ModelError):
        m.validate({"items": [{"k": "nope"}]}, schema)


def test_validator_rejects_non_object_document():
    with pytest.raises(m.ModelError):
        m.validate(["not", "an", "object"], {"type": "object"})


# ---------------------------------------------------------------------------
# HealthGate.
# ---------------------------------------------------------------------------


def test_healthgate_defaults_to_all_healthy():
    gate = m.HealthGate()
    assert gate.kind == m.GATE_ALL_HEALTHY
    assert gate.require == (m.STATE_APPLIED,)
    assert gate.as_dict() == {"type": "all_healthy", "require": ["applied"]}


def test_healthgate_round_trips():
    gate = m.HealthGate.from_dict({"type": "all_healthy", "require": ["applied"]})
    assert gate == m.HealthGate()


def test_healthgate_rejects_unknown_type():
    with pytest.raises(m.ModelError):
        m.HealthGate.from_dict({"type": "no_such_gate", "require": ["applied"]})


# ---------------------------------------------------------------------------
# Batch.
# ---------------------------------------------------------------------------


def test_batch_defaults_gate_and_serialises():
    batch = m.Batch(index=0, hosts=("canary-1",))
    assert batch.gate == m.HealthGate()
    assert batch.as_dict() == {
        "batch": 0,
        "hosts": ["canary-1"],
        "gate": {"type": "all_healthy", "require": ["applied"]},
    }


def test_batch_round_trips():
    data = {"batch": 2, "hosts": ["a", "b"], "gate": {"type": "all_healthy", "require": ["applied"]}}
    batch = m.Batch.from_dict(data)
    assert batch.index == 2
    assert batch.hosts == ("a", "b")
    assert batch.as_dict() == data


def test_batch_requires_batch_index():
    with pytest.raises(m.ModelError):
        m.Batch.from_dict({"hosts": ["a"]})


# ---------------------------------------------------------------------------
# RollbackEntry.
# ---------------------------------------------------------------------------


def test_rollbackentry_defaults_prior_and_new_to_unknown():
    entry = m.RollbackEntry(host="canary-1")
    assert entry.prior == m.UNKNOWN_ARTIFACT
    assert entry.new == m.UNKNOWN_ARTIFACT


def test_rollbackentry_round_trips():
    data = {"host": "core-1", "prior": "img-0", "new": "img-1"}
    entry = m.RollbackEntry.from_dict(data)
    assert entry == m.RollbackEntry(host="core-1", prior="img-0", new="img-1")
    assert entry.as_dict() == data


# ---------------------------------------------------------------------------
# RolloutPlan.
# ---------------------------------------------------------------------------


def test_plan_as_dict_has_the_schema_markers():
    plan = m.RolloutPlan(
        image="img-1",
        batches=(m.Batch(0, ("canary-1",)),),
        rollback=(m.RollbackEntry(host="canary-1", new="img-1"),),
    )
    data = plan.as_dict()
    assert data["schema"] == m.PLAN_SCHEMA
    assert data["image"] == "img-1"
    assert data["batches"][0]["gate"]["type"] == m.GATE_ALL_HEALTHY


def test_plan_hosts_flattens_batches_in_order():
    plan = m.RolloutPlan(
        image="img-1",
        batches=(
            m.Batch(0, ("canary-1",)),
            m.Batch(1, ("core-1", "core-2")),
        ),
    )
    assert plan.hosts() == ("canary-1", "core-1", "core-2")


def test_plan_canary_is_first_batch():
    plan = m.RolloutPlan(
        image="img-1",
        batches=(
            m.Batch(0, ("canary-1",)),
            m.Batch(1, ("core-1",)),
        ),
    )
    assert plan.canary() == ("canary-1",)


def test_plan_round_trips_through_json():
    plan = m.RolloutPlan(
        image="img-1",
        inventory="inv.toml",
        batches=(m.Batch(0, ("canary-1",)), m.Batch(1, ("core-1",))),
        rollback=(
            m.RollbackEntry(host="canary-1", new="img-1"),
            m.RollbackEntry(host="core-1", new="img-1"),
        ),
    )
    text = plan.to_json()
    again = m.RolloutPlan.from_dict(json.loads(text))
    assert again.as_dict() == plan.as_dict()


def test_plan_from_dict_rejects_a_malformed_document():
    with pytest.raises(m.ModelError):
        m.RolloutPlan.from_dict({"image": "img-1"})  # missing batches/rollback/schema


def test_plan_dump_and_load_round_trips(tmp_path):
    plan = m.RolloutPlan(image="img-1", batches=(m.Batch(0, ("canary-1",)),))
    path = tmp_path / "plan.json"
    plan.dump(path)
    loaded = m.RolloutPlan.load(path)
    assert loaded.as_dict() == plan.as_dict()


def test_plan_load_missing_file_raises(tmp_path):
    with pytest.raises(m.ModelError):
        m.RolloutPlan.load(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# JournalEntry.
# ---------------------------------------------------------------------------


def test_journalentry_defaults_state_to_applied_and_stamps_timestamp():
    entry = m.JournalEntry(host="canary-1", prior="img-0", new="img-1")
    assert entry.state == m.STATE_APPLIED
    assert entry.at != ""  # a UTC ISO-8601 timestamp was stamped


def test_journalentry_round_trips():
    entry = m.JournalEntry(
        host="core-1", prior="img-0", new="img-1", state=m.STATE_ROLLED_BACK, at="2026-09-25T10:00:00+00:00"
    )
    data = entry.as_dict()
    again = m.JournalEntry.from_dict(data)
    assert again == entry


def test_journalentry_defaults_state_when_omitted():
    entry = m.JournalEntry.from_dict({"host": "h", "prior": "p", "new": "n"})
    assert entry.state == m.STATE_APPLIED


def test_journalentry_rejects_invalid_state():
    with pytest.raises(m.ModelError):
        m.JournalEntry.from_dict({"host": "h", "prior": "p", "new": "n", "state": "bogus"})


# ---------------------------------------------------------------------------
# RolloutJournal.
# ---------------------------------------------------------------------------


def _journal():
    return m.RolloutJournal(
        entries=(
            m.JournalEntry(host="canary-1", prior="img-0", new="img-1", state=m.STATE_APPLIED, at="t1"),
            m.JournalEntry(host="core-1", prior="img-0", new="img-1", state=m.STATE_APPLIED, at="t2"),
            m.JournalEntry(host="core-2", prior="img-0", new="img-1", state=m.STATE_FAILED, at="t3"),
        )
    )


def test_journal_hosts_in_write_order():
    assert _journal().hosts() == ("canary-1", "core-1", "core-2")


def test_journal_append_is_immutable():
    journal = _journal()
    appended = journal.append(
        m.JournalEntry(host="edge-1", prior="img-0", new="img-1", state=m.STATE_APPLIED, at="t4")
    )
    assert len(journal.entries) == 3  # original untouched
    assert len(appended.entries) == 4
    assert appended.hosts() == ("canary-1", "core-1", "core-2", "edge-1")


def test_journal_latest_for_and_entries_for():
    journal = _journal().append(
        m.JournalEntry(host="canary-1", prior="img-1", new="img-2", state=m.STATE_ROLLED_BACK, at="t5")
    )
    assert journal.latest_for("canary-1").state == m.STATE_ROLLED_BACK
    assert len(journal.entries_for("canary-1")) == 2
    assert journal.latest_for("edge-9") is None


def test_journal_applied_hosts():
    journal = _journal()
    assert journal.applied_hosts() == ("canary-1", "core-1")


def test_journal_rolled_back_hosts():
    journal = _journal().append(
        m.JournalEntry(host="canary-1", prior="img-0", new="img-1", state=m.STATE_ROLLED_BACK, at="t5")
    )
    assert journal.rolled_back_hosts() == ("canary-1",)


def test_journal_untouched_hosts_are_the_untouched():
    # "Untouched" = hosts in the plan with no journal entry. The journal knows
    # which hosts it touched; apply computes the untouched set by difference
    # against the plan's host set.
    journal = _journal()
    touched = set(journal.hosts())
    plan_hosts = ("canary-1", "core-1", "core-2", "edge-1", "edge-2")
    untouched = tuple(h for h in plan_hosts if h not in touched)
    assert untouched == ("edge-1", "edge-2")


def test_journal_round_trips_through_json():
    text = _journal().to_json()
    again = m.RolloutJournal.from_dict(json.loads(text))
    assert again.as_dict() == _journal().as_dict()


def test_journal_dump_and_load_round_trips(tmp_path):
    journal = _journal()
    path = tmp_path / "journal.json"
    journal.dump(path)
    assert m.RolloutJournal.load(path).as_dict() == journal.as_dict()


def test_journal_from_dict_rejects_a_malformed_document():
    with pytest.raises(m.ModelError):
        m.RolloutJournal.from_dict({"entries": []})  # missing schema


# ---------------------------------------------------------------------------
# load_inventory.
# ---------------------------------------------------------------------------


def test_load_inventory_reads_the_committed_fixture(fixtures_dir):
    hosts = m.load_inventory(fixtures_dir / "inventory.toml")
    names = [h.name for h in hosts]
    # The fixture orders hosts canary-first, then by priority desc / name.
    assert names == ["canary-1", "core-1", "core-2", "edge-1", "edge-2"]


def test_load_inventory_captures_role_priority_address(fixtures_dir):
    (host,) = [h for h in m.load_inventory(fixtures_dir / "inventory.toml") if h.name == "core-1"]
    assert host.role == "core"
    assert host.priority == 50
    assert host.address == "core-1.home.lab"


def test_load_inventory_missing_file_raises(tmp_path):
    with pytest.raises(m.ModelError):
        m.load_inventory(tmp_path / "nope.toml")


def test_load_inventory_empty_inventory_raises(fixtures_dir, tmp_path):
    empty = tmp_path / "empty.toml"
    empty.write_text("# no hosts\n", encoding="utf-8")
    with pytest.raises(m.ModelError):
        m.load_inventory(empty)


def test_load_inventory_malformed_toml_raises(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = = not valid toml\n", encoding="utf-8")
    with pytest.raises(m.ModelError):
        m.load_inventory(bad)


def test_load_inventory_missing_name_raises(tmp_path):
    bad = tmp_path / "noname.toml"
    bad.write_text('[[hosts]]\naddress = "x"\n', encoding="utf-8")
    with pytest.raises(m.ModelError):
        m.load_inventory(bad)


def test_load_inventory_non_integer_priority_raises(tmp_path):
    bad = tmp_path / "badprio.toml"
    bad.write_text('[[hosts]]\nname = "h"\npriority = "high"\n', encoding="utf-8")
    with pytest.raises(m.ModelError):
        m.load_inventory(bad)


# ---------------------------------------------------------------------------
# The committed fixtures are well-formed and schema-valid.
# ---------------------------------------------------------------------------


def test_committed_plan_fixture_is_schema_valid(fixtures_dir):
    plan = m.RolloutPlan.load(fixtures_dir / "plan.json")
    assert plan.image == "img-2026.09.25"
    assert plan.batches[0].hosts == ("canary-1",)  # canary first
    assert plan.hosts() == ("canary-1", "core-1", "core-2", "edge-1", "edge-2")


def test_committed_journal_fixture_is_schema_valid(fixtures_dir):
    journal = m.RolloutJournal.load(fixtures_dir / "journal.json")
    assert journal.hosts() == ("canary-1", "core-1", "core-2")
    assert journal.applied_hosts() == ("canary-1", "core-1")
    assert journal.entries_for("core-2")[0].state == m.STATE_FAILED


def test_committed_plan_fixture_matches_inventory_order(fixtures_dir):
    # The committed plan's order must match the committed inventory's order
    # (canary first, then priority desc, name asc) — the plan is derived from
    # the inventory, so they cannot drift.
    hosts = [h.name for h in m.load_inventory(fixtures_dir / "inventory.toml")]
    plan = m.RolloutPlan.load(fixtures_dir / "plan.json")
    assert plan.hosts() == tuple(hosts)
