"""Lockstep tests for the port-pin operator runbook.

The runbook (``docs/port-pin-transition.md``) is the permanent,
repo-checked home of the port-pin transition recipe: the commands, the
files each stage touches, the failure modes, and the anchor tests that
go red the moment the port exists upstream.  Every one of those
surfaces is locked to the code constants and the live repo here, so a
drifting runbook (a renamed command, a missed transition file, a
port that crept in) fails the suite loudly instead of silently
misleading the next operator.
"""

import re
from pathlib import Path

from pengyos_apparmor import portdocsync, portpin

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNBOOK = REPO_ROOT / "docs" / "port-pin-transition.md"

#: The commands the runbook must name (every command the recipe uses).
RUNBOOK_COMMANDS = (
    "find-model-server-port",
    "plan-port-pin",
    "port-pin-status",
    "apply-port-pin",
    "sync-port-pin-docs",
)

#: The anchor tests that flip red the moment the port exists upstream —
#: the runbook names them so the operator knows what to update.
ANCHOR_TESTS = (
    "test_cli_default_root_is_the_pengyos_workspace_and_still_reports_blocked",
    "test_cli_blocked_on_the_live_repo_and_workspace",
    "test_cli_live_workspace_stays_blocked_and_writes_nothing",
    "test_live_docs_carry_the_pre_sync_anchors",
    "test_committed_grant_is_the_unbound_port_form",
)


def _text() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def test_runbook_exists_and_is_nonempty():
    assert RUNBOOK.is_file()
    assert len(_text().strip()) > 500


def test_runbook_names_every_transition_command():
    text = _text()
    for command in RUNBOOK_COMMANDS:
        assert command in text, f"runbook must name the `{command}` command"
    # The reviewable alternative is the patch form specifically.
    assert "plan-port-pin <N> --patch" in text


def test_runbook_names_every_transition_and_doc_file():
    text = _text()
    for rel in portpin.PORT_PIN_TRANSITION_FILES:
        assert rel in text, f"runbook must name transition file {rel}"
    for rel in portdocsync.PORT_PIN_DOC_FILES:
        assert rel in text, f"runbook must name doc surface {rel}"
    # Named next to the constants they mirror (lockstep, not prose).
    assert "PORT_PIN_TRANSITION_FILES" in text
    assert "PORT_PIN_DOC_FILES" in text


def test_runbook_contains_no_guessed_port():
    """The never-guess rule is the whole point — the runbook itself must
    not carry a port: only the ``<N>`` placeholder."""
    text = _text()
    assert "port=<N>" in text, "the <N> placeholder must be used"
    assert "<N>" in text
    # No `port=<digits>` (pinned grant form) anywhere in the runbook.
    assert not re.search(r"port=\d", text), "runbook must not name a port"
    # No `port NNNN` evidence-style mention either.
    assert not re.search(r"\bport\s+\d{2,5}\b", text), (
        "runbook must not name a port"
    )
    # And no bare 5-digit number (all plausible default ports are 4-5
    # digits; 11434 is the classic temptation).
    assert not re.search(r"(?<![\d.])11434(?![\d.])", text)


def test_runbook_sequence_is_in_order():
    """Steps must appear in the documented order: scan -> apply -> docs
    sync -> suite -> checklist flip."""
    text = _text()
    positions = []
    for needle in (
        "find-model-server-port",
        "apply-port-pin",
        "sync-port-pin-docs",
        "pytest -q",
        "validate --strict profiles/",
    ):
        idx = text.index(needle)
        positions.append((needle, idx))
    order = [idx for _, idx in positions]
    assert order == sorted(order), (
        "the runbook's transition sequence is out of order: "
        + ", ".join(f"{n}@{i}" for n, i in positions)
    )
    # The checklist-flip step (the LAST mention of TODO.md —
    # the title paragraph names it earlier) comes after the suite step.
    assert text.rindex("TODO.md") > order[-1]


def test_runbook_names_every_red_anchor_test():
    text = _text()
    for name in ANCHOR_TESTS:
        assert name in text, f"runbook must name the anchor test {name}"


def test_runbook_documents_every_probe_state_and_exit_codes():
    text = _text()
    # Every state the port-pin-status probe can report.
    for state in ("blocked", "ready", "pinned", "partial"):
        assert state in text, f"runbook must document the `{state}` state"
    # The exit codes the gate and the probe use.
    assert re.search(r"exits `3`", text), "blocked/no-evidence exit 3"
    assert re.search(r"exit `1`", text) or "exits `1`" in text, (
        "partial / docs-gate exit 1"
    )
    assert "exit `2`" in text, "drift exit 2"
    assert re.search(r"exits 0", text), "strict-clean exit 0"


def test_runbook_documents_the_failure_modes():
    text = _text()
    assert "never-guess" in text
    assert "port-pin-status" in text  # the partial-state detector
    assert "one-way" in text.lower(), "the one-way transition property"
    assert "git apply" in text, "the reviewable alternative path"


def test_runbook_documents_the_staged_atomic_write_side():
    """The half-applied failure mode must match the hardened writer:
    the commands commit staged-atomically (a `*.pin-tmp` staging file +
    an atomic rename), an interruption never leaves a *torn* file, and
    an interrupted docs sync is finished by an idempotent re-run."""
    text = _text()
    assert "staged-atomically" in text
    assert ".pin-tmp" in text, "the staging file suffix must be named"
    assert "torn" in text, "the never-torn invariant must be stated"
    assert "idempotent" in text, "the finishable docs-sync re-run"
    # The interrupted-commit exit code and detector stay named.
    assert "exit `2`" in text
    assert "partial" in text


def test_readme_points_at_the_runbook():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/port-pin-transition.md" in readme


def test_runbook_lives_next_to_the_transition_tooling():
    """The runbook sits under docs/ in the repo root — not inside
    profiles/ (the validator would treat it as a profile) and not in a
    test fixture."""
    assert RUNBOOK.parent.name == "docs"
    assert RUNBOOK.parent.is_relative_to(REPO_ROOT)
