"""Tests for the interactive ``run`` front-end.

These cover the spec's ``run`` contract — *drive the model on stdin/stdout,
rendering each step and applying answers*, scriptable via ``--answers`` — plus
the CLI surface:

* :func:`run_replay` applies a JSON answer set through the wizard (the
  ``--answers`` path);
* :func:`load_answers` reads/parses the answers file (a reported error, never a
  crash);
* :func:`render_step` / :func:`render_plan` render each step and the review;
* :class:`InteractiveRunner` drives the wizard over injectable streams: the happy
  path, back-navigation, invalid-answer re-prompt, the final ``yes``, and an EOF
  that ends the loop without a plan;
* the ``run`` CLI handler (``--answers``, ``--json``, and the error exits).

Everything is offline: streams are in-memory :class:`io.StringIO` and
block-device facts are injected through a fixture ``probe``.
"""

import contextlib
import io
import json

import pytest

from pengyos_install_tui import model
from pengyos_install_tui.auto import ConfigError
from pengyos_install_tui.cli import ExitCode, main
from pengyos_install_tui.model import ValidationError, Wizard
from pengyos_install_tui.run import (
    BACK_COMMAND,
    InteractiveRunner,
    load_answers,
    preview_plan,
    render_plan,
    render_step,
    run,
    run_replay,
)

# The canonical happy-path answer set (see tests/test_model.py / test_auto.py).
HAPPY = {
    "target": "/dev/sdb",
    "edition": "core",
    "layout": "single-root",
    "user": "alice",
    "network": "dhcp",
}

# The canonical confirmed plan (the exact shape every path must reproduce).
HAPPY_PLAN = {
    "target": {"path": "/dev/sdb"},
    "edition": "core",
    "layout": "single-root",
    "user": {"username": "alice", "uid": 1000},
    "network": {"mode": "dhcp"},
}

# A full static-network, A/B layout variant.
FULL = {
    "target": "/dev/nvme0n1",
    "edition": "server",
    "layout": "ab-split",
    "user": {"username": "root-admin", "uid": 1000},
    "network": {"mode": "static", "address": "10.0.0.5/24",
                "gateway": "10.0.0.1", "nameservers": ["1.1.1.1", "8.8.8.8"]},
}


# ---------------------------------------------------------------------------
# run_replay — the --answers path
# ---------------------------------------------------------------------------


def test_run_replay_happy_path():
    assert run_replay(HAPPY).to_dict() == HAPPY_PLAN


def test_run_replay_static_full_variant():
    plan = run_replay(FULL)
    assert plan.to_dict() == {
        "target": {"path": "/dev/nvme0n1"},
        "edition": "server",
        "layout": "ab-split",
        "user": {"username": "root-admin", "uid": 1000},
        "network": {"mode": "static", "address": "10.0.0.5/24",
                    "gateway": "10.0.0.1", "nameservers": ["1.1.1.1", "8.8.8.8"]},
    }


def test_run_replay_equals_interactive():
    """The --answers replay equals the interactive answer set for same inputs."""
    w = Wizard()
    w.answer("target", HAPPY["target"])
    w.answer("edition", HAPPY["edition"])
    w.answer("layout", HAPPY["layout"])
    w.answer("user", HAPPY["user"])
    w.answer("network", HAPPY["network"])
    interactive = w.confirm()
    assert run_replay(HAPPY).to_dict() == interactive.to_dict()


def test_run_replay_accepts_any_order():
    # The cursor is irrelevant: confirm() only needs every step answered.
    reversed_answers = {
        "network": "dhcp",
        "user": "alice",
        "layout": "single-root",
        "edition": "core",
        "target": "/dev/sdb",
    }
    assert run_replay(reversed_answers).to_dict() == HAPPY_PLAN


def test_run_replay_missing_field_raises_config_error():
    cfg = {k: v for k, v in HAPPY.items() if k != "network"}  # network removed
    with pytest.raises(ConfigError) as e:
        run_replay(cfg)
    # network missing -> ConfigError naming it
    assert "network" in e.value.message


def test_run_replay_invalid_field_raises_validation_error():
    with pytest.raises(ValidationError) as e:
        run_replay({**HAPPY, "edition": "desktop"})
    assert "edition" in e.value.step or "edition" in e.value.message


def test_run_replay_probe_can_reject_target():
    def probe(path):
        return "not a block device"

    with pytest.raises(ValidationError):
        run_replay(HAPPY, probe=probe)


def test_run_replay_probe_can_accept_target():
    def probe(path):
        return None

    assert run_replay(HAPPY, probe=probe).to_dict() == HAPPY_PLAN


def test_run_replay_reads_no_environment():
    import os

    before = dict(os.environ)
    run_replay(HAPPY)
    assert os.environ == before


# ---------------------------------------------------------------------------
# load_answers — the answers-file reader
# ---------------------------------------------------------------------------


def _write(tmp_path, text, name="answers.json"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_load_answers_parses_json(tmp_path):
    path = _write(
        tmp_path,
        json.dumps(HAPPY),
    )
    assert load_answers(path) == HAPPY


def test_load_answers_missing_file_reports_error_not_crash():
    with pytest.raises(ConfigError) as e:
        load_answers("/no/such/answers.json")
    assert "not found" in e.value.message
    assert e.value.path == "/no/such/answers.json"


def test_load_answers_invalid_json_reports_error_not_crash(tmp_path):
    path = _write(tmp_path, "{this is not json")
    with pytest.raises(ConfigError) as e:
        load_answers(path)
    assert "invalid JSON" in e.value.message
    assert e.value.path == path


def test_load_answers_non_table_reports_error_not_crash(tmp_path):
    path = _write(tmp_path, json.dumps(["target", "edition"]))
    with pytest.raises(ConfigError) as e:
        load_answers(path)
    assert "table" in e.value.message


def test_load_answers_reports_path_on_missing_file():
    with pytest.raises(ConfigError) as e:
        load_answers("/no/such/answers.json")
    assert e.value.path == "/no/such/answers.json"


# ---------------------------------------------------------------------------
# render_step / render_plan — the rendering
# ---------------------------------------------------------------------------


def test_render_step_target_has_progress_and_hint():
    state = model.StepState(key="target", index=0, total=6, answer=None, done=False)
    text = render_step(state)
    assert "Step 1/6" in text
    assert "Target disk" in text
    assert "/dev/sdb" in text  # the hint points at an example device


def test_render_step_closed_sets_list_valid_values():
    state = model.StepState(key="edition", index=1, total=6, answer=None, done=False)
    text = render_step(state)
    assert "valid values: core, server" in text


def test_render_step_layout_lists_both_options():
    state = model.StepState(key="layout", index=2, total=6, answer=None, done=False)
    assert "valid values: single-root, ab-split" in render_step(state)


def test_render_step_echoes_current_answer():
    state = model.StepState(key="edition", index=1, total=6, answer="core", done=True)
    assert "current: core" in render_step(state)


def test_render_step_no_current_when_unanswered():
    state = model.StepState(key="edition", index=1, total=6, answer=None, done=False)
    assert "current:" not in render_step(state)


def test_render_plan_lists_all_fields():
    plan = run_replay(HAPPY)
    text = render_plan(plan)
    assert "Confirm" in text
    assert "target:   /dev/sdb" in text
    assert "edition:  core" in text
    assert "user:     alice (uid 1000)" in text
    assert "network:  dhcp" in text


def test_preview_plan_matches_confirm():
    w = Wizard()
    for step, value in HAPPY.items():
        w.answer(step, value)
    # At confirm the wizard is not yet confirmed; the preview equals the plan.
    assert preview_plan(w).to_dict() == run_replay(HAPPY).to_dict()


# ---------------------------------------------------------------------------
# InteractiveRunner — the stdin/stdout prompt loop
# ---------------------------------------------------------------------------


def _interactive(answers_lines):
    """Drive the interactive loop over in-memory streams; return (plan, out)."""
    inp = io.StringIO("\n".join(answers_lines) + "\n")
    out = io.StringIO()
    err = io.StringIO()
    plan = InteractiveRunner(inp, out, err).run()
    return plan, out.getvalue(), err.getvalue()


def test_interactive_happy_path():
    plan, out, err = _interactive(
        ["/dev/sdb", "core", "single-root", "alice", "dhcp", "yes"]
    )
    assert plan is not None
    assert plan.to_dict() == HAPPY_PLAN
    # Each step was rendered.
    assert "Step 1/6" in out
    assert "Step 6/6" in out


def test_interactive_replays_same_plan_as_replay():
    plan, _out, _err = _interactive(
        ["/dev/sdb", "core", "single-root", "alice", "dhcp", "yes"]
    )
    assert plan.to_dict() == run_replay(HAPPY).to_dict()


def test_back_command_constant_is_back():
    assert BACK_COMMAND == "back"


def test_interactive_back_navigation_reanswer():
    # target, edition=core, back (to edition), edition=server, then finish.
    plan, out, err = _interactive(
        ["/dev/sdb", "core", BACK_COMMAND, "server", "single-root",
         "alice", "dhcp", "yes"]
    )
    assert plan is not None
    assert plan.edition == "server"
    # The edition step was re-rendered after back (showing the current value).
    assert out.count("current: core") == 1


def test_interactive_invalid_answer_reprompted():
    # edition=bogus (rejected, re-prompted) then edition=core again.
    plan, out, err = _interactive(
        ["/dev/sdb", "bogus", "core", "single-root", "alice", "dhcp", "yes"]
    )
    assert plan is not None
    assert plan.edition == "core"
    # The invalid edition was reported to stderr, not a crash.
    assert "edition" in err.lower()


def test_interactive_blank_input_ignored():
    # A blank line at the target prompt is ignored; the real answer follows.
    plan, _out, _err = _interactive(
        ["", "/dev/sdb", "core", "single-root", "alice", "dhcp", "yes"]
    )
    assert plan is not None
    assert plan.to_dict() == HAPPY_PLAN


def test_interactive_confirm_no_reprompts():
    # "no" at the confirm review re-prompts; "yes" confirms.
    plan, out, err = _interactive(
        ["/dev/sdb", "core", "single-root", "alice", "dhcp", "no", "yes"]
    )
    assert plan is not None
    assert plan.to_dict() == HAPPY_PLAN
    # The review was shown twice (once per confirm prompt).
    assert out.count("Confirm") == 2


def test_interactive_eof_before_plan_returns_none():
    # Only the target is answered, then EOF -> no plan.
    inp = io.StringIO("/dev/sdb\n")
    out = io.StringIO()
    err = io.StringIO()
    plan = InteractiveRunner(inp, out, err).run()
    assert plan is None


def test_interactive_eof_at_confirm_returns_none():
    # All answers given, then EOF at the confirm review (no "yes").
    inp = io.StringIO("/dev/sdb\ncore\nsingle-root\nalice\ndhcp\n")
    out = io.StringIO()
    err = io.StringIO()
    plan = InteractiveRunner(inp, out, err).run()
    assert plan is None


def test_interactive_reads_no_environment():
    import os

    before = dict(os.environ)
    _interactive(["/dev/sdb", "core", "single-root", "alice", "dhcp", "yes"])
    assert os.environ == before


# ---------------------------------------------------------------------------
# CLI: ``run [--answers answers.json] [--json]``
# ---------------------------------------------------------------------------


def _run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as exc:  # argparse surface errors
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def test_cli_run_answers_replays(tmp_path):
    path = _write(tmp_path, json.dumps(HAPPY))
    code, out, err = _run_cli("run", "--answers", path)
    assert code == ExitCode.OK
    assert "target:   /dev/sdb" in out
    assert "edition:  core" in out
    assert "user:     alice (uid 1000)" in out


def test_cli_run_answers_json(tmp_path):
    path = _write(tmp_path, json.dumps(HAPPY))
    code, out, err = _run_cli("run", "--answers", path, "--json")
    assert code == ExitCode.OK
    parsed = json.loads(out)
    assert parsed == HAPPY_PLAN


def test_cli_run_answers_static_variant(tmp_path):
    path = _write(tmp_path, json.dumps(FULL))
    code, out, err = _run_cli("run", "--answers", path, "--json")
    assert code == ExitCode.OK
    parsed = json.loads(out)
    assert parsed["network"]["mode"] == "static"
    assert parsed["network"]["nameservers"] == ["1.1.1.1", "8.8.8.8"]


def test_cli_run_answers_missing_file_is_error_not_crash():
    code, out, err = _run_cli("run", "--answers", "/no/such/answers.json")
    assert code == ExitCode.ERROR
    assert "not found" in err


def test_cli_run_answers_invalid_json_is_error_not_crash(tmp_path):
    path = _write(tmp_path, "{not json")
    code, out, err = _run_cli("run", "--answers", path)
    assert code == ExitCode.ERROR
    assert "invalid JSON" in err


def test_cli_run_answers_invalid_field_is_error_not_crash(tmp_path):
    path = _write(tmp_path, json.dumps({**HAPPY, "edition": "desktop"}))
    code, out, err = _run_cli("run", "--answers", path)
    assert code == ExitCode.ERROR
    assert "edition" in err


def test_cli_run_answers_missing_field_is_error_not_crash(tmp_path):
    cfg = {k: v for k, v in HAPPY.items() if k != "network"}  # network removed
    path = _write(tmp_path, json.dumps(cfg))
    code, out, err = _run_cli("run", "--answers", path)
    assert code == ExitCode.ERROR
    assert "network" in err


def test_cli_run_json_matches_auto_plan(tmp_path):
    """run --json emits the same plan the auto path produces (spec contract)."""
    answers = json.dumps(HAPPY)
    toml = (
        'target = "/dev/sdb"\n'
        'edition = "core"\n'
        'layout = "single-root"\n'
        'user = "alice"\n'
        'network = "dhcp"\n'
    )
    answers_path = _write(tmp_path, answers, "answers.json")
    toml_path = _write(tmp_path, toml, "install.toml")
    run_code, run_out, _ = _run_cli("run", "--answers", answers_path, "--json")
    auto_code, auto_out, _ = _run_cli("auto", "--config", toml_path, "--json")
    assert run_code == ExitCode.OK
    assert auto_code == ExitCode.OK
    # The interactive (replay) path and the unattended path agree exactly.
    assert json.loads(run_out) == json.loads(auto_out)


def test_run_help_lists_answers_option():
    code, out, err = _run_cli("run", "--help")
    assert code == ExitCode.OK
    assert "--answers" in out
    assert "--json" in out


# ---------------------------------------------------------------------------
# Purity: run (replay) performs no I/O beyond the injected probe
# ---------------------------------------------------------------------------


def test_run_replay_no_env_side_effects():
    import os

    before = dict(os.environ)
    run(answers=HAPPY)
    assert os.environ == before
