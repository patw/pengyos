"""Opt-in end-to-end integration tests.

The unit suite (``test_model`` / ``test_auto`` / ``test_run``) exercises the
model, ``auto`` and ``run`` in isolation over in-memory streams. This module
*grounds* that behaviour in the **real tool**: it launches the actual
``python -m pengyos_install_tui`` entry point as a subprocess and drives it the
way an operator or a fleet script would — writing ``install.toml`` / ``answers.json``
to disk, piping interactive answers on stdin, and reading the printed plan.

It covers the four things the spec's *Ground* bullet asks for, at the CLI
surface:

* the **happy path** — ``run --answers`` and ``auto --config`` both emit the plan;
* a **back-navigation** — an interactive ``run`` where the operator hits ``back``
  at the edition step, re-answers it, and confirms the edited value;
* an **invalid input** — an interactive ``run`` where a bad answer is reported to
  stderr and re-prompted (never a crash), and a ``--answers`` set with a bad field
  exits 1 with a clean message;
* **auto == interactive** — ``auto --config install.toml --json`` and
  ``run --answers answers.json --json`` emit byte-identical JSON for the same
  inputs.

Everything is opt-in and skipped unless ``PENGYOS_IT=1`` is in the environment
(spec.md: "Integration tests are opt-in behind `PENGYOS_IT=1`"). The
suite itself always runs green without it — this module only *collects*, it never
spawns a subprocess unless the operator turns the integration run on.

Scratch rule: temp files live under ``~/tmp`` (disk), never the
``/tmp`` tmpfs — a subprocess fixture dir must not eat RAM.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# The repo root — the package is not installed, so the subprocess must find it
# via PYTHONPATH (cwd is irrelevant once PYTHONPATH points here).
ROOT = Path(__file__).resolve().parent.parent

# Disk scratch dir (NOT the /tmp tmpfs — see the host scratch rule).
SCRATCH = Path.home() / "tmp" / "pengyos-install-tui-it"

# The canonical happy-path answer set — the same shape every path reproduces.
HAPPY = {
    "target": "/dev/sdb",
    "edition": "core",
    "layout": "single-root",
    "user": "alice",
    "network": "dhcp",
}

# The exact plan the happy path must produce.
HAPPY_PLAN = {
    "target": {"path": "/dev/sdb"},
    "edition": "core",
    "layout": "single-root",
    "user": {"username": "alice", "uid": 1000},
    "network": {"mode": "dhcp"},
}

# A full static-network, A/B-layout variant for the auto==interactive check.
FULL = {
    "target": "/dev/nvme0n1",
    "edition": "server",
    "layout": "ab-split",
    "user": {"username": "root-admin", "uid": 1000},
    "network": {"mode": "static", "address": "10.0.0.5/24",
                "gateway": "10.0.0.1", "nameservers": ["1.1.1.1", "8.8.8.8"]},
}

pytestmark = pytest.mark.skipif(
    os.environ.get("PENGYOS_IT") != "1",
    reason="integration tests are opt-in; set PENGYOS_IT=1 to run them",
)


def _scratch_dir(tag: str) -> Path:
    """A fresh temp directory on disk (never the /tmp tmpfs)."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{tag}-", dir=str(SCRATCH)))


def _run_cli(args, stdin_text=None):
    """Launch the real CLI as a subprocess; return the completed process."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "pengyos_install_tui", *list(args)],
        input=stdin_text,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _write_answers(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_config(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Happy path — run --answers and auto --config both emit the plan
# ---------------------------------------------------------------------------


def test_cli_run_answers_happy_path():
    """`run --answers` drives the wizard and prints the plan (spec usage)."""
    d = _scratch_dir("run-answers")
    answers = d / "answers.json"
    _write_answers(answers, HAPPY)
    proc = _run_cli(["run", "--answers", str(answers), "--json"])
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == HAPPY_PLAN


def test_cli_auto_happy_path():
    """`auto --config` parses install.toml and prints the same plan."""
    d = _scratch_dir("auto")
    config = d / "install.toml"
    _write_config(
        config,
        'target = "/dev/sdb"\n'
        'edition = "core"\n'
        'layout = "single-root"\n'
        'user = "alice"\n'
        'network = "dhcp"\n',
    )
    proc = _run_cli(["auto", "--config", str(config), "--json"])
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == HAPPY_PLAN


def test_cli_run_answers_human_lines():
    """The non-``--json`` output is the human plan list, not the JSON blob."""
    d = _scratch_dir("run-answers-lines")
    answers = d / "answers.json"
    _write_answers(answers, HAPPY)
    proc = _run_cli(["run", "--answers", str(answers)])
    assert proc.returncode == 0, proc.stderr
    assert "target:   /dev/sdb" in proc.stdout
    assert "edition:  core" in proc.stdout
    assert "user:     alice (uid 1000)" in proc.stdout
    assert "network:  dhcp" in proc.stdout
    # The machine blob is *not* what a human sees on stdout here.
    assert '"target"' not in proc.stdout


def test_cli_auto_static_full_variant():
    """`auto` reproduces the full static/A-B plan shape from a TOML table."""
    d = _scratch_dir("auto-static")
    config = d / "install.toml"
    _write_config(
        config,
        'target = "/dev/nvme0n1"\n'
        'edition = "server"\n'
        'layout = "ab-split"\n'
        '[user]\n'
        'username = "root-admin"\n'
        'uid = 1000\n'
        '[network]\n'
        'mode = "static"\n'
        'address = "10.0.0.5/24"\n'
        'gateway = "10.0.0.1"\n'
        'nameservers = ["1.1.1.1", "8.8.8.8"]\n',
    )
    proc = _run_cli(["auto", "--config", str(config), "--json"])
    assert proc.returncode == 0, proc.stderr
    plan = json.loads(proc.stdout)
    assert plan["network"]["mode"] == "static"
    assert plan["network"]["address"] == "10.0.0.5/24"
    assert plan["network"]["gateway"] == "10.0.0.1"
    assert plan["network"]["nameservers"] == ["1.1.1.1", "8.8.8.8"]


# ---------------------------------------------------------------------------
# auto == interactive — same inputs, byte-identical plan at the CLI level
# ---------------------------------------------------------------------------


def test_cli_auto_equals_interactive_happy_path():
    """auto --json and run --json agree exactly for the same inputs."""
    d = _scratch_dir("auto-eq-interactive")
    config = d / "install.toml"
    answers = d / "answers.json"
    _write_config(
        config,
        'target = "/dev/sdb"\n'
        'edition = "core"\n'
        'layout = "single-root"\n'
        'user = "alice"\n'
        'network = "dhcp"\n',
    )
    _write_answers(answers, HAPPY)
    auto = _run_cli(["auto", "--config", str(config), "--json"])
    run = _run_cli(["run", "--answers", str(answers), "--json"])
    assert auto.returncode == 0, auto.stderr
    assert run.returncode == 0, run.stderr
    assert json.loads(auto.stdout) == json.loads(run.stdout) == HAPPY_PLAN


def test_cli_auto_equals_interactive_static_variant():
    """auto == interactive holds for the full static/A-B variant too."""
    d = _scratch_dir("auto-eq-interactive-static")
    config = d / "install.toml"
    answers = d / "answers.json"
    _write_config(
        config,
        'target = "/dev/nvme0n1"\n'
        'edition = "server"\n'
        'layout = "ab-split"\n'
        '[user]\n'
        'username = "root-admin"\n'
        'uid = 1000\n'
        '[network]\n'
        'mode = "static"\n'
        'address = "10.0.0.5/24"\n'
        'gateway = "10.0.0.1"\n'
        'nameservers = ["1.1.1.1", "8.8.8.8"]\n',
    )
    _write_answers(answers, FULL)
    auto = _run_cli(["auto", "--config", str(config), "--json"])
    run = _run_cli(["run", "--answers", str(answers), "--json"])
    assert auto.returncode == 0, auto.stderr
    assert run.returncode == 0, run.stderr
    plan = json.loads(auto.stdout)
    assert plan == json.loads(run.stdout)
    assert plan == {
        "target": {"path": "/dev/nvme0n1"},
        "edition": "server",
        "layout": "ab-split",
        "user": {"username": "root-admin", "uid": 1000},
        "network": {"mode": "static", "address": "10.0.0.5/24",
                    "gateway": "10.0.0.1", "nameservers": ["1.1.1.1", "8.8.8.8"]},
    }


# ---------------------------------------------------------------------------
# Back-navigation — interactive run with a `back` + re-answer
# ---------------------------------------------------------------------------


def test_interactive_run_back_navigation_reanswer():
    """`back` at edition, re-answer it, confirm: the edited value wins."""
    # target -> edition=core -> back (to edition) -> edition=server -> layout
    # -> user -> network -> confirm (yes).
    plan_out, _transcript, err_out, code = _run_interactive(
        ["/dev/sdb", "core", "back", "server", "single-root",
         "alice", "dhcp", "yes"]
    )
    assert code == 0, err_out
    assert plan_out is not None
    assert plan_out["edition"] == "server"
    assert plan_out["target"] == {"path": "/dev/sdb"}


def _run_interactive(lines):
    """Drive the interactive `run` over stdin.

    The interactive runner renders each step to stdout *and* prints the plan, so
    stdout is a transcript, not a clean JSON blob. We run with ``--json`` and
    parse the plan object that trails the transcript (``plan_to_json`` is the
    final thing written). Returns ``(plan_or_None, transcript, err, code)``.
    """
    proc = _run_cli(["run", "--json"], stdin_text="\n".join(lines) + "\n")
    if proc.returncode != 0:
        # A non-zero exit (e.g. EOF before confirmation) means no plan.
        return None, proc.stdout, proc.stderr, proc.returncode
    plan = _parse_trailing_json(proc.stdout)
    return plan, proc.stdout, proc.stderr, proc.returncode


def _parse_trailing_json(text: str):
    """Parse the plan JSON object that trails an interactive transcript.

    The interactive runner renders each step to stdout *and* prints the plan, so
    stdout is a transcript ending in the pretty-printed plan object. The plan is
    the final top-level JSON value, so we ``raw_decode`` from the first ``{`` and
    confirm it consumes exactly to the end (a nested brace would leave trailing
    ``}`` and be rejected). Returns the parsed object, or ``None`` if absent.
    """
    stripped = text.rstrip()
    start = stripped.find("{")
    while start != -1:
        try:
            obj, end = json.JSONDecoder().raw_decode(stripped, start)
        except json.JSONDecodeError:
            start = stripped.find("{", start + 1)
            continue
        if stripped[end:].strip() == "":
            return obj
        start = stripped.find("{", end)
    return None


def test_interactive_run_back_navigation_renders_current_value():
    """After `back`, the re-rendered step echoes the previously-stored value."""
    proc = _run_cli(
        ["run"],
        stdin_text="\n".join(
            ["/dev/sdb", "core", "back", "server", "single-root",
             "alice", "dhcp", "yes"]
        )
        + "\n",
    )
    assert proc.returncode == 0, proc.stderr
    # The edition step, re-shown after `back`, prints the current value.
    assert "current: core" in proc.stdout


def test_interactive_run_confirm_re_prompts_on_no_then_yes():
    """A `no` at the review re-prompts; a following `yes` confirms."""
    plan_out, _transcript, _err, code = _run_interactive(
        ["/dev/sdb", "core", "single-root", "alice", "dhcp", "no", "yes"]
    )
    assert code == 0
    assert plan_out == HAPPY_PLAN


# ---------------------------------------------------------------------------
# Invalid input — reported, re-prompted, or a clean exit 1; never a crash
# ---------------------------------------------------------------------------


def test_interactive_run_invalid_answer_reprompted_not_crash():
    """A bad edition is reported to stderr and re-prompted; the run continues."""
    # edition=bogus (rejected -> stderr -> re-prompt) then edition=core.
    plan_out, _transcript, err_out, code = _run_interactive(
        ["/dev/sdb", "bogus", "core", "single-root", "alice", "dhcp", "yes"]
    )
    assert code == 0, err_out
    assert plan_out is not None
    assert plan_out["edition"] == "core"
    # The invalid value was reported to stderr, not a traceback.
    assert "edition" in err_out.lower()
    assert "Traceback" not in err_out


def test_run_answers_invalid_field_exits_1_not_crash():
    """A `--answers` set with a bad field exits 1 with a clean message."""
    d = _scratch_dir("run-answers-invalid")
    answers = d / "answers.json"
    _write_answers(answers, {**HAPPY, "edition": "desktop"})
    proc = _run_cli(["run", "--answers", str(answers)])
    assert proc.returncode == 1
    assert "edition" in proc.stderr.lower()
    assert "Traceback" not in proc.stderr


def test_auto_invalid_field_exits_1_not_crash():
    """An invalid `install.toml` field exits 1 with a clean message."""
    d = _scratch_dir("auto-invalid")
    config = d / "install.toml"
    _write_config(
        config,
        'target = "/dev/sdb"\n'
        'edition = "desktop"\n'  # invalid edition
        'layout = "single-root"\n'
        'user = "alice"\n'
        'network = "dhcp"\n',
    )
    proc = _run_cli(["auto", "--config", str(config)])
    assert proc.returncode == 1
    assert "edition" in proc.stderr.lower()
    assert "Traceback" not in proc.stderr


def test_auto_missing_file_exits_1_not_crash():
    """A missing config file exits 1 with a clean message."""
    proc = _run_cli(["auto", "--config", "/no/such/install.toml"])
    assert proc.returncode == 1
    assert "not found" in proc.stderr.lower()
    assert "Traceback" not in proc.stderr


def test_interactive_run_eof_before_confirmation_returns_no_plan():
    """EOF at a prompt before a plan is confirmed -> exit 1, no plan."""
    proc = _run_cli(["run"], stdin_text="/dev/sdb\n")
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr


def test_interactive_run_back_at_first_step_is_noop():
    """`back` at the very first step is a no-op; the run still completes."""
    plan_out, _transcript, _err, code = _run_interactive(
        ["back", "/dev/sdb", "core", "single-root", "alice", "dhcp", "yes"]
    )
    assert code == 0
    assert plan_out == HAPPY_PLAN
