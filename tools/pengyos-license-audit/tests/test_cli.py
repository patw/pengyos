"""Tests for the CLI: argument handling, exit codes, and outputs."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

from pengyos_license_audit import toml_io
from pengyos_license_audit.cli import _build_report, _render_licenses

from pengyos_license_audit.model import load_pins, load_policy


def test_run_emits_both_outputs(run_cli, pins_path, policy_path, tmp_path):
    out = tmp_path / "LICENSES.md"
    js = tmp_path / "report.json"
    rc = run_cli(
        "run",
        "--pins", pins_path,
        "--policy", policy_path,
        "--out", str(out),
        "--json", str(js),
    )
    assert rc == 0
    assert out.exists() and out.stat().st_size > 0
    assert js.exists()
    report = json.loads(js.read_text())
    assert report["tool"] == "pengyos-license-audit"
    assert report["summary"]["total"] == 7
    assert report["summary"]["core"] == 3


def test_run_licenses_md_groups_by_license(run_cli, pins_path, policy_path, tmp_path):
    out = tmp_path / "LICENSES.md"
    rc = run_cli(
        "run",
        "--pins", pins_path,
        "--policy", policy_path,
        "--out", str(out),
        "--json", str(tmp_path / "r.json"),
    )
    assert rc == 0
    text = out.read_text()
    # Each claimed license becomes a heading.
    assert "### MIT" in text
    assert "### LGPL-2.1-only" in text
    assert "### Proprietary" in text
    # Core marker is present for the core tier.
    assert "`glibc` (core)" in text


def test_run_report_schema(run_cli, pins_path, policy_path, tmp_path):
    js = tmp_path / "report.json"
    run_cli(
        "run",
        "--pins", pins_path,
        "--policy", policy_path,
        "--out", str(tmp_path / "L.md"),
        "--json", str(js),
    )
    report = json.loads(js.read_text())
    assert set(report) >= {"tool", "version", "generated_at", "summary", "components", "policy"}
    comp = report["components"][0]
    assert set(comp) >= {"id", "license", "tier", "link"}


def test_run_missing_pins_file(run_cli, policy_path, tmp_path):
    rc = run_cli(
        "run",
        "--pins", "/nonexistent/pins.toml",
        "--policy", policy_path,
        "--out", str(tmp_path / "L.md"),
        "--json", str(tmp_path / "r.json"),
    )
    assert rc == 1


def test_run_missing_required_arg(run_cli):
    # argparse exits with code 2 on a missing required argument.
    import pytest

    with pytest.raises(SystemExit) as exc:
        run_cli("run", "--pins", "x")
    assert exc.value.code == 2


def test_run_bad_toml_returns_1(run_cli, tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = = not valid toml [[[\n")
    rc = run_cli(
        "run",
        "--pins", str(bad),
        "--policy", "fixtures/policy.toml",
        "--out", str(tmp_path / "L.md"),
        "--json", str(tmp_path / "r.json"),
    )
    assert rc == 1


def test_module_invocation(pins_path, policy_path, tmp_path):
    """`python -m pengyos_license_audit` must work end to end."""
    out = tmp_path / "LICENSES.md"
    js = tmp_path / "report.json"
    proc = subprocess.run(
        [
            sys.executable, "-m", "pengyos_license_audit", "run",
            "--pins", pins_path,
            "--policy", policy_path,
            "--out", str(out),
            "--json", str(js),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists() and js.exists()


def test_render_licenses_stable_ordering():
    # Build pins directly to test ordering without a fixture file.
    from pengyos_license_audit.model import Pin, Policy

    pins = [
        Pin(id="b-lib", license="MIT", tier="leaf", link="dynamic"),
        Pin(id="a-lib", license="MIT", tier="core", link="static"),
        Pin(id="c-app", license="GPL-3.0-only", tier="leaf", link="dynamic"),
    ]
    policy = Policy(allowed_families=["MIT"], copyleft_in_core=False)
    md = _render_licenses(pins, policy)
    # Within the MIT group, ids are sorted ascending.
    assert md.index("`a-lib`") < md.index("`b-lib`")
    # License headings are sorted ascending too.
    assert md.index("### GPL-3.0-only") < md.index("### MIT")
    # Static-link marker present.
    assert "link=static" in md


def test_build_report_summary_counts():
    from pengyos_license_audit.model import Pin, Policy

    pins = [
        Pin(id="c1", license="MIT", tier="core", link="dynamic"),
        Pin(id="c2", license="GPL-2.0-only", tier="core", link="dynamic"),
        Pin(id="c3", license="Zlib", tier="leaf", link="dynamic"),
    ]
    policy = Policy(allowed_families=["MIT"], exceptions=["c2"])
    report = _build_report(pins, policy)
    assert report["summary"]["total"] == 3
    assert report["summary"]["core"] == 2
    assert report["summary"]["leaf"] == 1
    assert report["summary"]["exceptions"] == 1
    # The verdicts key is now populated (was reserved before the evaluation
    # subtask).
    assert set(report["summary"]["verdicts"]) == {"pass", "warn", "fail"}
    # c1 (MIT) passes, c2 is an exception (pass), c3 (GPL-2.0-only, leaf) fails.
    assert report["summary"]["verdicts"] == {"pass": 2, "warn": 0, "fail": 1}


def test_run_report_contains_per_component_verdicts(
    run_cli, pins_path, policy_path, tmp_path
):
    js = tmp_path / "report.json"
    run_cli(
        "run",
        "--pins", pins_path,
        "--policy", policy_path,
        "--out", str(tmp_path / "L.md"),
        "--json", str(js),
    )
    report = json.loads(js.read_text())
    # Every component has a verdict with a family + reason.
    assert len(report["verdicts"]) == len(report["components"])
    by_id = {v["component_id"]: v for v in report["verdicts"]}
    glibc = by_id["glibc"]
    assert glibc["verdict"] == "warn"  # LGPL-2.1-only, core, dynamic
    assert glibc["family"] == "weak-copyleft"
    assert glibc["reason"]
    firmware = by_id["firmware-blob"]
    assert firmware["verdict"] == "pass"  # documented exception


def test_run_exits_nonzero_on_policy_violation(run_cli, tmp_path):
    # A policy that allows nothing outright makes the proprietary firmware blob
    # (an exception) pass but every other component fail -> exit code 1.
    pins = tmp_path / "pins.toml"
    pins.write_text(
        textwrap.dedent(
            """
            [[component]]
            id = "a"
            license = "MIT"
            [[component]]
            id = "b"
            license = "Proprietary"
            tier = "leaf"
            """
        ).strip()
    )
    policy = tmp_path / "policy.toml"
    policy.write_text("allowed_families = []\nexceptions = []\n")
    rc = run_cli(
        "run",
        "--pins", str(pins),
        "--policy", str(policy),
        "--out", str(tmp_path / "L.md"),
        "--json", str(tmp_path / "r.json"),
    )
    assert rc == 1
    report = json.loads((tmp_path / "r.json").read_text())
    assert report["summary"]["verdicts"]["fail"] == 2


def test_run_exits_zero_when_clean(run_cli, pins_path, policy_path, tmp_path):
    # The fixture audit is clean (exit 0) — warnings do not fail the audit.
    rc = run_cli(
        "run",
        "--pins", pins_path,
        "--policy", policy_path,
        "--out", str(tmp_path / "L.md"),
        "--json", str(tmp_path / "r.json"),
    )
    assert rc == 0
    report = json.loads((tmp_path / "r.json").read_text())
    assert report["summary"]["verdicts"]["fail"] == 0
    # Warnings are still reported (copyleft-in-core dynamically linked).
    assert report["summary"]["verdicts"]["warn"] >= 1


# ---------------------------------------------------------------------------
# LICENSES.md emission: grouped + per-component verdict annotations
# ---------------------------------------------------------------------------

def test_render_licenses_annotates_verdicts():
    """LICENSES.md component lines carry [<verdict>, family=<family>]."""
    from pengyos_license_audit.model import Pin, Policy, Verdict
    from pengyos_license_audit.policy_eval import evaluate

    pins = [
        Pin(id="musl", license="MIT", tier="core", link="dynamic"),
        Pin(id="glibc", license="LGPL-2.1-only", tier="core", link="dynamic"),
    ]
    policy = Policy(allowed_families=["MIT"])
    results = evaluate(pins, policy)
    md = _render_licenses(pins, policy, results)

    # Each annotated line has the "[<verdict>, family=<family>]" shape.
    assert "`musl` (core) — link=dynamic [pass, family=permissive]" in md
    assert (
        "`glibc` (core) — link=dynamic [warn, family=weak-copyleft]" in md
    )
    # The verdict tally in the report matches the rendered annotations.
    verdicts = {r.component_id: r.verdict for r in results}
    assert verdicts == {"musl": Verdict.PASS, "glibc": Verdict.WARN}


def test_render_licenses_policy_section():
    """The Policy section reflects the loaded policy."""
    from pengyos_license_audit.model import Pin, Policy

    pins = [Pin(id="a", license="MIT", tier="leaf")]
    policy = Policy(
        allowed_families=["MIT", "BSD-3-Clause"],
        copyleft_in_core=False,
        exceptions=["blob"],
    )
    md = _render_licenses(pins, policy)
    assert "## Policy" in md
    assert "- Allowed families: BSD-3-Clause, MIT" in md
    assert "- Copyleft in core: flagged" in md
    assert "- Documented exceptions: blob" in md


def test_render_licenses_empty_manifest():
    """A manifest with no components renders a friendly placeholder."""
    from pengyos_license_audit.model import Policy

    md = _render_licenses([], Policy(allowed_families=["MIT"]))
    assert "_No components in the manifest._" in md


def test_build_report_verdicts_match_components():
    """report.json verdicts align 1:1 with components (id/family/verdict/reason)."""
    from pengyos_license_audit.model import Pin, Policy

    pins = [
        Pin(id="a", license="MIT", tier="core"),
        Pin(id="b", license="GPL-3.0-only", tier="core", link="static"),
    ]
    policy = Policy(allowed_families=["MIT"])
    report = _build_report(pins, policy)
    assert len(report["verdicts"]) == len(report["components"])
    by_id = {v["component_id"]: v for v in report["verdicts"]}
    assert set(by_id) == {"a", "b"}
    assert by_id["a"] == {
        "component_id": "a",
        "family": "permissive",
        "verdict": "pass",
        "reason": by_id["a"]["reason"],
    }
    assert by_id["a"]["reason"]
    assert by_id["b"]["verdict"] == "fail"  # static copyleft in core
    # The tally is self-consistent with the per-component verdicts.
    tally = report["summary"]["verdicts"]
    assert tally == {"pass": 1, "warn": 0, "fail": 1}


def test_build_report_verdicts_key_populated_not_reserved():
    """The verdicts key is populated (it was reserved before this subtask)."""
    from pengyos_license_audit.model import Pin, Policy

    pins = [Pin(id="a", license="MIT", tier="leaf")]
    policy = Policy(allowed_families=["MIT"])
    report = _build_report(pins, policy)
    assert "verdicts" in report
    assert isinstance(report["verdicts"], list)
    assert report["verdicts"][0]["component_id"] == "a"
