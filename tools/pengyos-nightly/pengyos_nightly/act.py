"""act: take one allowed action all the way to the testing channel.

The order is the safety property:

    guards -> branch -> edit -> recompose defconfigs -> VERSION -> commit
           -> gate -> build + boot-check + sign + publish (release.sh)
           -> merge to main -> record

Everything before `release.sh` is local and reversible. `release.sh` is the first
thing that can affect a machine, and it already refuses a dirty tree and refuses
to publish an image that was not boot-checked. `main` only moves after a
successful publish, so main is "what the fleet can actually get".

A failing step stops the run, restores main's working tree, leaves the branch for
inspection, and records the failure. Nothing is half-published.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

from pengyos_nightly import config as C
from pengyos_nightly import edits as E
from pengyos_nightly import guards as G
from pengyos_nightly import state as S

DRY = "(dry run)"


def _run(run, argv, **kw) -> subprocess.CompletedProcess:
    return run(argv, capture_output=True, text=True, **kw)


def _git(run, repo: Path, *args, check=True) -> str:
    p = _run(run, ["git", "-C", str(repo), *args])
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {p.stderr.strip()[:400]}")
    return (p.stdout or "").strip()


def _tracked(run, repo: Path, path: str) -> bool:
    """Is this path in main? Used to restore a file the right way instead of replaying
    edits whose order decides the result."""
    p = _run(run, ["git", "-C", str(repo), "ls-files", "--error-unmatch", "--", path])
    return p.returncode == 0



def published_versions(site: Path) -> set[str]:
    """Versions already in the update feed, across every image."""
    out: set[str] = set()
    for img in sorted((site / "update").glob("*")) if (site / "update").is_dir() else []:
        if not img.is_dir():
            continue
        for v in img.glob("*"):
            if v.is_dir() and (v / "manifest.json").exists():
                out.add(v.name)
    return out


def next_version(repo: Path, site: Path) -> tuple[str, str]:
    """The version this release should carry.

    A VERSION that has never been published is not a used version: 26.10.8 was
    built and gated but never published, so publishing it is not a new release
    line — bumping past it would leave a phantom version in the history.
    """
    current = (repo / "VERSION").read_text().strip()
    if current not in published_versions(site):
        return current, f"{current} has never been published, so it is reused"
    parts = current.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    bumped = ".".join(parts)
    return bumped, f"{current} is already published, so this release is {bumped}"


def notes_for(action: dict, version: str, version_note: str = "") -> str:
    lines = [f"PengyOS {version} - nightly {action['lane']} lane.", ""]
    if version_note:
        lines.append(f"({version_note})")
        lines.append("")
    cves = action.get("cves") or []
    closes = set(action.get("closes") or [])
    if closes:
        if action.get("shape") == "patch_dir":
            lines.append(f"{action['component']} {action['current']} carries "
                         f"{len(action.get('patches') or [])} upstream fix commit(s), "
                         f"closing {len(closes)} security issue(s):")
        else:
            lines.append(f"{action['component']} {action['current']} -> {action['target']} "
                         f"closes {len(closes)} security issue(s):")
        for c in cves:
            if c["cve"] not in closes:
                continue
            commit = (c.get("fix_commit") or {}).get("sha", "")
            lines.append(f"- {c['cve']} ({c['priority']}"
                         + (f", CVSS {c['cvss']}" if c.get("cvss") else "")
                         + (", actively exploited" if c.get("kev") else "")
                         + (f", upstream {commit[:12]}" if commit else "")
                         + f"): {c.get('affected_note') or c.get('note', '')}")
        lines.append("")
    other = action.get("still_open") or [c["cve"] for c in cves if c["cve"] not in closes]
    if other:
        lines.append(f"Still open in {action['component']}: {', '.join(other)} "
                     f"(NVD names no upstream fix to carry for these).")
        lines.append("")
    for d in (action.get("dropped_patches") or []):
        lines.append(f"Not carried: {Path(d['patch']).name}: {d['reason']}"
                     + (f" ({', '.join(d['cves'])})" if d.get("cves") else ""))
    if action.get("dropped_patches"):
        lines.append("")
    lines.append(f"Editions rebuilt: {', '.join(action['editions'])}.")
    if action.get("risks"):
        lines.append("")
        lines += [f"Note: {r}" for r in action["risks"]]
    return "\n".join(lines) + "\n"


def commit_message(action: dict, version: str) -> str:
    n = len(action.get("closes") or [])
    if action.get("shape") == "patch_dir":
        what = (f"carries {len(action.get('patches') or [])} upstream fix commit(s), "
                f"closing {n} CVE match(es)" if n else
                f"carries {len(action.get('patches') or [])} upstream fix commit(s)")
        head = f"nightly {action['lane']}: {action['component']} {action['current']} {what}"
    else:
        what = f"closes {n} CVE match(es)" if n else "routine bump"
        head = (f"nightly {action['lane']}: {action['component']} {action['current']} -> "
                f"{action['target']} ({what})")
    return (f"{head}\n\n"
            f"Applied by pengyos-nightly act; published to {G.CHANNEL} as {version}. "
            f"Stable is untouched.\n")


def _prune_empty(run, repo: Path, path: str) -> None:
    """Remove directories a reverted file leaves behind, up to the external tree.

    Deleting the last patch under patches/<pkg>/<version>/ leaves two empty
    directories. They are harmless to a build and misleading to a reader: an empty
    patches/libxml2/ says "we carry libxml2 patches" when we carry none.
    """
    import os
    cur = (repo / path).parent
    stop = repo / "buildroot-external"
    while cur != stop and cur.is_dir():
        try:
            os.rmdir(cur)               # only succeeds when empty
        except OSError:
            break
        cur = cur.parent


def _revert_dropped(run, repo: Path, action: dict) -> None:
    """Put the working tree back to main's state for one dropped action.

    A change that failed its pre-flight must not reach the commit that gets published
    and merged: a patch that does not apply is not something the image carries, and
    leaving it in main means the next build applies it and fails. Files main does not
    know about are removed rather than checked out, because `git checkout <path>`
    errors on a path that does not exist in HEAD.
    """
    for e in action.get("edits") or []:
        path = e.get("path", "")
        if not path:
            continue
        in_head = _run(run, ["git", "-C", str(repo), "cat-file", "-e", f"HEAD:{path}"])
        if in_head.returncode != 0:
            target = repo / path
            if target.is_dir():
                import shutil
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
                _prune_empty(run, repo, path)
            continue
        _git(run, repo, "checkout", "HEAD", "--", path)


def _commit(run, repo: Path, message: str) -> str:
    """Commit what is staged, or say nothing was staged.

    An edit can turn out to be a no-op — an `ensure` whose content is already there,
    a version already at its target — and `git commit` with nothing staged is an
    error, not a fact about the world. Reporting it as a failure would blame the batch
    for a change that was already true.
    """
    if _run(run, ["git", "-C", str(repo), "diff", "--cached", "--quiet"]).returncode == 0:
        return "(nothing staged)"
    _git(run, repo, "commit", "-m", message)
    return _git(run, repo, "rev-parse", "--short", "HEAD")


def published_today(state_dir: Path, today: dt.date) -> int:
    """Releases this loop already published on `today`, from its own action log.

    `release_cap` needs a fact, not an intention: the only trustworthy record of what
    reached the feed is what the loop wrote when it succeeded.
    """
    path = state_dir / "actions.json"
    if not path.exists():
        return 0
    try:
        log = json.loads(path.read_text())
    except (OSError, ValueError):
        return 0
    stamp = today.isoformat()
    # Distinct versions, not entries: a batch writes one entry per change plus one
    # batch entry, and all of them describe the *same* published image.
    versions = {a.get("version") for a in log.get("actions", [])
                if a.get("published") and str(a.get("date", ""))[:10] == stamp
                and a.get("version")}
    if versions:
        return len(versions)
    return sum(1 for a in log.get("actions", [])
               if a.get("published") and str(a.get("date", ""))[:10] == stamp
               and a.get("kind") != "batch")


def batch_notes(actions: list[dict], version: str, version_note: str = "") -> str:
    """One notes file for the whole release: every change, with its own evidence.

    The notes are what a machine on the testing channel (and the operator, the next
    day) reads to know what actually changed in this image. A batch that only says
    "twelve updates" is not reviewable; a batch that names each change and the CVE it
    closed is.
    """
    cves = sorted({c for a in actions for c in (a.get("closes") or [])})
    lines = [f"PengyOS {version} - nightly batch of {len(actions)} change(s).", ""]
    if version_note:
        lines.append(f"({version_note})")
        lines.append("")
    if cves:
        lines.append(f"Closes {len(cves)} security issue(s): {', '.join(cves)}.")
        lines.append("")
    for a in actions:
        n = len(a.get("closes") or [])
        if a.get("shape") == "patch_dir":
            what = (f"carries {len(a.get('patches') or [])} upstream fix commit(s)"
                    + (f", closing {n} security issue(s)" if n else ""))
            lines.append(f"- {a['component']} {a['current']} ({a['lane']}): {what}")
        else:
            what = f"closes {n} security issue(s)" if n else "routine bump"
            lines.append(f"- {a['component']} {a['current']} -> {a['target']} "
                         f"({a['lane']}, {what})")
        for c in (a.get("cves") or []):
            if c["cve"] not in (a.get("closes") or []):
                continue
            commit = (c.get("fix_commit") or {}).get("sha", "")
            lines.append(f"    - {c['cve']} ({c['priority']}"
                         + (f", CVSS {c['cvss']}" if c.get("cvss") else "")
                         + (", actively exploited" if c.get("kev") else "")
                         + (f", upstream {commit[:12]}" if commit else "")
                         + f"): {c.get('affected_note') or c.get('note', '')}")
        if a.get("still_open"):
            lines.append(f"    - Still open in {a['component']}: {', '.join(a['still_open'])} "
                         f"(NVD names no upstream fix to carry for these)")
        for d in (a.get("dropped_patches") or []):
            lines.append(f"    - Not carried: {Path(d['patch']).name}: {d['reason']}"
                         + (f" ({', '.join(d['cves'])})" if d.get("cves") else ""))
        for r in (a.get("risks") or []):
            lines.append(f"    - Note: {r}")
    editions = sorted({e for a in actions for e in (a.get("editions") or [])})
    lines.append("")
    lines.append(f"Editions rebuilt: {', '.join(editions)}.")
    lines.append("Verified as a batch: this image was built with all of the above in it "
                 "and boot-checked before it was signed.")
    return "\n".join(lines) + "\n"


def batch_commit_message(action: dict, version: str, index: int, total: int) -> str:
    """One commit per change, so each keeps its own *why* inside one shared branch."""
    return commit_message(action, version).replace(
        "Applied by pengyos-nightly act;",
        f"Applied by pengyos-nightly act (change {index + 1} of {total} in one batch;")


def batch_merge_message(actions: list[dict], version: str, branch: str) -> str:
    names = ", ".join(a["component"] for a in actions)
    n = len({c for a in actions for c in (a.get("closes") or [])})
    return (f"Merge {branch}: {len(actions)} change(s) [{names}] as {version} on "
            f"{G.CHANNEL}\n\n"
            f"One signed image carrying {len(actions)} change(s)"
            + (f", {n} CVE match(es) closed" if n else "")
            + ". Boot-checked before signing. Stable is untouched.\n")


def _failed_files(detail: str) -> str:
    """The files a patch failed on, from make's own output — enough for a human to
    decide 'strip the test hunks' versus 'wrong fix' without re-running the build."""
    names = re.findall(r"patching file (\S+)|saving rejects to file (\S+)|FAILED at",
                       detail)
    files = [n for pair in names for n in pair if n]
    if files:
        return ", ".join(sorted(set(files)))
    return detail.splitlines()[0][:80] if detail else "the source"


class PatchFailure(RuntimeError):
    """A pre-flight step failed, carrying the whole command output.

    The message is for a human and for `patch_failures.detail`; `.output` is for the
    triage, which has to find the failing patch's name in a log that can run to
    hundreds of lines (one ffmpeg cherry-pick produced 17 failed hunks). Truncating
    the message is fine; truncating the evidence is not.
    """

    def __init__(self, message: str, output: str):
        super().__init__(message)
        self.output = output


def _failed_patch(detail: str) -> str:
    """Which patch Buildroot stopped on.

    `apply-patches.sh` prints `Applying <name> using <type>:` immediately before each
    patch and runs with `set -e`, so a failing patch ends the script there: the last
    `Applying` line in the output *is* the culprit. (The script also has a
    `Patch failed!  Please fix <patch>!` line, but `set -e` means it is never reached
    on Buildroot 2026.08 - measured on a real `libsndfile-patch` failure. Keep the
    named form for the benefit of any Buildroot that does print it.)
    """
    m = re.search(r"Please fix (\S+)!", detail)
    if m:
        return m.group(1)
    names = re.findall(r"Applying (\S+) using", detail)
    return names[-1] if names else ""


def _patch_reason(detail: str) -> str:
    """Why Buildroot rejected a patch, in the two forms a reader can act on.

    `support/scripts/apply-patches.sh` runs `patch -F0 -g0 -p1 -t -N`, so a fix that
    is already in the pinned source is not a silent no-op: patch reports it as reversed
    and Buildroot fails the package. Saying "already present" is the difference between
    "this CVE is not open in what we ship" and "this cherry-pick is the wrong fix", and
    only the first is worth reading again tomorrow.
    """
    if "Reversed (or previously applied)" in detail:
        return "already present in the pinned version"
    return "does not apply to the pinned version"


def triage_package(run, repo: Path, build_root: Path, br_version: str, external_tree: str,
                   editions: list[str], action: dict) -> dict:
    """Pre-flight a package, dropping the patches Buildroot itself rejects.

    The unit of failure is a patch, not a package. `apply-patches.sh` names the one it
    could not apply (`Patch failed!  Please fix <patch>!`), so the loop takes that file
    out of the tree, re-extracts, and tries again — which is what "one bad cherry-pick
    must not spend the night" has always meant, applied at the level the evidence
    actually supports. What survives is what the image carries, and `shrink_action`
    recomputes the claim from it.

    A failure Buildroot does not attribute to a named patch stops the triage: the loop
    does not guess which change is at fault.

    Returns {"ok": bool, "carried": [edit], "dropped": [{patch, cves, fix_url, reason,
    where}], "error": str, "editions": [target]}. `ok` is the pre-flight's verdict, not
    "some patch survived": a version bump carries no patches and still passes.
    """
    carried = [e for e in action.get("edits") or [] if e.get("path", "").endswith(".patch")]
    dropped: list[dict] = []

    def give_up(detail: str) -> dict:
        # Whatever the build tree of a package we are about to drop looks like, it is not
        # a state the following full build may be handed. See dirclean_package.
        dirclean_package(run, build_root, br_version, external_tree, editions,
                         action["component"])
        return {"ok": False, "carried": carried, "dropped": dropped, "error": detail,
                "editions": []}

    while True:
        try:
            done = refresh_package(run, build_root, br_version, external_tree,
                                   editions, action["component"])
            return {"ok": True, "carried": carried, "dropped": dropped, "error": "",
                    "editions": done}
        except RuntimeError as exc:
            detail = getattr(exc, "output", None) or str(exc)
            name = _failed_patch(detail)
            victim = next((e for e in carried if Path(e["path"]).name == name), None)
            if victim is None:
                return give_up(detail)
            dropped.append({"patch": victim["path"], "cves": list(victim.get("cves") or []),
                            "fix_url": victim.get("fix_url", ""),
                            "reason": _patch_reason(detail), "where": _failed_files(detail)})
            target = repo / victim["path"]
            if target.exists():
                target.unlink()
            _prune_empty(run, repo, victim["path"])
            carried.remove(victim)
            action["edits"].remove(victim)
            if not carried:
                return give_up(detail)


def shrink_action(action: dict, carried: list[dict], dropped: list[dict]) -> None:
    """Recompute what an action may claim, from the patches that survived pre-flight.

    The planner's rule, applied after the build has had its say: the claim is the patch
    set. A CVE whose patch was rejected goes back to `still_open`, so the notes, the
    commit message and the `patched_by` record describe the image that was built.
    """
    closed = sorted({c for e in carried for c in (e.get("cves") or [])})
    action["patches"] = [e["path"] for e in carried]
    action["closes"] = closed
    action["still_open"] = sorted(({c["cve"] for c in action.get("cves") or []
                                    if c.get("affected")} - set(closed))
                                   | set(action.get("still_open") or []))
    action["dropped_patches"] = dropped


def refresh_package(run, build_root: Path, br_version: str, external_tree: str,
                    targets: list[str], pkg: str) -> list[str]:
    """Re-extract and re-patch one package in each output tree, and prove the patch
    applies before anything expensive starts.

    Two separate reasons, both learned the hard way on 2026-10-05:

    * Buildroot's stamps say `.stamp_extracted` / `.stamp_patched` are done, and a
      new patch under BR2_GLOBAL_PATCH_DIR changes nothing Buildroot can see — an
      existing tree would rebuild the image with the old, unpatched source still
      sitting in build/<pkg>-<version>. `<pkg>-dirclean` is what makes the patch real.
    * A cherry-picked upstream commit can apply to the source and fail on the
      upstream's own test files (podman's did: 3 hunks OK in
      libpod/container_internal_common.go, 2 FAILED in test/e2e/run_working_dir_test.go).
      `<pkg>-patch` runs extract + patch only, so that is caught in seconds instead
      of twenty minutes into a build — before any publish, not after.
    """
    src = build_root / f"buildroot-{br_version}"
    import os
    env = {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"}
    done = []
    for t in targets:
        for step in (f"{pkg}-dirclean", f"{pkg}-patch"):
            argv = ["make", "-C", str(src), f"O={build_root / 'output' / t}",
                    f"BR2_EXTERNAL={external_tree}", step]
            p = _run(run, argv, env=env)
            if p.returncode != 0:
                out = (p.stdout or "") + (p.stderr or "")
                # The tail is the part that says what happened: `Applying <patch> using
                # patch:`, patch's own `Reversed (or previously applied)` / `FAILED at`
                # lines, and make's `.stamp_patched Error 1`. Filtering to only lines
                # containing FAILED/error: drops exactly the lines the triage needs.
                lines = out.splitlines()
                head = [l for l in lines if "FAILED" in l or "error:" in l][:4]
                tail = "\n".join(lines[-25:])
                raise PatchFailure(f"{step} failed in {t}: "
                                   + ("; ".join(head) + "\n" + tail if head else tail), out)
        done.append(t)
    return done


def dirclean_package(run, build_root: Path, br_version: str, external_tree: str,
                     targets: list[str], pkg: str) -> None:
    """Wipe a package's build tree when its pre-flight failed, so it leaves no residue.

    A pre-flight that dies mid-patch leaves `build/<pkg>-<version>` extracted, partly
    patched, with `.applied_patches_list` populated and `.stamp_patched` missing. The
    next full `make` re-runs the patch step, `apply-patches.sh` sees basenames it has
    already applied and dies with `Error: duplicate filename` on Buildroot's own patch -
    which reads like a problem with the change we just carried and is really the corpse
    of the pre-flight. Measured on 2026-10-08: libsndfile's only carried fix was one
    Buildroot 2026.08 already ships, the package was dropped as it should have been, and
    the whole pengyos-desktop build then failed on libsndfile's own 0001-...patch.

    Failures here are ignored on purpose: a dirclean that cannot run must not turn a
    contained drop into an aborted night.
    """
    src = build_root / f"buildroot-{br_version}"
    import os
    env = {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"}
    for t in targets:
        _run(run, ["make", "-C", str(src), f"O={build_root / 'output' / t}",
                    f"BR2_EXTERNAL={external_tree}", f"{pkg}-dirclean"], env=env)



def run_act(action: dict, cfg: dict, repo: Path, build_root: Path, site: Path,
            state_dir: Path, apply: bool = False, run=subprocess.run,
            today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    journal: list[dict] = []
    result = {"action": action["id"], "applied": False, "published": False,
              "journal": journal, "channel": G.CHANNEL}

    def step(name, ok, detail=""):
        journal.append({"step": name, "ok": ok, "detail": detail})
        print(f"[act] {name}: {detail or ('ok' if ok else 'failed')}", flush=True)
        return ok

    def bail(name, detail):
        step(name, False, detail)
        if apply:
            try:
                _git(run, repo, "checkout", "-f", "main")
            except RuntimeError as exc:
                step("restore main", False, str(exc))
        result["error"] = f"{name}: {detail}"
        return result

    # --- guards, again, at the moment of action -------------------------------
    if not action.get("allowed"):
        return bail("guard", f"action {action['id']} is not allowed in the plan: "
                             + ", ".join(action.get("refused_by", [])))
    if not apply:
        step("dry run", True, f"{DRY}: would apply {len(action['edits'])} edit(s) and "
                             f"publish to {G.CHANNEL}; nothing on disk changed")
        for e in action["edits"]:
            print(E.Edit(**e).preview())
        return result
    if not cfg["act_enabled"]:
        return bail("kill switch", "act_enabled = false in nightly.toml")
    key = C.release_key(cfg)
    ok, why = G.key_present(key)
    if not ok:
        return bail("key_present", why)
    remote = C.site_remote(cfg) if cfg["push"] else None
    if cfg["push"] and not remote:
        return bail("site_remote", "push = true but PENGYOS_SITE_REMOTE is not set")
    ok, why = G.clean_tree(repo, run)
    if not ok:
        return bail("clean_tree", why)
    step("guards", True, "action allowed, key present, tree clean")

    # --- branch, edits, VERSION, commit ---------------------------------------
    # Everything that reads or writes the repo is inside the try: a repo that is not
    # a repo, or a VERSION file that is not there, is a reported failure, not a
    # traceback in a cron log.
    try:
        base = _git(run, repo, "rev-parse", "HEAD")
        branch = f"nightly/{today.isoformat()}-{action['component']}"
        version, why = next_version(repo, site)
        result["version"] = version
        result["branch"] = branch
        _git(run, repo, "checkout", "-B", branch)
        step("branch", True, branch)
        touched = E.apply_edits(repo, [E.Edit(**e) for e in action["edits"]])
        step("edits", True, ", ".join(touched))
        if any("configs/layers/" in t for t in touched):
            p = _run(run, ["python3", "scripts/compose-defconfigs.py"], cwd=str(repo))
            if p.returncode != 0:
                return bail("compose-defconfigs", (p.stderr or p.stdout).strip()[:400])
            step("compose-defconfigs", True, "defconfigs regenerated from the layers")
        (repo / "VERSION").write_text(version + "\n")
        step("VERSION", True, f"{version} ({why})")
        _git(run, repo, "add", "-A")
        _git(run, repo, "commit", "-m", commit_message(action, version))
        step("commit", True, _git(run, repo, "rev-parse", "--short", "HEAD"))
    except (RuntimeError, ValueError, OSError) as exc:
        return bail("local edit", str(exc))

    # --- the gate, before anything is built -----------------------------------
    p = _run(run, ["python3", "scripts/pengyos_gate.py", "run", "--repo", ".",
                   "--mode", "full", "--base", base], cwd=str(repo), timeout=3600)
    if p.returncode != 0:
        return bail("gate_first", "the repo gate failed; nothing was built or published:\n"
                                  + (p.stdout + p.stderr)[-1500:])
    step("gate_first", True, "repo gate green")

    # --- make the change real in the existing trees ---------------------------
    try:
        import tomllib
        targets = tomllib.loads((repo / "targets.toml").read_text())["targets"]
        by_name = {t["name"]: t for t in targets}
        br_version = targets[0]["br_version"]
        # external_tree in targets.toml is relative to the targets file (that is how
        # pengyos-build resolves it), not to whatever cwd this happens to run from.
        tree = str((repo / by_name[action["editions"][0]]["external_tree"]).resolve())
        refreshed = refresh_package(run, build_root, br_version, tree,
                                    action["editions"], action["component"])
        step("dirclean", True, f"{action['component']} re-extracted and re-patched in "
                               f"{', '.join(refreshed)}")
    except RuntimeError as exc:
        # Remember it. The pre-flight is cheap but it spends the night's one action
        # slot, so a candidate that is known not to apply would block a good one
        # forever, and re-extracting the package in every edition every night is not
        # free either. This is a human's call, so it is recorded as one.
        result["patch_failure"] = {"date": today.isoformat(),
                                   "where": _failed_files(str(exc)),
                                   "detail": str(exc)[:400]}
        return bail("dirclean", str(exc))

    # --- build, boot-check, sign, publish -------------------------------------
    state_dir.mkdir(parents=True, exist_ok=True)
    notes = state_dir / f"notes-{action['id']}.md"
    notes.write_text(notes_for(action, version))
    argv = ["scripts/release.sh", "--publish", "--channel", G.CHANNEL,
            "--notes", str(notes)]
    if cfg["skip_cosmic"]:
        argv.append("--skip-cosmic")
    if cfg["push"]:
        argv.append("--push")
    argv += [t for t in action["editions"]]
    try:
        G.assert_locked(argv)
    except G.GuardError as exc:
        return bail("channel_locked", str(exc))
    env = {"PENGYOS_RELEASE_KEY": key or ""}
    if remote:
        env["PENGYOS_SITE_REMOTE"] = remote
    step("release", True, " ".join(argv))
    try:
        p = _run(run, argv, cwd=str(repo), env={**__import__("os").environ, **env})
    except OSError as exc:
        return bail("release", f"could not start scripts/release.sh: {exc}")
    if p.returncode != 0:
        return bail("release", "build / boot check / publish failed; nothing was "
                               "published:\n" + (p.stdout + p.stderr)[-2000:])
    step("published", True, f"{version} on {G.CHANNEL}"
                           + (" and pushed to the live feed" if cfg["push"] else
                              " (local site dir only)"))

    # --- main moves only after a successful publish ---------------------------
    try:
        _git(run, repo, "checkout", "main")
        _git(run, repo, "merge", "--no-ff", branch, "-m",
             f"Merge {branch}: {action['component']} {action['target']} on {G.CHANNEL}")
        step("merge", True, f"main now contains {branch}")
    except RuntimeError as exc:
        return bail("merge", f"published {version} to {G.CHANNEL} but could not merge "
                             f"{branch} into main: {exc}")

    result["applied"] = result["published"] = True
    return result


def record(result: dict, action: dict, state_dir: Path, st: dict, today: dt.date,
           run=subprocess.run, repo: Path | None = None) -> None:
    """actions.json + the attestation chain + state's cooldown map."""
    path = state_dir / "actions.json"
    log = json.loads(path.read_text()) if path.exists() else {"actions": []}
    entry = {"date": today.isoformat(), "action": action["id"],
             "component": action["component"], "lane": action["lane"],
             "current": action["current"], "target": action["target"],
             "shape": action["shape"], "channel": G.CHANNEL,
             "version": result.get("version", ""), "branch": result.get("branch", ""),
             "published": result.get("published", False), "error": result.get("error", ""),
             "cves": list(action.get("closes") or
                          [c["cve"] for c in action.get("cves", []) if c.get("covered")]),
             "editions": action["editions"]}
    log["actions"].append(entry)
    S.save(path, log)
    # Only a published action consumes the cooldown. A failed attempt is recorded —
    # it is evidence about the candidate — but it must not spend the component's
    # cooldown: the first real run on 2026-10-05 failed at the patch pre-flight and
    # then found libxml2 blocked for 7 days by its own failure, and the plan moved on
    # to a different package.
    if result.get("published"):
        st.setdefault("actions", {})[action["component"]] = today.isoformat()
        # A patch shape does not move the version, so pkg-stats keeps reporting the
        # same CVEs tomorrow. Without recording the fix here the loop cries wolf about
        # issues it has already closed, re-triages them, and re-proposes the same action
        # the moment the cooldown expires. This is a distinct resolution kind because it
        # is a different fact: not "upstream fixed it" but "we carry the fix".
        if action.get("shape") == "patch_dir":
            for cve in action.get("closes") or []:
                key = f"{cve}|{action['component']}"
                if key in st["cves"]:
                    st["cves"][key]["patched_by"] = {
                        "date": today.isoformat(), "version": action["current"],
                        "release": result.get("version", ""),
                        "patches": list(action.get("patches") or [])}
    # A patch that was proved not to applies is remembered too, so the planner refuses
    # the candidate by name instead of spending the night's one action slot on it again.
    if result.get("patch_failure"):
        st.setdefault("patch_failures", {})[action["component"]] = result["patch_failure"]
    S.save(state_dir / "state.json", st)

    if repo is not None:
        # The chain is worth having, but a failure here must not turn a recorded,
        # already-reported action into a traceback: the action log and the state
        # update above are the parts the next night depends on.
        try:
            data = json.dumps(entry, sort_keys=True)
            _run(run, [sys.executable, "-m", "pengyos_attest", "append",
                       "--log", str(state_dir / "attest.jsonl"), "--type", "nightly-act",
                       "--data", data], cwd=str(repo / "tools" / "pengyos-attest"))
        except (OSError, ValueError) as exc:
            print(f"[act] attest record skipped: {exc}")


# --- phase 3: a batch of actions, one release ---------------------------------


def run_batch(actions: list[dict], cfg: dict, repo: Path, build_root: Path, site: Path,
              state_dir: Path, apply: bool = False, run=subprocess.run,
              today: dt.date | None = None) -> dict:
    """Apply a batch of allowed actions and publish **one** signed image.

    The batch is the unit of work; the signed image is the unit of risk. Everything
    that is per-change (edits, commits, package pre-flights) runs once per action;
    everything that is per-image (defconfig recompose, VERSION, the gate, the build,
    the boot check, the signature, the publish, the merge) runs once for the batch.

    A package whose pre-flight fails is dropped and recorded, and the batch continues
    without it: one bad cherry-pick must not spend the night. A build or boot failure
    publishes nothing at all.
    """
    today = today or dt.date.today()
    journal: list[dict] = []
    result = {"kind": "batch", "applied": False, "published": False,
              "journal": journal, "channel": G.CHANNEL,
              "planned": [a["id"] for a in actions], "applied_actions": [],
              "dropped": []}
    # Declaration edits, kept apart from an action's planned edits: they are written
    # after the pre-flight, from what survived, and a batch that bails has to take them
    # back out too.
    declared: list[E.Edit] = []

    def step(name, ok, detail=""):
        journal.append({"step": name, "ok": ok, "detail": detail})
        print(f"[act] {name}: {detail or ('ok' if ok else 'failed')}", flush=True)
        return ok

    def bail(name, detail):
        step(name, False, detail)
        if apply:
            try:
                _git(run, repo, "checkout", "-f", "main")
                # `checkout -f` does not touch files main has never seen, and the patch
                # files this batch wrote are exactly that. Left behind they leave the
                # tree dirty - and a dirty tree is what makes the *next* night's run
                # refuse to do anything at all (clean_tree). Take each action's edits
                # back the way a dropped package's are taken back, and take the
                # declarations back too.
                for a in actions:
                    _revert_dropped(run, repo, a)
                for e in declared:
                    target = repo / e.path
                    if not target.is_file():
                        continue
                    if _tracked(run, repo, e.path):
                        # Replaying each edit's `old` is order-dependent. Three packages
                        # in one batch means three declarations of the same file, each
                        # `old` holding the previous two blocks: writing them back in
                        # order leaves the earlier blocks behind. On 2026-10-08 a failed
                        # release left FFMPEG_IGNORE_CVES and GST1_PLUGINS_BAD_IGNORE_CVES
                        # on main for patches that were no longer in the tree - a
                        # declaration that tells pkg-stats a CVE is closed by a fix we do
                        # not carry. main already has the right content; take it from there.
                        _git(run, repo, "checkout", "-f", "main", "--", e.path)
                    else:
                        # Not in main: the batch made it, so the batch removes it.
                        target.unlink()
            except RuntimeError as exc:
                step("restore main", False, str(exc))
        result["error"] = f"{name}: {detail}"
        return result

    # --- guards, again, at the moment of action -------------------------------
    if not actions:
        return bail("guard", "the batch is empty")
    not_allowed = [a["id"] for a in actions if not a.get("allowed")]
    if not_allowed:
        return bail("guard", "refusing a batch containing actions the plan did not allow: "
                             + ", ".join(not_allowed))
    if not apply:
        step("dry run", True, f"{DRY}: batch of {len(actions)} action(s), one release to "
                             f"{G.CHANNEL}; nothing on disk changed")
        for a in actions:
            print(f"  - {a['id']}: {len(a['edits'])} edit(s), editions "
                  f"{', '.join(a['editions'])}")
            for e in a["edits"]:
                print(E.Edit(**e).preview())
            decl = E.cve_declaration_edit(repo, a["component"],
                                          [e for e in a["edits"] if e["path"].endswith(".patch")])
            if decl:
                print(f"    and, for the patches that survive the pre-flight, declares them "
                      f"to pkg-stats: {decl.note}")
        return result
    if not cfg["act_enabled"]:
        return bail("kill switch", "act_enabled = false in nightly.toml")
    key = C.release_key(cfg)
    ok, why = G.key_present(key)
    if not ok:
        return bail("key_present", why)
    remote = C.site_remote(cfg) if cfg["push"] else None
    if cfg["push"] and not remote:
        return bail("site_remote", "push = true but PENGYOS_SITE_REMOTE is not set")
    done = published_today(state_dir, today)
    ok, why = G.release_cap(done, cfg.get("max_releases", 1))
    if not ok:
        return bail("release_cap", why)
    ok, why = G.batch_conflict(actions)
    if not ok:
        return bail("batch_conflict", why)
    ok, why = G.clean_tree(repo, run)
    if not ok:
        return bail("clean_tree", why)
    ok, runnable = G.release_script_executable(repo)
    if not ok:
        return bail("release_script", runnable)
    step("guards", True, f"{len(actions)} action(s) allowed, key present, tree clean, "
                         f"{why}, {runnable}")

    # --- one branch; every edit applied, but nothing committed yet ------------
    # The order matters: pre-flight decides *which* changes are real, and a change
    # that fails it must not end up in the commit that gets published and merged.
    try:
        base = _git(run, repo, "rev-parse", "HEAD")
        branch = f"nightly/{today.isoformat()}-batch"
        version, why = next_version(repo, site)
        result["version"] = version
        result["branch"] = branch
        _git(run, repo, "checkout", "-B", branch)
        step("branch", True, f"{branch} ({len(actions)} action(s))")
        (repo / "VERSION").write_text(version + "\n")
        touched: dict[str, list[str]] = {}
        for a in actions:
            touched[a["id"]] = E.apply_edits(repo, [E.Edit(**e) for e in a["edits"]])
            step(f"edit {a['component']}", True, ", ".join(touched[a["id"]]))
    except (RuntimeError, ValueError, OSError) as exc:
        return bail("local edit", str(exc))

    # --- pre-flight every package: cheap, and it decides what is real ---------
    try:
        import tomllib
        targets = tomllib.loads((repo / "targets.toml").read_text())["targets"]
        by_name = {t["name"]: t for t in targets}
        br_version = targets[0]["br_version"]
        tree = str((repo / by_name[actions[0]["editions"][0]]["external_tree"]).resolve())
    except (OSError, ValueError, KeyError) as exc:
        return bail("targets.toml", f"could not read the build targets: {exc}")

    live: list[dict] = []
    for a in actions:
        triaged = triage_package(run, repo, build_root, br_version, tree, a["editions"], a)
        carried, dropped, error = triaged["carried"], triaged["dropped"], triaged["error"]
        if triaged["ok"]:
            if dropped:
                # Some of this package's fixes are carried and some are not. The claim
                # follows the tree, and the fixes Buildroot rejected are recorded per
                # fix, so the same cherry-pick is not retried while the package itself
                # stays open to a better one.
                shrink_action(a, carried, dropped)
                touched[a["id"]] = [p for p in touched[a["id"]]
                                    if p in {e["path"] for e in a["edits"]}]
                result.setdefault("patch_failures", {})[a["component"]] = {
                    "date": today.isoformat(),
                    "where": ", ".join(sorted({d["where"] for d in dropped})) or "the source",
                    "detail": "; ".join(f"{Path(d['patch']).name}: {d['reason']}"
                                        for d in dropped)[:400],
                    "fixes": sorted({d["fix_url"] for d in dropped if d.get("fix_url")}),
                    "carried": len(carried)}
                result.setdefault("patch_drops", {})[a["component"]] = dropped
                step("pre-flight", True,
                     f"{a['component']} re-extracted and re-patched in "
                     f"{', '.join(triaged['editions'])}: {len(carried)} patch(es) carried, "
                     f"{len(dropped)} not ({'; '.join(sorted({d['reason'] for d in dropped}))})")
            else:
                step("pre-flight", True, f"{a['component']} re-extracted and re-patched in "
                                         f"{', '.join(triaged['editions'])}")
            # Declare what this package actually carries - after the pre-flight, on
            # purpose. The tree may claim only the fixes that survived, so a patch
            # Buildroot rejected takes its CVE with it rather than leaving an ignore that
            # hides a real exposure. pkg-stats cannot see a BR2_GLOBAL_PATCH_DIR patch
            # any other way (buildroot-external/patches/README.md rule 3).
            decl = E.cve_declaration_edit(repo, a["component"], carried)
            if decl:
                try:
                    E.apply_edits(repo, [decl])
                except (OSError, ValueError) as exc:
                    return bail("cve-declaration", f"{a['component']}: {exc}")
                declared.append(decl)
                touched[a["id"]].append(decl.path)
                step("declare", True, decl.note)
            live.append(a)
            continue        # Nothing this package can carry: the change is dropped, its edits are taken
        # back out of the tree, and the failure is recorded by name.
        result["dropped"].append(a["id"])
        result.setdefault("patch_failures", {})[a["component"]] = {
            "date": today.isoformat(), "where": _failed_files(error), "detail": error[:400],
            "fixes": sorted({d["fix_url"] for d in dropped if d.get("fix_url")})}
        step("pre-flight", False, f"{a['component']} dropped from the batch: {error}")
        try:
            _revert_dropped(run, repo, a)
        except RuntimeError as exc2:
            return bail("revert dropped", f"{a['component']}: {exc2}")
    if not live:
        return bail("pre-flight", "every package in the batch failed its pre-flight; "
                                  "nothing was built or published")

    # --- commit: VERSION, then one commit per surviving change ----------------
    # Staged per step, deliberately. `git add -A` before the VERSION commit would
    # swallow every pending edit into it and leave the per-change commits empty —
    # one big commit is exactly what "small, atomic commits whose messages say why"
    # is not, and it is what makes a batch reviewable change by change.
    try:
        _git(run, repo, "add", "VERSION")
        _commit(run, repo, f"nightly batch {version}: set VERSION\n\n{why}\n")
        step("VERSION", True, version)
        for i, a in enumerate(live):
            _git(run, repo, "add", "--", *touched[a["id"]])
            _commit(run, repo, batch_commit_message(a, version, i, len(live)))
            step(f"commit {i + 1}/{len(live)}", True,
                 f"{a['component']}: {', '.join(touched[a['id']])}")
        if any(any("configs/layers/" in t for t in touched[a["id"]]) for a in live):
            p = _run(run, ["python3", "scripts/compose-defconfigs.py"], cwd=str(repo))
            if p.returncode != 0:
                return bail("compose-defconfigs", (p.stderr or p.stdout).strip()[:400])
            _git(run, repo, "add", "-A")
            _commit(run, repo, f"nightly batch {version}: recompose defconfigs from the layers")
            step("compose-defconfigs", True, "defconfigs regenerated once for the batch")
    except (RuntimeError, ValueError, OSError) as exc:
        return bail("local edit", str(exc))

    # --- the gate, once, before anything is built -----------------------------
    p = _run(run, ["python3", "scripts/pengyos_gate.py", "run", "--repo", ".",
                   "--mode", "full", "--base", base], cwd=str(repo), timeout=3600)
    if p.returncode != 0:
        return bail("gate_first", "the repo gate failed; nothing was built or published:\n"
                                  + (p.stdout + p.stderr)[-1500:])
    step("gate_first", True, "repo gate green")

    # --- build, boot-check, sign, publish: once -------------------------------
    editions = sorted({e for a in live for e in a["editions"]})
    state_dir.mkdir(parents=True, exist_ok=True)
    notes = state_dir / f"notes-batch-{today.isoformat()}.md"
    notes.write_text(batch_notes(live, version))
    argv = ["scripts/release.sh", "--publish", "--channel", G.CHANNEL,
            "--notes", str(notes)]
    if cfg["skip_cosmic"]:
        argv.append("--skip-cosmic")
    if cfg["push"]:
        argv.append("--push")
    argv += editions
    try:
        G.assert_locked(argv)
    except G.GuardError as exc:
        return bail("channel_locked", str(exc))
    env = {"PENGYOS_RELEASE_KEY": key or ""}
    if remote:
        env["PENGYOS_SITE_REMOTE"] = remote
    step("release", True, " ".join(argv))
    try:
        p = _run(run, argv, cwd=str(repo), env={**__import__("os").environ, **env})
    except OSError as exc:
        # A step that cannot even start is still a step failure: bail takes the edits
        # back out and restores main, instead of throwing past run_batch and leaving a
        # nightly branch checked out (what a PermissionError did on 2026-10-08).
        return bail("release", f"could not start scripts/release.sh: {exc}")
    if p.returncode != 0:
        result["batch_failure"] = {"date": today.isoformat(),
                                   "detail": (p.stdout + p.stderr)[-400:]}
        return bail("release", "build / boot check / publish failed; nothing was "
                               "published:\n" + (p.stdout + p.stderr)[-2000:])
    step("published", True, f"{version} on {G.CHANNEL} carrying {len(live)} change(s)"
                           + (" and pushed to the live feed" if cfg["push"] else
                              " (local site dir only)"))

    # --- main moves only after a successful publish ---------------------------
    try:
        _git(run, repo, "checkout", "main")
        _git(run, repo, "merge", "--no-ff", branch,
             "-m", batch_merge_message(live, version, branch))
        step("merge", True, f"main now contains {branch}")
    except RuntimeError as exc:
        return bail("merge", f"published {version} to {G.CHANNEL} but could not merge "
                             f"{branch} into main: {exc}")

    result["applied"] = result["published"] = True
    result["applied_actions"] = [a["id"] for a in live]
    result["editions"] = editions
    return result


def record_batch(result: dict, actions: list[dict], state_dir: Path, st: dict,
                 today: dt.date, run=subprocess.run, repo: Path | None = None) -> None:
    """Record the batch: one action entry per change that shipped, one batch entry,
    the cooldown map, and the `patched_by` facts for patch shapes."""
    by_id = {a["id"]: a for a in actions}
    shipped = [by_id[i] for i in (result.get("applied_actions") or []) if i in by_id]
    for action in shipped:
        record(result, action, state_dir, st, today, run=run, repo=repo)
    for component, bad in (result.get("patch_failures") or {}).items():
        # Merge, do not replace. The known-bad set for a component accumulates night
        # after night; overwriting it would forget the fixes already proved wrong and
        # spend another pre-flight rediscovering them.
        cur = (st.get("patch_failures") or {}).get(component) or {}
        merged = {**cur, **bad}
        merged["fixes"] = sorted({*(cur.get("fixes") or []), *(bad.get("fixes") or [])})
        st.setdefault("patch_failures", {})[component] = merged

    entry = {"kind": "batch", "date": today.isoformat(),
             "published": bool(result.get("published")),
             "version": result.get("version", ""), "branch": result.get("branch", ""),
             "channel": G.CHANNEL,
             "changes": [a["component"] for a in shipped],
             "cves": sorted({c for a in shipped for c in (a.get("closes") or [])}),
             "editions": result.get("editions", []),
             "dropped": result.get("dropped", []), "error": result.get("error", "")}
    path = state_dir / "actions.json"
    log = json.loads(path.read_text()) if path.exists() else {"actions": []}
    log["actions"].append(entry)
    S.save(path, log)
    S.save(state_dir / "state.json", st)

    if repo is None or not shipped:
        return
    try:
        data = json.dumps({"kind": "batch", "version": result.get("version", ""),
                           "changes": [a["component"] for a in shipped],
                           "cves": entry["cves"], "dropped": entry["dropped"]},
                          sort_keys=True)
        _run(run, [sys.executable, "-m", "pengyos_attest", "append",
                   "--log", str(state_dir / "attest.jsonl"), "--type", "nightly-batch",
                   "--data", data], cwd=str(repo / "tools" / "pengyos-attest"))
    except (OSError, ValueError) as exc:
        print(f"[act] batch attest record skipped: {exc}")
