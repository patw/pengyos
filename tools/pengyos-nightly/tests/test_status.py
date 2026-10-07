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
        cli.do_report(args, send=lambda *a, **k: None, today=dt.date(2026, 10, 7))
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
    r = cli.do_report(args, send=lambda *a, **k: None, today=dt.date(2026, 10, 12))
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
