"""Command-line interface for the PengyOS license-audit tool.

Usage:
    pengyos-license-audit run --pins pins.toml --policy policy.toml \\
        --out LICENSES.md --json report.json

Exit codes:
    0  success
    1  audit/runtime error (bad input, policy violation, I/O failure)
    2  usage error (bad arguments)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Sequence

from . import __version__, toml_io
from .classification import classify
from .errors import AuditError, ManifestError, PolicyError
from .model import Pin, Policy, Verdict, load_pins, load_policy
from .policy_eval import evaluate, has_violations


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pengyos-license-audit",
        description="Verify PengyOS component license claims and enforce the freedom policy.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser(
        "run",
        help="audit a pins manifest against a policy and emit LICENSES.md + JSON",
    )
    run.add_argument("--pins", required=True, help="path to the pins/license manifest (TOML)")
    run.add_argument("--policy", required=True, help="path to the policy file (TOML)")
    run.add_argument(
        "--out",
        required=True,
        help="path to write the human-readable LICENSES.md report",
    )
    run.add_argument(
        "--json",
        required=True,
        dest="json_out",
        help="path to write the machine-readable JSON report",
    )
    return parser


def _render_licenses(
    pins: list[Pin],
    policy: Policy,
    results: list | None = None,
) -> str:
    """Render LICENSES.md: components grouped by claimed license, stable order.

    ``results`` is the per-component policy evaluation (optional); when present
    each component line is annotated with its classified family and verdict.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    verdict_by_id: dict[str, str] = {}
    family_by_id: dict[str, str] = {}
    if results:
        for r in results:
            verdict_by_id[r.component_id] = r.verdict.value
            family_by_id[r.component_id] = r.family

    grouped: dict[str, list[Pin]] = {}
    for pin in pins:
        grouped.setdefault(pin.license, []).append(pin)

    lines: list[str] = []
    lines.append("# LICENSES — PengyOS license-audit")
    lines.append("")
    lines.append(f"_Generated {now} by pengyos-license-audit v{__version__}._")
    lines.append("")
    lines.append("Every license cell below is the component maintainer's **claim**; "
                 "this tool verifies and enforces the freedom policy.")
    lines.append("")
    lines.append("## Policy")
    lines.append("")
    if policy.allowed_families:
        lines.append("- Allowed families: " + ", ".join(sorted(policy.allowed_families)))
    else:
        lines.append("- Allowed families: *(none declared)*")
    lines.append(
        "- Copyleft in core: "
        + ("allowed" if policy.copyleft_in_core else "flagged")
    )
    if policy.exceptions:
        lines.append("- Documented exceptions: " + ", ".join(sorted(policy.exceptions)))
    else:
        lines.append("- Documented exceptions: *(none)*")
    lines.append("")
    lines.append("## Components by license")
    lines.append("")

    if not grouped:
        lines.append("_No components in the manifest._")
        lines.append("")
        return "\n".join(lines)

    for license_id in sorted(grouped):
        lines.append(f"### {license_id}")
        lines.append("")
        for pin in sorted(grouped[license_id], key=lambda p: p.id):
            note = " (core)" if pin.is_core else ""
            verdict = verdict_by_id.get(pin.id)
            family = family_by_id.get(pin.id)
            if verdict is not None:
                lines.append(
                    f"- `{pin.id}`{note} — link={pin.link} "
                    f"[{verdict}, family={family}]"
                )
            else:
                lines.append(f"- `{pin.id}`{note} — link={pin.link}")
        lines.append("")

    return "\n".join(lines)


def _build_report(
    pins: list[Pin],
    policy: Policy,
    results: list | None = None,
) -> dict:
    # Evaluate the policy when the caller did not supply results, so the report
    # is always self-consistent (verdicts match the components).
    verdicts = results if results is not None else evaluate(pins, policy)
    verdict_counts = {
        "pass": sum(1 for r in verdicts if r.verdict == Verdict.PASS),
        "warn": sum(1 for r in verdicts if r.verdict == Verdict.WARN),
        "fail": sum(1 for r in verdicts if r.verdict == Verdict.FAIL),
    }
    return {
        "tool": "pengyos-license-audit",
        "version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": len(pins),
            "core": sum(1 for p in pins if p.is_core),
            "leaf": sum(1 for p in pins if not p.is_core),
            "exceptions": len(policy.exceptions),
            "verdicts": verdict_counts,
        },
        "components": [
            {
                "id": p.id,
                "license": p.license,
                "tier": p.tier,
                "link": p.link,
            }
            for p in pins
        ],
        # Per-component pass/warn/fail verdicts (populated by the policy
        # evaluation subtask; this is the previously-reserved key).
        "verdicts": [
            {
                "component_id": r.component_id,
                "family": r.family,
                "verdict": r.verdict.value,
                "reason": r.reason,
            }
            for r in verdicts
        ],
        "policy": {
            "allowed_families": policy.allowed_families,
            "copyleft_in_core": policy.copyleft_in_core,
            "exceptions": policy.exceptions,
        },
    }


def cmd_run(args: argparse.Namespace) -> int:
    try:
        pins = load_pins(args.pins)
    except FileNotFoundError as exc:
        print(f"error: pins manifest not found: {exc}", file=sys.stderr)
        return 1
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        policy = load_policy(args.policy)
    except FileNotFoundError as exc:
        print(f"error: policy file not found: {exc}", file=sys.stderr)
        return 1
    except PolicyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Evaluate each component against the policy (pass/warn/fail per component,
    # link-aware for copyleft-in-core claims).
    results = evaluate(pins, policy)

    licenses_md = _render_licenses(pins, policy, results)
    report = _build_report(pins, policy, results)

    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(licenses_md)
        toml_io.write_json(args.json_out, report)
    except OSError as exc:
        print(f"error: could not write output: {exc}", file=sys.stderr)
        return 1

    s = report["summary"]
    v = s["verdicts"]
    print(
        f"audited {s['total']} components "
        f"({s['core']} core, {s['leaf']} leaf) "
        f"-> {v['pass']} pass, {v['warn']} warn, {v['fail']} fail "
        f"-> {args.out}, {args.json_out}"
    )

    # Non-zero exit when the policy is violated without an exception.
    if has_violations(results):
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return cmd_run(args)
    except AuditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unknown command {args.command!r}")  # unreachable; exits 2
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
