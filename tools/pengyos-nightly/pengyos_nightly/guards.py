"""The guards. Each one is named, each one is tested, and none of them negotiate.

Two of them are not planner rules but hard walls, checked again on the way out:

* `CHANNEL` is a constant. The act lane publishes to `testing` and has no code
  path to `update-promote` or to a stable pointer; `assert_locked()` re-checks
  every argv the tool is about to run, so a future edit that assembles a
  different command fails here rather than in front of machines.
* `clean_tree` / `key_present` / `gate_first` are checked at act time and not
  only in the plan, because the tree and the key can change between the two.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
from pathlib import Path

from pengyos_nightly import edits as E

CHANNEL = "testing"          # the only channel this tool may ever publish to
PROMOTE = "update-promote"   # the command this tool must never run

# Components the act lane may not touch in this phase, with the reason that goes
# into the plan. Report-only here is a decision, not an oversight.
DENIED = {
    "linux": "a kernel bump invalidates the NVIDIA module build and every package stamp "
             "chain in the tree; it needs its own lane and its own gate",
    "nvidia-open": "a driver change is only proven on real hardware, which QEMU cannot do",
    "buildroot": "a Buildroot release change means a new tree and a full rebuild of every "
                 "edition; that is a scheduled event, not a nightly one",
    "cosmic": "COSMIC is host-built from pinned commits and moves as an epoch, which is a "
              "major decision by design",
}


class GuardError(Exception):
    """Raised when a hard wall is reached. The act lane never catches it."""


def assert_locked(argv: list[str]) -> list[str]:
    """Return argv unchanged, or raise. The stable channel is not reachable.

    Checked on the assembled command rather than on the planner's intent, so the
    guard survives someone later reordering or extending the argv.
    """
    joined = " ".join(argv)
    for word in (PROMOTE, "--channel stable", "--channel=stable"):
        if word in joined:
            raise GuardError(f"refusing to run a command that touches stable: {joined!r}")
    if "--channel" in argv:
        i = argv.index("--channel")
        if i + 1 < len(argv) and argv[i + 1] != CHANNEL:
            raise GuardError(f"refusing to publish to channel {argv[i + 1]!r}: this tool only "
                             f"ever publishes to {CHANNEL!r}")
    return argv


def lane_allowed(action: dict, cfg: dict) -> tuple[bool, str]:
    lane = action.get("lane")
    if lane == "major":
        return False, "major_never: a new release series is a human decision"
    if lane == "bump":
        # Its own lane, so that scheduling a Buildroot bump cannot quietly enable the
        # other major findings (the kernel, the NVIDIA driver) that share the label.
        if not (cfg or {}).get("bump_buildroot"):
            return False, "lane_disabled: the bump lane exists only when bump_buildroot is set"
        return True, "lane bump is enabled by the scheduled event"
    if lane not in cfg["lanes"]:
        return False, f"lane_disabled: {lane!r} is not in lanes={cfg['lanes']}"
    return True, f"lane {lane} is enabled"


def major_never(action: dict) -> tuple[bool, str]:
    """A version bump may not cross a release series. The patch shape does not move
    a version at all, so it has nothing to check here — `stock_version_immovable`
    is what keeps the patch shape from pretending to be a bump."""
    if action.get("shape") == "patch_dir":
        return True, "patch shape: the pinned version does not move"
    if action.get("shape") == "tree_move":
        return True, "tree move: crossing to a new release series is the event itself"
    if action.get("bump_kind") == "major":
        return False, f"major_never: {action['current']} -> {action['target']} changes the series"
    if action.get("bump_kind") == "none":
        return False, "nothing_newer: upstream has no newer release than the pin"
    return True, f"{action['current']} -> {action['target']} is patch-level"


def stock_version_immovable(action: dict) -> tuple[bool, str]:
    """The routine lane means 'move the pin', and a stock package's pin is not ours
    to move (see edits.py). Routine bumps apply to recipes we author."""
    if action.get("lane") == "routine" and action.get("shape") == "patch_dir":
        return False, ("stock_version_immovable: a stock package moves with a Buildroot "
                       "release, not with a nightly bump; only recipes we author are "
                       "bumped")
    return True, "the pin is ours to move"


def evidence_required(action: dict) -> tuple[bool, str]:
    if action.get("lane") != "security":
        return True, "routine lane: no CVE evidence required"
    if action.get("shape") == "patch_dir":
        hit = [c for c in action.get("cves", []) if c.get("affected")]
        if not hit:
            return False, (f"evidence_required: NVD does not place {action.get('current')} "
                           "inside any affected range here, so there is nothing to fix")
        return True, (f"{len(hit)} CVE match(es) put {action['current']} inside NVD's "
                      "affected range")
    covered = [c for c in action.get("cves", []) if c.get("covered")]
    if not covered:
        return False, (f"evidence_required: nothing proves {action.get('target')} closes any "
                       "CVE match here")
    return True, f"{len(covered)} CVE match(es) closed by {action['target']} per NVD's range"


def fix_commits_available(action: dict) -> tuple[bool, str]:
    """A patch is carried per CVE, and only for a CVE NVD names an upstream fix for.

    Per CVE, not per component: `edits.carried_cves` decides what is written to the
    tree and the planner takes `closes` from the same function, so a component whose
    CVEs name a mix of fetchable fixes and vendor errata is actionable for the
    fetchable ones and names the rest as still open in the notes. Refusing a whole
    component because one CVE has no named commit throws away the fixes upstream
    *did* publish — measured 2026-10-06, that veto was holding 28 fetchable patches
    behind 17 CVEs that have nothing to fetch.
    """
    if action.get("shape") != "patch_dir":
        return True, "bump shape: no patch to fetch"
    need = [c for c in action.get("cves", []) if c.get("affected")]
    carried = E.carried_cves(need)
    if not carried:
        return False, ("no_fix_commit: NVD names no upstream commit or review request for "
                       + ", ".join(c["cve"] for c in need)
                       + " (vendor errata are not something to cherry-pick)")
    missing = [c["cve"] for c in need if c["cve"] not in carried]
    if missing:
        return True, (f"{len(carried)} of {len(need)} affected CVE(s) name an upstream fix; "
                      f"report-only: {', '.join(missing)}")
    return True, f"{len(carried)} upstream fix commit(s) named by NVD"


def confirmed_only(action: dict) -> tuple[bool, str]:
    unsure = [c["cve"] for c in action.get("cves", []) if c.get("unsure")]
    if unsure:
        return False, f"confirmed_only: {', '.join(unsure)} are pkg-stats 'unsure' matches"
    return True, "every match is a confirmed pkg-stats CVE"


def not_host_only(pkg: dict) -> tuple[bool, str]:
    if pkg.get("host_only"):
        return False, "not_host_only: a build-host tool is not in any image"
    if not pkg.get("editions"):
        return False, "not_host_only: the package is in no configured edition"
    return True, f"in {', '.join(pkg['editions'])}"


def denied_component(component: str, cfg: dict | None = None) -> tuple[bool, str]:
    if component in DENIED:
        # The one thing that lifts a report-only decision is the operator scheduling it.
        # A Buildroot bump is not something the loop may decide for itself; when
        # `bump_buildroot` names a target, that is the operator's decision, for that
        # event only.
        if component == "buildroot" and (cfg or {}).get("bump_buildroot"):
            return True, (f"operator scheduled it: bump_buildroot = {cfg['bump_buildroot']} "
                          "lifts the report-only default for this event")
        return False, f"denied_component: {component} is report-only — {DENIED[component]}"
    return True, "component is actable"


def bump_event_approved(component: str, target: str, cfg: dict) -> tuple[bool, str]:
    """A new Buildroot release series is a *finding*. Moving the tree to it is a decision.

    The finding arrives on its own (sources.buildroot_findings reads the tag list); what
    must not happen is the loop deciding quarterly that every edition rebuilds against a
    new line. `bump_buildroot = "2026.11"` in nightly.toml is the operator scheduling that
    event, and the target has to match what was scheduled.
    """
    if component != "buildroot":
        return True, "not a Buildroot bump"
    want = (cfg or {}).get("bump_buildroot") or ""
    if not want:
        return False, ("bump_event_approved: no Buildroot bump is scheduled; a newer release "
                       "series is a finding, and moving the tree is the operator's decision")
    if want != target:
        return False, (f"bump_event_approved: the scheduled bump is to {want}, not {target}")
    return True, f"scheduled by the operator: bump_buildroot = {want}"


def tree_move_executable(shape: str) -> tuple[bool, str]:
    """A tree move is planned and reviewed here; the act lane cannot run one yet.

    Refusing it by name is the point: a plan that *allows* a tree move would hand it to an
    act lane that has no full-rebuild path, and the honest failure would surface at 21:00
    as a build error rather than as a guard that says what is not built.
    """
    if shape != "tree_move":
        return True, "not a tree move"
    return False, ("tree_move_not_executable: the bump lane can plan, guard and account for "
                   "a tree move (spec phase 4), but the act lane has no full-rebuild path "
                   "for it yet, so it is published as a decision to review, not an action")


def release_script_executable(repo: Path) -> tuple[bool, str]:
    """The one command the act lane cannot run without must actually be runnable.

    Commit 2d894fba (2026-10-07, "Build path: document and run pengyos-build acquire")
    flipped scripts/release.sh from git mode 100755 to 100644 while editing it. The
    2026-10-08 batch found out at the last step - after the branch, the edits, four
    pre-flights, three commits and a green gate - as a PermissionError that propagated
    out of run_batch, so no failure was recorded and main's working tree was never
    restored. A missing exec bit is a precondition, not a build failure: name it before
    a branch exists, when nothing has been spent yet.
    """
    script = repo / "scripts" / "release.sh"
    if not script.is_file():
        return False, f"release_script_missing: {script} is not in the repo"
    if not os.access(script, os.X_OK):
        return False, ("release_script_not_executable: scripts/release.sh is not runnable "
                       "(git file mode 100644). chmod +x it and commit the mode.")
    return True, "scripts/release.sh is runnable"



def patch_failure(component: str, st: dict, version: str = "") -> dict:
    """The recorded pre-flight failure for a component, or `{}` when it does not bind.

    The **only** reader of `state.json:patch_failures`. The guard and the planner ask
    the same question through this function, because two copies of the lookup is how a
    guard and a plan start disagreeing about the same package.

    A known-bad verdict is about a **pinned version**: a commit authored against
    ffmpeg 8.x that failed on 6.1.5 says nothing about the same commit on 6.1.6, so an
    entry recorded against a pin we no longer ship stops binding when the pin moves.
    An entry with no recorded version predates pin tracking and is honoured as
    version-independent — dropping a recorded failure by accident is the wrong
    direction to err in.
    """
    bad = (st.get("patch_failures") or {}).get(component) or {}
    if not bad:
        return {}
    recorded = str(bad.get("version") or "")
    if recorded and version and recorded != version:
        return {}
    return bad


def known_bad_fixes(component: str, st: dict, version: str = "") -> set[str]:
    """Fix URLs a pre-flight already proved do not apply to this component.

    Empty when nothing was recorded, and empty when the only record is about a version
    we no longer pin (see `patch_failure`). A record from before per-fix tracking has
    no fix list and therefore contributes nothing here — that case is a component-wide
    block, and `patch_does_not_apply` is the one that says so.
    """
    return {f for f in (patch_failure(component, st, version).get("fixes") or []) if f}


def patch_does_not_apply(component: str, st: dict,
                         fixes: list[str] | None = None) -> tuple[bool, str]:
    """A **specific upstream fix** that was proved not to apply is not retried.

    Scoped to the fixes, not to the component, deliberately. The cheap-but-not-free
    pre-flight must not let one bad cherry-pick block every future fix for a package:
    a commit authored against ffmpeg 8.x failing on 6.1.5 says nothing about a fix
    written for 6.1.5. `fixes` is the candidate's upstream fix URLs; the candidate is
    refused when it wants one that already failed, and allowed when it does not.

    A record with no fix list predates per-fix tracking. It is honoured as a
    component-wide block rather than silently dropped: refusing something for the
    wrong reason in the open beats un-blocking it by accident.
    """
    bad = (st.get("patch_failures") or {}).get(component)
    if not bad:
        return True, "no recorded patch failure"
    known = sorted({f for f in (bad.get("fixes") or []) if f})
    if not known:
        return False, (f"patch_does_not_apply: {component}'s upstream patch failed on "
                       f"{bad.get('where', 'the source')} on {bad.get('date', '?')}; recorded "
                       "before per-fix tracking, so it needs a human to adapt the patch, "
                       "pick another fix, or clear state.json:patch_failures")
    hit = sorted({f for f in (fixes or []) if f in set(known)})
    if hit:
        return False, (f"patch_does_not_apply: {len(hit)} of {component}'s candidate fix(es) "
                       f"already failed on {bad.get('where', 'the source')} "
                       f"({bad.get('date', '?')}); {len(known)} fix(es) are known-bad for "
                       f"{component}, the rest are still tryable")
    return True, (f"{component} has {len(known)} known-bad fix(es); this candidate names "
                  "none of them")


def cooldown(component: str, log: dict, days: int, today: dt.date) -> tuple[bool, str]:
    last = log.get(component)
    if not last:
        return True, "never acted on before"
    try:
        then = dt.date.fromisoformat(str(last)[:10])
    except ValueError:
        return True, "never acted on before"
    gap = (today - then).days
    if gap < days:
        return False, f"cooldown: {component} was bumped {gap}d ago (needs {days}d)"
    return True, f"last bumped {gap}d ago"


def cap(index: int, limit: int) -> tuple[bool, str]:
    if index >= limit:
        return False, f"cap: {limit} action(s) per night is the limit"
    return True, f"action {index + 1} of {limit}"


def release_cap(published_today: int, limit: int) -> tuple[bool, str]:
    """How many signed images may reach the feed in one night.

    Separate from `cap` on purpose: `cap` sizes the batch, this limits the blast
    radius. A batch of twelve changes is still one image. Checked against what has
    already been published today, so a second `act --apply` cannot publish a second
    release and a re-run after a success is a no-op rather than a surprise.
    """
    if published_today >= limit:
        return False, (f"release_cap: {published_today} release(s) already published today, "
                       f"limit is {limit}")
    return True, f"release {published_today + 1} of {limit} today"


#: Edit kinds `apply_edits` makes idempotent: the line is written only when it is
#: missing, so applying the same one twice is the same as applying it once.
IDEMPOTENT_KINDS = ("ensure", "append")


def batch_conflict(actions: list[dict]) -> tuple[bool, str]:
    """Two changes in one batch may not edit the same file *differently*.

    Batching is safe when the changes are independent, which they usually are: a
    patch under patches/<pkg>/ and a version in a recipe we author do not touch. When
    two candidates do want the same file — the same recipe, the same layer config —
    the order they are applied in changes the result. That is a human's decision, so
    it is refused before a branch exists rather than resolved by whoever ran last.

    Identical idempotent edits are not an ordering problem, and calling them one was
    a real cost: every stock-package action carries the same `ensure` of
    BR2_GLOBAL_PATCH_DIR into core.config, so four security actions "conflicted" with
    each other and 25 CVE fixes across ffmpeg, gst1-plugins-bad, gst1-plugins-good and
    libsndfile sat unpublished from 2026-10-07 to 2026-10-08. `ensure`/`append` write
    the line only if it is missing, so two of them are one change. A `replace` or a
    `create` on the same path still conflicts even when the text matches: applying it
    twice is not the same as applying it once.
    """
    seen: dict[str, tuple[tuple[str, str], str]] = {}
    for a in actions:
        for e in a.get("edits") or []:
            path = e.get("path", "")
            if not path:
                continue
            sig = (e.get("kind", ""), e.get("new", ""))
            prev = seen.get(path)
            if prev is not None:
                same_line = prev[0] == sig and sig[0] in IDEMPOTENT_KINDS
                if not same_line:
                    return False, (f"batch_conflict: {prev[1]} and {a['id']} both edit "
                                   f"{path}; one of them needs a human to sequence it")
                continue
            seen[path] = (sig, a["id"])
    return True, f"{len(seen)} file(s), no two actions editing the same one differently"


def patch_shape_allowed(shape: str, cfg: dict) -> tuple[bool, str]:
    if shape == "patch_dir" and not cfg["allow_patch_shape"]:
        return False, ("patch_shape_off: cherry-picking upstream fix commits into "
                       "BR2_GLOBAL_PATCH_DIR is planned but not applied unless "
                       "allow_patch_shape = true")
    return True, f"shape {shape} is allowed"


def clean_tree(repo: Path, run=subprocess.run) -> tuple[bool, str]:
    out = run(["git", "-C", str(repo), "status", "--porcelain"],
              capture_output=True, text=True).stdout.strip()
    if out:
        return False, "clean_tree: the working tree has uncommitted changes"
    return True, "working tree is clean"


def key_present(key: str | None) -> tuple[bool, str]:
    if not key:
        return False, "key_present: no release key (PENGYOS_RELEASE_KEY or the key file)"
    if not Path(key).expanduser().is_file():
        return False, f"key_present: {key} is not a readable file"
    return True, "release key is present"
