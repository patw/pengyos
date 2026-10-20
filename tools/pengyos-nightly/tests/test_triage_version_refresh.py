"""Tests for the triage loop refreshing a record's version (cli.do_triage).

The defect, from the 2026-10-19 security report: `CVE-2022-38096|linux` read
`version: 7.2.5` while every tree built 7.2.11. The record's `version` is the
version at first triage and nothing moved it, so the verdict ("P1, plausibly
applies") is attached to a version the tree no longer contains, and a reader
cannot tell which version the assessment describes. For a report-only component
like linux, where the record *is* the whole deliverable, that is the whole
finding going stale.

The fix refreshes the version the record claims. It does not re-run the triage
verdict: the loop's existing rule is that a match already triaged is not
re-triaged, because that spends a model call to re-learn a decision already
acted on.

Run: python3 -m pytest tests -q   (from tools/pengyos-nightly)
"""

import datetime as dt
import json
from pathlib import Path

import pytest

from pengyos_nightly import cli

TODAY = dt.date(2026, 10, 19)


def _wire(tmp_path, recorded_version, observed_version, monkeypatch):
    """Run the real triage loop over a one-package observation, with the model call
    stubbed so the test measures the loop and not a verdict."""
    state_dir = tmp_path / "nightly"
    state_dir.mkdir()
    repo = tmp_path / "repo"
    (repo / "tools" / "pengyos-nightly").mkdir(parents=True)

    obs = {
        "date": TODAY.isoformat(),
        "packages": {"linux": {"current": observed_version, "cves": ["CVE-2022-38096"],
                               "unsure_cves": [], "editions": ["pengyos-server"],
                               "latest": "7.2.11"}},
        "kev": {}, "epss": {}, "errors": [], "findings": [],
    }
    (state_dir / "observations.json").write_text(json.dumps(obs))
    st = {"cves": {"CVE-2022-38096|linux": {
              "cve": "CVE-2022-38096", "package": "linux", "version": recorded_version,
              "priority": "P1", "cvss": 7.8, "triaged_on": "2026-09-01",
              "reasoning": "plausibly applies", "reported": True}},
        "findings": {}, "actions": {}, "patch_failures": {},
        "last_report": "", "last_delivery_error": ""}
    (state_dir / "state.json").write_text(json.dumps(st))

    # No new CVEs to triage: the model call must not happen at all.
    calls = []
    def fake_nvd(endpoint, cve):
        calls.append(cve)
        return {"cvss": 7.8}
    def fake_triage(*a, **k):
        calls.append("triage")
        return {"priority": "P1", "reasoning": "plausibly applies"}
    monkeypatch.setattr("pengyos_nightly.sources.nvd_record", fake_nvd)
    monkeypatch.setattr("pengyos_nightly.triage.triage_cve", fake_triage)

    args = type("A", (), {"state_dir": state_dir, "repo": repo,
                          "build_root": tmp_path / "build",
                          "site": tmp_path / "site", "max_triage": 5,
                          "jobs": 1, "no_model": True})()
    return args, state_dir, calls


def test_a_stale_record_version_is_refreshed(tmp_path, monkeypatch):
    """The defect, reproduced: 7.2.5 recorded, 7.2.11 built."""
    args, state_dir, calls = _wire(tmp_path, "7.2.5", "7.2.11", monkeypatch)
    cli.do_triage(args)
    rec = json.loads((state_dir / "state.json").read_text())["cves"]["CVE-2022-38096|linux"]
    assert rec["version"] == "7.2.11", rec["version"]
    assert rec["version_refreshed"] == TODAY.isoformat()
    # The verdict is carried over, not re-derived.
    assert rec["priority"] == "P1" and rec["triaged_on"] == "2026-09-01"
    assert calls == [], "refreshing a version must not spend a model call"


def test_a_matching_version_is_left_alone(tmp_path, monkeypatch):
    """The negative control: no churn, no spurious timestamp, no write of a field
    that would make every record look touched."""
    args, state_dir, calls = _wire(tmp_path, "7.2.11", "7.2.11", monkeypatch)
    cli.do_triage(args)
    rec = json.loads((state_dir / "state.json").read_text())["cves"]["CVE-2022-38096|linux"]
    assert rec["version"] == "7.2.11"
    assert "version_refreshed" not in rec, rec


def test_a_new_match_is_still_triaged_normally(tmp_path, monkeypatch):
    """The refresh must not swallow the path that actually needs a verdict: a match
    that came back after being resolved is re-triaged, as the loop has always done."""
    args, state_dir, calls = _wire(tmp_path, "7.2.5", "7.2.11", monkeypatch)
    st = json.loads((state_dir / "state.json").read_text())
    st["cves"]["CVE-2022-38096|linux"]["resolved"] = TODAY.isoformat()
    (state_dir / "state.json").write_text(json.dumps(st))
    cli.do_triage(args)
    assert "triage" in calls, "a resolved match that is present again must be re-triaged"
    out = json.loads((state_dir / "state.json").read_text())["cves"]["CVE-2022-38096|linux"]
    assert out["version"] == "7.2.11", out["version"]


def test_the_refresh_is_not_applied_on_the_re_triage_path(tmp_path, monkeypatch):
    """A resolved match goes through re-triage, which writes a whole new record.
    The refresh branch is the `elif`, so it must not also stamp that record - the
    new record already carries the observed version from `one()`."""
    args, state_dir, calls = _wire(tmp_path, "7.2.5", "7.2.11", monkeypatch)
    st = json.loads((state_dir / "state.json").read_text())
    st["cves"]["CVE-2022-38096|linux"]["resolved"] = "2026-09-30"
    (state_dir / "state.json").write_text(json.dumps(st))
    cli.do_triage(args)
    out = json.loads((state_dir / "state.json").read_text())["cves"]["CVE-2022-38096|linux"]
    assert "triage" in calls
    assert "version_refreshed" not in out, out
