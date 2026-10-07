"""Tests for the ``check`` CLI subcommand.

Covers the "Aggregate into a report + non-zero exit on any fail"
subtask at the command-line boundary: ``pengyos-verifier check``
wraps ``build_report``, prints the human report to stdout, writes an
optional ``--json OUT``, honors ``--min-score``, and maps any check
failure to a non-zero exit code (and usage errors to exit 2).
Everything is offline (fixtures in ``tests/fixtures_build.py``).
"""

from __future__ import annotations

import json
import os

from pengyos_verifier import checks
from pengyos_verifier.cli import build_parser, main
from tests.fixtures_build import build_good_dir

from conftest import make_chain


def _write_attest(path, text) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return str(path)


# ----------------------------------------------------------------------
# parser shape
# ----------------------------------------------------------------------

def test_parser_has_check_subcommand():
    parser = build_parser()
    args = parser.parse_args(["check", "--build", "some/dir"])
    assert args.command == "check"
    assert args.build == "some/dir"
    assert args.json_out is None
    # --attest is optional (default None); --min-score defaults to 1.0.
    assert args.attest is None
    assert args.min_score == checks.DEFAULT_REPRO_THRESHOLD


def test_parser_check_requires_build():
    parser = build_parser()
    import pytest
    with pytest.raises(SystemExit):
        parser.parse_args(["check"])  # missing required --build


def test_parser_min_score_flag():
    args = build_parser().parse_args(
        ["check", "--build", "d", "--min-score", "0.7"]
    )
    assert args.min_score == 0.7


# ----------------------------------------------------------------------
# check: good build
# ----------------------------------------------------------------------

def test_check_good_build_exits_zero(capsys, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_good_dir(build_dir)
    attest = _write_attest(tmp_path / "attest.log", make_chain())

    rc = main(["check", "--build", str(build_dir), "--attest", attest])
    assert rc == 0
    out = capsys.readouterr().out
    assert "verdict: PASS" in out
    assert "[FAIL]" not in out
    for cid in checks.CHECK_IDS:
        assert f"[PASS] {cid}" in out


def test_check_good_build_without_attest_flag(capsys, tmp_path):
    # --attest omitted -> empty chain -> attest check passes.
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_good_dir(build_dir)
    rc = main(["check", "--build", str(build_dir)])
    assert rc == 0
    assert "verdict: PASS" in capsys.readouterr().out


def test_check_good_build_missing_attest_file(capsys, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_good_dir(build_dir)
    rc = main(["check", "--build", str(build_dir),
               "--attest", str(tmp_path / "does-not-exist.log")])
    assert rc == 0
    assert "verdict: PASS" in capsys.readouterr().out


# ----------------------------------------------------------------------
# check: failing builds -> non-zero exit
# ----------------------------------------------------------------------

def test_check_missing_sbom_exits_nonzero(capsys, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_good_dir(build_dir)
    os.remove(str(build_dir / checks.SBOM_FILENAMES[0]))

    rc = main(["check", "--build", str(build_dir)])
    assert rc != 0
    out = capsys.readouterr().out
    assert "verdict: FAIL" in out
    assert f"[FAIL] {checks.CHECK_SBOM}" in out


def test_check_broken_chain_exits_nonzero(capsys, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_good_dir(build_dir)
    bad = json.dumps({"seq": 1, "ts": "2026-09-16T00:00:00Z",
                      "type": "build", "data": {},
                      "prev_hash": "0" * 64, "hash": "0" * 64}) + "\n"
    attest = _write_attest(tmp_path / "attest.log", bad)

    rc = main(["check", "--build", str(build_dir), "--attest", attest])
    assert rc != 0
    assert f"[FAIL] {checks.CHECK_ATTEST_CHAIN}" in capsys.readouterr().out


def test_check_low_repro_default_threshold_fails(capsys, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_good_dir(build_dir)
    with open(str(build_dir / checks.REPRO_REPORT_FILENAME), "w",
              encoding="utf-8") as fh:
        fh.write(json.dumps({"score": 0.5, "identical": False}) + "\n")

    rc = main(["check", "--build", str(build_dir)])
    assert rc != 0
    assert f"[FAIL] {checks.CHECK_REPRO}" in capsys.readouterr().out


def test_check_min_score_allows_triage(capsys, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_good_dir(build_dir)
    with open(str(build_dir / checks.REPRO_REPORT_FILENAME), "w",
              encoding="utf-8") as fh:
        fh.write(json.dumps({"score": 0.5, "identical": False}) + "\n")

    # 0.5 < default 1.0 -> fail; 0.5 >= 0.4 -> pass.
    assert main(["check", "--build", str(build_dir)]) != 0
    assert main(["check", "--build", str(build_dir),
                 "--min-score", "0.4"]) == 0


# ----------------------------------------------------------------------
# check: --json OUT
# ----------------------------------------------------------------------

def test_check_writes_json_report(tmp_path, capsys):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_good_dir(build_dir)
    out = tmp_path / "report.json"

    rc = main(["check", "--build", str(build_dir), "--json", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["verifier"] == "pengyos-verifier"
    assert data["ok"] is True
    assert [f["check"] for f in data["findings"]] == list(checks.CHECK_IDS)
    assert data["build"] == str(build_dir)
    # stdout pretty and the JSON agree on the verdict.
    assert "verdict: PASS" in capsys.readouterr().out


def test_check_json_reflects_failure(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_good_dir(build_dir)
    os.remove(str(build_dir / checks.SBOM_FILENAMES[0]))
    out = tmp_path / "report.json"

    rc = main(["check", "--build", str(build_dir), "--json", str(out)])
    assert rc == 1
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert any(f["check"] == checks.CHECK_SBOM and f["status"] == "fail"
               for f in data["findings"])


def test_check_json_write_failure_returns_two(capsys, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_good_dir(build_dir)
    # A path whose parent directory does not exist -> OSError on write.
    rc = main(["check", "--build", str(build_dir),
               "--json", str(tmp_path / "nope" / "nested.json")])
    assert rc == 2
    assert "could not write JSON report" in capsys.readouterr().err


# ----------------------------------------------------------------------
# module entrypoint (python -m) reaches the check command
# ----------------------------------------------------------------------

def test_module_entrypoint_check(capsys, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_good_dir(build_dir)
    import subprocess
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_verifier",
         "check", "--build", str(build_dir)],
        capture_output=True, text=True, cwd=root,
    )
    assert result.returncode == 0, result.stderr
    assert "verdict: PASS" in result.stdout
