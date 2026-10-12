"""Tests for the pure wizard state machine.

These exercise the three spec.verification bullets:

* the state machine path is deterministic; back-navigation returns to the prior
  step;
* later steps derive their values (the plan) from earlier answers;
* an invalid answer is rejected with a message, not a crash.

The whole suite is offline: no I/O, no device, no root. Block-device/size facts
are injected through a fixture ``probe``.
"""

import pytest

from pengyos_install_tui import model
from pengyos_install_tui.model import (
    Edition,
    Layout,
    NetworkMode,
    PengyAnswer,
    Plan,
    StepKey,
    ValidationError,
    Wizard,
    validate_edition,
    validate_layout,
    validate_network,
    validate_pengy,
    validate_target,
    validate_user,
)

# The canonical happy-path answer set (spec.md: target, edition, layout, user,
# network, pengy, confirm). ``auto`` must reproduce this exact shape. The
# canonical path *skips* Pengy — the endpoint step is optional, and a plan that
# skips it carries no ``pengy`` key at all.
HAPPY = {
    "target": "/dev/sdb",
    "edition": "core",
    "layout": "single-root",
    "user": "alice",
    "network": "dhcp",
    "pengy": "",
}


def _happy_wizard(probe=None) -> Wizard:
    """Drive a wizard through the full happy path; returns the confirmed plan."""
    w = Wizard(probe=probe)
    w.answer(StepKey.TARGET, HAPPY["target"])
    w.answer(StepKey.EDITION, HAPPY["edition"])
    w.answer(StepKey.LAYOUT, HAPPY["layout"])
    w.answer(StepKey.USER, HAPPY["user"])
    w.answer(StepKey.NETWORK, HAPPY["network"])
    w.answer(StepKey.PENGY, HAPPY["pengy"])
    assert w.current_step == StepKey.CONFIRM
    return w.confirm()


# ---------------------------------------------------------------------------
# Step ordering / structure
# ---------------------------------------------------------------------------


def test_wizard_order_is_target_through_confirm():
    assert model.WIZARD_ORDER == (
        StepKey.TARGET,
        StepKey.EDITION,
        StepKey.LAYOUT,
        StepKey.USER,
        StepKey.NETWORK,
        StepKey.PENGY,
        StepKey.CONFIRM,
    )
    assert model.WIZARD_ORDER[-1] == StepKey.CONFIRM
    # The endpoint step is the one step a plan does not require.
    assert StepKey.PENGY not in model.REQUIRED_STEPS


def test_wizard_starts_on_target():
    w = Wizard()
    assert w.current_step == StepKey.TARGET
    assert w.step_index == 0
    assert w.progress() == 0


def test_step_keys_are_stable_strings():
    # Both front-ends (this wizard + the Rust GUI) must agree on these keys.
    assert StepKey.TARGET == "target"
    assert StepKey.EDITION == "edition"
    assert StepKey.LAYOUT == "layout"
    assert StepKey.USER == "user"
    assert StepKey.NETWORK == "network"
    assert StepKey.CONFIRM == "confirm"


def test_closed_value_sets():
    assert Edition.ALL == ("core", "server")
    assert Layout.ALL == ("single-root", "ab-split")
    assert Layout.DEFAULT == "single-root"
    assert NetworkMode.ALL == ("dhcp", "static")


# ---------------------------------------------------------------------------
# The happy path: deterministic, in order, derived plan
# ---------------------------------------------------------------------------


def test_happy_path_advances_through_every_step():
    w = Wizard()
    assert w.current_step == StepKey.TARGET
    w.answer(StepKey.TARGET, "/dev/sdb")
    assert w.current_step == StepKey.EDITION
    w.answer(StepKey.EDITION, "core")
    assert w.current_step == StepKey.LAYOUT
    w.answer(StepKey.LAYOUT, "single-root")
    assert w.current_step == StepKey.USER
    w.answer(StepKey.USER, "alice")
    assert w.current_step == StepKey.NETWORK
    w.answer(StepKey.NETWORK, "dhcp")
    assert w.current_step == StepKey.PENGY
    assert w.progress() == 5
    w.answer(StepKey.PENGY, "")  # the optional step: skipped, and that is an answer
    assert w.current_step == StepKey.CONFIRM
    assert w.progress() == 6


def test_pengy_api_key_is_not_echoed_by_the_validator():
    value, message = validate_pengy({"base_url": "http://127.0.0.1:8080/v1",
                                     "api_key": "sk-super secret"})
    assert value is None and message is not None
    assert "sk-super" not in message


def test_pengy_empty_answer_is_valid():
    """The whole step is optional: every empty shape means 'leave Pengy alone'."""
    for raw in ("", "   ", None, {}):
        value, message = validate_pengy(raw)
        assert message is None, raw
        assert value.configured is False


def test_happy_path_produces_the_canonical_plan():
    plan = _happy_wizard()
    assert isinstance(plan, Plan)
    assert plan.to_dict() == {
        "target": {"path": "/dev/sdb"},
        "edition": "core",
        "layout": "single-root",
        "user": {"username": "alice", "uid": 1000},
        "network": {"mode": "dhcp"},
    }


def test_plan_is_deterministic_across_runs():
    assert _happy_wizard().to_dict() == _happy_wizard().to_dict()


def test_done_flag_after_confirm():
    w = Wizard()
    assert w.done is False
    w.answer(StepKey.TARGET, "/dev/sdb")
    w.answer(StepKey.EDITION, "core")
    w.answer(StepKey.LAYOUT, "single-root")
    w.answer(StepKey.USER, "alice")
    w.answer(StepKey.NETWORK, "dhcp")
    # confirm() needs no Pengy answer: the endpoint step is optional.
    w.confirm()
    assert w.done is True


def test_plan_as_lines_is_stable():
    lines = _happy_wizard().as_lines()
    assert lines == (
        "target:   /dev/sdb",
        "edition:  core",
        "layout:   single-root",
        "user:     alice (uid 1000)",
        "network:  dhcp",
        # The endpoint step always shows on the review, so the operator sees
        # that Pengy was left unconfigured rather than wondering if it shipped.
        "pengy:    not configured (Pengy keeps its defaults)",
    )


# ---------------------------------------------------------------------------
# Derived answers
# ---------------------------------------------------------------------------


def test_edition_is_normalised_lowercase():
    plan = _happy_wizard()
    assert plan.edition == "core"  # stored lower-cased regardless of input case


def test_edition_accepts_any_case():
    w = Wizard()
    w.answer(StepKey.TARGET, "/dev/sdb")
    w.answer(StepKey.EDITION, "SERVER")
    assert w.current_step == StepKey.LAYOUT
    assert w.get_answer(StepKey.EDITION) == "server"


def test_target_is_trimmed():
    w = Wizard()
    w.answer(StepKey.TARGET, "  /dev/sdb  ")
    assert w.get_answer(StepKey.TARGET) == "/dev/sdb"


def test_user_accepts_mapping_and_defaults_uid():
    w = Wizard()
    w.answer(StepKey.TARGET, "/dev/sdb")
    w.answer(StepKey.EDITION, "core")
    w.answer(StepKey.LAYOUT, "single-root")
    w.answer(StepKey.USER, {"username": "bob"})
    user = w.get_answer(StepKey.USER)
    assert user.username == "bob"
    assert user.uid == 1000


def test_network_static_carries_fields():
    w = Wizard()
    w.answer(StepKey.TARGET, "/dev/sdb")
    w.answer(StepKey.EDITION, "core")
    w.answer(StepKey.LAYOUT, "single-root")
    w.answer(StepKey.USER, "alice")
    w.answer(
        StepKey.NETWORK,
        {"mode": "static", "address": "10.0.0.5/24", "gateway": "10.0.0.1",
         "nameservers": ["1.1.1.1"]},
    )
    net = w.get_answer(StepKey.NETWORK)
    assert net.mode == "static"
    assert net.address == "10.0.0.5/24"
    assert net.gateway == "10.0.0.1"
    assert net.nameservers == ("1.1.1.1",)


def test_network_normalises_nameserver_list():
    w = Wizard()
    w.answer(StepKey.TARGET, "/dev/sdb")
    w.answer(StepKey.EDITION, "core")
    w.answer(StepKey.LAYOUT, "single-root")
    w.answer(StepKey.USER, "alice")
    w.answer(StepKey.NETWORK, {"mode": "static", "address": "10.0.0.5",
                               "nameservers": ["1.1.1.1", "8.8.8.8"]})
    assert w.get_answer(StepKey.NETWORK).nameservers == ("1.1.1.1", "8.8.8.8")


# ---------------------------------------------------------------------------
# Back navigation
# ---------------------------------------------------------------------------


def test_back_returns_to_prior_step():
    w = Wizard()
    w.answer(StepKey.TARGET, "/dev/sdb")
    w.answer(StepKey.EDITION, "core")
    w.answer(StepKey.LAYOUT, "single-root")
    assert w.current_step == StepKey.USER
    assert w.back() == StepKey.LAYOUT
    assert w.back() == StepKey.EDITION


def test_back_at_first_step_is_noop():
    w = Wizard()
    assert w.back() == StepKey.TARGET
    assert w.step_index == 0


def test_back_allows_reanswer_and_readvances():
    w = Wizard()
    w.answer(StepKey.TARGET, "/dev/sdb")
    w.answer(StepKey.EDITION, "core")
    w.answer(StepKey.LAYOUT, "single-root")
    w.answer(StepKey.USER, "alice")
    assert w.current_step == StepKey.NETWORK
    # Back to edition, change it, and confirm it re-advances to layout.
    assert w.back() == StepKey.USER
    assert w.back() == StepKey.LAYOUT
    assert w.back() == StepKey.EDITION
    w.answer(StepKey.EDITION, "server")
    assert w.current_step == StepKey.LAYOUT
    plan = _finish(w)
    assert plan.edition == "server"


def test_back_retains_prior_answers():
    w = Wizard()
    w.answer(StepKey.TARGET, "/dev/sdb")
    w.answer(StepKey.EDITION, "core")
    w.answer(StepKey.LAYOUT, "single-root")
    w.answer(StepKey.USER, "alice")
    w.answer(StepKey.NETWORK, "dhcp")
    assert w.current_step == StepKey.PENGY
    w.back()
    assert w.back()
    # Earlier answers survive back-navigation.
    assert w.get_answer(StepKey.TARGET) == "/dev/sdb"
    assert w.get_answer(StepKey.EDITION) == "core"


def _finish(w: Wizard) -> Plan:
    """Answer any remaining steps then confirm (helper for re-answer tests)."""
    order = (StepKey.LAYOUT, StepKey.USER, StepKey.NETWORK, StepKey.PENGY)
    values = {"layout": "single-root", "user": "alice", "network": "dhcp", "pengy": ""}
    for step in order:
        if not w.is_answered(step):
            w.answer(step, values[step])
    return w.confirm()


# ---------------------------------------------------------------------------
# Invalid answers: rejected with a message, not a crash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["not-a-device", "sdb", "/tmp/root", "", "   ", "/dev/sda1", "/dev/cdrom"],
)
def test_invalid_target_rejected(bad):
    w = Wizard()
    with pytest.raises(ValidationError) as e:
        w.answer(StepKey.TARGET, bad)
    assert e.value.step == StepKey.TARGET
    assert e.value.message  # a human-readable message, not empty


def test_target_non_string_rejected():
    with pytest.raises(ValidationError):
        Wizard().answer(StepKey.TARGET, 123)


@pytest.mark.parametrize("bad", ["desktop", "", "CORE2", None])
def test_invalid_edition_rejected(bad):
    with pytest.raises(ValidationError) as e:
        Wizard().answer(StepKey.EDITION, bad)
    assert e.value.step == StepKey.EDITION
    assert "core" in e.value.message and "server" in e.value.message


@pytest.mark.parametrize("bad", ["lvm", "", "gpt", None])
def test_invalid_layout_rejected(bad):
    with pytest.raises(ValidationError) as e:
        Wizard().answer(StepKey.LAYOUT, bad)
    assert e.value.step == StepKey.LAYOUT


@pytest.mark.parametrize("bad", ["", "1 bad", "has space", "a" * 33, None])
def test_invalid_user_rejected(bad):
    with pytest.raises(ValidationError) as e:
        Wizard().answer(StepKey.USER, bad)
    assert e.value.step == StepKey.USER


@pytest.mark.parametrize("bad", ["", "vpn", None])
def test_invalid_network_mode_rejected(bad):
    with pytest.raises(ValidationError) as e:
        Wizard().answer(StepKey.NETWORK, bad)
    assert e.value.step == StepKey.NETWORK


def test_static_network_without_address_rejected():
    with pytest.raises(ValidationError) as e:
        Wizard().answer(StepKey.NETWORK, {"mode": "static"})
    assert "address" in e.value.message


def test_invalid_answer_leaves_step_unanswered():
    w = Wizard()
    w.answer(StepKey.TARGET, "/dev/sdb")
    w.answer(StepKey.EDITION, "core")
    with pytest.raises(ValidationError):
        w.answer(StepKey.LAYOUT, "nope")
    assert not w.is_answered(StepKey.LAYOUT)
    # The cursor did not advance past the failed step.
    assert w.current_step == StepKey.LAYOUT


def test_confirm_is_not_an_answerable_step():
    with pytest.raises(ValidationError):
        Wizard().answer(StepKey.CONFIRM, "yes")


def test_confirm_requires_all_steps():
    w = Wizard()
    w.answer(StepKey.TARGET, "/dev/sdb")
    w.answer(StepKey.EDITION, "core")
    w.answer(StepKey.LAYOUT, "single-root")
    w.answer(StepKey.USER, "alice")
    # Network unanswered -> confirm() must report the missing step.
    with pytest.raises(ValidationError) as e:
        w.confirm()
    assert "incomplete" in e.value.message
    assert "network" in e.value.message


def test_confirm_works_from_any_cursor_when_complete():
    # The run layer navigates to confirm first; but confirm() itself only needs
    # every step answered, so a complete wizard confirms regardless of cursor.
    w = Wizard()
    w.answer(StepKey.TARGET, "/dev/sdb")
    w.answer(StepKey.EDITION, "core")
    w.answer(StepKey.LAYOUT, "single-root")
    w.answer(StepKey.USER, "alice")
    w.answer(StepKey.NETWORK, "dhcp")
    # Jump the cursor back two steps; confirm still succeeds.
    assert w.back() == StepKey.NETWORK
    assert w.back() == StepKey.USER
    plan = w.confirm()
    assert plan.to_dict()["edition"] == "core"


# ---------------------------------------------------------------------------
# Injected probe: block-device / size checks stay out of the pure validator
# ---------------------------------------------------------------------------


def test_probe_failure_rejects_target():
    def probe(path):
        return "not a block device" if path != "/dev/sdb" else None

    w = Wizard(probe=probe)
    with pytest.raises(ValidationError):
        w.answer(StepKey.TARGET, "/dev/loop0")
    assert not w.is_answered(StepKey.TARGET)


def test_probe_passes_valid_target():
    def probe(path):
        return None  # always acceptable

    w = Wizard(probe=probe)
    w.answer(StepKey.TARGET, "/dev/sdb")
    assert w.is_answered(StepKey.TARGET)


# ---------------------------------------------------------------------------
# Pure validators (unit-level, directly)
# ---------------------------------------------------------------------------


def test_validate_target_tuples():
    assert validate_target("/dev/sdb") == ("/dev/sdb", None)
    assert validate_target("nope")[1] is not None
    assert validate_target(5)[1] is not None


def test_validate_edition_tuples():
    assert validate_edition("Server") == ("server", None)
    assert validate_edition("x")[0] is None


def test_validate_layout_tuples():
    assert validate_layout("AB-SPLIT") == ("ab-split", None)
    assert validate_layout("x")[0] is None


def test_validate_user_tuples():
    assert validate_user("alice")[0].username == "alice"
    assert validate_user("")[0] is None
    assert validate_user("bad name")[0] is None


def test_validate_network_tuples():
    assert validate_network("dhcp")[0].mode == "dhcp"
    assert validate_network({"mode": "static"})[0] is None


# ---------------------------------------------------------------------------
# Purity: the model performs no I/O
# ---------------------------------------------------------------------------


def test_model_reads_no_environment():
    # If the model touched the environment/filesystem, this would be brittle.
    # We simply assert the wizard can be constructed and driven with no globals
    # in play — a structural guarantee, not an assertion on os.environ.
    import os

    before = dict(os.environ)
    _happy_wizard()
    assert os.environ == before  # env untouched
