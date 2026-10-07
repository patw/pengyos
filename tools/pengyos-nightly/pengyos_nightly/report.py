"""The report: an HTML email (and a copy on disk) of what is new tonight."""
from __future__ import annotations

import html
import subprocess
from pathlib import Path

EMAIL_SCRIPT = Path.home() / "Personal/skills/email/send_email.py"
PRI_COLOR = {"P0": "#b00020", "P1": "#c75000", "P2": "#666666"}


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


def render(date: str, new_cves: list[dict], new_findings: list[dict], errors: dict,
           all_cves: list[dict] | None = None, all_findings: list[dict] | None = None,
           digest: bool = False, carried: list[dict] | None = None,
           acted: list[dict] | None = None) -> str:
    serious = [c for c in new_cves if c["priority"] in ("P0", "P1")]
    minor = [c for c in new_cves if c["priority"] == "P2"]
    parts = [f"<h2>PengyOS nightly — {_e(date)}</h2>"]
    if errors:
        parts.append("<h3>Source errors</h3><ul>" + "".join(
            f"<li><b>{_e(k)}</b>: {_e(v)}</li>" for k, v in sorted(errors.items())) + "</ul>")
    parts.append(f"<h3>Security — new P0/P1 ({len(serious)})</h3>")
    parts.append(_by_package(serious) if serious else "<p><i>none</i></p>")
    parts.append(f"<h3>Major upgrades — need your decision</h3>")
    parts.append(_finding_list([f for f in new_findings if f["lane"] == "major"]))
    parts.append(f"<h3>Routine updates</h3>")
    parts.append(_finding_list([f for f in new_findings if f["lane"] == "routine"]))
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
            all_findings: list[dict] | None = None) -> str:
    p0 = sum(c["priority"] == "P0" for c in new_cves)
    p1 = sum(c["priority"] == "P1" for c in new_cves)
    major = sum(f["lane"] == "major" for f in new_findings)
    routine = sum(f["lane"] == "routine" for f in new_findings)
    bits = []
    if p0:
        bits.append(f"{p0} P0")
    if p1:
        bits.append(f"{p1} P1")
    if major:
        bits.append(f"{major} major")
    if routine:
        bits.append(f"{routine} routine")
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


def send(subject_line: str, html_body: str, body_file: Path, to: str | None = None,
         script: Path = EMAIL_SCRIPT, run=subprocess.run) -> None:
    body_file.write_text(html_body)
    argv = ["python3", str(script), "--quiet", "--html", "--subject", subject_line,
            "--body-file", str(body_file)]
    if to:
        argv += ["--to", to]
    run(argv, check=True, timeout=120)
