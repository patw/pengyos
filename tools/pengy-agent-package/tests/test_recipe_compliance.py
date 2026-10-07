"""Recipe-compliance tests.

The spec's Verification contract is that the recipe *passes
`pengyos-recipe-lint`* (version pinned, hash present, license declared, no
unverified downloads) and that it is **headless-only** (builds only
``pengy-cli`` + ``pengy-web``, never pulls Qt / C++ / CMake).

This module owns the *compliance* half: it runs the **real** sibling
``pengyos-recipe-lint`` tool (not a reimplementation) against the committed
``package/pengy/`` recipe and asserts it comes back clean. The headless-only
assertions live in :mod:`tests.test_recipe` (recipe bodies) and the unit/config
assertions in :mod:`tests.test_service_and_config`.

The lint tool is a sibling tool under the shared repo's ``tools/`` dir and
requires Python >= 3.11, so running it is an **opt-in** integration check behind
``PENGYOS_IT=1`` (the verifier may run an older interpreter). The individual
lint *rules* themselves (version pinned, hash present, MIT license, verified
https site) are asserted offline in ``test_recipe.py`` / ``test_hash.py`` without
this tool.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "package" / "pengy"
MK_FILE = PACKAGE_DIR / "pengy.mk"
HASH_FILE = PACKAGE_DIR / "pengy.hash"


def _run_lint_cli(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """Run the sibling ``pengyos-recipe-lint`` CLI, or skip if unavailable.

    The tool is a sibling tool under the shared repo's ``tools/`` dir (one level
    up from this tool root) and requires Python >= 3.11, so it is opt-in behind
    ``PENGYOS_IT=1`` (the verifier may run an older interpreter).
    """
    lint_root = REPO_ROOT.parent / "pengyos-recipe-lint"
    if not (lint_root / "pengyos_recipe_lint").is_dir():
        pytest.skip("pengyos-recipe-lint tool not present in the repo")
    if sys.version_info < (3, 11):
        pytest.skip("pengyos-recipe-lint requires Python >= 3.11")
    env = {**os.environ, "PYTHONPATH": str(lint_root)}
    return subprocess.run(
        [sys.executable, "-m", "pengyos_recipe_lint", *args, str(PACKAGE_DIR)],
        cwd=lint_root,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


@pytest.mark.skipif(
    not os.environ.get("PENGYOS_IT"),
    reason="runs the pengyos-recipe-lint tool (opt-in integration check)",
)
def test_recipe_passes_pengyos_recipe_lint():
    """The recipe + hash pass ``pengyos-recipe-lint`` with zero violations.

    This is the spec's "recipe passes ``pengyos-recipe-lint`` rules" contract,
    run against the REAL tool rather than a reimplementation. ``check`` exits
    non-zero when any violation is found, so exit 0 + the "0 violations" line
    together prove all four rules (version pinned, hash present, license
    declared, no unverified downloads) passed.
    """
    proc = _run_lint_cli(["check"])
    assert proc.returncode == 0, (
        f"pengyos-recipe-lint reported violations:\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    assert "0 violations" in proc.stdout, (
        f"expected a clean lint, got:\n{proc.stdout}"
    )


@pytest.mark.skipif(
    not os.environ.get("PENGYOS_IT"),
    reason="runs the pengyos-recipe-lint tool (opt-in integration check)",
)
def test_recipe_lint_json_report_is_clean():
    """The structured ``--json`` report also reports zero violations.

    A second, independent verification path from the plain ``check`` stdout test
    above: the CLI's JSON report is parsed and asserted to carry exactly one
    recipe and zero violations. This locks the structured output contract, not
    just a substring of the text report.
    """
    with tempfile.TemporaryDirectory() as out_dir:
        json_path = os.path.join(out_dir, "report.json")
        proc = _run_lint_cli(["check", f"--json={json_path}"])
        assert proc.returncode == 0, (
            f"pengyos-recipe-lint reported violations:\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
        report = json.loads(Path(json_path).read_text(encoding="utf-8"))
        assert report["count"] == 1, (
            f"expected exactly one recipe under package/pengy/, got {report['count']}"
        )
        assert report["violation_count"] == 0, (
            f"expected zero violations in the JSON report, got {report['violation_count']}"
        )
        assert report["violations"] == [], (
            f"expected an empty violations list, got {report['violations']}"
        )


def test_lint_tool_present_for_opt_in_check():
    """The sibling ``pengyos-recipe-lint`` tool is present in the shared repo.

    This is not itself opt-in: it documents (and guards) the precondition the
    two opt-in tests above rely on. If the tool is absent the opt-in tests skip
    cleanly; if it is present this asserts the precondition holds so a missing
    tool fails loudly rather than silently.
    """
    lint_root = REPO_ROOT.parent / "pengyos-recipe-lint"
    assert (lint_root / "pengyos_recipe_lint").is_dir(), (
        "pengyos-recipe-lint tool is not present at tools/pengyos-recipe-lint/ "
        "(the opt-in compliance tests skip without it)"
    )
    assert MK_FILE.is_file() and HASH_FILE.is_file(), (
        "package/pengy/ must ship both pengy.mk and pengy.hash for the lint"
    )
