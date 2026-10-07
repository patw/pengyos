"""Tests for `pengyos-nightly status`.

The loop is silent by design when nothing is new. That is the right default, but it
is also what made two nights of "no email" indistinguishable from a broken loop.
`status` is the inspectable half of that choice, so its failure modes matter more
than its success path: a stale loop, a source error, and a triaged-but-never-reported
backlog must all be visible and must make the command exit non-zero.
"""

import argparse
import datetime as dt
import json
import time

from pengyos_nightly import cli

import test_triage_report as flow

STATE = "st"          # flow._args uses <tmp>/st as the state dir


def _dir(tmp_path):
    return tmp_path / STATE


def _seed(tmp_path, priority="P1", drain=True, errors=None):
    """Run the real observe+triage pipeline once, then optionally tweak the result.

    drain=True also runs the report (with a fake send), which is what a healthy
    night looks like: the queue is consumed because someone was actually told.
    """
    flow._fake_repo(tmp_path)
    get, run_cmd, run_pkgstats = flow._fakes(tmp_path)
    args = flow._args(tmp_path)
    cli.observe(args, get=get, run_cmd=run_cmd, run_pkgstats=run_pkgstats)
    cli.do_triage(args, complete=flow.model(
        f'{{"priority":"{priority}","reachable":"local","rationale":"x"}}'))
    if drain:
        cli.do_report(args, send=lambda *a, **k: True, today=dt.date(2026, 10, 7))
    if errors is not None:
        obs = json.loads((_dir(tmp_path) / "observations.json").read_text())
        obs["errors"] = errors
        (_dir(tmp_path) / "observations.json").write_text(json.dumps(obs))
    if not drain:
        st = cli.state.load(_dir(tmp_path) / "state.json")
        for c in st["cves"].values():
            c.pop("reported", None)
        cli.state.save(_dir(tmp_path) / "state.json", st)
    return args


def test_status_reports_a_healthy_loop(tmp_path, capsys):
    args = _seed(tmp_path)
    result = cli.do_status(args, now=time.time())
    out = capsys.readouterr().out
    assert result["ok"] is True and result["problems"] == [], out
    assert out.startswith("status: OK")
    assert result["packages"] and result["tracked"] and result["serious"]
    assert result["age_hours"] < 1.0
    assert result["unreported"] == 0


def test_a_carried_fix_is_counted_as_closed_by_every_command(tmp_path, capsys):
    """A patch shape leaves the pinned version where it was, so pkg-stats keeps
    reporting the same CVE tomorrow. Every reader of state.json has to treat "we carry
    the fix" as closed, or the loop cries wolf about issues it already shipped fixes
    for - in the status line, in the report subject, and in the next night's plan."""
    args = _seed(tmp_path)
    st = cli.state.load(_dir(tmp_path) / "state.json")
    key = next(k for k, v in st["cves"].items() if v["priority"] in ("P0", "P1"))
    st["cves"][key]["patched_by"] = {"date": "2026-10-05", "version": st["cves"][key]["version"],
                                     "release": "26.10.8",
                                     "patches": ["buildroot-external/patches/x/0001.patch"]}
    cli.state.save(_dir(tmp_path) / "state.json", st)
    before = cli.do_status(args, now=time.time())

    carried = before["carried"]
    assert carried == 1
    assert before["tracked"] + carried == sum(
        1 for c in st["cves"].values() if not c.get("resolved")), "open + carried = tracked"
    assert "closed by patches we carry" in capsys.readouterr().out

    # The report must not count it among what is still open. The standing state only
    # travels with a digest or a forced send, so force one here.
    args.force_email = True
    r = cli.do_report(args, send=lambda *a, **k: True, today=dt.date(2026, 10, 12))
    body = (_dir(tmp_path) / "reports" / "2026-10-12.html").read_text()
    assert "Closed by patches we carry (1)" in body
    assert f"still open of {before['tracked']} tracked" in r["subject"], r["subject"]
    cve = key.split("|")[0]
    open_part = body.split("everything still open")[1].split("Closed by patches")[0]
    assert cve not in open_part, "a carried CVE is still listed as open"
    assert cve in body.split("Closed by patches")[1], "and not listed as carried"

    # And triage must not spend a model call re-deciding it.
    new = cli.do_triage(args, complete=flow.model(
        '{"priority":"P1","reachable":"local","rationale":"x"}'))
    assert key not in [f"{c['cve']}|{c['package']}" for c in new], \
        "a carried CVE was re-triaged"


def test_status_exits_nonzero_when_the_loop_is_stale(tmp_path):
    """A nightly loop that has not observed in over a day is stuck, not quiet."""
    import os
    args = _seed(tmp_path)
    old = time.time() - 3 * 24 * 3600
    os.utime(_dir(tmp_path) / "observations.json", (old, old))     # 3 days stale

    result = cli.do_status(args, now=time.time())
    assert result["ok"] is False
    assert any("old" in p and "24h" in p for p in result["problems"])
    assert result["age_hours"] > 70

    # And through the CLI, so a watchdog can rely on the exit code alone.
    assert cli.main(["--state-dir", str(_dir(tmp_path)), "status"]) == 1


def test_status_flags_source_errors(tmp_path):
    args = _seed(tmp_path, errors={"pkgstats:linux": "HTTP 503"})
    result = cli.do_status(args, now=time.time())
    assert result["ok"] is False
    assert result["source_errors"] == 1
    assert any("source error" in p and "pkgstats:linux" in p for p in result["problems"])


def test_status_flags_a_triaged_but_never_reported_backlog(tmp_path):
    """The exact signature of the 2026-10-03 incident: CVEs triaged, marked as if
    reported, nobody told. status must not read that as healthy."""
    args = _seed(tmp_path, drain=False)
    result = cli.do_status(args, now=time.time())
    assert result["unreported"] == result["tracked"] and result["unreported"] > 0
    assert any("never reported" in p for p in result["problems"])
    assert result["last_report"] == "never"


def test_status_before_the_loop_has_ever_run(tmp_path, capsys):
    args = flow._args(tmp_path)
    result = cli.do_status(args, now=time.time())
    assert result["ok"] is False
    assert "never observed" in result["problems"]
    assert "NEVER RUN" in capsys.readouterr().out


def test_status_is_read_only(tmp_path):
    """status must not mutate the queue - it is a probe, not a run."""
    args = _seed(tmp_path)
    before = (_dir(tmp_path) / "state.json").read_text()
    cli.do_status(args, now=time.time())
    assert (_dir(tmp_path) / "state.json").read_text() == before


def test_status_names_the_last_report_and_when_the_next_digest_is(tmp_path):
    args = _seed(tmp_path)
    result = cli.do_status(args, now=time.time())
    assert result["last_report"] == "2026-10-07"
    # Thursday drain, so no digest has gone out.
    assert result["last_digest"] == "never"


# --- the Buildroot release-bump lane (spec phase 4) --------------------------

def _buildroot_finding(tmp_path, available="2026.11", current="2026.08", lane="major"):
    """What the observer writes when a newer Buildroot release appears: `major` for a
    new release series, `routine` for a point release inside our own series."""
    obs = json.loads((_dir(tmp_path) / "observations.json").read_text())
    obs["buildroot"] = current
    obs["findings"] = [f for f in obs["findings"] if f.get("component") != "buildroot"]
    obs["findings"].append({"lane": lane, "component": "buildroot", "current": current,
                            "available": available,
                            "note": f"new Buildroot release series {available}"
                            if lane == "major" else f"Buildroot {current} point release"})
    (_dir(tmp_path) / "observations.json").write_text(json.dumps(obs))


def _policy(tmp_path, target):
    (_dir(tmp_path) / "nightly.toml").write_text(f'bump_buildroot = "{target}"\n')


def test_the_bump_lane_is_inert_and_says_so(tmp_path, capsys):
    """The default. A newer series is not a candidate and nothing moves - but status has
    to say the lane exists and is off, or 'inert' is indistinguishable from 'missing'."""
    args = _seed(tmp_path)
    result = cli.do_status(args, now=time.time())
    assert result["bump_lane"] == "inert", result
    assert result["bump_finding"] == "" and result["bump_executable"] is False
    assert result["problems"] == []
    assert "bump lane: inert" in capsys.readouterr().out


def test_a_newer_buildroot_series_is_reported_as_an_unscheduled_decision(tmp_path, capsys):
    """The finding is real and the lane is inert: status must name the decision that is
    waiting on the operator, and how to make it, without calling it a failure."""
    args = _seed(tmp_path)
    _buildroot_finding(tmp_path)
    result = cli.do_status(args, now=time.time())
    assert result["bump_lane"] == "inert" and result["bump_finding"] == "2026.11", result
    flagged = [p for p in result["problems"] if "Buildroot 2026.11" in p]
    assert len(flagged) == 1 and "bump_buildroot" in flagged[0], result["problems"]
    assert result["ok"] is True, "an open human decision is attention, not a broken loop"
    out = capsys.readouterr().out
    assert "finding: 2026.11 (new release series)" in out and "! Buildroot 2026.11" in out, out


def test_a_point_release_in_our_own_series_is_not_reported_as_a_tree_move(tmp_path, capsys):
    """The observer calls 2026.08.1 routine and 2026.11 major. Only the latter is the
    tree-move decision; crying tree-move about a point release trains the operator to
    ignore the line that matters."""
    args = _seed(tmp_path)
    _buildroot_finding(tmp_path, available="2026.08.1", lane="routine")
    result = cli.do_status(args, now=time.time())
    assert result["bump_finding"] == "", "a point release is not a new release series"
    assert result["bump_available"] == ["2026.08.1"]
    assert result["problems"] == [], result["problems"]
    assert "finding: 2026.08.1 (point release)" in capsys.readouterr().out


def test_a_scheduled_bump_names_the_target_and_that_it_cannot_run_yet(tmp_path, capsys):
    args = _seed(tmp_path)
    _buildroot_finding(tmp_path)
    _policy(tmp_path, "2026.11")
    result = cli.do_status(args, now=time.time())
    assert result["bump_lane"] == "scheduled -> 2026.11 (not executable yet)", result
    assert result["problems"] == [], "scheduled and matching the finding: nothing to flag"
    assert "bump lane: scheduled -> 2026.11 (not executable yet)" in capsys.readouterr().out


def test_a_scheduled_point_release_is_not_flagged(tmp_path):
    """The schedule is validated against every Buildroot finding, not only the major one:
    bump_buildroot = "2026.08.1" is a legitimate thing for the operator to schedule."""
    args = _seed(tmp_path)
    _buildroot_finding(tmp_path, available="2026.08.1", lane="routine")
    _policy(tmp_path, "2026.08.1")
    result = cli.do_status(args, now=time.time())
    assert result["problems"] == [], result["problems"]


def test_a_scheduled_bump_that_is_not_what_the_observer_found_is_named(tmp_path):
    args = _seed(tmp_path)
    _buildroot_finding(tmp_path, available="2027.02")
    _policy(tmp_path, "2026.11")
    result = cli.do_status(args, now=time.time())
    assert any('bump_buildroot = "2026.11" is not what the observer found' in p and "2027.02" in p
               for p in result["problems"]), result["problems"]


def test_a_scheduled_bump_with_no_finding_behind_it_is_named(tmp_path):
    """A schedule that names a line upstream has not released (or one older than ours)
    would move the tree for no reason; the plan would refuse it, status says why first."""
    args = _seed(tmp_path)
    _policy(tmp_path, "2027.02")
    result = cli.do_status(args, now=time.time())
    assert any("no newer Buildroot release at all" in p for p in result["problems"]), result["problems"]
