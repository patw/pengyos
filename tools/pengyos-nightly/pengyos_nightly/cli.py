"""pengyos-nightly: observe upstream, triage CVEs with the local model, report.

    pengyos-nightly run     [--no-email] [--force-email]
    pengyos-nightly observe
    pengyos-nightly triage  [--max-triage N] [--jobs N] [--model ALIAS]
    pengyos-nightly report  [--no-email] [--force-email] [--mark-reported]
    pengyos-nightly plan    [--json]
    pengyos-nightly act     [--only ID] [--max N] [--apply] [--no-email] [--to ADDR]
    pengyos-nightly status          # is the loop alive? read-only, exit 1 if not
    pengyos-nightly web             # the decision page: approve or deny (needs Flask)

Phase 1 of tools/pengyos-nightly/spec.md: nothing is built or published.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import subprocess
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pengyos_nightly import (__version__, act as actlane, config as cfgmod, decisions, edits,
                             guards, planner, report, sources, state, triage)

HOME = Path.home()


def _paths(args) -> dict:
    sd = Path(args.state_dir).expanduser()
    return {"state_dir": sd, "obs": sd / "observations.json", "state": sd / "state.json",
            "nvd": sd / "nvd", "reports": sd / "reports", "plan": sd / "plan.json",
            "policy": sd / "nightly.toml", "actions": sd / "actions.json",
            "decisions": sd / "decisions.json",
            "site": Path(getattr(args, "site", HOME / "build/pengyos/site")).expanduser()}


def _buildroot_dir(build_root: Path, version: str) -> Path:
    return build_root / f"buildroot-{version}"


def observe(args, get=sources.http_get, run_cmd=sources.run_cmd, run_pkgstats=sources.run_pkgstats) -> dict:
    p = _paths(args)
    repo, build_root = Path(args.repo).expanduser(), Path(args.build_root).expanduser()
    p["state_dir"].mkdir(parents=True, exist_ok=True)
    errors: dict[str, str] = {}
    findings: list[dict] = []
    targets = tomllib.loads((repo / "targets.toml").read_text())["targets"]
    br_version = targets[0]["br_version"]
    buildroot = _buildroot_dir(build_root, br_version)

    # Buildroot packages, per edition with a configured tree.
    per_edition = []
    for t in targets:
        out = build_root / "output" / t["name"]
        if not (out / ".config").exists():
            continue
        js = p["state_dir"] / f"pkgstats-{t['name']}.json"
        try:
            run_pkgstats(buildroot, out, js, p["nvd"])
            per_edition.append(sources.parse_pkgstats(json.loads(js.read_text()), t["name"]))
        except Exception as e:  # one edition failing is reported, not fatal
            errors[f"pkg-stats {t['name']}"] = str(e)[:300]
    packages = sources.merge_packages(per_edition)
    findings += sources.package_findings(packages)

    def guard(name, fn):
        try:
            return fn()
        except Exception as e:
            errors[name] = str(e)[:300]
            return []

    findings += guard("buildroot", lambda: sources.buildroot_findings(
        sources.parse_ls_remote(run_cmd(["git", "ls-remote", "--tags",
                                         "https://gitlab.com/buildroot.org/buildroot.git"])),
        br_version))
    defconfig = (repo / "buildroot-external/configs/pengyos_core_defconfig").read_text()
    kver = sources.defconfig_value(defconfig, "BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE") or ""
    findings += guard("kernel.org", lambda: sources.kernel_findings(
        json.loads(get("https://www.kernel.org/releases.json")), kver))
    nv_mk = repo / "buildroot-external/package/nvidia-open/nvidia-open.mk"
    nver = sources.mk_value(nv_mk.read_text(), "NVIDIA_OPEN_VERSION") if nv_mk.exists() else None
    if nver:
        findings += guard("nvidia", lambda: sources.nvidia_findings(
            sources.parse_ls_remote(run_cmd(["git", "ls-remote", "--tags",
                                             "https://github.com/NVIDIA/open-gpu-kernel-modules"])),
            nver))

    # PengyR: the agent every image ships is an authored pin with its own
    # upstream, so pkg-stats cannot see it go stale. Read the pin out of the
    # recipe the way the nvidia pin is read.
    pengy_mk = repo / "buildroot-external/package/pengy/pengy.mk"
    pver = sources.mk_value(pengy_mk.read_text(), "PENGY_VERSION") if pengy_mk.exists() else None
    if pver:
        findings += guard("pengyr", lambda: sources.pengyr_findings(
            sources.parse_ls_remote(run_cmd(["git", "ls-remote", "--tags",
                                             "https://github.com/patw/PengyR"])),
            pver))

    # OpenSSH: our fork (package/openssh-pengyos) is what ships and the stock recipe is
    # switched off, so pkg-stats never sees it. This is the observe_source the divergence
    # ledger entry names; without it the fork rots silently - the PengyR case, with an
    # exposed sshd attached.
    openssh_mk = repo / "buildroot-external/package/openssh-pengyos/openssh-pengyos.mk"
    over = sources.mk_value(openssh_mk.read_text(), "OPENSSH_PENGYOS_VERSION") \
        if openssh_mk.exists() else None
    if over:
        findings += guard("openssh-fork", lambda: sources.openssh_fork_findings(
            sources.parse_ls_remote(run_cmd(["git", "ls-remote", "--tags",
                                             "https://github.com/openssh/openssh-portable"])),
            over))

    # ffmpeg: our fork (package/ffmpeg-pengyos) is what ships and the stock recipe is
    # switched off, so pkg-stats never sees it - the same blindness the openssh fork
    # has, on the Desktop's media path. divergence.toml -> ffmpeg-9.0.2-fork names
    # this as its observe_source. ffmpeg publishes no git tags, so the source is the
    # release directory listing.
    ffmpeg_mk = repo / "buildroot-external/package/ffmpeg-pengyos/ffmpeg-pengyos.mk"
    fver = sources.mk_value(ffmpeg_mk.read_text(), "FFMPEG_PENGYOS_VERSION") \
        if ffmpeg_mk.exists() else None
    if fver:
        findings += guard("ffmpeg-fork", lambda: sources.ffmpeg_fork_findings(
            sources.http_get("https://ffmpeg.org/releases/"), fver))

    # hplip: our fork (package/hplip-pengyos) is what ships and the stock recipe is
    # switched off, so pkg-stats never sees it. divergence.toml -> hplip-3.26.6-fork
    # names this as its observe_source. hplip publishes no git tags; the SourceForge
    # files page is the index.
    hplip_mk = repo / "buildroot-external/package/hplip-pengyos/hplip-pengyos.mk"
    hver = sources.mk_value(hplip_mk.read_text(), "HPLIP_PENGYOS_VERSION") \
        if hplip_mk.exists() else None
    if hver:
        findings += guard("hplip-fork", lambda: sources.hplip_fork_findings(
            sources.http_get("https://sourceforge.net/projects/hplip/files/hplip/"), hver))

    # upower: our fork (package/upower-pengyos) is what ships and the stock recipe is
    # switched off, so pkg-stats never sees it - and NVD has no upower CPE, so this is
    # the fork's only observer. divergence.toml -> upower-1.91.5-fork names it as the
    # observe_source. gitlab.freedesktop.org's bot wall blocks the web UI, not git.
    upower_mk = repo / "buildroot-external/package/upower-pengyos/upower-pengyos.mk"
    uver = sources.mk_value(upower_mk.read_text(), "UPOWER_PENGYOS_VERSION") \
        if upower_mk.exists() else None
    if uver:
        findings += guard("upower-fork", lambda: sources.upower_fork_findings(
            sources.parse_ls_remote(run_cmd(["git", "ls-remote", "--tags",
                                             "https://gitlab.freedesktop.org/upower/upower.git"])),
            uver))

    def cosmic():
        comps = sources.read_cosmic_components((repo / "scripts/cosmic-components.tsv").read_text())
        tags = {}
        for c in comps:
            try:
                tags[c["name"]] = sources.parse_ls_remote(run_cmd(["git", "ls-remote", "--tags", c["url"]]))
            except Exception as e:
                errors[f"cosmic {c['name']}"] = str(e)[:200]
        return sources.cosmic_findings(comps, tags)
    findings += guard("cosmic", cosmic)

    cves = sorted({c for pk in packages.values() for c in pk["cves"] + pk["unsure_cves"]})
    kev_all = guard("cisa-kev", lambda: sources.parse_kev(json.loads(get(sources.KEV_URL)))) or {}
    kev = {c: v for c, v in kev_all.items() if c in set(cves)}
    epss = guard("epss", lambda: sources.fetch_epss(cves, get=get)) or {}

    obs = {"date": dt.date.today().isoformat(), "kernel": kver, "buildroot": br_version,
           "packages": packages, "findings": findings, "kev": kev, "epss": epss,
           "errors": errors}
    p["obs"].write_text(json.dumps(obs, indent=1, sort_keys=True))
    return obs


def do_triage(args, complete=None) -> list[dict]:
    """Triage every CVE match not already in state.json; returns the new ones."""
    p = _paths(args)
    obs = json.loads(p["obs"].read_text())
    st = state.load(p["state"])
    if complete is None and not args.no_model:
        s = triage.pengy_settings()
        complete = triage.make_complete(s["base_url"], s.get("api_key", ""), args.model)

    todo = []
    present = set()
    for name, pkg in sorted(obs["packages"].items()):
        for cve in pkg["cves"] + pkg["unsure_cves"]:
            key = f"{cve}|{name}"
            present.add(key)
            rec = st["cves"].get(key)
            # Triage a match when it is new, or when it came back after being resolved
            # (the package moved past it and back). A match we carry a patch for is
            # neither: pkg-stats will keep flagging it because the version did not move,
            # and re-triaging it every night spends a model call to re-learn a decision
            # that has already been acted on.
            if rec is None or (rec.get("resolved") and not rec.get("patched_by")):
                todo.append((cve, name, {**pkg, "unsure": cve in pkg["unsure_cves"]}))
            elif rec.get("version") != pkg["current"]:
                # The recorded version is the version at first triage, and nothing
                # moved it since. CVE-2022-38096|linux read `version: 7.2.5` while every
                # tree built 7.2.11: the triage verdict ("P1, plausibly applies") was
                # made against 7.2.5, and a reader of the record cannot tell which
                # version it describes. Refresh the version the record claims so it
                # names the tree it is about; the verdict itself is not re-derived,
                # which is the model call this loop is trying not to spend twice.
                rec["version"] = pkg["current"]
                rec["version_refreshed"] = obs["date"]
    # Mark matches that disappeared (the package moved past them) as resolved.
    for key, rec in st["cves"].items():
        if key not in present and not rec.get("resolved"):
            rec["resolved"] = obs["date"]

    todo = todo[: args.max_triage]

    def one(item):
        cve, name, pkg = item
        nvd = sources.nvd_record(p["nvd"], cve)
        verdict = triage.triage_cve(cve, name, pkg, nvd, obs["kev"], obs["epss"], complete)
        return {"cve": cve, "package": name, "version": pkg["current"],
                "editions": pkg["editions"], "cvss": nvd.get("cvss"),
                "kev": cve in obs["kev"], "epss": (obs["epss"].get(cve) or {}).get("epss"),
                "first_seen": obs["date"], "reported": False, **verdict}

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(one, todo))
    for r in results:
        st["cves"][f"{r['cve']}|{r['package']}"] = r
    state.save(p["state"], st)
    return results


def do_report(args, send=report.send, today: dt.date | None = None) -> dict:
    p = _paths(args)
    obs = json.loads(p["obs"].read_text())
    st = state.load(p["state"])
    today = today or dt.date.today()
    digest = today.weekday() == 0 and st.get("last_digest") != today.isoformat()

    new_cves = [c for c in st["cves"].values()
                if not c.get("reported") and not c.get("resolved") and not c.get("patched_by")]
    new_findings = [f for f in obs["findings"] if state.finding_key(f) not in st["findings"]]
    errors = obs.get("errors", {})
    # "Open" means open in the image we ship. A CVE closed by a patch we carry is not
    # open, even though pkg-stats still flags it - counting it as open would put the
    # same 7 libxml2 CVEs in every report from now on.
    open_cves = [c for c in st["cves"].values()
                 if not c.get("resolved") and not c.get("patched_by")]
    carried = [c for c in st["cves"].values() if c.get("patched_by")]
    acted = []
    if p["actions"].exists():
        try:
            acted = [a for a in json.loads(p["actions"].read_text())["actions"]
                     if a.get("published")]
        except (ValueError, KeyError):
            acted = []
    order = {"P0": 0, "P1": 1, "P2": 2}
    new_cves.sort(key=lambda c: (order[c["priority"]], -(c.get("cvss") or 0)))
    open_cves.sort(key=lambda c: (order[c["priority"]], -(c.get("cvss") or 0)))

    # Whenever we DO send, the standing state travels with it. A digest or a forced
    # report that says only "nothing new" is indistinguishable from a broken loop -
    # which is how two quiet nights went unnoticed.
    show_open = digest or bool(args.force_email)
    body = report.render(today.isoformat(), new_cves, new_findings, errors,
                         open_cves if show_open else None,
                         obs["findings"] if show_open else None, digest,
                         carried if show_open else None, acted if show_open else None,
                         web_url=cfgmod.load(p["policy"]).get("web_url", ""),
                         act_at=cfgmod.act_time(cfgmod.load(p["policy"])))
    subj = report.subject(today.isoformat(), new_cves, new_findings, errors, digest,
                          open_cves if show_open else None,
                          obs["findings"] if show_open else None)
    p["reports"].mkdir(parents=True, exist_ok=True)
    out = p["reports"] / f"{today.isoformat()}.html"
    out.write_text(body)

    should_send = bool(new_cves or new_findings or errors or digest or args.force_email)
    sent = False
    delivery_error = None
    if should_send and not args.no_email:
        try:
            sent = send(subj, body, out, to=args.to)
        except subprocess.CalledProcessError as exc:
            # A broken mailer is a delivery failure, not a reason to abandon the run:
            # the plan step still has to happen, or one bad email silently costs the
            # night's whole act lane.
            sent = False
            detail = (exc.stderr or exc.stdout or "").strip().splitlines()
            delivery_error = f"{exc.cmd[0]} exited {exc.returncode}"
            if detail:
                delivery_error += f": {detail[-1]}"
    if should_send and not args.no_email and not sent:
        why = (f"delivery failed - {delivery_error}" if delivery_error
               else "no mailer configured (set PENGYOS_EMAIL_SCRIPT)")
        print(f"report: {why}; the report is at {out} and the queue stays unreported")
    # Only a DELIVERED report consumes the queue. `--no-email` renders to disk and
    # leaves everything unreported, so the next real run still sends it. Marking on
    # --no-email is what cost the operator their first digest: a development run on
    # 2026-10-03 marked 184 CVE matches and 7 findings as reported, and the 19:00
    # cron run that night correctly found "nothing new" and stayed quiet.
    # --mark-reported is the explicit opt-in for when you have already read the
    # on-disk report and want it cleared.
    if sent or getattr(args, "mark_reported", False):
        for c in new_cves:
            c["reported"] = True
        for f in new_findings:
            st["findings"][state.finding_key(f)] = today.isoformat()
        st["last_report"] = today.isoformat()
        st["last_delivery_error"] = ""
        if digest:
            st["last_digest"] = today.isoformat()
        state.save(p["state"], st)
    elif delivery_error:
        # A failed delivery is a fault in the loop, not a quiet night. Recorded in
        # state so `status` can name it instead of someone reading a traceback.
        st["last_delivery_error"] = f"{today.isoformat()}: {delivery_error}"
        state.save(p["state"], st)
    return {"subject": subj, "report": str(out), "sent": sent, "new_cves": len(new_cves),
            "new_findings": len(new_findings), "errors": len(errors),
            "delivery_error": delivery_error}


def do_plan(args, today: dt.date | None = None, get=None) -> dict:
    """Decide what to do, write plan.json, change nothing else."""
    p = _paths(args)
    repo, build_root = Path(args.repo).expanduser(), Path(args.build_root).expanduser()
    if not p["obs"].exists():
        print("plan: no observations.json - run observe first")
        return {"actions": [], "refused": [], "counts": {"actions": 0, "refused": 0}}
    obs = json.loads(p["obs"].read_text())
    st = state.load(p["state"])
    cfg = cfgmod.load(p["policy"])
    plan = planner.build_plan(obs, st, cfg, today or dt.date.today(), repo, build_root,
                              lambda cve: sources.nvd_raw(p["nvd"], cve),
                              get or edits.fetch_bytes,
                              decisions=decisions.load(p["decisions"]))
    p["state_dir"].mkdir(parents=True, exist_ok=True)
    p["plan"].write_text(json.dumps(plan, indent=1, sort_keys=True))
    return plan


def notify_act(args, result: dict, batch: list[dict], today: dt.date,
               send=report.send) -> bool:
    """Tell the operator about a night the act lane refused. Fail-soft.

    Silence is right for "nothing to do" and was being used for "refused". The refusal
    landed in the cron log, in actions.json and in `status`; the operator's only way to
    find out was to ask. On 2026-10-18 a 6-change batch closing 11 CVE matches was
    refused at clean_tree by a PengyTasker job holding /data/PengyOS dirty, and the
    night went missing with no signal at all.

    A mailer that fails must not change the act result or the exit code: the refusal is
    already recorded, and a broken mailer must not turn a reported failure into a
    different one.
    """
    p = _paths(args)
    payload = dict(result)
    payload.setdefault("planned", [a["id"] for a in batch])
    payload.setdefault("editions", sorted({e for a in batch for e in a["editions"]}))
    payload.setdefault("cves", sorted({c for a in batch for c in (a.get("closes") or [])}))
    web_url = cfgmod.load(p["policy"]).get("web_url", "")
    body = report.render_act_failure(today.isoformat(), payload, web_url=web_url)
    subj = report.act_subject(today.isoformat(), payload)
    p["reports"].mkdir(parents=True, exist_ok=True)
    out = p["reports"] / f"{today.isoformat()}-act.html"
    out.write_text(body)          # on disk first: the mailer may fail, the notice may not
    try:
        sent = send(subj, body, out, to=getattr(args, "to", None))
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip().splitlines()
        why = f"{exc.cmd[0]} exited {exc.returncode}" + (f": {detail[-1]}" if detail else "")
        print(f"act: refusal notice NOT delivered ({why}); the report is at {out}")
        return False
    print(f"act: refusal notice sent -> {out}")
    return sent


def do_act(args, today: dt.date | None = None, run=subprocess.run,
           send=report.send) -> dict:
    """Run the night's batch of allowed actions end to end, as ONE release.

    Dry run unless --apply. The batch is the unit of work; the signed image is the
    unit of risk: several changes, one branch, one build, one boot check, one
    signature, one published version (see spec.md, phase 3). `run` is injectable so
    the batch can be tested against a fake build without faking git.
    """
    p = _paths(args)
    if not p["plan"].exists():
        print("act: no plan.json - run plan first")
        return {"error": "no plan"}
    # The plan is written at 19:00 and act runs at 21:00. A decision made on the web page
    # in between must count tonight, not tomorrow, so a plan older than the last decision
    # is rebuilt from the same observations first.
    if (p["decisions"].exists() and p["obs"].exists()
            and p["decisions"].stat().st_mtime > p["plan"].stat().st_mtime):
        print("act: decisions changed since the plan was written - re-planning")
        do_plan(args, today=today)
    plan = json.loads(p["plan"].read_text())
    cfg = cfgmod.load(p["policy"])
    allowed = [a for a in plan["actions"] if a.get("allowed")]
    if args.only:
        allowed = [a for a in allowed if a["id"] == args.only]
    if not allowed:
        print(f"act: nothing allowed in the plan ({len(plan['refused'])} candidate(s) refused)")
        for r in plan["refused"]:
            print(f"  - {r['id']}: {', '.join(r.get('refused_by', []))}")
        # Not an error: most nights the correct answer is "nothing to do", and a
        # non-zero exit here would make a healthy loop look broken to a watchdog.
        # `status` is the probe for that question.
        return {"nothing_allowed": True, "refused": len(plan["refused"])}
    limit = min(cfg["max_actions"], getattr(args, "max", 0) or cfg["max_actions"])
    batch = allowed[:limit]
    if len(allowed) > len(batch):
        print(f"act: {len(allowed)} allowed, batching the first {len(batch)} "
              f"(max_actions = {cfg['max_actions']}); the rest are queued for the next night")
    editions = sorted({e for a in batch for e in a["editions"]})
    cves = sorted({c for a in batch for c in (a.get("closes") or [])})
    print(f"act: batch of {len(batch)} change(s) "
          f"[{', '.join(a['component'] for a in batch)}] -> one image for "
          f"{', '.join(editions)}"
          + (f", closing {len(cves)} CVE match(es)" if cves else ""))
    result = actlane.run_batch(batch, cfg, Path(args.repo).expanduser(),
                               Path(args.build_root).expanduser(), p["site"],
                               p["state_dir"], apply=args.apply, run=run, today=today)
    if args.apply:
        actlane.record_batch(result, batch, p["state_dir"], state.load(p["state"]),
                             today or dt.date.today(), repo=Path(args.repo).expanduser())
        if result.get("published"):
            # Re-plan so plan.json and `status` describe the world after the release,
            # not the one before it: otherwise status keeps advertising the changes it
            # just published as the next thing to do.
            do_plan(args, today=today)
        elif result.get("error") and not getattr(args, "no_email", False):
            # A refusal is the guard doing its job, and the guard is silent. Say so.
            notify_act(args, result, batch, today or dt.date.today(), send=send)
    return result


def _blocked_batches(actions_path: Path, today: dt.date) -> list[dict]:
    """Batches that ran and published nothing **since the last one that did**.

    A blocked loop is not a quiet loop - but a failure a later batch already recovered
    from is not a current problem either. Reading the most recent *failure* instead of
    the most recent *batch* kept `status` reporting "last batch published nothing" all
    morning on 2026-10-08, after 26.10.14 had been built, signed, published and merged.
    """
    if not actions_path.exists():
        return []
    try:
        log = json.loads(actions_path.read_text()).get("actions", [])
    except (OSError, ValueError):
        return []
    out = []
    for a in reversed(log):
        if a.get("kind") != "batch":
            continue
        if a.get("published"):
            break
        out.append(a)
    return list(reversed(out))


def _bump_lane(cfg: dict, obs: dict) -> dict:
    """The Buildroot release-bump lane as a status reader sees it (spec phase 4).

    A newer Buildroot release *series* is a finding whose action is the operator's
    decision; a point release inside our own series is not a tree move and is not
    flagged as one. `status` has to say which state we are in, because "the loop is
    quiet" and "a decision is waiting on a human" look identical from outside — and the
    lane is inert by default, so silence is its normal state.

    These are attention lines, not failures: they do not flip `ok`, the same way a queued
    batch with act_enabled = false does not. The loop is doing what it was told; what is
    outstanding is a human decision.
    """
    scheduled = (cfg or {}).get("bump_buildroot") or ""
    found = [f for f in obs.get("findings", []) if f.get("component") == "buildroot"]
    series = next((f for f in found if f.get("lane") == "major"), None)
    available = sorted({f.get("available", "") for f in found if f.get("available")})
    out = {"current": obs.get("buildroot") or "",
           "finding": (series or {}).get("available", ""),
           "available": available,
           "scheduled": scheduled,
           "problems": [],
           # Never true until the act lane grows a full-rebuild path
           # (guards.tree_move_executable): a scheduled bump is a decision to review.
           "executable": False}
    out["state"] = (f"scheduled -> {scheduled} (not executable yet)"
                    if scheduled else "inert")
    if out["finding"] and not scheduled:
        out["problems"] = [
            f'Buildroot {out["finding"]} is a newer release series and nothing is scheduled '
            f'(bump lane: inert). Set bump_buildroot = "{out["finding"]}" in nightly.toml to '
            f'schedule the tree move, or leave it a finding.']
    elif scheduled and available and scheduled not in available:
        out["problems"] = [
            f'bump_buildroot = "{scheduled}" is not what the observer found (newer '
            f'release(s): {", ".join(available)}). The plan would still move every edition '
            f'to {scheduled}, because that is what was scheduled - which is the point of '
            f'checking it against the finding first.']
    elif scheduled and not available:
        out["problems"] = [
            f'bump_buildroot = "{scheduled}" is scheduled but the observer found no newer '
            f'Buildroot release at all: {out["current"]} is still the newest series upstream.']
    return out


def do_status(args, now: float | None = None, today: dt.date | None = None) -> dict:
    """Answer "is the loop alive?" without waiting for an email.

    The loop is silent by design when nothing is new, which is the right default -
    but silence is exactly what made two dead-looking nights indistinguishable from
    a healthy one. `status` is the inspectable half of that choice: read-only, no
    email, non-zero exit when something is actually wrong, so a watchdog or a cron
    `-f` check can use it later.
    """
    p = _paths(args)
    now = time.time() if now is None else now
    out: dict = {"ok": True, "problems": []}

    if not p["obs"].exists():
        print("status: NEVER RUN - no observations.json")
        return {"ok": False, "problems": ["never observed"]}

    obs = json.loads(p["obs"].read_text())
    st = state.load(p["state"])
    age_h = (now - p["obs"].stat().st_mtime) / 3600.0
    open_cves = [c for c in st["cves"].values()
                 if not c.get("resolved") and not c.get("patched_by")]
    carried = [c for c in st["cves"].values() if c.get("patched_by")]
    serious = sum(c["priority"] in ("P0", "P1") for c in open_cves)
    unreported = sum(1 for c in open_cves if not c.get("reported"))
    errors = obs.get("errors") or {}

    out.update(observed=obs["date"], age_hours=round(age_h, 1),
               packages=len(obs.get("packages", [])), findings=len(obs.get("findings", [])),
               source_errors=len(errors), tracked=len(open_cves), serious=serious,
               carried=len(carried),
               unreported=unreported, findings_recorded=len(st["findings"]),
               last_report=st.get("last_report") or "never",
               last_digest=st.get("last_digest") or "never")

    # The act lane's state, so "the loop is quiet" and "the loop has work queued that
    # it is not doing" are different, visible answers.
    cfg = cfgmod.load(p["policy"])
    out["act_enabled"] = cfg["act_enabled"]
    # The batch is the unit of work and the release is the unit of risk. Status has to
    # say both even before a plan exists, or "the loop is quiet" hides the fact that a
    # twelve-change batch is what would run.
    out["max_actions"] = cfg["max_actions"]
    out["max_releases"] = cfg.get("max_releases", 1)
    out["releases_today"] = actlane.published_today(p["state_dir"],
                                                    today or dt.date.today())
    bump = _bump_lane(cfg, obs)
    out["bump_lane"] = bump["state"]
    out["bump_current"] = bump["current"]
    out["bump_finding"] = bump["finding"]
    out["bump_available"] = bump["available"]
    out["bump_executable"] = bump["executable"]
    out["problems"].extend(bump["problems"])
    if p["plan"].exists():
        try:
            plan = json.loads(p["plan"].read_text())
        except ValueError:
            plan = {}
        allowed = [a for a in plan.get("actions", []) if a.get("allowed")]
        out["plan_date"] = plan.get("date", "?")
        out["plan_age_hours"] = round((now - p["plan"].stat().st_mtime) / 3600.0, 1)
        out["actions_allowed"] = len(allowed)
        out["actions_refused"] = len(plan.get("refused", []))
        out["actions_done"] = len(st.get("actions", {}))
        out["batch_size"] = min(cfg["max_actions"], len(allowed))
        if allowed:
            out["next_action"] = allowed[0]["id"]
        if not cfg["act_enabled"] and allowed:
            out["problems"].append(f"{len(allowed)} action(s) queued but act_enabled = false")
        elif out["plan_age_hours"] > 30.0:
            out["problems"].append(f"plan.json is {out['plan_age_hours']:.0f}h old")
    # A batch that ran and published nothing must not read as a quiet, healthy night —
    # that is the same ambiguity that made two dead-looking nights indistinguishable
    # from a working loop. Checked whether or not a plan exists: the action log is the
    # record of what actually happened.
    blocked = _blocked_batches(p["actions"], today or dt.date.today())
    if blocked:
        out["ok"] = False
        out["batch_blocked"] = blocked[-1]
        out["problems"].append("last batch published nothing: "
                               + (blocked[-1].get("error") or "no reason recorded"))

    # A nightly loop that has not observed in over a day is not quiet, it is stuck.
    if age_h > 30.0:
        out["ok"] = False
        out["problems"].append(f"last observation is {age_h:.0f}h old (expected < 24h)")
    if errors:
        out["ok"] = False
        out["problems"].append(f"{len(errors)} source error(s): "
                               + ", ".join(sorted(errors)[:4]))
    if unreported:
        out["problems"].append(f"{unreported} CVE match(es) triaged but never reported")
    # "No email" is only healthy if the loop never had anything to send. A report that
    # was due and failed to deliver must not read as a quiet night.
    if st.get("last_delivery_error"):
        out["ok"] = False
        out["problems"].append("last report delivery failed: " + st["last_delivery_error"])

    print(f"status: {'OK' if out['ok'] else 'PROBLEMS'} | observed {out['observed']} "
          f"({out['age_hours']}h ago) | {out['packages']} packages, "
          f"{out['findings']} findings, {out['source_errors']} source errors | "
          f"{out['tracked']} CVE matches open ({out['serious']} P0/P1), "
          f"{out['carried']} closed by patches we carry, {out['unreported']} unreported | "
          f"last report {out['last_report']}, last digest {out['last_digest']}")
    if "plan_date" in out:
        print(f"  act lane: {'on' if out['act_enabled'] else 'OFF'} | "
              f"plan {out['plan_date']} ({out['plan_age_hours']}h ago) | "
              f"{out['actions_allowed']} action(s) allowed, {out['actions_refused']} refused, "
              f"{out['actions_done']} component(s) acted on"
              + (f" | next batch: {out['batch_size']} change(s) -> {out['max_releases']} "
                 f"release(s)/night, {out['releases_today']} published today"
                 if "batch_size" in out else "")
              + (f" | next: {out['next_action']}" if out.get("next_action") else ""))
    else:
        print(f"  act lane: {'on' if out['act_enabled'] else 'OFF'} | no plan.json yet | "
              f"batch policy: up to {out['max_actions']} change(s) -> {out['max_releases']} "
              f"release(s)/night, {out['releases_today']} published today")
    detail = (f"finding: {out['bump_finding']} (new release series)" if out["bump_finding"]
              else (f"finding: {', '.join(out['bump_available'])} (point release)"
                    if out["bump_available"] else "no newer Buildroot release found"))
    print(f"  bump lane: {out['bump_lane']} | Buildroot {out['bump_current'] or '?'} | {detail}")
    for problem in out["problems"]:
        print(f"  ! {problem}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pengyos-nightly", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"pengyos-nightly {__version__}")
    ap.add_argument("--state-dir", default=str(HOME / "build/pengyos/nightly"))
    ap.add_argument("--repo", default=str(HOME / "Desktop/Personal/pengyos"))
    ap.add_argument("--build-root", default=str(HOME / "build/pengyos"))
    ap.add_argument("--site", default=str(HOME / "build/pengyos/site"),
                    help="the local site dir release.sh publishes into")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "observe", "triage", "report", "status", "plan", "act", "web"):
        sp = sub.add_parser(name)
        if name in ("run", "triage"):
            sp.add_argument("--max-triage", type=int, default=400,
                            help="model calls per run; the rest wait for the next run")
            sp.add_argument("--jobs", type=int, default=2)
            sp.add_argument("--model", default="local")
            sp.add_argument("--no-model", action="store_true", help="rules and fallback only")
        if name in ("run", "report"):
            sp.add_argument("--no-email", action="store_true",
                            help="render the report to disk but do not send it, and do NOT "
                                 "mark anything as reported: the next run still reports it.")
            sp.add_argument("--force-email", action="store_true",
                            help="send even when nothing is new")
            sp.add_argument("--mark-reported", action="store_true",
                            help="mark this run's new items as reported even without sending "
                                 "(for when you have already read the on-disk report)")
            sp.add_argument("--to", default=None)
        if name == "plan":
            sp.add_argument("--json", action="store_true", help="also print plan.json")
        if name == "act":
            sp.add_argument("--only", default=None, help="run this action id only")
            sp.add_argument("--max", type=int, default=0,
                            help="cap this run's batch below max_actions (0 = use max_actions)")
            sp.add_argument("--apply", action="store_true",
                            help="really branch, build, sign and publish; without this the "
                                 "run is a dry run that changes nothing")
            # A refusal mails the operator (see notify_act). --no-email is the dev
            # escape hatch; --to aims it, same as `report`.
            sp.add_argument("--no-email", action="store_true",
                            help="do not send the refusal notice; the refusal is still "
                                 "recorded in actions.json and reported by `status`")
            sp.add_argument("--to", default=None)
        if name == "web":
            sp.add_argument("--host", default="127.0.0.1",
                            help="address to listen on (0.0.0.0 for the LAN)")
            sp.add_argument("--port", type=int, default=5050)
    args = ap.parse_args(argv)
    report_failed = False

    if args.cmd == "web":
        from pengyos_nightly import web          # Flask is needed for this one command only
        web.create_app(_paths(args), Path(args.repo).expanduser()).run(
            host=args.host, port=args.port)
        return 0

    if args.cmd in ("run", "observe"):
        obs = observe(args)
        print(f"observe: {len(obs['packages'])} packages, {len(obs['findings'])} findings, "
              f"{len(obs['errors'])} source errors")
    if args.cmd in ("run", "triage"):
        new = do_triage(args)
        print(f"triage: {len(new)} new CVE matches triaged")
    if args.cmd in ("run", "report"):
        r = do_report(args)
        print(f"report: {r['subject']} -> {r['report']} (sent: {r['sent']})")
        # A report that was due and did not go out is a failed night, even when the
        # rest of the loop behaved. Cron/log readers get a non-zero exit for it.
        report_failed = bool(r.get("delivery_error"))
    if args.cmd in ("run", "plan"):
        plan = do_plan(args)
        print(f"plan: {plan['counts']['actions']} action(s), {plan['counts']['refused']} "
              f"refused -> {Path(args.state_dir).expanduser() / 'plan.json'}")
        for a in plan["actions"]:
            print(f"  action {a['id']}: {a['component']} {a['shape']} "
                  f"({', '.join(a['editions'])}) closes {len(a['closes'])} CVE match(es)"
                  + (f", {len(a['skipped_known_bad'])} fix(es) skipped as known-bad"
                     if a.get("skipped_known_bad") else ""))
        for r in plan["refused"][:8]:
            print(f"  refused {r['id']}: {', '.join(r['refused_by'])}")
        if args.cmd == "plan" and getattr(args, "json", False):
            print(json.dumps(plan, indent=1, sort_keys=True))
    if args.cmd == "act":
        r = do_act(args)
        if r.get("error"):
            print(f"act: {r['error']}")
            return 1
        if r.get("nothing_allowed"):
            return 0                     # nothing to do is a healthy night
        if not r.get("published"):
            print(f"act: nothing published ({r.get('error', 'dry run')})")
            return 0 if not args.apply else 1
    if args.cmd == "status":
        # Non-zero when the loop is stuck or a source failed, so this can be used
        # by a watchdog without anyone having to read prose.
        return 0 if do_status(args)["ok"] else 1
    return 1 if report_failed else 0


if __name__ == "__main__":
    sys.exit(main())
