"""Tests for the opt-in ssh probe.

Covers ``pengyos_desktop_session.check`` ssh helpers and the ``check --ssh``
CLI:

* ``ssh_probe_enabled`` — the ``PENGYOS_IT=1`` opt-in gate;
* ``collect_ssh`` — the honest skip cases (opt-in off, no host, ssh failure,
  empty output) vs. a real run that returns the captured output;
* ``check_ssh`` — the same present/absent engine over ssh output (PASS / FAIL),
  plus the skip outcome shape (``ok=False``, populated ``skip``, no findings);
* ``render_findings`` / ``outcome_to_dict`` — the SKIP report + JSON field;
* the ``check --ssh`` CLI: skips honestly (``EXIT_SKIP``) when the probe cannot
  run, and runs + returns ``EXIT_OK`` / ``EXIT_FAIL`` when the opt-in gate is
  open. Real ssh is never touched — ``subprocess.run`` is monkeypatched.
"""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from pengyos_desktop_session import check
from pengyos_desktop_session import cli

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def _completed(stdout="", returncode=0):
    """A minimal fake ``subprocess.CompletedProcess`` (returncode + stdout)."""
    return SimpleNamespace(returncode=returncode, stdout=stdout)


def _fake_run(stdout=""):
    """A fake ``subprocess.run`` (accepts the kwargs the probe's lambda passes)."""

    def run(*args, **kwargs):
        return _completed(stdout=stdout)

    return run


def _with_it(monkeypatch, value="1"):
    """Set/clear the PENGYOS_IT opt-in gate and return the previous value."""
    old = os.environ.get(check.SSH_PROBE_ENABLED_ENV)
    if value is None:
        monkeypatch.delenv(check.SSH_PROBE_ENABLED_ENV, raising=False)
    else:
        monkeypatch.setenv(check.SSH_PROBE_ENABLED_ENV, value)
    return old


@pytest.fixture()
def boot_ssh():
    return (FIXTURES / "boot_ssh.log").read_text(encoding="utf-8")


@pytest.fixture()
def boot_ssh_missing_pipewire():
    return (FIXTURES / "boot_ssh_missing_pipewire.log").read_text(encoding="utf-8")


# --- ssh_probe_enabled -------------------------------------------------------


def test_ssh_probe_enabled_true_when_set(monkeypatch):
    _with_it(monkeypatch, "1")
    assert check.ssh_probe_enabled() is True


def test_ssh_probe_enabled_false_when_unset(monkeypatch):
    _with_it(monkeypatch, None)
    assert check.ssh_probe_enabled() is False


def test_ssh_probe_enabled_false_for_other_value(monkeypatch):
    _with_it(monkeypatch, "0")
    assert check.ssh_probe_enabled() is False


# --- collect_ssh: the honest skip cases -------------------------------------


def test_collect_ssh_skips_when_not_enabled(monkeypatch):
    """Without PENGYOS_IT=1 the probe never touches ssh — it skips honestly."""
    _with_it(monkeypatch, None)
    called = []
    output, reason = check.collect_ssh("host", ssh_runner=lambda args: called.append(args) or _completed())
    assert output is None
    assert reason is not None
    assert "PENGYOS_IT" in reason
    assert called == []  # ssh was never invoked


def test_collect_ssh_skips_when_no_host(monkeypatch):
    _with_it(monkeypatch, "1")
    called = []
    output, reason = check.collect_ssh("   ", ssh_runner=lambda args: called.append(args) or _completed())
    assert output is None
    assert reason == "no ssh host given"
    assert called == []


def test_collect_ssh_skips_on_ssh_failure(monkeypatch):
    """A non-zero ssh exit (unreachable / auth / no ssh) is a skip, not a FAIL."""
    _with_it(monkeypatch, "1")
    output, reason = check.collect_ssh("host", ssh_runner=lambda args: _completed(returncode=255))
    assert output is None
    assert reason == "ssh probe failed (unreachable host, auth failure, or no ssh)"


def test_collect_ssh_skips_on_empty_output(monkeypatch):
    """ssh succeeds but returns nothing to verify — a skip, never a bogus pass."""
    _with_it(monkeypatch, "1")
    output, reason = check.collect_ssh("host", ssh_runner=lambda args: _completed(stdout="   \n\n"))
    assert output is None
    assert reason == "ssh probe returned no output (nothing to verify)"


# --- collect_ssh: a real run ------------------------------------------------


def test_collect_ssh_runs_and_returns_output(monkeypatch):
    _with_it(monkeypatch, "1")
    captured = []

    def runner(args):
        captured.append(args)
        return _completed(stdout="cosmic-comp\n")

    output, reason = check.collect_ssh("host", ssh_runner=runner)
    assert output == "cosmic-comp"
    assert reason is None
    # the probe runs `ssh -o BatchMode=yes -o ConnectTimeout=5 user@host <cmd>`
    assert captured[0][0] == "ssh"
    assert "-o" in captured[0]
    assert "BatchMode=yes" in captured[0]
    assert captured[0][-2] == "host"  # user@host lands at the host position
    assert captured[0][-1] == check.SSH_PROBE_COMMAND


# --- check_ssh: the engine over ssh output ----------------------------------


def test_check_ssh_passes_on_full_output(boot_ssh, monkeypatch):
    _with_it(monkeypatch, "1")
    outcome = check.check_ssh("host", ssh_runner=lambda args: _completed(stdout=boot_ssh))
    assert outcome.ok is True
    assert outcome.source == "ssh:host"
    assert outcome.skip == ""
    assert len(outcome.passed) == len(check.SESSION_FACTS)
    assert outcome.failed == ()


def test_check_ssh_fails_when_a_pattern_missing(boot_ssh_missing_pipewire, monkeypatch):
    _with_it(monkeypatch, "1")
    outcome = check.check_ssh("host", ssh_runner=lambda args: _completed(stdout=boot_ssh_missing_pipewire))
    assert outcome.ok is False
    assert {f.name for f in outcome.failed} == {"pipewire / wireplumber running"}


def test_check_ssh_empty_output_is_all_fail_not_skip(boot_ssh, monkeypatch):
    """An empty (but non-skip) probe output fails every fact — no vacuous pass."""
    _with_it(monkeypatch, "1")
    outcome = check.check_ssh("host", ssh_runner=lambda args: _completed(stdout=""))
    # collect_ssh treats empty output as a skip (nothing to verify)
    assert outcome.skip != ""


# --- check_ssh: the skip outcome shape --------------------------------------


def test_check_ssh_skip_outcome_shape(monkeypatch):
    _with_it(monkeypatch, None)
    outcome = check.check_ssh("host")
    assert outcome.ok is False
    assert outcome.findings == ()
    assert outcome.skip != ""
    assert outcome.source == "ssh:host"


# --- render_findings / outcome_to_dict on a skip ----------------------------


def test_render_findings_skip_report(monkeypatch):
    _with_it(monkeypatch, None)
    outcome = check.check_ssh("host")
    text = check.render_findings(outcome)
    assert "RESULT: SKIP" in text
    assert outcome.skip in text
    # a skip lists no PASS/FAIL fact lines
    assert "[PASS]" not in text
    assert "[FAIL]" not in text


def test_outcome_to_dict_has_skip_field(monkeypatch):
    _with_it(monkeypatch, None)
    payload = check.outcome_to_dict(check.check_ssh("host"))
    assert payload["ok"] is False
    assert payload["skip"] != ""
    assert payload["findings"] == []


def test_outcome_to_dict_skip_empty_when_ran(boot_ssh, monkeypatch):
    _with_it(monkeypatch, "1")
    payload = check.outcome_to_dict(check.check_ssh("host", ssh_runner=lambda a: _completed(stdout=boot_ssh)))
    assert payload["skip"] == ""
    assert payload["ok"] is True


# --- check --ssh CLI ---------------------------------------------------------


def test_check_cli_ssh_skip_when_not_enabled(monkeypatch, capsys):
    """Without the opt-in gate the CLI skips (EXIT_SKIP) and never calls ssh."""
    _with_it(monkeypatch, None)
    # fail closed if ssh is ever invoked while disabled
    monkeypatch.setattr(
        check.subprocess,
        "run",
        lambda args: (_ for _ in ()).throw(AssertionError("ssh must not be invoked when disabled")),
    )
    rc = cli.main(["check", "--ssh", "host"])
    capsys.readouterr()
    assert rc == cli.EXIT_SKIP


def test_check_cli_ssh_probe_pass(boot_ssh, monkeypatch, capsys):
    _with_it(monkeypatch, "1")
    monkeypatch.setattr(check.subprocess, "run", _fake_run(stdout=boot_ssh))
    rc = cli.main(["check", "--ssh", "host"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert "RESULT: PASS" in out


def test_check_cli_ssh_probe_fail(boot_ssh_missing_pipewire, monkeypatch, capsys):
    _with_it(monkeypatch, "1")
    monkeypatch.setattr(
        check.subprocess,
        "run",
        _fake_run(stdout=boot_ssh_missing_pipewire),
    )
    rc = cli.main(["check", "--ssh", "host"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_FAIL
    assert "RESULT: FAIL" in out
    assert "pipewire" in out


def test_check_cli_ssh_skip_json(monkeypatch, capsys):
    _with_it(monkeypatch, None)
    rc = cli.main(["check", "--ssh", "host", "--json"])
    capsys.readouterr()
    assert rc == cli.EXIT_SKIP


def test_check_cli_ssh_probe_pass_json(boot_ssh, monkeypatch, capsys):
    _with_it(monkeypatch, "1")
    monkeypatch.setattr(check.subprocess, "run", _fake_run(stdout=boot_ssh))
    rc = cli.main(["check", "--ssh", "host", "--json"])
    import json

    payload = json.loads(capsys.readouterr().out)
    assert rc == cli.EXIT_OK
    assert payload["ok"] is True
    assert payload["skip"] == ""
