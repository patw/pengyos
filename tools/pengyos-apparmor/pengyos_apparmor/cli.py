"""Command-line interface for ``pengyos-apparmor``.

Every subcommand is a thin wrapper over the pure logic in the rest of the
package; all functions return a process exit code and report errors to
``stderr`` (or ``stdout`` for structured output).

Subcommands are registered lazily as they are implemented so the parser
never advertises a command that does not yet exist. Implemented so far:

* ``validate`` — validate profile grammar (header, braces, well-formed
  capability/path rules) and lint over-broad grants; exit 1 on any
  error, 0 otherwise (``--strict`` also fails on warnings).
* ``plan-port-pin`` — dry-run the model-server port-pin transition for a
  profile: render the exact port-pinned rule and local ``# sanctioned:``
  line, transform the profile text in memory, and validate the result
  with the strict contract; exit 0 when the transformed profile is
  strict-clean, 1 otherwise, 2 on bad input. ``--patch`` also renders the
  complete mechanical transition as a unified diff over the repo files
  (fail-loud on drift). No files are ever modified.
* ``find-model-server-port [root]`` — scan the PengyOS workspace tree for
  text lines that define the model server's default port (the recurring
  upstream check for the blocked port-pin item). Exit 0 when candidates
  are found (each printed as evidence for human review), 3 when none are
  found (the item stays blocked — the command never guesses a port), 2 on
  bad input. Every checkout of this tool (any directory containing its
  ``pengyos_apparmor`` package) is excluded from the scan; a scan whose
  root is a tool checkout reports no candidates.
* ``find-agent-workspace-layout [root]`` — scan the PengyOS workspace
  tree for text lines that define the agent's working layout below its
  workspace root (the recurring upstream check for the still-blocked
  "pin the workspace file-write surface" item). Exit 0 when candidates
  are found, 3 when none are (the item stays blocked — the path set is
  never invented), 2 on bad input. Every checkout of this tool is
  excluded from the scan; a scan whose root is a tool checkout reports
  no candidates.
* ``port-pin-status [repo-root] [--scan-root ROOT]`` — report the
  state of the port-pin transition: ``blocked`` (all four transition
  files unbound and no upstream port definition found — exit 3),
  ``ready`` (all unbound, upstream candidates found — exit 0),
  ``pinned`` (all files consistently pinned to one port — commit-able
  when the pinned port is defined upstream, exit 0; *not-yet-commit-able*
  when it is not yet defined upstream, exit 4; upstream scan reported as
  evidence), or ``partial`` (mixed or drifted repo — exit 1, with per-file
  detail). For a ``pinned`` repo the verdict is printed as a
  machine-greppable ``commit-ability: commit-able|not-yet-commit-able``
  line. Exit 2 on bad input. Read-only: nothing is modified and no port is
  ever guessed.
* ``apply-port-pin <port> [repo-root] [--scan-root ROOT] [--dry-run]``
  — apply the port-pin transition in place. The never-guess gate:
  the requested port must carry upstream evidence — the scan (default:
  the PengyOS workspace) must have found *that* port as a candidate,
  otherwise the command exits 3 without touching anything. On success
  it writes the four transition files (the profile grant + local
  ``# sanctioned:`` line, the ``SANCTIONED_NETWORK_RULES`` entry, the
  two test pins), re-probes the state (must be ``pinned``), and prints
  the remaining reviewed steps (docs, suite, checklist). Exit 0 on
  success, 1 when the post-apply re-probe does not report
  ``pinned``, 2 on bad input or repo drift, 3 when the port has no
  upstream evidence (it is never guessed).
* ``sync-port-pin-docs <port> [repo-root] [--scan-root ROOT]
  [--dry-run]`` — the post-apply docs sync (the first
  ``portpin.POST_APPLY_STEPS`` step): once the repo is ``pinned:<N>``,
  update the four prose doc surfaces (the profiles/pengy-agent network
  comment block, the README profile row, the spec.md pinned-grant
  paragraph, the CHANGELOG entry) from the unbound prose to the pinned
  prose. Per-surface editable/synced/drifted detection makes the sync
  idempotent and all-or-nothing; the edited profile is re-checked for
  strict-clean. Exit 0 when synced (or already in sync), 1 when the
  repo is not ``pinned:<port>``, 2 on bad input or doc drift.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import (
    __version__,
    layoutdiscovery,
    portdiscovery,
    portdocsync,
    portpin,
    portstatus,
    validator,
)

PROG = "pengyos-apparmor"
DESCRIPTION = (
    "Minimal, strict-by-default AppArmor profiles for PengyOS and a "
    "profile validator/lint."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description=DESCRIPTION)
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROG} {__version__}",
        help="print the program version and exit",
    )
    sub = parser.add_subparsers(dest="command")
    p_validate = sub.add_parser(
        "validate",
        help="validate profile grammar and lint over-broad grants",
        description=(
            "Validate AppArmor profiles: header, balanced braces, well-formed "
            "capability/path rules, strict posture, and write confinement. "
            "A directory target validates every profile file it contains "
            "(README.md excluded). Exit code: 1 on any error, 0 otherwise; "
            "--strict also fails on warnings."
        ),
    )
    p_validate.add_argument(
        "targets",
        nargs="+",
        help="profile file(s) or directory(ies) containing profiles",
    )
    p_validate.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors (exit 1 when any warning is reported)",
    )
    p_pin = sub.add_parser(
        "plan-port-pin",
        help="dry-run the model-server port-pin transition for a profile",
        description=(
            "Plan the port-pin transition for a profile's unbound model-server "
            "grant: render the exact port-pinned rule and the matching local "
            "'# sanctioned:' line, transform the profile text in memory, and "
            "validate the result with the strict contract (0 errors AND 0 "
            "warnings). No files are modified. Exit code: 0 when the "
            "transformed profile is strict-clean, 1 when validation finds "
            "issues, 2 on bad input (missing file, invalid port, profile "
            "already pinned or without the unbound grant, or --patch drift). "
            "The port must be the one PengyOS has defined upstream — this "
            "command plans, it never invents one."
        ),
    )
    p_pin.add_argument(
        "port",
        type=_port_pin_arg,
        help="the model server's port (1..65535)",
    )
    p_pin.add_argument(
        "profile",
        nargs="?",
        default="profiles/pengy-agent",
        help="profile file to plan the pin for (default: profiles/pengy-agent)",
    )
    p_pin.add_argument(
        "--patch",
        action="store_true",
        help=(
            "also print the complete mechanical transition as a unified "
            "diff (profile grant + local sanction, the "
            "SANCTIONED_NETWORK_RULES entry, and the two test pins); "
            "fails loudly (exit 2) when the repo has drifted from the "
            "transition recipe"
        ),
    )
    p_pin.add_argument(
        "--repo-root",
        default=".",
        help="tool root the --patch diff is rendered relative to (default: .)",
    )
    p_find = sub.add_parser(
        "find-model-server-port",
        help="scan the PengyOS workspace for a model-server default port definition",
        description=(
            "Scan a directory tree (default: the PengyOS workspace root, two "
            "levels above this tool) for text lines that define the model "
            "server's default port. The scan is deliberately conservative: "
            "both the words 'model server' and 'port' must be on the line. "
            "Candidates are printed as evidence for a human to review; the "
            "command never decides or guesses the port. Exit code: 0 when "
            "candidates are found, 3 when none are (the port-pin item stays "
            "blocked), 2 on bad input (missing root). Every checkout of "
            "this tool (any directory containing its pengyos_apparmor "
            "package) is excluded from the scan; a scan whose root is a "
            "tool checkout reports no candidates."
        ),
    )
    p_find.add_argument(
        "root",
        nargs="?",
        default=str(portdiscovery.DEFAULT_SCAN_ROOT),
        help=(
            "directory tree to scan (default: the PengyOS workspace root — "
            "two levels above this tool, where the upstream specs live)"
        ),
    )
    p_layout = sub.add_parser(
        "find-agent-workspace-layout",
        help="scan the PengyOS workspace for an agent working-layout definition",
        description=(
            "Scan a directory tree (default: the PengyOS workspace root, two "
            "levels above this tool) for text lines that name concrete paths "
            "below the agent's workspace root ("
            f"{layoutdiscovery.AGENT_WORKSPACE_ROOT}) — the recurring upstream "
            "check for the still-blocked workspace file-write surface item. "
            "The scan is deliberately conservative: a hit must be a path "
            "definition below the workspace root (the root grant already "
            "exists, so the root alone is not a candidate). Candidates are "
            "printed as evidence for a human to review; the command never "
            "decides or invents the path set. Exit code: 0 when candidates "
            "are found, 3 when none are (the item stays blocked), 2 on bad "
            "input (missing root). Every checkout of this tool is excluded "
            "from the scan; a scan whose root is a tool checkout reports no "
            "candidates."
        ),
    )
    p_layout.add_argument(
        "root",
        nargs="?",
        default=str(portdiscovery.DEFAULT_SCAN_ROOT),
        help=(
            "directory tree to scan (default: the PengyOS workspace root — "
            "two levels above this tool, where the upstream specs live)"
        ),
    )
    p_status = sub.add_parser(
        "port-pin-status",
        help="report the port-pin transition state (blocked / ready / partial / pinned, commit-ability)",
        description=(
            "Classify the port-pin transition in a repo: inspect the four "
            "transition files (profile grant, the SANCTIONED_NETWORK_RULES "
            "entry, the two test pins) and the upstream port scan. States: "
            "blocked (all unbound, no upstream definition — exit 3), ready "
            "(all unbound, upstream candidates found — exit 0), pinned "
            "(all files consistently pinned to one port — commit-able, "
            "exit 0, when the pinned port is defined upstream; "
            "not-yet-commit-able, exit 4, when it is not yet defined "
            "upstream; the upstream scan is reported as evidence), "
            "partial (mixed or drifted repo — exit 1). For a pinned repo "
            "the verdict is printed as a machine-greppable "
            "'commit-ability: commit-able|not-yet-commit-able' line. "
            "Exit 2 on bad input. Read-only: "
            "nothing is modified and no port is ever guessed."
        ),
    )
    p_status.add_argument(
        "repo_root",
        nargs="?",
        default=".",
        help="tool root containing the four transition files (default: .)",
    )
    p_status.add_argument(
        "--scan-root",
        default=None,
        help=(
            "directory tree to scan for the upstream port definition "
            "(default: the PengyOS workspace root, like find-model-server-port)"
        ),
    )
    p_apply = sub.add_parser(
        "apply-port-pin",
        help="apply the port-pin transition in place (upstream-evidence gated)",
        description=(
            "Apply the complete mechanical port-pin transition in place: "
            "the profile grant plus its local '# sanctioned:' line, the "
            "SANCTIONED_NETWORK_RULES entry, and the two test pins. The "
            "never-guess gate: the requested port must appear among the "
            "upstream scan's candidates (default scan root: the PengyOS "
            "workspace) — otherwise nothing is written and the command "
            "exits 3. On success the state re-probe must report 'pinned' "
            "and the transformed profile must be strict-clean. Exit code: "
            "0 applied and consistent, 1 the post-apply re-probe is not "
            "pinned, 2 on bad input (missing roots, invalid port, repo "
            "drifted from the transition recipe) or an interrupted "
            "staged-atomic commit (a file-write failure; no file is ever "
            "left torn), 3 when the port has no upstream evidence. "
            "--dry-run computes and prints the changes without writing."
        ),
    )
    p_apply.add_argument(
        "port",
        type=_port_pin_arg,
        help="the model server's port (1..65535; must be found upstream)",
    )
    p_apply.add_argument(
        "repo_root",
        nargs="?",
        default=".",
        help="tool root containing the four transition files (default: .)",
    )
    p_apply.add_argument(
        "--scan-root",
        default=None,
        help=(
            "directory tree to scan for the upstream port definition "
            "(default: the PengyOS workspace root, like find-model-server-port)"
        ),
    )
    p_apply.add_argument(
        "--dry-run",
        action="store_true",
        help="compute and print the transition without writing any file",
    )
    p_sync = sub.add_parser(
        "sync-port-pin-docs",
        help="sync the prose docs to the pinned port (post-apply step)",
        description=(
            "Post-apply docs sync for the port-pin transition: once the repo "
            "is 'pinned:<N>' (all four mechanical transition files "
            "consistent), update the four prose doc surfaces from the "
            "unbound prose to the pinned prose — the profiles/pengy-agent "
            "network comment block, the README profile row, the spec.md "
            "pinned-grant paragraph, and the CHANGELOG entry. Each surface "
            "is editable (pre-sync anchor present — rewritten), already in "
            "sync (pinned marker present — untouched), or drifted (neither "
            "— fail loudly, nothing written). Exit code: 0 synced or "
            "already in sync, 1 when the repo is not pinned to the "
            "requested port (apply the transition first), 2 on bad input "
            "(missing roots, invalid port), doc drift, or an interrupted "
            "staged-atomic commit (a file-write failure; no file is ever "
            "left torn). --dry-run prints "
            "the docs diff without writing. The port is never guessed: it "
            "must be the one the repo already pins."
        ),
    )
    p_sync.add_argument(
        "port",
        type=_port_pin_arg,
        help="the model server's port (1..65535; must be the port the repo pins)",
    )
    p_sync.add_argument(
        "repo_root",
        nargs="?",
        default=".",
        help="tool root containing the transition files and the docs (default: .)",
    )
    p_sync.add_argument(
        "--scan-root",
        default=None,
        help=(
            "directory tree to scan for the upstream port definition "
            "(default: the PengyOS workspace root; reported as evidence only)"
        ),
    )
    p_sync.add_argument(
        "--dry-run",
        action="store_true",
        help="print the docs diff without writing any file",
    )
    return parser


def cmd_validate(args: argparse.Namespace) -> int:
    seen: dict = {}
    reports = []
    for target in args.targets:
        t = Path(target)
        if t.is_file():
            files = [t]
        elif t.is_dir():
            files = validator.discover_profiles(t)
        else:
            print(f"{PROG}: no such file or directory: {target}", file=sys.stderr)
            return 2
        for path in files:
            if path in seen:
                continue
            seen[path] = True
            reports.append(validator.validate_profile(path))

    error_count = sum(len(r.errors) for r in reports)
    warn_count = sum(len(r.warnings) for r in reports)
    for report in reports:
        for issue in report.issues:
            print(issue.format(report.name))
        if report.issues:
            continue
        print(f"{report.name}: OK")

    print(
        f"{len(reports)} profile(s): {error_count} error(s), {warn_count} warning(s)"
    )
    if error_count:
        return 1
    if warn_count and args.strict:
        return 1
    return 0


def _port_pin_arg(value: str) -> int:
    """argparse type for the plan-port-pin port (exit 2 on bad input)."""
    try:
        return portpin.normalize_port(value)
    except portpin.PortPinTransitionError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def cmd_port_pin(args: argparse.Namespace) -> int:
    path = Path(args.profile)
    if not path.is_file():
        print(f"{PROG}: no such file or directory: {args.profile}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    try:
        plan = portpin.plan_port_pin(args.port, profile=path.name)
        report = portpin.verify_port_pin(text, plan)
    except portpin.PortPinTransitionError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2

    print(f"port-pin transition plan for {path.name} (port {plan.port})")
    print(f"  old rule: {plan.old_rule},")
    print(f"  new rule: {plan.new_rule},")
    print(f"  new line: {plan.directive}")
    print("  (dry run — no files are modified)")

    if args.patch:
        try:
            patch = portpin.render_transition_patch(args.port, args.repo_root)
        except portpin.PortPinTransitionError as exc:
            print(f"{PROG}: {exc}", file=sys.stderr)
            return 2
        print("  mechanical transition diff (review it, then git apply):")
        print(patch)
    print("  remaining reviewed steps:")
    for step in portpin.REMAINING_MANUAL_STEPS:
        print(f"    - {step}")

    for issue in report.issues:
        print(issue.format(plan.profile))
    if not report.issues:
        print(f"{plan.profile}: OK (strict-clean)")
        return 0
    errors, warnings = len(report.errors), len(report.warnings)
    print(
        f"{plan.profile}: {errors} error(s), {warnings} warning(s) — "
        "the transformed profile is NOT strict-clean; do not ship the pin"
    )
    return 1


def cmd_find_model_server_port(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if not root.is_dir():
        print(f"{PROG}: no such directory: {args.root}", file=sys.stderr)
        return 2
    candidates = portdiscovery.find_model_server_port(root)
    if not candidates:
        print(
            f"no model-server port definition found under {root}"
        )
        print(
            "the port-pin item stays blocked — do not guess a port; the "
            "port must come from PengyOS upstream"
        )
        return 3
    for c in candidates:
        print(f"candidate: {c.path}:{c.lineno}: port {c.port}")
        print(f"  {c.line}")
    distinct = sorted({c.port for c in candidates})
    if len(distinct) == 1:
        print(
            f"one distinct port candidate: {distinct[0]} — review the line "
            "above, then run: pengyos-apparmor plan-port-pin "
            f"{distinct[0]} --patch"
        )
    else:
        print(
            f"{len(distinct)} distinct port candidates ({', '.join(map(str, distinct))}) "
            "— confirm which one PengyOS defines before pinning"
        )
    return 0


def cmd_find_agent_workspace_layout(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if not root.is_dir():
        print(f"{PROG}: no such directory: {args.root}", file=sys.stderr)
        return 2
    candidates = layoutdiscovery.find_agent_workspace_layout(root)
    if not candidates:
        print(
            f"no agent workspace layout definition found under {root}"
        )
        print(
            "the workspace file-write surface item stays blocked — do not "
            "invent a path set; the layout must come from PengyOS upstream"
        )
        return 3
    for c in candidates:
        print(
            f"candidate: {c.path}:{c.lineno}: {', '.join(c.surfaces)}"
        )
        print(f"  {c.line}")
    distinct = sorted({s for c in candidates for s in c.surfaces})
    if len(candidates) == 1 and len(distinct) == 1:
        print(
            f"one layout candidate (surface: {distinct[0]}) — review the "
            "line above, then pin the workspace file-write surface "
            "(TODO.md: 'Pin the pengy-agent's workspace "
            "file-write surface')"
        )
    else:
        print(
            f"{len(distinct)} distinct surface candidate(s) "
            f"({', '.join(distinct)}) — confirm which layout PengyOS "
            "defines before widening the profile"
        )
    return 0


def cmd_port_pin_status(args: argparse.Namespace) -> int:
    repo = Path(args.repo_root)
    scan_root = args.scan_root if args.scan_root is not None else portdiscovery.DEFAULT_SCAN_ROOT
    if not Path(scan_root).is_dir():
        print(f"{PROG}: no such directory: {scan_root}", file=sys.stderr)
        return 2
    try:
        status = portstatus.inspect_transition(repo, scan_root)
    except portstatus.PortStatusError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    except portdiscovery.PortDiscoveryError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2

    state = status.state
    if state == portstatus.STATE_PINNED:
        title = f"PINNED to port {status.port} (all transition files consistent)"
    elif state == portstatus.STATE_READY:
        title = "READY (upstream candidates found; the transition is not yet applied)"
    elif state == portstatus.STATE_BLOCKED:
        title = "BLOCKED (port not yet defined upstream)"
    else:
        title = "PARTIAL (the transition is inconsistent across files — drift)"
    print(f"port-pin transition state: {title}")
    for f in status.files:
        print(f"  {f.rel}: {f.form} — {f.detail}")
    print(f"  upstream scan ({scan_root}):")
    if not status.candidates:
        print("    no model-server port definition found")
    for c in status.candidates:
        print(f"    candidate: {c.path}:{c.lineno}: port {c.port}")
        print(f"      {c.line}")
    if state == portstatus.STATE_BLOCKED:
        print(
            "  action: stays blocked — do not guess a port; re-run once "
            "PengyOS defines the model server's default port"
        )
    elif state == portstatus.STATE_READY:
        distinct = sorted({c.port for c in status.candidates})
        if len(distinct) == 1:
            print(
                f"  action: pengyos-apparmor plan-port-pin {distinct[0]} --patch "
                "(review the candidates above first)"
            )
        else:
            print(
                "  action: confirm which port PengyOS defines, then "
                "pengyos-apparmor plan-port-pin <N> --patch"
            )
    elif state == portstatus.STATE_PINNED:
        distinct = {c.port for c in status.candidates}
        # The commit-ability verdict is the machine signal for the
        # runbook's final step (commit + flip the checklist): a pinned
        # repo is commit-able only when the pinned port is defined
        # upstream. The two pinned sub-states are distinct exit codes
        # (0 vs 4) so a wrapper can gate on them without parsing prose.
        if status.commitable:
            print(
                f"  commit-ability: {portstatus.COMMITABLE} — port "
                f"{status.port} is defined upstream; commit the pin and "
                "flip the checklist item"
            )
            print(f"  upstream evidence: port {status.port} found in the scan above")
        else:
            print(
                f"  commit-ability: {portstatus.NOT_YET_COMMITABLE} — port "
                f"{status.port} is not defined upstream yet; do not commit "
                "the pin until PengyOS defines it upstream (the repo is "
                "consistent, the decision is not upstream's yet)"
            )
            if distinct:
                print(
                    f"  upstream evidence: the scan found candidate port(s) "
                    f"{', '.join(map(str, sorted(distinct)))} — none is the "
                    f"pinned port {status.port}"
                )
            else:
                print("  upstream evidence: no model-server port definition found")
    else:
        print(
            "  action: reconcile the files above — the rendered patch must "
            "apply to all four files together (pengyos-apparmor "
            "plan-port-pin <N> --patch); re-derive it by hand if the repo "
            "has drifted from the transition recipe"
        )
    if state == portstatus.STATE_PARTIAL:
        return portstatus.EXIT_PARTIAL
    if state == portstatus.STATE_BLOCKED:
        return portstatus.EXIT_BLOCKED
    if state == portstatus.STATE_PINNED and not status.commitable:
        return portstatus.EXIT_NOT_YET_COMMITABLE
    return portstatus.EXIT_OK


def cmd_apply_port_pin(args: argparse.Namespace) -> int:
    repo = Path(args.repo_root)
    if not repo.is_dir():
        print(f"{PROG}: no such directory: {args.repo_root}", file=sys.stderr)
        return 2
    scan_root = (
        args.scan_root if args.scan_root is not None else portdiscovery.DEFAULT_SCAN_ROOT
    )
    if not Path(scan_root).is_dir():
        print(f"{PROG}: no such directory: {scan_root}", file=sys.stderr)
        return 2

    candidates = portdiscovery.find_model_server_port(scan_root)
    distinct = sorted({c.port for c in candidates})
    # The never-guess gate: the requested port must carry upstream
    # evidence (the scan found it as a definition candidate). No
    # override flag exists on purpose — a port PengyOS has not defined
    # is never pinned by this command.
    if args.port not in distinct:
        print(
            f"port {args.port} has no upstream evidence — the scan under "
            f"{scan_root} found "
            f"{'no model-server port definition' if not distinct else 'candidate port(s) ' + ', '.join(map(str, distinct))}"
        )
        print(
            "refusing to pin: the port must come from PengyOS upstream "
            "(never guessed). Re-run once the scan finds port "
            f"{args.port}, or review with: pengyos-apparmor "
            f"port-pin-status {repo} --scan-root {scan_root}"
        )
        return 3
    for c in candidates:
        if c.port == args.port:
            print(f"upstream evidence: {c.path}:{c.lineno}: port {c.port}")
            print(f"  {c.line}")
    if len(distinct) > 1:
        print(
            f"note: {len(distinct)} distinct candidate ports "
            f"({', '.join(map(str, distinct))}) — pinning {args.port} as "
            "explicitly requested"
        )

    try:
        edits = portpin.compute_transition(args.port, repo)
    except portpin.PortPinTransitionError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2

    profile_edit = next(e for e in edits if e.rel == "profiles/pengy-agent")
    report = validator.validate_text(profile_edit.new, "pengy-agent")
    if not report.ok or report.warnings:
        for issue in report.issues:
            print(issue.format("pengy-agent"))
        print(
            "the transformed profile is not strict-clean — "
            "nothing was written"
        )
        return 1

    for edit in edits:
        added = len([l for l in edit.new.splitlines() if l not in edit.old.splitlines()])
        print(f"  {edit.rel}: {added} line(s) added, anchor line(s) replaced")
    if args.dry_run:
        print("dry run — no files were modified")
        return 0

    try:
        portpin.apply_transition(args.port, repo)
    except OSError as exc:
        print(f"{PROG}: the staged-atomic commit was interrupted: {exc}", file=sys.stderr)
        print(
            "every file is either its complete old or complete new content "
            "(never torn) — check with: pengyos-apparmor "
            f"port-pin-status {repo} --scan-root {scan_root}",
            file=sys.stderr,
        )
        return 2
    status = portstatus.inspect_transition(repo, scan_root)
    if status.state != portstatus.STATE_PINNED or status.port != args.port:
        print(
            f"post-apply re-probe reports {status.state!r} (expected "
            f"pinned:{args.port}) — the repo is inconsistent:"
        )
        for f in status.files:
            print(f"  {f.rel}: {f.form} — {f.detail}")
        return 1

    print(f"port-pin transition applied: port {args.port} pinned in {len(edits)} files")
    print(f"state re-probe: pinned:{args.port} (all transition files consistent)")
    print("remaining reviewed steps:")
    for step in portpin.POST_APPLY_STEPS:
        print(f"    - {step}")
    return 0


def cmd_sync_port_pin_docs(args: argparse.Namespace) -> int:
    repo = Path(args.repo_root)
    if not repo.is_dir():
        print(f"{PROG}: no such directory: {args.repo_root}", file=sys.stderr)
        return 2
    scan_root = (
        args.scan_root if args.scan_root is not None else portdiscovery.DEFAULT_SCAN_ROOT
    )
    if not Path(scan_root).is_dir():
        print(f"{PROG}: no such directory: {scan_root}", file=sys.stderr)
        return 2

    try:
        status = portdocsync.check_transition_port(repo, scan_root, args.port)
    except portdocsync.RepoNotPinnedError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    except portstatus.PortStatusError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    except portdiscovery.PortDiscoveryError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    except portpin.PortPinTransitionError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2

    print(
        f"docs sync for port {args.port} (repo pinned: all four transition "
        "files consistent)"
    )
    if status.candidates and status.port in {c.port for c in status.candidates}:
        c = next(c for c in status.candidates if c.port == status.port)
        print(f"upstream evidence: {c.path}:{c.lineno}: port {c.port}")
        print(f"  {c.line}")
    elif status.candidates:
        print(
            f"upstream evidence: the scan found candidate port(s) "
            f"{', '.join(map(str, sorted({c.port for c in status.candidates})))} "
            f"— not the pinned port {args.port}; the docs sync does not "
            "decide the port (the repo pin stands as-is)"
        )
    else:
        print(
            f"upstream evidence: no model-server port definition for "
            f"port {args.port} found — the docs describe the repo pin; "
            "do not commit until PengyOS defines it upstream"
        )

    try:
        edits = portdocsync.compute_doc_edits(args.port, repo, scan_root)
    except portdocsync.DocSyncError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2

    edited = [e for e in edits if e.status == portdocsync.DOC_FILE_EDITABLE]
    for e in edits:
        if e.status == portdocsync.DOC_FILE_EDITABLE:
            print(
                f"  {e.rel}: editable — unbound prose replaced with the "
                "pinned form"
            )
        else:
            print(f"  {e.rel}: synced — already carries the pinned form")

    if args.dry_run:
        if edited:
            print("docs diff (dry run):")
            print(portdocsync.render_doc_diff(edits))
        print("dry run — no files were modified")
        return 0

    try:
        portdocsync.sync_docs(args.port, repo, scan_root)
    except OSError as exc:
        print(
            f"{PROG}: the staged-atomic docs commit was interrupted: {exc}",
            file=sys.stderr,
        )
        print(
            "every doc file is either its complete pre-sync or complete "
            "post-sync content (never torn) — re-run the sync to finish "
            "(it is idempotent)",
            file=sys.stderr,
        )
        return 2
    if edited:
        print(f"docs synced for port {args.port}: {len(edited)} file(s) updated")
    else:
        print(f"docs already in sync for port {args.port} — nothing was written")
    print("remaining reviewed steps:")
    for step in portpin.POST_APPLY_STEPS[1:]:
        print(f"    - {step}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "plan-port-pin":
        return cmd_port_pin(args)
    if args.command == "find-model-server-port":
        return cmd_find_model_server_port(args)
    if args.command == "find-agent-workspace-layout":
        return cmd_find_agent_workspace_layout(args)
    if args.command == "port-pin-status":
        return cmd_port_pin_status(args)
    if args.command == "apply-port-pin":
        return cmd_apply_port_pin(args)
    if args.command == "sync-port-pin-docs":
        return cmd_sync_port_pin_docs(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
