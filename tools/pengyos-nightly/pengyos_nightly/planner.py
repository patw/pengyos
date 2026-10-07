"""plan.json — what the loop decided to do, and the guard that stopped the rest.

Deterministic: same observations + same state + same policy = same plan. No model,
no side effects. The refusals are as important as the actions: they are the record
that the guards actually ran, and they say *which* guard fired, so a candidate that
should have become an action is a visible bug rather than a silence.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from pengyos_nightly import edits as E
from pengyos_nightly import evidence as EV
from pengyos_nightly import guards as G
from pengyos_nightly.versions import bump_kind

ORDER = {"P0": 0, "P1": 1, "P2": 2}


def _matches(obs: dict, st: dict, cfg: dict, name: str, pkg: dict) -> list[dict]:
    """Triaged CVE matches for one package, in the priorities the policy allows."""
    out = []
    for key, rec in st["cves"].items():
        cve, _, pkgname = key.partition("|")
        if pkgname != name or rec.get("resolved"):
            continue
        # Already carried as a patch in our tree: not a candidate again, and not an
        # open issue - pkg-stats cannot know we carry the fix because the version moved
        # nowhere.
        if rec.get("patched_by"):
            continue
        if rec.get("priority") not in cfg["priorities"]:
            continue
        out.append({"cve": cve, "priority": rec["priority"], "cvss": rec.get("cvss"),
                    "kev": bool(rec.get("kev")), "epss": rec.get("epss"),
                    "unsure": cve in (pkg.get("unsure_cves") or []),
                    "confirmed": cve in (pkg.get("cves") or [])})
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
        ("denied_component", lambda: G.denied_component(name)),
        ("not_host_only", lambda: G.not_host_only(pkg)),
        ("lane_allowed", lambda: G.lane_allowed(action, cfg)),
        ("stock_version_immovable", lambda: G.stock_version_immovable(action)),
        ("major_never", lambda: G.major_never(action)),
        ("confirmed_only", lambda: G.confirmed_only(action)),
        ("evidence_required", lambda: G.evidence_required(action)),
        ("fix_commits_available", lambda: G.fix_commits_available(action)),
        ("patch_does_not_apply", lambda: G.patch_does_not_apply(name, st)),
        ("patch_shape_allowed", lambda: G.patch_shape_allowed(action["shape"], cfg)),
        ("cooldown", lambda: G.cooldown(name, st.get("actions", {}), cfg["cooldown_days"],
                                        today)),
        ("cap", lambda: G.cap(index, cfg["max_actions"])),
    ]
    out = []
    for label, fn in checks:
        ok, reason = fn()
        out.append({"guard": label, "ok": ok, "reason": reason})
    return out


def _candidate(obs: dict, st: dict, cfg: dict, name: str, pkg: dict, lane: str,
               target: str, today: dt.date, repo: Path, build_root: Path, br_version: str,
               nvd_of, index: int, get) -> dict:
    """One candidate action, with its guards evaluated. Never raises.

    The cheap guards run first: a series jump or a denied component is refused for
    that reason, not for whatever went wrong while fetching a hash for an edit that
    was never going to be allowed.
    """
    current = pkg.get("current") or ""
    authored = pkg.get("tree") != "BUILDROOT"
    # A stock package cannot be version-bumped from here at all (see edits.py), so
    # the security lane carries upstream's fix commits onto the pinned version
    # instead. The patch shape therefore has no target: it does not move the pin.
    shape = "recipe_bump" if authored else "patch_dir"
    if shape == "patch_dir":
        target = current
    kind = bump_kind(current, target) if current and target else "none"

    cves = _matches(obs, st, cfg, name, pkg)
    for c in cves:
        c.update(EV.evidence(c["cve"], name, nvd_of(c["cve"]), current, target,
                             pkg.get("cpeid", "")))
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

    guards_done = _verdicts(name, action, pkg, cfg, st, today, index)
    if not all(g["ok"] for g in guards_done):
        action["edits"], action["hash"], action["risks"] = [], None, []
        action["guards"] = guards_done
        action["allowed"] = False
        return action

    stock_patches = sorted(pkg.get("patch_files") or [])
    try:
        if shape == "patch_dir":
            e = E.patch_dir(repo, name, current, [c for c in cves if c.get("affected")],
                            action["editions"], get)
            hi = None
        else:
            p = repo / "buildroot-external" / "package" / name
            mk_text = (p / f"{name}.mk").read_text()
            e, hi = E.recipe_bump(repo, name, mk_text, (p / f"{name}.hash").read_text(),
                                  target, get)
    except (OSError, ValueError) as exc:
        action["edits"] = []
        action["risks"] = []
        action["guards"] = guards_done + [{"guard": "recipe_unreadable", "ok": False,
                                           "reason": f"could not read the recipe to edit: "
                                                     f"{exc}"}]
        action["allowed"] = False
        return action

    action["edits"] = [vars(x) for x in e]
    action["hash"] = vars(hi) if hi else None
    action["patches"] = [x.path for x in e if x.path.endswith(".patch")]
    action["risks"] = ([f"the stock recipe's own patches still apply: {', '.join(stock_patches)}"]
                        if stock_patches and shape == "patch_dir" else [])
    action["guards"] = guards_done
    action["allowed"] = True
    return action


def build_plan(obs: dict, st: dict, cfg: dict, today: dt.date, repo: Path,
               build_root: Path, nvd_of=lambda cve: {}, get=E.fetch_bytes) -> dict:
    """Actions (ranked, capped) and refusals (named), from the loop's own state."""
    packages = obs.get("packages", {})
    br_version = obs.get("buildroot", "")
    considered: dict[str, dict] = {}

    # Security lane: any package carrying a triaged P0/P1 match.
    for name, pkg in sorted(packages.items()):
        if not _matches(obs, st, cfg, name, pkg):
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

    actions, refused = [], []
    # Security first, worst first; routine after it.
    ranked = sorted(considered.items(),
                    key=lambda kv: (0 if kv[1][0] == "security" else 1, kv[0]))
    for name, (lane, target) in ranked:
        pkg = packages.get(name) or {"current": "", "latest": target, "tree": "",
                                     "editions": [], "cves": [], "unsure_cves": [],
                                     "host_only": False}
        a = _candidate(obs, st, cfg, name, pkg, lane, target, today, repo, build_root,
                       br_version, nvd_of, len(actions), get)
        if a["allowed"]:
            actions.append(a)
        else:
            a["refused_by"] = [g["guard"] for g in a["guards"] if not g["ok"]]
            a["refused_reason"] = [g["reason"] for g in a["guards"] if not g["ok"]]
            refused.append(a)

    targets = sorted({e for a in actions for e in a["editions"]})
    return {"date": today.isoformat(), "channel": G.CHANNEL, "policy": cfg,
            "actions": actions, "refused": refused,
            "release": {"targets": targets,
                        "core_note": "pengyos-core has no A/B layout yet, so it is built "
                                     "but never update-published"},
            "counts": {"actions": len(actions), "refused": len(refused)}}
