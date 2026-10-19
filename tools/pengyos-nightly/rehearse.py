#!/usr/bin/env python3
"""Rehearse tonight's plan against the REAL Buildroot trees — no publish, no state.

Why this exists and why it is not a test. The act lane's pre-flight is the thing that
decides what gets published, and the unit suite runs a fake `make`. This runs the real
`make <pkg>-dirclean` / `<pkg>-patch` against the real output trees, so the loop is
proven against Buildroot's actual behaviour before it is left alone with it at 21:00.
Everything this file has ever found, unit tests could not:

  1. `apply-patches.sh` runs `set -e`, so its own "Patch failed! Please fix <patch>!"
     echo is never reached — the culprit is the last `Applying <name> using` line.
  2. Buildroot's series-file `-pN` field is documented and then discarded
     (`cut -d ' ' -f1`, then line 119 hardcodes `-p1`), so a re-rooted monorepo patch
     cannot be expressed as a series entry; it has to be rewritten.
  3. A GitLab MR / GitHub PR `.patch` is a whole concatenated series (gstreamer MR 11243
     = 4 patches in one file); unsplit, patch reads the next email's headers as garbage.
  4. 2026-10-07: which of the 31 planned fixes actually apply on the pinned versions —
     ffmpeg 11 of 19, gst1-plugins-good 6 of 10, libsndfile 0 of 1 — and therefore how
     many CVE matches a night really closes (14, not 25).

It is a rehearsal, not a release: a scratch branch, no publish, no state file touched,
`main` restored, and HEAD proved unchanged at the end. It refuses to run on a dirty tree
for the same reason the act lane does.

Usage:
    python3 rehearse.py                      # every allowed action in plan.json
    python3 rehearse.py ffmpeg libsndfile    # only these components
    python3 rehearse.py --edition pengyos-server

Takes a few minutes: it is a real extract-and-patch per package.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]          # <repo>/tools/pengyos-nightly/
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pengyos_nightly import act as A, edits as E    # noqa: E402

BUILD_ROOT = Path(os.environ.get("PENGYOS_BUILD_ROOT") or (Path.home() / "build/pengyos"))
BR = "2026.08"
PLAN = BUILD_ROOT / "nightly/plan.json"


def git(*args, check=True) -> str:
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stdout}{r.stderr}")
    return r.stdout.strip()


def _generated_hash(a: dict, edition: str, tree: str) -> E.Edit | None:
    """The act lane's generated-hash step, run the same way: build the archive, read the
    digest Buildroot produced, write it, and prove the download step passes with it.

    Returns the edit so the caller can append it to the action's edits and the restore
    path takes it back out with the rest. Returns None and prints the failure when the
    build cannot produce the archive - a rehearsal result, not a crash.
    """
    want = (a.get("hash") or {}).get("filename") or ""
    hash_path = REPO / f"buildroot-external/package/{a['component']}/{a['component']}.hash"
    try:
        hi = E.generated_hash(subprocess.run, BUILD_ROOT, BR, tree, edition,
                              a["component"], want)
        he = E.generated_hash_edit(a["component"], hash_path.read_text(),
                                   a.get("current") or "", hi)
        E.apply_edits(REPO, [he])
        E.verify_source(subprocess.run, BUILD_ROOT, BR, tree, edition, a["component"])
    except (E.GeneratedHashError, RuntimeError, OSError, ValueError) as exc:
        print(f"    generated hash FAILED for {a['component']}: {str(exc)[:200]}", flush=True)
        return None
    print(f"    generated hash {a['component']}: {hi.sha256[:16]}... verified", flush=True)
    return he


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("components", nargs="*", help="only rehearse these components")
    ap.add_argument("--edition", default="pengyos-desktop",
                    help="which edition's output tree to patch (default: pengyos-desktop; "
                         "the sources are the same in every edition)")
    ap.add_argument("--plan", default=str(PLAN))
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text())
    actions = [a for a in plan["actions"] if a.get("allowed")]
    if args.components:
        actions = [a for a in actions if a["component"] in set(args.components)]
    if not actions:
        print("nothing allowed in the plan — nothing to rehearse", flush=True)
        return 0

    # This is a harness against a real build host, not a test. Say so instead of
    # failing inside make(1).
    edition_dir = BUILD_ROOT / "output" / args.edition
    br_dir = BUILD_ROOT / f"buildroot-{BR}"
    if not br_dir.is_dir() or not edition_dir.is_dir():
        print(f"ABORT: no real Buildroot tree at {br_dir} or {edition_dir}. "
              f"This harness only runs on a build host.", flush=True)
        return 1

    print(f"rehearsal: {len(actions)} allowed action(s), edition {args.edition}, "
          f"Buildroot {BR}", flush=True)

    start = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain")
    if dirty:
        print(f"ABORT: working tree is not clean:\n{dirty}", flush=True)
        return 1

    branch = f"rehearsal/{dt.date.today().isoformat()}"
    git("checkout", "-q", "-b", branch)
    tree = str((REPO / "buildroot-external").resolve())
    total_carried = total_dropped = total_closes = 0
    restore_bad = False
    try:
        for a in actions:
            E.apply_edits(REPO, [E.Edit(**e) for e in a["edits"]])
            # A generated archive's hash can only be read from the build, and the act lane
            # takes it BEFORE the pre-flight (act.run_batch's generated-hash step). A
            # harness that skips that step reports every cargo recipe as a failure - which
            # is what this file did to pengy, starship and uutils-coreutils on 2026-10-19,
            # three of the six actions in the night's batch, on a night the lane was fine.
            if a.get("hash_generated"):
                he = _generated_hash(a, args.edition, tree)
                if he is not None:
                    a["edits"].append(vars(he))
            r = A.triage_package(subprocess.run, REPO, BUILD_ROOT, BR, tree,
                                 [args.edition], a)
            A.shrink_action(a, r["carried"], r["dropped"])
            total_carried += len(r["carried"])
            total_dropped += len(r["dropped"])
            total_closes += len(a["closes"])
            print(f"\n=== {a['component']} {a['current']}: ok={r['ok']} "
                  f"carried={len(r['carried'])} dropped={len(r['dropped'])} "
                  f"closes {len(a['closes'])} "
                  f"(planned {len(a['closes']) + len(r['dropped'])})", flush=True)
            for d in r["dropped"]:
                print(f"    not carried: {Path(d['patch']).name} [{d['reason']}] "
                      f"{', '.join(d['cves'])}", flush=True)
            if r["error"]:
                print(f"    error: {r['error'][:200]}", flush=True)
        print(f"\nrehearsal totals: {total_carried} patch(es) carried, "
              f"{total_dropped} dropped, {total_closes} CVE match(es) actually closed",
              flush=True)
    finally:
        # `git checkout -f main` does not remove untracked files, and the patches the
        # rehearsal wrote are untracked. Take each action's edits back the way the act
        # lane does, then restore main and prove the tree is where it started.
        for a in actions:
            try:
                A._revert_dropped(subprocess.run, REPO, a)
            except RuntimeError as exc:
                print(f"restore note: {a['component']}: {exc}", flush=True)
        git("checkout", "-q", "-f", "main")
        git("branch", "-D", branch, check=False)
        after = git("rev-parse", "HEAD")
        left = git("status", "--porcelain")
        print(f"restored: HEAD {after} ({'same as before' if after == start else 'CHANGED!'}), "
              f"working tree {'clean' if not left else 'DIRTY: ' + left}", flush=True)
        if left:
            print(left, flush=True)
        if after != start or left:
            restore_bad = True
    return 0 if not restore_bad else 1


if __name__ == "__main__":
    sys.exit(main())
