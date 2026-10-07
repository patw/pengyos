"""CLI-level tests for ``lint``.

Exercises the ``lint`` subcommand end-to-end through :func:`main`: a lint-clean
golden plan passes, every defect the validator knows about is reported with its
stable code and a FAILED verdict, the bad-input paths (missing/unreadable file,
bad JSON, not-a-plan) are hard errors, and ``--json`` emits the
machine-readable verdict. Nothing here touches disk beyond the temp plan files
``tmp_path`` hands out, and no build tool is run.
"""

import contextlib
import io
import json
from pathlib import Path

from pengyos_media.cli import ExitCode, main, verdict_to_dict, verdict_to_text
from pengyos_media.model import make_plan
from pengyos_media.validate import (
    BOOT_ENTRY_MISSING,
    EDITION_MISMATCH_RELEASE,
    INSTALLER_MISSING,
    OS_RELEASE_MISMATCH,
    PlanError,
    RELEASE_MISSING_VERSION,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _write_plan(tmp_path: Path, *, mutate=None) -> Path:
    """Emit a golden plan file and return its path.

    ``mutate`` is an optional callable applied to the plan's ``to_dict`` before
    it is written, so each test can hand ``lint`` a plan with one defect.
    """
    data = make_plan("desktop", "2026.09.1").to_dict()
    if mutate is not None:
        mutate(data)
    out = tmp_path / "media.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:  # argparse failures
            code = e.code or ExitCode.USAGE
    return code, out.getvalue(), err.getvalue()


def _codes(text: str) -> list[str]:
    """Pull the ``code:`` labels out of a FAILED verdict's text."""
    import re

    return re.findall(r"^\s*-\s*([a-z-]+):", text, re.MULTILINE)


# --------------------------------------------------------------------------- #
# the clean plan passes
# --------------------------------------------------------------------------- #
def test_lint_clean_plan_passes_text(tmp_path):
    plan = _write_plan(tmp_path)
    code, out, err = _run("lint", "--plan", str(plan))
    assert code == ExitCode.OK
    assert "OK" in out
    assert "desktop" in out  # names the edition it validated
    assert err == ""


def test_lint_clean_plan_passes_json(tmp_path):
    plan = _write_plan(tmp_path)
    code, out, _ = _run("lint", "--plan", str(plan), "--json")
    assert code == ExitCode.OK
    verdict = json.loads(out)
    assert verdict["valid"] is True
    assert verdict["problems"] == []
    # The verdict is self-contained: it carries the plan it validated.
    assert verdict["plan"]["edition"] == "desktop"
    assert verdict["plan"]["installer"]["binary"] == "pengyos-install"


def test_lint_committed_golden_fixture_passes():
    # The committed golden plan (tests/fixtures/media-desktop.json) is lint-clean:
    # linting the fixture on disk is the strongest "golden plan" assertion.
    code, out, err = _run("lint", "--plan", str(FIXTURES / "media-desktop.json"))
    assert code == ExitCode.OK
    assert "OK" in out
    assert err == ""


# --------------------------------------------------------------------------- #
# each defect the validator knows about is reported with its stable code
# --------------------------------------------------------------------------- #
def test_lint_installer_missing(tmp_path):
    plan = _write_plan(tmp_path, mutate=lambda d: d["installer"].__setitem__("binary", ""))
    code, out, _ = _run("lint", "--plan", str(plan))
    assert code == ExitCode.ERROR
    assert INSTALLER_MISSING in _codes(out)


def test_lint_boot_entry_missing(tmp_path):
    def mutate(d: dict) -> None:
        d["boot_entry"]["loader"] = ""

    plan = _write_plan(tmp_path, mutate=mutate)
    code, out, _ = _run("lint", "--plan", str(plan))
    assert code == ExitCode.ERROR
    assert BOOT_ENTRY_MISSING in _codes(out)


def test_lint_release_missing_version(tmp_path):
    def mutate(d: dict) -> None:
        d["release"]["version"] = ""

    plan = _write_plan(tmp_path, mutate=mutate)
    code, out, _ = _run("lint", "--plan", str(plan))
    assert code == ExitCode.ERROR
    assert RELEASE_MISSING_VERSION in _codes(out)


def test_lint_os_release_mismatch(tmp_path):
    def mutate(d: dict) -> None:
        d["os_release"]["id"] = "core"

    plan = _write_plan(tmp_path, mutate=mutate)
    code, out, _ = _run("lint", "--plan", str(plan))
    assert code == ExitCode.ERROR
    assert OS_RELEASE_MISMATCH in _codes(out)


def test_lint_edition_mismatch_release(tmp_path):
    def mutate(d: dict) -> None:
        d["edition"] = "core"

    plan = _write_plan(tmp_path, mutate=mutate)
    code, out, _ = _run("lint", "--plan", str(plan))
    assert code == ExitCode.ERROR
    assert EDITION_MISMATCH_RELEASE in _codes(out)


def test_lint_reports_every_defect(tmp_path):
    # installer + boot entry + release version all empty, edition wrong: one run
    # names every problem, not just the first.
    def mutate(d: dict) -> None:
        d["installer"]["binary"] = ""
        d["boot_entry"]["loader"] = ""
        d["release"]["version"] = ""
        d["edition"] = "core"

    plan = _write_plan(tmp_path, mutate=mutate)
    code, out, _ = _run("lint", "--plan", str(plan))
    assert code == ExitCode.ERROR
    codes = _codes(out)
    for code_ in (INSTALLER_MISSING, BOOT_ENTRY_MISSING,
                  RELEASE_MISSING_VERSION, EDITION_MISMATCH_RELEASE):
        assert code_ in codes


def test_lint_json_verdict_reports_problems(tmp_path):
    plan = _write_plan(tmp_path, mutate=lambda d: d["installer"].__setitem__("binary", ""))
    code, out, _ = _run("lint", "--plan", str(plan), "--json")
    assert code == ExitCode.ERROR
    verdict = json.loads(out)
    assert verdict["valid"] is False
    assert len(verdict["problems"]) == 1
    assert verdict["problems"][0]["code"] == INSTALLER_MISSING
    assert verdict["problems"][0]["message"]


# --------------------------------------------------------------------------- #
# invalid (non-loadable) plans are hard errors, not a crash
# --------------------------------------------------------------------------- #
def test_lint_invalid_kind_is_error(tmp_path):
    # An unknown kind cannot round-trip through from_dict, so it surfaces as a
    # hard error rather than a crash — the plan is simply not buildable.
    def mutate(d: dict) -> None:
        d["kind"] = "cd"

    plan = _write_plan(tmp_path, mutate=mutate)
    code, _, err = _run("lint", "--plan", str(plan))
    assert code == ExitCode.ERROR
    assert "not a medium plan" in err


def test_lint_missing_file_is_error():
    code, _, err = _run("lint", "--plan", "/no/such/media.json")
    assert code == ExitCode.ERROR
    assert "cannot read plan" in err


def test_lint_bad_json_is_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    code, _, err = _run("lint", "--plan", str(bad))
    assert code == ExitCode.ERROR
    assert "cannot read plan" in err


def test_lint_json_array_is_error(tmp_path):
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2, 3]", encoding="utf-8")
    code, _, err = _run("lint", "--plan", str(arr))
    assert code == ExitCode.ERROR
    assert "cannot read plan" in err


# --------------------------------------------------------------------------- #
# the render helpers (unit-level, exercised directly)
# --------------------------------------------------------------------------- #
def test_verdict_to_text_ok():
    plan = make_plan("desktop", "2026.09.1")
    text = verdict_to_text(plan, [])
    assert text.startswith("lint: OK")
    assert "desktop" in text


def test_verdict_to_text_failed_lists_problems():
    plan = make_plan("desktop", "2026.09.1")
    text = verdict_to_text(
        plan, [PlanError(INSTALLER_MISSING, "no installer")]
    )
    assert "FAILED" in text
    assert "1 problem" in text
    assert INSTALLER_MISSING in text
    assert "no installer" in text


def test_verdict_to_dict_shape():
    plan = make_plan("desktop", "2026.09.1")
    good = verdict_to_dict(plan, [])
    assert good == {
        "valid": True,
        "plan": plan.to_dict(),
        "problems": [],
    }
    bad = verdict_to_dict(
        plan,
        [
            PlanError(INSTALLER_MISSING, "no installer"),
            PlanError(BOOT_ENTRY_MISSING, "no boot entry"),
        ],
    )
    assert bad["valid"] is False
    assert len(bad["problems"]) == 2
    assert bad["problems"][0] == {
        "code": INSTALLER_MISSING,
        "message": "no installer",
    }


def test_verdict_to_text_multiple_defects_counts_all():
    plan = make_plan("desktop", "2026.09.1")
    problems = [
        PlanError(INSTALLER_MISSING, "a"),
        PlanError(BOOT_ENTRY_MISSING, "b"),
    ]
    text = verdict_to_text(plan, problems)
    assert "2 problems" in text
