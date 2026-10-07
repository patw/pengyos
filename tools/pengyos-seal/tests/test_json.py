"""Tests for the machine-readable seal result.

Covers the ``--json`` contract: :meth:`SealResult.to_dict` / ``to_json`` emit the
sealed plan, the artifacts signed, the names normalised, the repro status, and
the verifier's checks + verdict + exit code; the ``_verifier_checks`` parser
extracts a structured ``checks`` summary from the verifier's stdout; and
``cmd_run --json`` writes it to a file or stdout (JSON only, never interleaved
with the human lines). Every test injects a :class:`MemoryRunner` — no real
files, network, or root.
"""

from __future__ import annotations

import argparse
import json

import pytest

from pengyos_seal import cli, plan, seal
from pengyos_seal.runner import MemoryRunner, RunResult

BUILD = "/virtual/build"


# --- helpers ----------------------------------------------------------------


def _seeded_runner(
    *,
    verify_stdout: str = "",
    verify_rc: int = 0,
    witness_rc: int = 0,
    repro: str = "images/repro-report.json",
) -> MemoryRunner:
    """A runner with a full M1-style build tree (artifacts + SBOM + repro)."""
    r = MemoryRunner()
    r.seed_dir(BUILD)
    for artifact in ("images/rootfs.ext4", "images/bzImage"):
        r.seed_file(f"{BUILD}/{artifact}", b"payload")
    r.seed_file(f"{BUILD}/images/m2.spdx.json", b"{}")
    if repro is not None:
        r.seed_file(f"{BUILD}/{repro}", b"{}")
    r.results["pengyos-sign sign"] = RunResult(args=(), rc=0, stdout="signed")
    r.results["pengyos-witness-bundle run"] = RunResult(args=(), rc=witness_rc)
    r.results["pengyos-verifier check"] = RunResult(
        args=(), rc=verify_rc, stdout=verify_stdout,
    )
    return r


def _plan(r: MemoryRunner) -> "plan.SealPlan":
    return plan.build_plan(BUILD, runner=r)


def _run_args(**overrides) -> argparse.Namespace:
    base = dict(
        build=BUILD, key="seed", pub=None, attest=None, prev=None, json=None,
        sign_bin=None, verify_bin=None, witness_bin=None, repro_bin=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


# --- SealResult.to_dict / to_json -------------------------------------------


def test_to_dict_has_the_contract_keys():
    """The machine view mirrors the spec's --json contract keys."""
    r = _seeded_runner()
    result = seal.run_seal(BUILD, "seed", r, plan=_plan(r))
    data = result.to_dict()
    assert set(data) == {
        "build_dir", "plan", "signed", "names", "repro", "verifier",
    }
    assert data["build_dir"] == BUILD


def test_to_dict_round_trips_through_json():
    r = _seeded_runner()
    result = seal.run_seal(BUILD, "seed", r, plan=_plan(r))
    # Valid JSON, and re-parsing yields the same dict.
    parsed = json.loads(result.to_json())
    assert parsed == result.to_dict()


def test_to_dict_includes_the_sealed_plan():
    r = _seeded_runner()
    result = seal.run_seal(BUILD, "seed", r, plan=_plan(r))
    assert result.to_dict()["plan"] == _plan(r).to_dict()
    assert result.to_dict()["plan"]["target"] == "m2"


def test_to_dict_records_signed_artifacts_in_order():
    r = _seeded_runner()
    result = seal.run_seal(BUILD, "seed", r, plan=_plan(r))
    assert result.to_dict()["signed"] == [
        "images/rootfs.ext4", "images/bzImage",
    ]


def test_to_dict_names_normalised():
    """Both the SBOM and the repro report are normalised to their root names."""
    r = _seeded_runner()
    result = seal.run_seal(BUILD, "seed", r, plan=_plan(r))
    names = result.to_dict()["names"]
    assert names["sbom"] == {"source": "images/m2.spdx.json", "root": "sbom.spdx.json"}
    assert names["repro"] == {"source": "images/repro-report.json", "root": "repro-report.json"}


def test_to_dict_names_none_when_not_normalised():
    """No SBOM -> the sbom name is None; a skip -> the repro name is None."""
    r = MemoryRunner()
    r.seed_dir(BUILD)
    for artifact in ("images/rootfs.ext4", "images/bzImage"):
        r.seed_file(f"{BUILD}/{artifact}", b"payload")
    r.results["pengyos-sign sign"] = RunResult(args=(), rc=0, stdout="signed")
    r.results["pengyos-witness-bundle run"] = RunResult(args=(), rc=0)
    r.results["pengyos-verifier check"] = RunResult(args=(), rc=0)
    result = seal.run_seal(BUILD, "seed", r, plan=_plan(r))
    names = result.to_dict()["names"]
    assert names["sbom"] is None
    assert names["repro"] is None


def test_to_dict_repro_status():
    r = _seeded_runner()
    result = seal.run_seal(BUILD, "seed", r, plan=_plan(r))
    assert result.to_dict()["repro"] == {"status": "existing"}


def test_to_dict_verifier_checks_verdict_exit_code():
    """A structured verifier summary is surfaced as checks + verdict + code."""
    stdout = (
        '{"verdict": "PASS", "checks": {'
        '"sbom": "PASS", "signatures": "PASS", "repro-score": "PASS", '
        '"expected-files": "PASS", "attest-chain": "PASS"}}'
    )
    r = _seeded_runner(verify_stdout=stdout)
    result = seal.run_seal(BUILD, "seed", r, plan=_plan(r))
    v = result.to_dict()["verifier"]
    assert v["exit_code"] == 0
    assert v["verdict"] == seal.VERDICT_PASS
    assert v["checks"] == {
        "sbom": "PASS", "signatures": "PASS", "repro-score": "PASS",
        "expected-files": "PASS", "attest-chain": "PASS",
    }


def test_to_dict_verifier_none_when_no_verify_stage():
    r = _seeded_runner()
    result = seal.Seal(BUILD, "seed", r, plan=_plan(r)).sign()
    assert result.to_dict()["verifier"] is None


# --- _verifier_checks (unit) ------------------------------------------------


def test_verifier_checks_parses_structured_stdout():
    stdout = '{"verdict": "PASS", "checks": {"sbom": "pass", "signatures": "FAIL"}}'
    assert seal._verifier_checks(stdout) == {"sbom": "PASS", "signatures": "FAIL"}


def test_verifier_checks_none_when_no_checks_key():
    assert seal._verifier_checks('{"verdict": "PASS"}') is None


def test_verifier_checks_none_when_checks_not_a_dict():
    assert seal._verifier_checks('{"checks": ["sbom"]}') is None


def test_verifier_checks_none_when_empty():
    assert seal._verifier_checks('{"checks": {}}') is None


def test_verifier_checks_drops_non_string_values():
    # JSON keys are always strings; non-string *values* are dropped.
    stdout = '{"checks": {"sbom": "PASS", "x": 1}}'
    assert seal._verifier_checks(stdout) == {"sbom": "PASS"}


def test_verifier_checks_none_on_non_json():
    assert seal._verifier_checks("not json") is None
    assert seal._verifier_checks("") is None


def test_verifier_checks_none_on_non_object_json():
    assert seal._verifier_checks("[1, 2, 3]") is None


def test_run_verifier_populates_checks_from_stdout():
    stdout = '{"verdict": "FAIL", "checks": {"sbom": "FAIL"}}'
    r = _seeded_runner(verify_stdout=stdout, verify_rc=1)
    result = seal.Seal(BUILD, "seed", r, plan=_plan(r)).run_verifier()
    assert result.verifier.checks == {"sbom": "FAIL"}


def test_run_verifier_checks_none_without_structured_stdout():
    r = _seeded_runner(verify_stdout="all green")
    result = seal.Seal(BUILD, "seed", r, plan=_plan(r)).run_verifier()
    assert result.verifier.checks is None


# --- cmd_run --json ---------------------------------------------------------


def test_cmd_run_json_to_stdout(capsys):
    r = _seeded_runner()
    rc = cli.cmd_run(_run_args(json="-"), r=r)
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["signed"] == ["images/rootfs.ext4", "images/bzImage"]
    assert data["verifier"]["verdict"] == seal.VERDICT_PASS


def test_cmd_run_json_flag_also_emits_to_stdout(capsys):
    """'--json' with no argument prints to stdout (const '-' defaults)."""
    r = _seeded_runner()
    rc = cli.cmd_run(_run_args(json="-"), r=r)
    assert rc == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["plan"]["target"] == "m2"


def test_cmd_run_json_to_file(tmp_path):
    r = _seeded_runner()
    out = tmp_path / "seal.json"
    rc = cli.cmd_run(_run_args(json=str(out)), r=r)
    assert rc == cli.EXIT_OK
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["names"]["sbom"]["root"] == "sbom.spdx.json"
    assert data["repro"]["status"] == "existing"


def test_cmd_run_json_emits_only_json_no_human_lines(capsys):
    """With --json the human stage lines are suppressed — JSON only."""
    r = _seeded_runner()
    cli.cmd_run(_run_args(json="-"), r=r)
    out = capsys.readouterr().out
    assert "signed images/rootfs.ext4" not in out
    assert "verifier PASS" not in out
    # The only thing on stdout parses as JSON.
    json.loads(out)


def test_cmd_run_json_returns_verifier_exit_code(capsys):
    r = _seeded_runner(verify_rc=2, verify_stdout='{"verdict": "FAIL"}')
    rc = cli.cmd_run(_run_args(json="-"), r=r)
    assert rc == 2  # the verifier's exit code, not a mapped 1
    data = json.loads(capsys.readouterr().out)
    assert data["verifier"]["exit_code"] == 2
    assert data["verifier"]["verdict"] == seal.VERDICT_FAIL


def test_cmd_run_json_writes_file_even_on_failing_verifier(tmp_path):
    r = _seeded_runner(verify_rc=1, verify_stdout='{"verdict": "FAIL"}')
    out = tmp_path / "seal.json"
    rc = cli.cmd_run(_run_args(json=str(out)), r=r)
    assert rc == 1
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["verifier"]["verdict"] == seal.VERDICT_FAIL


def test_cmd_run_no_json_flag_still_prints_human_lines(capsys):
    """Without --json the human output is unchanged (the default path)."""
    r = _seeded_runner()
    rc = cli.cmd_run(_run_args(), r=r)
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "signed images/rootfs.ext4" in out
    assert "verifier PASS" in out


def test_to_dict_plan_null_when_no_plan():
    """A seal run with no plan still emits valid JSON (plan is null)."""
    r = _seeded_runner()
    # run_seal with plan=None -> the driver runs with no plan, so the machine
    # view records plan as null (cmd_run always builds one, so this is the
    # direct-API path).
    result = seal.run_seal(BUILD, "seed", r, plan=None)
    data = result.to_dict()
    assert data["plan"] is None
    assert data["signed"] == ["images/rootfs.ext4", "images/bzImage"]
    # Still valid JSON.
    json.loads(result.to_json())
