"""Fixture-grounding tests.

The spec's acceptance criterion is "A fixture release yields a lint-clean,
golden medium plan". These tests *ground* the two committed fixtures together so
they are the single source of truth for the whole pipeline, not two fixtures that
could drift apart:

* ``tests/fixtures/release-desktop.json`` — the committed **release** the medium
  is built from (its ``version`` / ``channel``).
* ``tests/fixtures/media-desktop.json`` — the committed **golden plan** the
  release yields.

The grounding is the round-trip the other tests skip: the committed release
fixture, run through ``build_plan`` *and* the ``plan`` CLI, produces the committed
golden plan **byte-for-byte**. The golden plan is then shown to be lint-clean, and
every lint rule is shown to have exactly one mutation grounded to the committed
golden fixture (mutate one field, write it out, lint it, assert the single code).

Hermetic: fixtures are read from disk and written to ``tmp_path``; no build tool
ever runs.
"""

import contextlib
import io
import json
import re
from pathlib import Path

import pytest

from pengyos_media.cli import ExitCode, main
from pengyos_media.model import MediumPlan
from pengyos_media.plan import build_plan, read_release_manifest
from pengyos_media.validate import (
    BOOT_ENTRY_MISSING,
    EDITION_MISMATCH_RELEASE,
    INSTALLER_MISSING,
    OS_RELEASE_MISMATCH,
    RELEASE_MISSING_VERSION,
    validate_plan,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RELEASE = FIXTURES / "release-desktop.json"
GOLDEN = FIXTURES / "media-desktop.json"


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
    return re.findall(r"^\s*-\s*([a-z-]+):", text, re.MULTILINE)


# --------------------------------------------------------------------------- #
# the committed release fixture yields the committed golden plan (byte-for-byte)
# --------------------------------------------------------------------------- #
def test_committed_release_fixture_yields_committed_golden_plan():
    # build_plan (the library half of `plan`) reads the committed release fixture
    # and returns the committed golden plan, byte-for-byte identical. This is the
    # "fixture release yields a golden medium plan" acceptance criterion.
    release = read_release_manifest(RELEASE)
    plan = build_plan("desktop", release)
    assert plan.to_json() == GOLDEN.read_text(encoding="utf-8")


def test_committed_release_fixture_via_cli_matches_golden(tmp_path):
    # The `plan` CLI, pointed at the committed release fixture, writes the
    # committed golden plan to disk — byte-for-byte identical (not a sibling
    # fixture that could drift).
    out = tmp_path / "media.json"
    code = main(
        ["plan", "--edition", "desktop",
         "--release", str(RELEASE), "--out", str(out), "--json"]
    )
    assert code == ExitCode.OK
    assert out.read_text(encoding="utf-8") == GOLDEN.read_text(encoding="utf-8")


def test_committed_release_fixture_is_lint_clean():
    # The release fixture is a real release: its version + channel parse, and the
    # plan it yields is well-formed by construction.
    release = read_release_manifest(RELEASE)
    assert release["version"]
    assert build_plan("desktop", release).release.channel == release["channel"]


# --------------------------------------------------------------------------- #
# the golden plan is lint-clean
# --------------------------------------------------------------------------- #
def test_committed_golden_plan_is_lint_clean():
    # validate_plan on the golden plan (the library ground truth) reports nothing.
    plan = MediumPlan.from_dict(json.loads(GOLDEN.read_text(encoding="utf-8")))
    assert validate_plan(plan) == []


def test_committed_golden_plan_passes_lint_cli():
    # The golden plan passes the `lint` CLI (exit 0, an OK verdict naming the
    # edition), the strongest "golden plan is clean" assertion.
    code, out, err = _run("lint", "--plan", str(GOLDEN))
    assert code == ExitCode.OK
    assert "OK" in out
    assert "desktop" in out
    assert err == ""


def test_committed_golden_plan_json_verdict_is_valid():
    code, out, _ = _run("lint", "--plan", str(GOLDEN), "--json")
    assert code == ExitCode.OK
    verdict = json.loads(out)
    assert verdict["valid"] is True
    assert verdict["problems"] == []
    assert verdict["plan"]["edition"] == "desktop"


# --------------------------------------------------------------------------- #
# each lint rule has exactly one mutation, grounded to the golden fixture
# --------------------------------------------------------------------------- #
# One mutator per lint rule. Each mutates exactly one part of the golden fixture
# so that `lint` reports exactly one code — the rule under test. (The edition
# rule must also rename the os-release id, otherwise the edition-agreement check
# would fire a second code; the validator reports every defect.)
def _mut_installer(d: dict) -> None:
    d["installer"]["binary"] = ""


def _mut_boot_entry(d: dict) -> None:
    d["boot_entry"]["loader"] = ""


def _mut_release(d: dict) -> None:
    d["release"]["version"] = ""


def _mut_os_release(d: dict) -> None:
    d["os_release"]["id"] = "core"


def _mut_edition(d: dict) -> None:
    # edition + os-release both rename to core, so only the release-agreement
    # check fires (the os-release still agrees with the edition).
    d["edition"] = "core"
    d["os_release"]["id"] = "core"


_MUTATORS = [
    (INSTALLER_MISSING, _mut_installer),
    (BOOT_ENTRY_MISSING, _mut_boot_entry),
    (RELEASE_MISSING_VERSION, _mut_release),
    (OS_RELEASE_MISMATCH, _mut_os_release),
    (EDITION_MISMATCH_RELEASE, _mut_edition),
]


@pytest.mark.parametrize("code, mutate", _MUTATORS)
def test_each_lint_rule_has_one_mutation(code, mutate, tmp_path: Path):
    # One mutation per lint rule, grounded to the committed golden fixture: mutate
    # exactly one part, write it out, and assert `lint` reports exactly that one
    # code. A clean golden plan is the control, so a second code would be a bug.
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    mutate(data)

    mutated = tmp_path / "mutated.json"
    mutated.write_text(json.dumps(data), encoding="utf-8")
    result, text, _ = _run("lint", "--plan", str(mutated))
    assert result == ExitCode.ERROR
    assert _codes(text) == [code]

    # The mutation was the only change: the clean golden plan is still clean
    # (this is exactly one mutation per rule, not a cascade).
    clean = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert validate_plan(MediumPlan.from_dict(clean)) == []
