"""The report: an HTML email (and a copy on disk) of what is new tonight."""
from __future__ import annotations

import html
import os
import subprocess
from pathlib import Path

from pengyos_nightly import config as cfgmod

#: The mailer is machine-local and deliberately not part of this repository.
#: $PENGYOS_EMAIL_SCRIPT names it; the default is the conventional per-user
#: launcher. A machine with neither stays report-only - the HTML report is still
#: written to disk, and the queue is not marked as reported.
DEFAULT_EMAIL_SCRIPT = Path.home() / ".local/bin/pengyos-email"
PRI_COLOR = {"P0": "#b00020", "P1": "#c75000", "P2": "#666666"}


def email_script() -> Path | None:
    """The mailer configured on this machine, or None when there is none."""
    env = os.environ.get("PENGYOS_EMAIL_SCRIPT", "").strip()
    path = Path(env).expanduser() if env else DEFAULT_EMAIL_SCRIPT
    return path if path.is_file() else None


def mailer_argv(script: Path) -> list[str]:
    """How to invoke the mailer: whatever its own shebang says, python3 only as a fallback.

    The mailer is machine-local, and on a real host it is usually a shell wrapper around
    somebody's actual mail setup (`~/.local/bin/pengyos-email` is a bash `exec` of the
    email skill). Invoking it as `python3 <mailer>` - the leftover from when the mailer
    *was* a .py file - turned that wrapper into a SyntaxError and took the whole nightly
    run down with it. The mailer decides how it runs.
    """
    if os.access(script, os.X_OK):
        return [str(script)]                      # the kernel reads the shebang for us
    try:
        line = script.read_text(errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        line = ""
    interp = line[2:].split() if line.startswith("#!") else ["python3"]
    return interp + [str(script)]


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _cve_rows(items: list[dict]) -> str:
    rows = []
    for c in items:
        kev = " · <b>CISA KEV</b>" if c.get("kev") else ""
        epss = f" · EPSS {c['epss']:.1%}" if c.get("epss") is not None else ""
        cvss = f"CVSS {c['cvss']}" if c.get("cvss") is not None else "CVSS ?"
        rows.append(
            f'<tr><td style="padding:4px 8px;vertical-align:top;color:{PRI_COLOR[c["priority"]]}">'
            f'<b>{_e(c["priority"])}</b></td>'
            f'<td style="padding:4px 8px;vertical-align:top">'
            f'<a href="https://nvd.nist.gov/vuln/detail/{_e(c["cve"])}">{_e(c["cve"])}</a> — '
            f'<b>{_e(c["package"])}</b> {_e(c.get("version", ""))} '
            f'({_e(", ".join(c.get("editions", [])))})<br>'
            f'<small>{cvss}{kev}{epss} · reachable: {_e(c.get("reachable", "?"))} · '
            f'{_e(c.get("source", ""))}</small><br>{_e(c.get("rationale", ""))}</td></tr>')
    return '<table style="border-collapse:collapse">' + "".join(rows) + "</table>"


def _by_package(items: list[dict]) -> str:
    """Serious CVEs grouped by package: the decision is "move ffmpeg", not
    thirty separate CVEs. Worst package first."""
    groups: dict[str, list[dict]] = {}
    for c in items:
        groups.setdefault(c["package"], []).append(c)
    order = {"P0": 0, "P1": 1, "P2": 2}

    def rank(kv):
        cs = kv[1]
        return (min(order[c["priority"]] for c in cs), -len(cs),
                -max((c.get("cvss") or 0) for c in cs))
    out = []
    for name, cs in sorted(groups.items(), key=rank):
        counts = ", ".join(f"{sum(c['priority'] == p for c in cs)} {p}"
                           for p in ("P0", "P1") if any(c["priority"] == p for c in cs))
        top = max((c.get("cvss") or 0) for c in cs)
        kev = " · <b>CISA KEV</b>" if any(c.get("kev") for c in cs) else ""
        reach = ", ".join(sorted({c.get("reachable", "?") for c in cs}))
        editions = ", ".join(sorted({e for c in cs for e in c.get("editions", [])}))
        out.append(f'<h4 style="margin:12px 0 2px">{_e(name)} {_e(cs[0].get("version", ""))} — '
                   f'{counts} (max CVSS {top}){kev}</h4>'
                   f'<small>{_e(editions)} · reachable: {_e(reach)}</small>')
        out.append(_cve_rows(sorted(cs, key=lambda c: (order[c["priority"]], -(c.get("cvss") or 0)))))
    return "".join(out)


def _finding_list(items: list[dict]) -> str:
    li = []
    for f in items:
        arrow = f' → <b>{_e(f["available"])}</b>' if f.get("available") else ""
        li.append(f"<li><b>{_e(f['component'])}</b> {_e(f.get('current', ''))}{arrow}"
                  f" — {_e(f.get('note', ''))}</li>")
    return "<ul>" + "".join(li) + "</ul>" if li else "<p><i>none</i></p>"


SEV_COLOR = {"CRITICAL": "#b00020", "HIGH": "#c75000"}


def _bundled(items: list[dict]) -> str:
    """Critical and high advisories one per line; the rest counted per package, since
    a moderate in one of COSMIC's 2,000 crates is not a decision on its own."""
    serious = [b for b in items if b["severity"] in ("CRITICAL", "HIGH")]
    rest = [b for b in items if b["severity"] not in ("CRITICAL", "HIGH")]
    rows = []
    for b in serious:
        fixed = f" · fixed in {_e(b['fixed'])}" if b.get("fixed") else ""
        alias = f" ({_e(', '.join(b['aliases'][:3]))})" if b.get("aliases") else ""
        rows.append(
            f'<tr><td style="padding:4px 8px;vertical-align:top;color:{SEV_COLOR[b["severity"]]}">'
            f'<b>{_e(b["severity"])}</b></td><td style="padding:4px 8px;vertical-align:top">'
            f'<a href="https://osv.dev/vulnerability/{_e(b["id"])}">{_e(b["id"])}</a>{alias} — '
            f'<b>{_e(b["name"])}</b> {_e(b["version"])} in {_e(", ".join(b["in"]))}{fixed}<br>'
            f'<small>{_e(", ".join(b["editions"]))}</small><br>{_e(b.get("summary", ""))}</td></tr>')
    out = ('<table style="border-collapse:collapse">' + "".join(rows) + "</table>") if rows else ""
    if rest:
        per: dict[str, dict[str, int]] = {}
        for b in rest:
            for pkg in b["in"]:
                per.setdefault(pkg, {}).setdefault(b["severity"].lower(), 0)
                per[pkg][b["severity"].lower()] += 1
        out += ("<p><small>Also: " + "; ".join(
            f"{_e(pkg)} " + ", ".join(f"{n} {s}" for s, n in sorted(c.items()))
            for pkg, c in sorted(per.items())) + "</small></p>")
    return out or "<p><i>none</i></p>"


def render(date: str, new_cves: list[dict], new_findings: list[dict], errors: dict,
           all_cves: list[dict] | None = None, all_findings: list[dict] | None = None,
           digest: bool = False, carried: list[dict] | None = None,
           acted: list[dict] | None = None, web_url: str = "",
           act_at: str = cfgmod.DEFAULTS["act_time"], gate: dict | None = None,
           bundled_new: list[dict] | None = None,
           bundled_all: list[dict] | None = None) -> str:
    serious = [c for c in new_cves if c["priority"] in ("P0", "P1")]
    minor = [c for c in new_cves if c["priority"] == "P2"]
    parts = [f"<h2>PengyOS nightly — {_e(date)}</h2>"]
    if web_url:
        # The email tells; the page is where the answer goes (pengyos_nightly web).
        parts.append(f'<p><a href="{_e(web_url)}"><b>Approve or deny</b></a> what this '
                     f"report recommends. An approval is built by the {_e(act_at)} run "
                     "and published to testing.</p>")
    if gate and not gate.get("ok", True):
        # First, above the CVEs: a red gate means the next release cannot be cut
        # (release.sh --publish runs the same gate) until it is fixed.
        parts.append("<h3>Repo gate is red</h3><p>" + _e(gate.get("summary", ""))
                     + ". release.sh will not publish until it is green.</p><ul>" + "".join(
                         f"<li>{_e(f)}</li>" for f in gate.get("failed", [])) + "</ul>")
    if errors:
        parts.append("<h3>Source errors</h3><ul>" + "".join(
            f"<li><b>{_e(k)}</b>: {_e(v)}</li>" for k, v in sorted(errors.items())) + "</ul>")
    parts.append(f"<h3>Security — new P0/P1 ({len(serious)})</h3>")
    parts.append(_by_package(serious) if serious else "<p><i>none</i></p>")
    parts.append(f"<h3>Major upgrades — need your decision</h3>")
    parts.append(_finding_list([f for f in new_findings if f["lane"] == "major"]))
    parts.append(f"<h3>Routine updates</h3>")
    parts.append(_finding_list([f for f in new_findings if f["lane"] == "routine"]))
    if bundled_new:
        parts.append(f"<h3>Bundled crates and Go modules — new advisories ({len(bundled_new)})</h3>")
        parts.append(_bundled(bundled_new))
    if minor:
        parts.append(f"<h3>New P2 (not reachable / low; {len(minor)})</h3>")
        parts.append(_cve_rows(minor))
    if all_cves is not None and all_findings is not None:
        open_serious = [c for c in all_cves if c["priority"] in ("P0", "P1")]
        parts.append("<hr><h3>" + ("Weekly digest — " if digest else "")
                     + "everything still open</h3>")
        parts.append(f"<p>{len(all_cves)} CVE matches tracked; {len(open_serious)} P0/P1.</p>")
        parts.append(_by_package(open_serious) if open_serious else "")
        parts.append(_finding_list(all_findings))
    if bundled_all is not None:
        parts.append(f"<h3>Bundled crates and Go modules — open advisories ({len(bundled_all)})</h3>")
        parts.append(_bundled(bundled_all))
    if carried:
        # pkg-stats still flags these, because a patch shape does not move the version.
        # Saying so out loud is the difference between a report that informs and one that
        # cries wolf about issues the tree already carries fixes for.
        parts.append(f"<h3>Closed by patches we carry ({len(carried)})</h3>")
        parts.append(_cve_rows(carried))
        parts.append("<p><small>pkg-stats still flags these: the pinned version did not "
                     "move, the fix is carried as a patch.</small></p>")
    if acted:
        parts.append(f"<h3>Published by the loop ({len(acted)})</h3><ul>"
                     + "".join(
                         f"<li><b>{_e(a.get('component', ''))}</b> {_e(a.get('current', ''))} "
                         f"({_e(a.get('shape', ''))}) &rarr; <b>{_e(a.get('version', ''))}</b> "
                         f"on {_e(a.get('channel', ''))}, {len(a.get('cves') or [])} CVE "
                         f"match(es), {_e(a.get('date', ''))}</li>" for a in acted) + "</ul>")
    parts.append("<p><small>pengyos-nightly: observe, triage, report, and act on the "
                 "testing channel. Stable is a human decision and this tool has no path "
                 "to it. Priorities from the local model unless marked rule/fallback."
                 "</small></p>")
    return "<html><body style=\"font-family:sans-serif\">" + "\n".join(parts) + "</body></html>"


def subject(date: str, new_cves: list[dict], new_findings: list[dict], errors: dict,
            digest: bool, open_cves: list[dict] | None = None,
            all_findings: list[dict] | None = None, gate_red: bool = False,
            scan_failed: bool = False, bundled_serious: int = 0) -> str:
    p0 = sum(c["priority"] == "P0" for c in new_cves)
    p1 = sum(c["priority"] == "P1" for c in new_cves)
    major = sum(f["lane"] == "major" for f in new_findings)
    routine = sum(f["lane"] == "routine" for f in new_findings)
    # A failed CVE scan leads: "0 new P0/P1" on a night nothing was scanned is a
    # false all-clear, and it read as one on 11-10 and 11-11.
    bits = ["CVE scan failed"] if scan_failed else []
    if gate_red:
        bits.append("gate red")
    if p0:
        bits.append(f"{p0} P0")
    if p1:
        bits.append(f"{p1} P1")
    if major:
        bits.append(f"{major} major")
    if routine:
        bits.append(f"{routine} routine")
    if bundled_serious:
        bits.append(f"{bundled_serious} bundled high")
    if errors:
        bits.append(f"{len(errors)} source errors")
    head = "PengyOS weekly digest" if digest else "PengyOS nightly"
    # A digest that says "nothing new" while carrying 67 open P1 CVEs teaches the
    # reader to ignore it. On a digest, "nothing NEW" is not the headline - the
    # standing load is.
    if open_cves is not None:
        open_serious = sum(c["priority"] in ("P0", "P1") for c in open_cves)
        bits.append(f"{open_serious} P0/P1 still open of {len(open_cves)} tracked")
        if all_findings:
            bits.append(f"{len(all_findings)} upgrades tracked")
    return f"{'[P0] ' if p0 else ''}{head} {date}: " + (", ".join(bits) or "nothing new")


# --- the act lane's refusal notice --------------------------------------------------
# "Nothing to do" is a healthy night and stays silent. "Refused" is not, and it used to
# be silent too: the act lane's refusals landed in the cron log, in actions.json and in
# `status`, and reaching them meant asking. On 2026-10-18 a 6-change batch closing 11 CVE
# matches was refused at clean_tree by a PengyTasker job holding the shared checkout
# dirty, and the operator's question the next morning was "did the act loop run?".

#: Refusals that mean "tonight's work is blocked", the ones worth a mail.
#: `nothing_allowed` is deliberately absent: most nights the correct answer is that
#: there is nothing to do, and mailing it nightly would train everyone to ignore this.
ACT_NOTIFY_STEPS = ("guard", "clean_tree", "key_present", "site_remote", "kill switch",
                    "no plan", "build", "boot", "sign", "publish", "gate", "pre-flight",
                    "compose-defconfigs", "VERSION", "edits", "branch", "merge")


def act_failure_step(result: dict) -> str:
    """The step that refused the batch, from `error` ("clean_tree: the tree ...").

    A bail writes `f"{name}: {detail}"`, and a guard's own detail repeats its name
    ("clean_tree: clean_tree: the working tree has ..."), so the text before the first
    colon is the step. Multi-word names ("kill switch") stay whole.
    """
    err = (result.get("error") or "").strip()
    if not err:
        for j in reversed(result.get("journal") or []):
            if not j.get("ok"):
                return str(j.get("step", ""))
        return ""
    return err.split(":", 1)[0].strip()


def act_subject(date: str, result: dict) -> str:
    step = act_failure_step(result) or "refused"
    n = len(result.get("planned") or [])
    cves = len(result.get("cves") or [])
    return (f"[act lane] {step}: {n} change(s) published nothing"
            + (f", {cves} CVE match(es) left open" if cves else "")
            + f" ({date})")


def render_act_failure(date: str, result: dict, web_url: str = "") -> str:
    """The mail an act refusal sends: what was planned, what stopped it, what it cost."""
    step = act_failure_step(result) or "refused"
    planned = result.get("planned") or []
    editions = result.get("editions") or []
    cves = result.get("cves") or []
    parts = [f"<h2>PengyOS act lane refused tonight — {_e(date)}</h2>",
             f'<p style="color:#b00020"><b>{_e(step)}</b> — {_e(result.get("error", ""))}</p>']
    if web_url:
        parts.append(f'<p><a href="{_e(web_url)}"><b>Open the decision page</b></a> '
                     "to see the batch and approve or deny what is waiting.</p>")
    parts.append(f"<h3>What was planned ({len(planned)} change(s))</h3>")
    parts.append("<ul>" + ("".join(f"<li>{_e(a)}</li>" for a in planned)
                           or "<li><i>nothing</i></li>") + "</ul>")
    if editions:
        parts.append(f"<p>Editions the image would have covered: <b>{_e(', '.join(editions))}</b></p>")
    parts.append(f"<h3>What it cost</h3>")
    parts.append(f"<p><b>{len(cves)}</b> CVE match(es) the batch would have closed are "
                 "still open on the testing channel. Nothing was built, signed or "
                 "published; the tree was left exactly as the run found it.</p>")
    dropped = result.get("dropped") or []
    if dropped:
        # Batch-level drops are action ids; a per-patch drop carries a dict. Render both.
        def _drop(d):
            if isinstance(d, dict):
                return (f"<li><b>{_e(d.get('package') or d.get('patch') or d.get('id') or '?')}</b>: "
                        f"{_e(d.get('why') or d.get('reason') or '')}</li>")
            return f"<li><b>{_e(d)}</b></li>"
        parts.append(f"<h3>Dropped from the batch ({len(dropped)})</h3><ul>"
                     + "".join(_drop(d) for d in dropped) + "</ul>")
    journal = result.get("journal") or []
    if journal:
        parts.append(f"<h3>The run's steps</h3><ul>" + "".join(
            f'<li>{"&#10003;" if j.get("ok") else "<b>&#10007;</b>"} {_e(j.get("step", ""))}'
            f'{" — " + _e(j.get("detail")) if j.get("detail") else ""}</li>' for j in journal)
            + "</ul>")
    parts.append("<p><small>The act lane publishes to <b>testing</b> only. Stable is a "
                 "human decision and this tool has no path to it. A refusal is the "
                 "guard doing its job: it refuses to act on a tree it does not own.</small></p>")
    return "<html><body style=\"font-family:sans-serif\">" + "\n".join(parts) + "</body></html>"


def send(subject_line: str, html_body: str, body_file: Path, to: str | None = None,
         script: Path | None = None, run=subprocess.run) -> bool:
    """Deliver the report; True only when it was actually sent.

    ``script`` None means "whatever this machine configures". With no mailer
    configured nothing goes out on the wire and the caller must not treat the
    queue as reported - a report nobody received has not been reported.
    """
    script = Path(script) if script is not None else email_script()
    if script is None:
        return False
    body_file.write_text(html_body)
    argv = mailer_argv(script) + ["--quiet", "--html", "--subject", subject_line,
                                  "--body-file", str(body_file)]
    if to:
        argv += ["--to", to]
    # Captured, not inherited: a failed mailer's own error line is what makes the cron
    # log and `status` say *why* nothing arrived, instead of a bare traceback.
    run(argv, check=True, timeout=120, capture_output=True, text=True)
    return True
