"""decisions.json and the web page that writes it.

The page is a thin form over `decisions.decide`; what matters is that an answer lands
where the planner reads it, that it cannot approve what no act path can carry out,
and that act re-plans when a decision is newer than the plan.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
from pathlib import Path

import pytest

from pengyos_nightly import cli, decisions as D, guards as G

flask = pytest.importorskip("flask")
from pengyos_nightly import web  # noqa: E402  (needs flask)


# --- decisions.py --------------------------------------------------------------

@pytest.mark.parametrize("decided,target,covered", [
    ("1.12.2", "1.12.2", True),
    ("1.12.2", "1.12.3", True),     # a later patch release of the approved series
    ("1.12.2", "1.13.0", False),    # a new series is a new question
    ("1.12.2", "1.12.1", False),    # older than what was approved
    ("615.78.08", "615.78.10", True),
])
def test_what_a_decision_covers(decided, target, covered):
    assert D.covers({"target": decided}, target) is covered


def test_deciding_again_replaces_and_undo_removes(tmp_path):
    p = tmp_path / "decisions.json"
    D.decide(p, "update", "pengy", "approved", "1.12.2")
    D.decide(p, "update", "pengy", "denied", "1.12.2")
    assert D.lookup(D.load(p), "update", "pengy", "1.12.2") == "denied"
    assert D.undo(p, "update", "pengy") and D.load(p) == {}
    assert not D.undo(p, "update", "pengy")


def test_a_missing_or_broken_file_is_no_decisions(tmp_path):
    assert D.load(tmp_path / "nope.json") == {}
    (tmp_path / "bad.json").write_text("{not json")
    assert D.load(tmp_path / "bad.json") == {}


def test_only_approve_and_deny_are_decisions(tmp_path):
    with pytest.raises(ValueError):
        D.decide(tmp_path / "d.json", "update", "pengy", "maybe", "1.12.2")


# --- the guards an approval lifts, and the ones it does not --------------------

def test_an_approval_lifts_only_the_lane_and_the_series_guard():
    a = {"lane": "approved", "shape": "recipe_bump", "bump_kind": "major",
         "current": "1.11.5", "target": "1.12.2"}
    assert G.lane_allowed(a, {"lanes": ["security"]})[0]
    assert G.major_never(a)[0]
    assert not G.major_never({**a, "lane": "major"})[0]
    assert not G.major_never({**a, "bump_kind": "none"})[0], "nothing newer is still nothing"
    assert not G.stock_version_immovable({**a, "shape": "patch_dir"})[0]
    assert not G.denied_component("linux")[0]


def test_pengy_is_no_longer_report_only():
    assert "pengy" not in G.DENIED


# --- the page ---------------------------------------------------------------------

def _state(tmp_path, plan=None):
    sd = tmp_path / "state"
    sd.mkdir()
    obs = {"date": "2026-10-15", "buildroot": "2026.08",
           "packages": {
               "pengy": {"current": "1.11.5", "tree": "PENGYOS", "editions": ["pengyos-core"]},
               "linux": {"current": "7.2.5", "tree": "BUILDROOT", "editions": ["pengyos-core"]},
               "libqalculate": {"current": "5.12.0", "tree": "PENGYOS",
                                "editions": ["pengyos-desktop"]}},
           "findings": [
               {"lane": "major", "component": "pengy", "current": "1.11.5",
                "available": "1.12.2", "note": "new PengyR minor"},
               {"lane": "routine", "component": "linux", "current": "7.2.5",
                "available": "7.2.11", "note": "Linux 7.2 stable update"},
               {"lane": "major", "component": "libqalculate", "current": "5.12.0",
                "available": "5.13.2", "note": "authored recipe"}]}
    (sd / "observations.json").write_text(json.dumps(obs))
    (sd / "plan.json").write_text(json.dumps(plan or {"date": "2026-10-15", "actions": [],
                                                      "refused": []}))
    (sd / "nightly.toml").write_text("act_enabled = true\n")
    args = argparse.Namespace(state_dir=str(sd), site=str(tmp_path / "site"))
    return cli._paths(args)


def _clean(argv, **kw):
    return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


def _client(paths, tmp_path):
    app = web.create_app(paths, tmp_path, run=_clean)
    app.testing = True
    return app.test_client()


def test_the_page_lists_what_waits_and_says_what_cannot_be_approved(tmp_path):
    paths = _state(tmp_path)
    v = web.view(paths, tmp_path, run=_clean)
    names = [i["component"] for i in v["waiting"]]
    assert names == ["libqalculate", "pengy", "linux"], \
        "approvable first, a new series before an update, what cannot be approved last"
    linux = next(i for i in v["waiting"] if i["component"] == "linux")
    assert "can't" in linux["cannot_act"]
    assert not next(i for i in v["waiting"] if i["component"] == "pengy")["cannot_act"]
    page = _client(paths, tmp_path).get("/")
    assert page.status_code == 200 and b"pengy" in page.data and b"Approve" in page.data


def test_approving_writes_the_decision_the_planner_reads(tmp_path):
    paths = _state(tmp_path)
    r = _client(paths, tmp_path).post("/decide", data={
        "kind": "update", "component": "pengy", "target": "1.12.2", "decision": "approved"})
    assert r.status_code == 302
    assert D.lookup(D.load(paths["decisions"]), "update", "pengy", "1.12.2") == "approved"
    v = web.view(paths, tmp_path, run=_clean)
    assert [i["component"] for i in v["approved"]] == ["pengy"]
    assert "pengy" not in [i["component"] for i in v["waiting"]]
    assert "21:00" in v["approved"][0]["status"]


def test_the_page_states_the_act_time_this_machine_actually_uses(tmp_path):
    """The act schedule is a crontab, so it is machine-local policy. The page used to
    print a hardcoded 21:00, which told the operator a time that was wrong on any machine
    that moved the cron (behoserver moved to 02:00 on 2026-10-19) — an approval looked
    like it had 2 hours to land when it had 7.
    """
    paths = _state(tmp_path)
    _client(paths, tmp_path).post("/decide", data={
        "kind": "update", "component": "pengy", "target": "1.12.2", "decision": "approved"})

    # Default: a machine that says nothing about the schedule gets the historical value.
    assert web.view(paths, tmp_path, run=_clean)["act_time"] == "21:00"

    (paths["policy"]).write_text('act_enabled = true\nact_time = "02:00"\n')
    v = web.view(paths, tmp_path, run=_clean)
    assert v["act_time"] == "02:00", v["act_time"]
    assert "02:00" in v["approved"][0]["status"], v["approved"][0]["status"]
    assert "21:00" not in v["approved"][0]["status"], "the stale default must not leak"
    page = _client(paths, tmp_path).get("/")
    assert b"02:00" in page.data, "the page the operator reads says the real time"


def test_what_no_act_path_can_carry_out_cannot_be_approved(tmp_path):
    paths = _state(tmp_path)
    c = _client(paths, tmp_path)
    r = c.post("/decide", data={"kind": "update", "component": "linux", "target": "7.2.11",
                                "decision": "approved"})
    assert r.status_code == 400 and D.load(paths["decisions"]) == {}
    r = c.post("/decide", data={"kind": "security", "component": "ffmpeg", "target": "6.1.5",
                                "decision": "approved"})
    assert r.status_code == 400, "a security action runs on its own guards; it can be held"


def test_deny_hides_it_and_undo_brings_it_back(tmp_path):
    paths = _state(tmp_path)
    c = _client(paths, tmp_path)
    c.post("/decide", data={"kind": "update", "component": "libqalculate",
                            "target": "5.13.2", "decision": "denied"})
    v = web.view(paths, tmp_path, run=_clean)
    assert "libqalculate" not in [i["component"] for i in v["waiting"]]
    assert [i["component"] for i in v["denied"]] == ["libqalculate"]
    assert c.post("/undo", data={"key": "update:libqalculate"}).status_code == 302
    assert "libqalculate" in [i["component"] for i in web.view(paths, tmp_path, run=_clean)["waiting"]]


def test_the_status_of_an_approval_comes_from_the_plan_once_it_is_newer(tmp_path):
    refused = {"component": "pengy", "lane": "approved", "allowed": False,
               "current": "1.11.5", "target": "1.12.2",
               "refused_reason": ["cooldown: pengy was bumped 2d ago (needs 7d)"]}
    paths = _state(tmp_path, {"date": "2026-10-16", "actions": [], "refused": [refused]})
    D.decide(paths["decisions"], "update", "pengy", "approved", "1.12.2")
    old = paths["decisions"].stat().st_mtime - 60
    os.utime(paths["decisions"], (old, old))
    v = web.view(paths, tmp_path, run=_clean)
    assert "cooldown" in v["approved"][0]["status"]


def test_a_dirty_tree_is_shown(tmp_path):
    paths = _state(tmp_path)

    def dirty(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, stdout=" M CHANGELOG.md\n", stderr="")
    assert web.view(paths, tmp_path, run=dirty)["clean_tree"] is False
    page = web.create_app(paths, tmp_path, run=dirty).test_client().get("/")
    assert b"uncommitted changes" in page.data


def test_a_decision_for_something_already_done_says_so(tmp_path):
    paths = _state(tmp_path)
    D.decide(paths["decisions"], "update", "rtkit", "approved", "0.14")
    obs = json.loads(paths["obs"].read_text())
    obs["packages"]["rtkit"] = {"current": "0.14", "tree": "PENGYOS", "editions": []}
    paths["obs"].write_text(json.dumps(obs))
    (done,) = web.view(paths, tmp_path, run=_clean)["finished"]
    assert done["component"] == "rtkit" and done["status"].startswith("done")


# --- act re-plans when a decision is newer than the plan --------------------------

def test_act_replans_when_a_decision_is_newer_than_the_plan(tmp_path, monkeypatch):
    paths = _state(tmp_path)
    D.decide(paths["decisions"], "update", "pengy", "approved", "1.12.2")
    old = paths["plan"].stat().st_mtime - 60
    os.utime(paths["plan"], (old, old))
    calls = []
    monkeypatch.setattr(cli, "do_plan", lambda args, today=None: calls.append(today) or {})
    args = argparse.Namespace(state_dir=str(paths["state_dir"]), site=str(tmp_path / "site"),
                              repo=str(tmp_path), build_root=str(tmp_path), only=None,
                              max=0, apply=False)
    cli.do_act(args, today=dt.date(2026, 10, 16))
    assert calls == [dt.date(2026, 10, 16)]


def test_act_does_not_replan_for_an_older_decision(tmp_path, monkeypatch):
    paths = _state(tmp_path)
    D.decide(paths["decisions"], "update", "pengy", "approved", "1.12.2")
    old = paths["decisions"].stat().st_mtime - 60
    os.utime(paths["decisions"], (old, old))
    calls = []
    monkeypatch.setattr(cli, "do_plan", lambda args, today=None: calls.append(today) or {})
    args = argparse.Namespace(state_dir=str(paths["state_dir"]), site=str(tmp_path / "site"),
                              repo=str(tmp_path), build_root=str(tmp_path), only=None,
                              max=0, apply=False)
    cli.do_act(args, today=dt.date(2026, 10, 16))
    assert calls == []
