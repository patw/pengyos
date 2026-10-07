"""Tests for ``plan`` (``pengyos_fleet_rollout.plan``).

These lock the behaviour subtask: read a fleet inventory, order the fleet
canary-first (priority desc, then name asc), group it into deterministic
batches, and wrap that in a :class:`RolloutPlan` with an explicit health gate
per batch and a per-host rollback record. They also cover the two hard errors
(empty inventory, unknown image) and the ``plan`` CLI surface. No network, no
root, no hardware — the inventory is a committed fixture (or a small temp file).
"""

from __future__ import annotations

import json

import pytest

import pengyos_fleet_rollout
from pengyos_fleet_rollout import model as m
from pengyos_fleet_rollout.cli import build_parser, main
from pengyos_fleet_rollout.errors import RolloutPlanError
from pengyos_fleet_rollout.plan import (
    group_into_batches,
    is_known_image,
    order_hosts,
    plan,
)


# ---------------------------------------------------------------------------
# is_known_image.
# ---------------------------------------------------------------------------


def test_is_known_image_accepts_a_dated_build_id():
    assert is_known_image("img-2026.09.25")


def test_is_known_image_accepts_a_hashed_build_id():
    assert is_known_image("img-a1b2c3d4")


def test_is_known_image_rejects_empty():
    assert not is_known_image("")


def test_is_known_image_rejects_whitespace_only():
    assert not is_known_image("   ")


def test_is_known_image_rejects_a_word_without_a_digit():
    # "known" means a well-formed, versioned artifact id — a bare word like
    # "latest" can never resolve to a built image, so it is unknown.
    assert not is_known_image("latest")
    assert not is_known_image("nonsense")


def test_is_known_image_rejects_a_malformed_id():
    assert not is_known_image("img!!!")


def test_is_known_image_rejects_an_id_with_whitespace_inside():
    assert not is_known_image("img-2026 09 25")


# ---------------------------------------------------------------------------
# order_hosts — canary first, priority desc, name asc.
# ---------------------------------------------------------------------------


def test_order_hosts_is_canary_first_from_the_committed_fixture(fixtures_dir):
    hosts = m.load_inventory(fixtures_dir / "inventory.toml")
    ordered = [h.name for h in order_hosts(hosts)]
    assert ordered == ["canary-1", "core-1", "core-2", "edge-1", "edge-2"]


def test_order_hosts_pulls_a_low_priority_canary_to_the_front():
    # The canary is first by *role*, even when its priority is the lowest — the
    # canary batch is always the first step, regardless of the priority hint.
    hosts = [
        m.InventoryHost(name="core-a", role="core", priority=100),
        m.InventoryHost(name="canary-1", role="canary", priority=1),
        m.InventoryHost(name="core-b", role="core", priority=50),
    ]
    ordered = [h.name for h in order_hosts(hosts)]
    assert ordered[0] == "canary-1"
    assert ordered == ["canary-1", "core-a", "core-b"]


def test_order_hosts_orders_by_priority_desc_then_name_asc():
    hosts = [
        m.InventoryHost(name="z-core", role="core", priority=50),
        m.InventoryHost(name="a-core", role="core", priority=50),
        m.InventoryHost(name="high", role="core", priority=90),
        m.InventoryHost(name="low", role="core", priority=10),
    ]
    ordered = [h.name for h in order_hosts(hosts)]
    assert ordered == ["high", "a-core", "z-core", "low"]


def test_order_hosts_sorts_missing_priority_last():
    hosts = [
        m.InventoryHost(name="noprio", role="core"),
        m.InventoryHost(name="low", role="core", priority=5),
        m.InventoryHost(name="high", role="core", priority=50),
    ]
    ordered = [h.name for h in order_hosts(hosts)]
    assert ordered == ["high", "low", "noprio"]


def test_order_hosts_does_not_mutate_its_input(fixtures_dir):
    hosts = m.load_inventory(fixtures_dir / "inventory.toml")
    original = [h.name for h in hosts]
    order_hosts(hosts)
    assert [h.name for h in hosts] == original


def test_order_hosts_is_deterministic(fixtures_dir):
    hosts = m.load_inventory(fixtures_dir / "inventory.toml")
    first = tuple(h.name for h in order_hosts(hosts))
    twice = tuple(h.name for h in order_hosts(list(reversed(hosts))))
    assert first == twice


# ---------------------------------------------------------------------------
# group_into_batches — deterministic chunking.
# ---------------------------------------------------------------------------


def _group_names(groups):
    """The host names of each group, flattened one level."""

    return [[h.name for h in group] for group in groups]


def test_group_into_batches_batch_size_one():
    hosts = [
        m.InventoryHost(name="a"),
        m.InventoryHost(name="b"),
        m.InventoryHost(name="c"),
    ]
    groups = group_into_batches(hosts, 1)
    assert _group_names(groups) == [["a"], ["b"], ["c"]]


def test_group_into_batches_batch_size_two_with_remainder():
    hosts = [
        m.InventoryHost(name="a"),
        m.InventoryHost(name="b"),
        m.InventoryHost(name="c"),
        m.InventoryHost(name="d"),
        m.InventoryHost(name="e"),
    ]
    groups = group_into_batches(hosts, 2)
    assert _group_names(groups) == [["a", "b"], ["c", "d"], ["e"]]
    # Every host is kept exactly once, in order.
    assert [h.name for group in groups for h in group] == [h.name for h in hosts]


def test_group_into_batches_batch_size_larger_than_fleet():
    hosts = [m.InventoryHost(name="a"), m.InventoryHost(name="b")]
    groups = group_into_batches(hosts, 10)
    assert _group_names(groups) == [["a", "b"]]


def test_group_into_batches_rejects_zero_batch_size():
    with pytest.raises(RolloutPlanError):
        group_into_batches([], 0)


def test_group_into_batches_preserves_order():
    # The batch order must match the rollout order — batching is a stable chunk:
    # the first batch opens with the canary, and the remainder follows in order.
    hosts = [
        m.InventoryHost(name="canary-1"),
        m.InventoryHost(name="core-1"),
        m.InventoryHost(name="core-2"),
    ]
    groups = group_into_batches(hosts, 2)
    assert _group_names(groups) == [["canary-1", "core-1"], ["core-2"]]


# ---------------------------------------------------------------------------
# plan — the full behaviour.
# ---------------------------------------------------------------------------


def test_plan_is_deterministic_and_canary_first(fixtures_dir, tmp_path):
    produced = plan("img-2026.09.25", str(fixtures_dir / "inventory.toml"), batch_size=1)
    assert produced.canary() == ("canary-1",)
    assert produced.hosts() == ("canary-1", "core-1", "core-2", "edge-1", "edge-2")
    # Deterministic: a second call produces the identical plan.
    again = plan("img-2026.09.25", str(fixtures_dir / "inventory.toml"), batch_size=1)
    assert produced.as_dict() == again.as_dict()


def test_plan_sets_the_image_and_inventory_provenance(fixtures_dir):
    produced = plan("img-2026.09.25", str(fixtures_dir / "inventory.toml"))
    assert produced.image == "img-2026.09.25"
    assert produced.inventory == str(fixtures_dir / "inventory.toml")


def test_plan_gives_each_batch_an_explicit_health_gate(fixtures_dir):
    produced = plan("img-2026.09.25", str(fixtures_dir / "inventory.toml"), batch_size=1)
    for index, batch in enumerate(produced.batches):
        assert batch.index == index
        assert batch.gate.kind == m.GATE_ALL_HEALTHY
        assert batch.gate.require == (m.STATE_APPLIED,)


def test_plan_builds_a_rollback_entry_per_host(fixtures_dir):
    produced = plan("img-2026.09.25", str(fixtures_dir / "inventory.toml"), batch_size=1)
    assert [r.host for r in produced.rollback] == list(produced.hosts())
    for entry in produced.rollback:
        assert entry.new == "img-2026.09.25"
        # prior is the placeholder until apply records the real prior artifact.
        assert entry.prior == m.UNKNOWN_ARTIFACT


def test_plan_batches_are_indexed_from_zero_in_order(fixtures_dir):
    produced = plan("img-2026.09.25", str(fixtures_dir / "inventory.toml"), batch_size=1)
    assert [b.index for b in produced.batches] == [0, 1, 2, 3, 4]


def test_plan_batch_size_groups_multiple_hosts(fixtures_dir):
    produced = plan("img-2026.09.25", str(fixtures_dir / "inventory.toml"), batch_size=2)
    # canary-1 + core-1, then core-2 + edge-1, then the trailing edge-2.
    assert [b.hosts for b in produced.batches] == [
        ("canary-1", "core-1"),
        ("core-2", "edge-1"),
        ("edge-2",),
    ]
    # The rollback record still names every host once.
    assert [r.host for r in produced.rollback] == list(produced.hosts())


def test_plan_batch_size_one_matches_the_committed_fixture(fixtures_dir):
    # The committed plan.json is the plan this inventory produces with
    # --batch-size 1. Any drift in ordering/batching/rollback breaks this.
    produced = plan("img-2026.09.25", str(fixtures_dir / "inventory.toml"), batch_size=1)
    committed = json.loads((fixtures_dir / "plan.json").read_text(encoding="utf-8"))
    assert produced.image == committed["image"]
    assert produced.batches == tuple(
        m.Batch.from_dict(b) for b in committed["batches"]
    )
    assert produced.rollback == tuple(
        m.RollbackEntry.from_dict(r) for r in committed["rollback"]["hosts"]
    )


def test_plan_round_trips_through_json(fixtures_dir):
    produced = plan("img-2026.09.25", str(fixtures_dir / "inventory.toml"), batch_size=1)
    again = m.RolloutPlan.from_dict(json.loads(produced.to_json()))
    assert again.as_dict() == produced.as_dict()


def test_plan_text_renders_batches_and_rollback(fixtures_dir):
    produced = plan("img-2026.09.25", str(fixtures_dir / "inventory.toml"), batch_size=1)
    text = produced.render_text()
    assert "PengyOS fleet rollout plan" in text
    assert "img-2026.09.25" in text
    assert "batch 0: [canary-1]" in text
    assert "gate=all_healthy" in text
    assert "prior=UNKNOWN" in text


# ---------------------------------------------------------------------------
# plan — the hard errors.
# ---------------------------------------------------------------------------


def test_plan_rejects_an_unknown_image(fixtures_dir):
    for bad in ("", "   ", "nonsense", "img!!!"):
        with pytest.raises(RolloutPlanError):
            plan(bad, str(fixtures_dir / "inventory.toml"))


def test_plan_rejects_an_empty_inventory(tmp_path):
    # An empty inventory is a plan-level input error (translated from the
    # model-layer ModelError), so the CLI maps it to exit code 2.
    empty = tmp_path / "empty.toml"
    empty.write_text("# no hosts\n", encoding="utf-8")
    with pytest.raises(RolloutPlanError):
        plan("img-2026.09.25", empty)


def test_plan_rejects_a_missing_inventory():
    with pytest.raises(RolloutPlanError):
        plan("img-2026.09.25", "/no/such/inventory.toml")


def test_plan_rejects_a_malformed_inventory(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = = not valid toml\n", encoding="utf-8")
    with pytest.raises(RolloutPlanError):
        plan("img-2026.09.25", bad)


def test_plan_rejects_a_non_positive_batch_size(fixtures_dir):
    with pytest.raises(RolloutPlanError):
        plan("img-2026.09.25", str(fixtures_dir / "inventory.toml"), batch_size=0)


def test_plan_rejects_a_negative_batch_size(fixtures_dir):
    with pytest.raises(RolloutPlanError):
        plan("img-2026.09.25", str(fixtures_dir / "inventory.toml"), batch_size=-3)


# ---------------------------------------------------------------------------
# plan — the CLI surface.
# ---------------------------------------------------------------------------


def test_cli_plan_json_emits_valid_json(fixtures_dir, capsys):
    rc = main(
        [
            "plan",
            "--image",
            "img-2026.09.25",
            "--inventory",
            str(fixtures_dir / "inventory.toml"),
            "--json",
        ]
    )
    assert rc == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["image"] == "img-2026.09.25"
    assert emitted["batches"][0]["hosts"] == ["canary-1"]  # canary first


def test_cli_plan_text_defaults_to_one_host_per_batch(fixtures_dir, capsys):
    rc = main(
        [
            "plan",
            "--image",
            "img-2026.09.25",
            "--inventory",
            str(fixtures_dir / "inventory.toml"),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "batch 0: [canary-1]" in out
    assert "batch 4: [edge-2]" in out


def test_cli_plan_batch_size_argument(fixtures_dir, capsys):
    rc = main(
        [
            "plan",
            "--image",
            "img-2026.09.25",
            "--inventory",
            str(fixtures_dir / "inventory.toml"),
            "--batch-size",
            "2",
            "--json",
        ]
    )
    assert rc == 0
    emitted = json.loads(capsys.readouterr().out)
    assert [b["hosts"] for b in emitted["batches"]] == [
        ["canary-1", "core-1"],
        ["core-2", "edge-1"],
        ["edge-2"],
    ]


def test_cli_plan_unknown_image_returns_two(fixtures_dir, capsys):
    rc = main(
        [
            "plan",
            "--image",
            "nonsense",
            "--inventory",
            str(fixtures_dir / "inventory.toml"),
        ]
    )
    assert rc == 2
    assert "unknown image" in capsys.readouterr().err.lower()


def test_cli_plan_empty_inventory_returns_two(tmp_path, capsys):
    empty = tmp_path / "empty.toml"
    empty.write_text("# no hosts\n", encoding="utf-8")
    rc = main(
        [
            "plan",
            "--image",
            "img-2026.09.25",
            "--inventory",
            str(empty),
        ]
    )
    assert rc == 2
    assert "hosts" in capsys.readouterr().err.lower()


def test_cli_plan_requires_image_and_inventory():
    # argparse enforces the required args before the handler runs.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["plan", "--inventory", "inv.toml"])
