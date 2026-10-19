"""`pengyos_nightly web` — the nightly's recommendations on one page, approve or deny.

The email says what upstream moved; this page is where the answer goes. It reads what
the loop already wrote (observations.json, plan.json, actions.json, nightly.toml) and
writes one file, decisions.json (see decisions.py). It never builds, signs or
publishes anything: an approval is picked up by the 21:00 act run, which re-plans when
a decision is newer than the plan and then runs every guard as usual.

Flask is imported here and nowhere else, so the rest of the tool stays stdlib-only.
`view()` is the whole page as plain data, so it is tested without a server.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

from pengyos_nightly import config as cfgmod
from pengyos_nightly import decisions as D
from pengyos_nightly import guards as G
from pengyos_nightly.versions import vkey

#: When this machine's act run happens. The schedule is a crontab, so it is machine-local
#: policy and lives in nightly.toml as `act_time = "02:00"`; the default is the historical
#: 21:00. Hardcoding it here made the page tell the operator a time that was wrong on the
#: machine the page was running on, after that machine moved its act cron to 02:00
#: (2026-10-19). See ../../docs/operations.md §3.
DEFAULT_ACT_TIME = "21:00"


def act_time(cfg: dict) -> str:
    """When tonight's act run fires, as this machine configures it."""
    return str(cfg.get("act_time") or DEFAULT_ACT_TIME)


def _read(path: Path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def _mtime(path: Path) -> float:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


def _first_line(text: str, limit: int = 220) -> str:
    line = next((x.strip() for x in (text or "").splitlines() if x.strip()), "")
    return line if len(line) <= limit else line[:limit - 1] + "…"


def _plan_entry(plan: dict, component: str, lanes: tuple[str, ...]) -> dict | None:
    for a in plan.get("actions", []) + plan.get("refused", []):
        if a.get("component") == component and a.get("lane") in lanes:
            return a
    return None


def _cannot_act(component: str, pkg: dict | None) -> str:
    """Why an approval could not be carried out, or "" when it could."""
    if component in G.DENIED:
        return f"The nightly can't do this one: {G.DENIED[component]}."
    if pkg and pkg.get("tree") == "BUILDROOT":
        return ("A stock Buildroot package moves with a Buildroot release, not on its own; "
                "the nightly only bumps recipes we author.")
    return ""


def _status(entry: dict | None, plan_new: bool, decided: dict | None,
            at: str = DEFAULT_ACT_TIME) -> str:
    """One sentence on what tonight's run will do with an approved item."""
    if decided is None:
        return ""
    if not plan_new or entry is None:
        return f"Approved. The {at} run plans it and builds it if every check passes."
    if entry.get("allowed"):
        return (f"Approved and planned: the {at} run builds "
                f"{entry['current']} → {entry['target']}, boot-checks it and publishes to testing.")
    return "Approved, but the plan refused it: " + "; ".join(entry.get("refused_reason") or [])


def view(paths: dict, repo: Path, run=subprocess.run, now: dt.datetime | None = None) -> dict:
    """Everything the page shows, as data."""
    obs = _read(paths["obs"], {})
    plan = _read(paths["plan"], {})
    decided = D.load(paths["decisions"])
    cfg = cfgmod.load(paths["policy"])
    packages = obs.get("packages", {})
    plan_new = _mtime(paths["plan"]) >= _mtime(paths["decisions"])

    waiting, approved, denied = [], [], []
    seen: set[str] = set()
    for f in obs.get("findings", []):
        name, available = f["component"], f.get("available") or ""
        k = D.key("update", name)
        seen.add(k)
        entry = decided.get(k)
        verdict = D.lookup(decided, "update", name, available)
        pkg = packages.get(name)
        item = {"key": k, "kind": "update", "component": name, "lane": f.get("lane", ""),
                "current": f.get("current") or (pkg or {}).get("current", ""),
                "target": available, "note": f.get("note", ""),
                "editions": (pkg or {}).get("editions", []),
                "cannot_act": _cannot_act(name, pkg), "decision": entry if verdict else None}
        if verdict == D.APPROVED:
            item["status"] = _status(_plan_entry(plan, name, ("approved",)), plan_new, entry,
                                      at=act_time(cfg))
            approved.append(item)
        elif verdict == D.DENIED:
            denied.append(item)
        else:
            # A routine finding the loop will try anyway: say so, so "approve" is not
            # mistaken for the only way it happens.
            auto = _plan_entry(plan, name, ("routine",))
            item["auto"] = ("The nightly does this one itself tonight."
                            if auto and auto.get("allowed") else "")
            if auto and not auto.get("allowed") and not item["cannot_act"]:
                item["blocked"] = "; ".join(auto.get("refused_reason") or [])
            waiting.append(item)

    security = []
    for a in plan.get("actions", []) + plan.get("refused", []):
        if a.get("lane") != "security":
            continue
        k = D.key("security", a["component"])
        seen.add(k)
        entry = decided.get(k)
        held = D.lookup(decided, "security", a["component"], a.get("current") or "")
        item = {"key": k, "kind": "security", "component": a["component"],
                "current": a.get("current", ""), "target": a.get("current", ""),
                "closes": a.get("closes") or [], "still_open": a.get("still_open") or [],
                "editions": a.get("editions", []), "allowed": bool(a.get("allowed")),
                "reason": "; ".join(a.get("refused_reason") or []),
                "decision": entry if held else None}
        (denied if held == D.DENIED else security).append(item)

    # Decisions about things no longer recommended: done (the pin moved past it), or
    # upstream withdrew it. Kept so a stale approval is visible and can be cleared.
    finished = []
    for k, entry in sorted(decided.items()):
        if k in seen:
            continue
        pkg = packages.get(entry.get("component", ""), {})
        cur = pkg.get("current", "")
        done = bool(cur and entry.get("target") and vkey(cur) >= vkey(entry["target"]))
        finished.append({**entry, "key": k, "current": cur,
                         "status": ("done: the tree now pins " + cur) if done else
                                   "no longer recommended"})

    ok_tree, _ = G.clean_tree(repo, run=run)
    runs = []
    for r in reversed((_read(paths["actions"], {}) or {}).get("actions", [])[-6:]):
        what = r.get("component") or ", ".join(r.get("changes") or []) or "(refused before starting)"
        runs.append({"date": r.get("date", ""), "component": what,
                     "version": r.get("version", ""), "published": bool(r.get("published")),
                     "error": _first_line(r.get("error", ""))})

    return {"now": (now or dt.datetime.now()).strftime("%Y-%m-%d %H:%M"),
            "observed": obs.get("date", "never"), "planned": plan.get("date", "never"),
            "act_enabled": bool(cfg.get("act_enabled")), "clean_tree": ok_tree,
            "repo": str(repo), "act_time": act_time(cfg),
            # What can be approved first, a new series before an update; what no act path
            # can carry out last.
            "waiting": sorted(waiting, key=lambda i: (bool(i["cannot_act"]),
                                                      i["lane"] != "major", i["component"])),
            "approved": approved, "security": security, "denied": denied,
            "finished": finished, "runs": runs}


PAGE = """<!doctype html>
<html lang="en" data-bs-theme="auto">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PengyOS nightly</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
<script>
  document.documentElement.dataset.bsTheme =
    matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
</script>
<style>
  body { max-width: 980px; }
  .ver { font-family: var(--bs-font-monospace); }
  form.inline { display: inline; }
</style>
</head>
<body class="container py-4">
<h1 class="h3 mb-1">🐧 PengyOS nightly</h1>
<p class="text-body-secondary mb-3">
  Observed {{ v.observed }} · planned {{ v.planned }} · the act run is at {{ v.act_time }}
  and publishes to <b>testing</b> only.
</p>

{% if not v.act_enabled %}
<div class="alert alert-warning">The act run is switched off (<code>act_enabled = false</code>
in nightly.toml): approvals are recorded but nothing is built.</div>
{% endif %}
{% if not v.clean_tree %}
<div class="alert alert-warning">{{ v.repo }} has uncommitted changes. Tonight's run refuses
to start on a dirty tree, so nothing here gets built until it is committed.</div>
{% endif %}

<h2 class="h5 mt-4">Waiting for you <span class="badge text-bg-secondary">{{ v.waiting|length }}</span></h2>
{% if not v.waiting %}<p class="text-body-secondary">Nothing new.</p>{% endif %}
{% for i in v.waiting %}
<div class="card mb-2"><div class="card-body py-2">
  <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
    <div>
      <b>{{ i.component }}</b> <span class="ver">{{ i.current }} → {{ i.target }}</span>
      {% if i.lane == "major" %}<span class="badge text-bg-warning">new series</span>
      {% else %}<span class="badge text-bg-info">update</span>{% endif %}
      <div class="small text-body-secondary">{{ i.note }}{% if i.editions %} · {{ i.editions|join(", ") }}{% endif %}</div>
    </div>
    <div class="text-nowrap">
      <form class="inline" method="post" action="{{ url_for('decide') }}">
        <input type="hidden" name="kind" value="update"><input type="hidden" name="component" value="{{ i.component }}">
        <input type="hidden" name="target" value="{{ i.target }}">
        <button class="btn btn-sm btn-success" name="decision" value="approved"
          {% if i.cannot_act %}disabled title="{{ i.cannot_act }}"{% endif %}>Approve</button>
        <button class="btn btn-sm btn-outline-danger" name="decision" value="denied">Deny</button>
      </form>
    </div>
  </div>
  {% if i.cannot_act %}<div class="small mt-1 text-warning-emphasis">{{ i.cannot_act }}</div>{% endif %}
  {% if i.auto %}<div class="small mt-1 text-success-emphasis">{{ i.auto }}</div>{% endif %}
  {% if i.blocked %}<div class="small mt-1 text-warning-emphasis">Tonight's plan refused it: {{ i.blocked }}</div>{% endif %}
</div></div>
{% endfor %}

{% if v.approved %}
<h2 class="h5 mt-4">Approved</h2>
{% for i in v.approved %}
<div class="card mb-2 border-success"><div class="card-body py-2">
  <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
    <div><b>{{ i.component }}</b> <span class="ver">{{ i.current }} → {{ i.target }}</span>
      <div class="small">{{ i.status }}</div>
      <div class="small text-body-secondary">approved {{ i.decision.at }}</div></div>
    <form class="inline" method="post" action="{{ url_for('undo') }}">
      <input type="hidden" name="key" value="{{ i.key }}">
      <button class="btn btn-sm btn-outline-secondary">Undo</button></form>
  </div>
</div></div>
{% endfor %}
{% endif %}

<h2 class="h5 mt-4">Security fixes the nightly handles itself</h2>
{% if not v.security %}<p class="text-body-secondary">None open.</p>{% endif %}
{% for i in v.security %}
<div class="card mb-2"><div class="card-body py-2">
  <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
    <div><b>{{ i.component }}</b> <span class="ver">{{ i.current }}</span>
      {% if i.allowed %}<span class="badge text-bg-success">tonight</span>
      {% else %}<span class="badge text-bg-secondary">blocked</span>{% endif %}
      <div class="small text-body-secondary">{{ ([("closes %d CVE%s" % (i.closes|length, "" if i.closes|length == 1 else "s")) if i.closes else ""]
          + [("%d still open" % i.still_open|length) if i.still_open else ""]
          + [i.editions|join(", ")]) | select | join(" · ") }}</div>
      {% if not i.allowed %}<div class="small">{{ i.reason }}</div>{% endif %}
    </div>
    <form class="inline" method="post" action="{{ url_for('decide') }}">
      <input type="hidden" name="kind" value="security"><input type="hidden" name="component" value="{{ i.component }}">
      <input type="hidden" name="target" value="{{ i.target }}">
      <button class="btn btn-sm btn-outline-danger" name="decision" value="denied">
        {{ "Hold" if i.allowed else "Dismiss" }}</button></form>
  </div>
</div></div>
{% endfor %}

{% if v.denied or v.finished %}
<h2 class="h5 mt-4">Decided earlier</h2>
<ul class="list-group mb-3">
{% for i in v.denied %}
  <li class="list-group-item d-flex justify-content-between align-items-center">
    <span><b>{{ i.component }}</b> <span class="ver">{{ i.target }}</span> — denied {{ i.decision.at }}</span>
    <form class="inline" method="post" action="{{ url_for('undo') }}">
      <input type="hidden" name="key" value="{{ i.key }}">
      <button class="btn btn-sm btn-outline-secondary">Undo</button></form></li>
{% endfor %}
{% for i in v.finished %}
  <li class="list-group-item d-flex justify-content-between align-items-center">
    <span><b>{{ i.component }}</b> <span class="ver">{{ i.target }}</span> — {{ i.decision }}, {{ i.status }}</span>
    <form class="inline" method="post" action="{{ url_for('undo') }}">
      <input type="hidden" name="key" value="{{ i.key }}">
      <button class="btn btn-sm btn-outline-secondary">Clear</button></form></li>
{% endfor %}
</ul>
{% endif %}

{% if v.runs %}
<h2 class="h5 mt-4">Recent act runs</h2>
<ul class="list-group small mb-4">
{% for r in v.runs %}
  <li class="list-group-item">{{ r.date }} · {{ r.component }} {{ r.version }} ·
    {% if r.published %}<span class="text-success">published</span>
    {% else %}<span class="text-danger">not published</span> — {{ r.error }}{% endif %}</li>
{% endfor %}
</ul>
{% endif %}
<p class="small text-body-secondary">{{ v.now }} · decisions are kept in decisions.json next to the plan.</p>
</body>
</html>
"""


def create_app(paths: dict, repo: Path, run=subprocess.run):
    from flask import Flask, abort, redirect, render_template_string, request, url_for

    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template_string(PAGE, v=view(paths, repo, run=run))

    @app.post("/decide")
    def decide():
        kind = request.form.get("kind", "")
        component = request.form.get("component", "")
        decision = request.form.get("decision", "")
        target = request.form.get("target", "")
        if kind not in ("update", "security") or not component or not target \
                or decision not in (D.APPROVED, D.DENIED):
            abort(400)
        if kind == "security" and decision == D.APPROVED:
            abort(400)      # nothing to approve: the security lane runs on its own guards
        if decision == D.APPROVED:
            pkg = _read(paths["obs"], {}).get("packages", {}).get(component)
            if _cannot_act(component, pkg):
                abort(400)
        D.decide(paths["decisions"], kind, component, decision, target)
        return redirect(url_for("index"))

    @app.post("/undo")
    def undo():
        kind, _, component = request.form.get("key", "").partition(":")
        if not D.undo(paths["decisions"], kind, component):
            abort(404)
        return redirect(url_for("index"))

    return app
