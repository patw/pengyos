"""plan.json — what the loop decided to do, and the guard that stopped the rest.

Deterministic: same observations + same state + same policy = same plan. No model,
no side effects. The refusals are as important as the actions: they are the record
that the guards actually ran, and they say *which* guard fired, so a candidate that
should have become an action is a visible bug rather than a silence.
"""
from __future__ import annotations

import datetime as dt
import tomllib
from pathlib import Path

from pengyos_nightly import edits as E
from pengyos_nightly import evidence as EV
from pengyos_nightly import guards as G
from pengyos_nightly.versions import bump_kind

ORDER = {"P0": 0, "P1": 1, "P2": 2}


def _matches(obs: dict, st: dict, cfg: dict, name: str, pkg: dict,
             attribution: dict | None = None) -> list[dict]:
    """Triaged CVE matches for one package, in the priorities the policy allows.

    `attribution` can move a match to a different package (see `_attribution`). A moved
    match carries its `attributed` record, so the plan says where it came from and on what
    evidence its exposure is claimed.
    """
    out = []
    for key, rec in st["cves"].items():
        cve, _, pkgname = key.partition("|")
        moved = (attribution or {}).get((cve, pkgname))
        owner = (moved or {}).get("package") or pkgname
        if owner != name or rec.get("resolved") or rec.get("patched_by"):
            continue
        # Already carried as a patch in our tree: not a candidate again, and not an
        # open issue - pkg-stats cannot know we carry the fix because the version moved
        # nowhere.
        if rec.get("patched_by"):
            continue
        if rec.get("priority") not in cfg["priorities"]:
            continue
        entry = {"cve": cve, "priority": rec["priority"], "cvss": rec.get("cvss"),
                 "kev": bool(rec.get("kev")), "epss": rec.get("epss"),
                 "unsure": cve in (pkg.get("unsure_cves") or []),
                 "confirmed": cve in (pkg.get("cves") or [])}
        if moved:
            entry["attributed"] = moved
        out.append(entry)
    out.sort(key=lambda c: (ORDER.get(c["priority"], 9), -(c.get("cvss") or 0), c["cve"]))
    return out


def _stock_recipe(build_root: Path, br_version: str, pkg: str,
                  info: dict | None = None) -> tuple[str, str, list[str]]:
    """(mk text, hash text, patch files) for a stock Buildroot package.

    pkg-stats already told us where the recipe is (`mk_path` / `pkg_path`), which is
    what makes a sub-package like gst1-plugins-good resolvable: its .mk lives under
    package/gstreamer1/, not package/gst1-plugins-good/.
    """
    root = build_root / f"buildroot-{br_version}"
    mk = None
    if info and info.get("mk_path"):
        cand = root / info["mk_path"]
        if cand.is_file():
            mk = cand
    if mk is None:
        cand = root / "package" / pkg / f"{pkg}.mk"
        if not cand.is_file():
            raise FileNotFoundError(f"{cand} not found")
        mk = cand
    hs = mk.with_suffix(".hash")
    patches = sorted(p.name for p in mk.parent.glob("*.patch"))
    if info and info.get("patch_files"):
        patches = sorted(info["patch_files"])
    return mk.read_text(), (hs.read_text() if hs.is_file() else ""), patches


def _verdicts(name: str, action: dict, pkg: dict, cfg: dict, st: dict,
              today: dt.date, index: int) -> list[dict]:
    checks = [
        ("denied_component", lambda: G.denied_component(name, cfg)),
        ("bump_event_approved", lambda: G.bump_event_approved(name, action["target"], cfg)),
        ("not_host_only", lambda: G.not_host_only(pkg)),
        ("lane_allowed", lambda: G.lane_allowed(action, cfg)),
        ("stock_version_immovable", lambda: G.stock_version_immovable(action)),
        ("major_never", lambda: G.major_never(action)),
        ("confirmed_only", lambda: G.confirmed_only(action)),
        ("evidence_required", lambda: G.evidence_required(action)),
        ("fix_commits_available", lambda: G.fix_commits_available(action)),
        ("patch_does_not_apply", lambda: G.patch_does_not_apply(
            name, st, [c["fix_commit"]["url"] for c in action["cves"] if c.get("fix_commit")])),
        ("patch_shape_allowed", lambda: G.patch_shape_allowed(action["shape"], cfg)),
        ("tree_move_not_executable", lambda: G.tree_move_executable(action["shape"])),
        ("cooldown", lambda: G.cooldown(name, st.get("actions", {}), cfg["cooldown_days"],
                                        today)),
        ("cap", lambda: G.cap(index, cfg["max_actions"])),
    ]
    out = []
    for label, fn in checks:
        ok, reason = fn()
        out.append({"guard": label, "ok": ok, "reason": reason})
    return out


def _edition_names(repo: Path) -> list[str]:
    """Every edition the repo declares, from its own targets.toml."""
    try:
        return [t["name"] for t in tomllib.loads((repo / "targets.toml").read_text())["targets"]]
    except (OSError, ValueError, KeyError):
        return []


def _cached(get):
    """One fetch per upstream URL per plan. Attribution and the edits want the same patch
    text; asking twice is how a plan becomes slow and self-inconsistent."""
    cache: dict[str, bytes] = {}

    def fetch(url: str) -> bytes:
        if url not in cache:
            cache[url] = get(url)
        return cache[url]
    return fetch


def _fix_components(url: str, get) -> set[str]:
    """Which upstream components a named fix touches, read from the patch itself."""
    try:
        parts = E.fetch_patches(url, get)
    except (OSError, ValueError):
        return set()
    comps: set[str] = set()
    for text in parts:
        comps |= E.patch_components(text)
    return comps


def _attribution(obs: dict, st: dict, cfg: dict, packages: dict, nvd_of, get) -> dict:
    """CVEs whose named fix changes a *different* component of the same monorepo.

    pkg-stats matches a project-wide CPE against the component carrying that CPE id, so a
    gst-plugins-bad fix is reported against our gstreamer1 while gst1-plugins-bad itself
    shows zero CVEs. The patch says which component it changes. If we ship that component
    at the same version - a monorepo releases its components in lockstep, and our
    gstreamer1, gst1-plugins-good and gst1-plugins-bad are all 1.24.13 - the CVE belongs to
    that package, and the fix can actually be carried there.

    If we ship no such component (gst-plugins-ugly is in no edition), the match stays where
    it was so the refusal is recorded rather than the CVE quietly vanishing.
    """
    out: dict[tuple[str, str], dict] = {}
    for key, rec in st["cves"].items():
        cve, _, pkgname = key.partition("|")
        if rec.get("resolved") or rec.get("patched_by"):
            continue
        if rec.get("priority") not in cfg["priorities"]:
            continue
        pkg = packages.get(pkgname)
        if not pkg or not EV.is_monorepo_product(EV.upstream_component(pkgname)):
            continue
        fc = EV.fix_commit(nvd_of(cve))
        if not fc or not fc.get("url"):
            continue
        comps = _fix_components(fc["url"], get)
        if len(comps) != 1:
            continue                      # no single component to attribute it to
        comp = next(iter(comps))
        if comp.lower() == EV.upstream_component(pkgname).lower():
            continue                      # already in the right place
        version = pkg.get("current") or ""
        owner = next((n for n, p in packages.items()
                      if EV.upstream_component(n).lower() == comp.lower()
                      and (p.get("current") or "") == version), "")
        out[(cve, pkgname)] = {
            "from": pkgname, "component": comp, "package": owner, "lockstep": version,
            "reason": f"the fix changes {comp}/, not {pkgname}'s files" + (
                f"; attributed to {owner}, pinned at the same {version}"
                if owner else f"; we ship no package for {comp} at {version}")}
    return out


def _candidate(obs: dict, st: dict, cfg: dict, name: str, pkg: dict, lane: str,
               target: str, today: dt.date, repo: Path, build_root: Path, br_version: str,
               nvd_of, index: int, get, packages: dict | None = None,
               attribution: dict | None = None) -> dict:
    """One candidate action, with its guards evaluated. Never raises.

    The cheap guards run first: a series jump or a denied component is refused for
    that reason, not for whatever went wrong while fetching a hash for an edit that
    was never going to be allowed.
    """
    current = pkg.get("current") or ""
    authored = pkg.get("tree") != "BUILDROOT"
    # A Buildroot bump is neither shape: it does not move a package pin and it does not
    # cherry-pick onto one. It moves the tree every edition builds from.
    is_bump = name == "buildroot" and lane == "bump"
    # A stock package cannot be version-bumped from here at all (see edits.py), so
    # the security lane carries upstream's fix commits onto the pinned version
    # instead. The patch shape therefore has no target: it does not move the pin.
    shape = "tree_move" if is_bump else ("recipe_bump" if authored else "patch_dir")
    if shape == "patch_dir":
        target = current
    kind = bump_kind(current, target) if current and target else "none"

    cves = _matches(obs, st, cfg, name, pkg, attribution)
    for c in cves:
        # A CVE attributed from a sibling component of a monorepo keeps the evidence that
        # matched it there - NVD's range is written against the project, and the only
        # reason it transfers is that both components are pinned at the same lockstep
        # version. That is recorded on the CVE, not assumed silently.
        moved = c.get("attributed") or {}
        src = moved.get("from") or name
        src_pkg = (packages or {}).get(src) or pkg
        c.update(EV.evidence(c["cve"], src, nvd_of(c["cve"]), current, target,
                             src_pkg.get("cpeid", "")))
        if c.get("affected") or c.get("covered"):
            c["fix_commit"] = EV.fix_commit(nvd_of(c["cve"]))

    # What this action may *claim*. For a patch shape the claim is exactly the set
    # of CVEs whose fix will be written into the tree — `carried_cves` is the same
    # function `patch_dir` iterates, so the notes, the commit message and the
    # `patched_by` record can never overstate the patch set. The affected CVEs left
    # out are recorded as `still_open` and named in the notes, not dropped.
    closes = (E.carried_cves([c for c in cves if c.get("affected")])
              if shape == "patch_dir" else
              [c["cve"] for c in cves if c.get("covered")])
    still_open = ([c["cve"] for c in cves if c.get("affected") and c["cve"] not in closes]
                  if shape == "patch_dir" else [])
    action = {"id": f"{lane}-{name}-{target}", "lane": lane, "component": name,
              "current": current, "target": target, "bump_kind": kind, "shape": shape,
              "editions": list(pkg.get("editions") or []), "cves": cves,
              "closes": closes, "still_open": still_open, "tree": pkg.get("tree", ""),
              "latest_upstream": pkg.get("latest", ""),
              "recipe": pkg.get("mk_path") or pkg.get("pkg_path", "")}

    # A tree move is a decision the operator reads in the plan, so its edit is built
    # before the verdicts: one local file read, no network, no build.
    bump_edits: list[E.Edit] = []
    if shape == "tree_move":
        try:
            bump_edits = E.tree_move(repo, target)
        except (OSError, ValueError) as exc:
            action["tree_move_error"] = str(exc)

    guards_done = _verdicts(name, action, pkg, cfg, st, today, index)
    if not all(g["ok"] for g in guards_done):
        # A tree move is a decision the operator reads *in the plan*, and building its edit
        # costs one local file read - no network, no build. So it is kept even when the
        # action is refused: a refusal that hides what would have changed is not reviewable.
        action["edits"] = [vars(x) for x in bump_edits]
        action["hash"], action["risks"] = None, []
        if shape == "tree_move":
            action["retires"] = E.carried_patch_files(repo)
            action["retire_check"] = (f"each patch must be re-checked against Buildroot "
                                      f"{target}: retired if that line already carries the fix, "
                                      "moved to the new version's directory if it does not")
        action["guards"] = guards_done
        action["allowed"] = False
        return action

    stock_patches = sorted(pkg.get("patch_files") or [])
    try:
        if shape == "tree_move":
            e, refused = bump_edits, []
        elif shape == "patch_dir":
            e, refused = E.patch_dir(repo, name, current,
                                     [c for c in cves if c.get("affected")],
                                     action["editions"], get,
                                     upstream=EV.upstream_component(name))
            hi = None
        else:
            p = repo / "buildroot-external" / "package" / name
            mk_text = (p / f"{name}.mk").read_text()
            e, hi = E.recipe_bump(repo, name, mk_text, (p / f"{name}.hash").read_text(),
                                  target, get)
            refused = []
    except E.NoCarriableFix as exc:
        # Named by its own guard, not folded into `recipe_unreadable`: "NVD's commit for
        # these CVEs is the advisory document" and "the recipe could not be read" are
        # different problems and the next night needs to know which one it has.
        action["edits"], action["hash"], action["risks"] = [], None, []
        action["not_carried"] = exc.refused
        action["guards"] = guards_done + [{"guard": "no_fix_for_this_package", "ok": False,
                                           "reason": str(exc)}]
        action["allowed"] = False
        return action
    except (OSError, ValueError) as exc:
        action["edits"] = []
        action["risks"] = []
        action["guards"] = guards_done + [{"guard": "recipe_unreadable", "ok": False,
                                           "reason": f"could not read the recipe to edit: "
                                                     f"{exc}"}]
        action["allowed"] = False
        return action

    # Recompute the claim from what was actually written. The planner's first `closes` is
    # what NVD's metadata promised; after the patches are fetched, some of those CVEs may
    # have no patch at all - the named commit was an advisory document, or a fix for a
    # sibling component of a monorepo. Those go back to `still_open` with a reason, so the
    # notes can only ever claim what the tree carries.
    if shape == "patch_dir":
        action["closes"] = sorted({c for x in e for c in (x.cves or [])})
        action["still_open"] = sorted({c["cve"] for c in cves if c.get("affected")}
                                      - set(action["closes"]))
        action["not_carried"] = refused
    action["edits"] = [vars(x) for x in e]
    action["hash"] = vars(hi) if hi else None
    action["patches"] = [x.path for x in e if x.path.endswith(".patch")]
    action["risks"] = ([f"the stock recipe's own patches still apply: {', '.join(stock_patches)}"]
                        if stock_patches and shape == "patch_dir" else [])
    rerooted = [x for x in e if x.path.endswith(".patch") and "re-rooted" in x.note]
    if rerooted:
        action["risks"].append(
            f"{len(rerooted)} patch(es) re-rooted from the upstream monorepo layout "
            f"(subprojects/<component>/ stripped) because Buildroot builds this package "
            f"from its own tarball; the hunks are upstream's, unchanged")
    action["guards"] = guards_done
    action["allowed"] = True
    if shape == "tree_move":
        # The retirement list is the point of recording it: these are the loop's own
        # security maintenance, and a tree move that does not say which of them the new
        # line already carries is a tree move that re-fixes upstream's work.
        action["retires"] = E.carried_patch_files(repo)
        action["retire_check"] = (f"each patch must be re-checked against Buildroot {target}: "
                                  "retired if that line already carries the fix, moved to the "
                                  "new version's directory if it does not")
    return action


def build_plan(obs: dict, st: dict, cfg: dict, today: dt.date, repo: Path,
               build_root: Path, nvd_of=lambda cve: {}, get=E.fetch_bytes) -> dict:
    """Actions (ranked, capped) and refusals (named), from the loop's own state."""
    packages = obs.get("packages", {})
    br_version = obs.get("buildroot", "")
    considered: dict[str, dict] = {}

    # Which CVEs belong to a different component of a monorepo than the one pkg-stats
    # reported them against. Computed once, with a shared fetch cache, before any candidate
    # is built - so a gst-plugins-bad fix is considered for gst1-plugins-bad.
    get = _cached(get)
    attribution = _attribution(obs, st, cfg, packages, nvd_of, get)

    # Security lane: any package carrying a triaged P0/P1 match.
    for name, pkg in sorted(packages.items()):
        if not _matches(obs, st, cfg, name, pkg, attribution):
            continue
        considered[name] = ("security", pkg.get("latest") or "")

    # Routine lane: the version gaps observe already found (authored recipes, the
    # kernel's own series, the driver branch). They are considered even when the lane
    # is disabled, so the plan records *why* they were not acted on: a candidate that
    # never appears is indistinguishable from one nobody looked at.
    for f in obs.get("findings", []):
        if f.get("lane") != "routine":
            continue
        name = f["component"]
        if name in considered:
            continue
        pkg = packages.get(name)
        if pkg is None:
            considered[name] = ("routine", f.get("available") or "")
            continue
        considered[name] = ("routine", pkg.get("latest") or f.get("available") or "")

    # The bump lane exists only when the operator scheduled it. A newer Buildroot series
    # is already a finding; this is the decision to act on one, and it is considered even
    # when it will be refused, so the plan records which guard stopped it.
    if cfg.get("bump_buildroot"):
        considered["buildroot"] = ("bump", cfg["bump_buildroot"])

    actions, refused = [], []
    # Security first, worst first; routine after it.
    ranked = sorted(considered.items(),
                    key=lambda kv: (0 if kv[1][0] == "security" else 1, kv[0]))
    for name, (lane, target) in ranked:
        pkg = packages.get(name) or {"current": "", "latest": target, "tree": "",
                                     "editions": [], "cves": [], "unsure_cves": [],
                                     "host_only": False}
        if name == "buildroot":
            pkg = {"current": obs.get("buildroot") or "", "latest": target, "tree": "EXTERNAL",
                   "editions": _edition_names(repo), "cves": [], "unsure_cves": [],
                   "host_only": False, "mk_path": "", "pkg_path": "", "patch_files": [],
                   "cpeid": ""}
        a = _candidate(obs, st, cfg, name, pkg, lane, target, today, repo, build_root,
                       br_version, nvd_of, len(actions), get, packages, attribution)
        if a["allowed"]:
            actions.append(a)
        else:
            a["refused_by"] = [g["guard"] for g in a["guards"] if not g["ok"]]
            a["refused_reason"] = [g["reason"] for g in a["guards"] if not g["ok"]]
            refused.append(a)

    targets = sorted({e for a in actions for e in a["editions"]})
    return {"date": today.isoformat(), "channel": G.CHANNEL, "policy": cfg,
            "actions": actions, "refused": refused,
            "attributed": [{"cve": c, "from": p, **a} for (c, p), a in sorted(attribution.items())],
            "release": {"targets": targets,
                        "core_note": "pengyos-core has no A/B layout yet, so it is built "
                                     "but never update-published"},
            "counts": {"actions": len(actions), "refused": len(refused),
                       "attributed": len(attribution)}}
