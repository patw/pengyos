"""Bundled crates and Go modules: SBOM -> OSV -> report."""
import argparse
import datetime as dt
import json
from pathlib import Path

from pengyos_nightly import bundled, cli, report, state

# conftest swaps bundled.observe for a no-network fake in every test; these test the
# real one (against fake OSV calls), taken before that happens.
REAL_OBSERVE = bundled.observe

CDX = {"components": [
    {"name": "cosmic", "components": [
        {"type": "library", "name": "zip", "version": "8.6.0", "purl": "pkg:cargo/zip@8.6.0"},
        {"type": "library", "name": "serde", "version": "1.0.0", "purl": "pkg:cargo/serde@1.0.0"}]},
    {"name": "podman", "components": [
        {"type": "library", "name": "golang.org/x/crypto", "version": "v0.43.0",
         "purl": "pkg:golang/golang.org/x/crypto@v0.43.0"}]},
    {"name": "zlib"}]}

VULNS = {
    "RUSTSEC-2026-0001": {"id": "RUSTSEC-2026-0001", "aliases": ["GHSA-aaaa", "CVE-2026-1"],
                          "summary": "zip: path traversal in extract",
                          "affected": [{"package": {"ecosystem": "crates.io", "name": "zip"},
                                        "ranges": [{"events": [{"introduced": "0"}, {"fixed": "8.6.1"}]}]}]},
    "GHSA-aaaa": {"id": "GHSA-aaaa", "aliases": ["RUSTSEC-2026-0001", "CVE-2026-1"],
                  "database_specific": {"severity": "HIGH"}},
    "RUSTSEC-2026-0002": {"id": "RUSTSEC-2026-0002", "summary": "zip is unmaintained",
                          # where OSV really keeps it: per affected package
                          "affected": [{"package": {"ecosystem": "crates.io", "name": "zip"},
                                        "database_specific": {"informational": "unmaintained"}}]},
    "GO-2026-1": {"id": "GO-2026-1", "aliases": ["GHSA-bbbb"], "summary": "ssh DoS"},
    "GHSA-bbbb": {"id": "GHSA-bbbb", "aliases": ["GO-2026-1"], "database_specific": {"severity": "MODERATE"}},
    "GHSA-cccc": {"id": "GHSA-cccc", "withdrawn": "2026-01-01", "database_specific": {"severity": "CRITICAL"}},
}


def _post(url, body):
    assert url == bundled.OSV_BATCH
    out = []
    for q in body["queries"]:
        name = q["package"]["name"]
        if name == "zip":
            assert q["version"] == "8.6.0" and q["package"]["ecosystem"] == "crates.io"
            ids = ["RUSTSEC-2026-0001", "GHSA-aaaa", "RUSTSEC-2026-0002", "GHSA-cccc"]
        elif name == "golang.org/x/crypto":
            assert q["version"] == "v0.43.0" and q["package"]["ecosystem"] == "Go"
            ids = ["GO-2026-1", "GHSA-bbbb"]
        else:
            ids = []
        out.append({"vulns": [{"id": i, "modified": "m1"} for i in ids]} if ids else {})
    return {"results": out}


def _get(calls):
    def get(url):
        vid = url.rsplit("/", 1)[1]
        calls.append(vid)
        return json.dumps(VULNS[vid])
    return get


def _tree(tmp_path):
    out = tmp_path / "build" / "output" / "pengyos-desktop"
    (out / "images").mkdir(parents=True)
    (out / ".config").write_text("")
    (out / "images" / "pengyos-desktop.cdx.json").write_text(json.dumps(CDX))
    old = tmp_path / "build" / "output" / "pengyos-core"
    (old / "images").mkdir(parents=True)
    (old / ".config").write_text("")
    (old / "images" / "pengyos-core.cdx.json").write_text(json.dumps({"components": [{"name": "zlib"}]}))
    return tmp_path / "build", [{"name": "pengyos-desktop"}, {"name": "pengyos-core"},
                                {"name": "pengyos-server"}]


def test_scan_folds_aliases_and_drops_noise(tmp_path):
    build, targets = _tree(tmp_path)
    calls = []
    found, errors = REAL_OBSERVE(build, targets, tmp_path, post=_post, get=_get(calls))
    assert [b["id"] for b in found] == ["RUSTSEC-2026-0001", "GO-2026-1"], \
        "one record per flaw; informational and withdrawn ones dropped; worst first"
    z = found[0]
    assert z["severity"] == "HIGH", "a RustSec record takes its GHSA alias's severity"
    assert z["fixed"] == "8.6.1" and z["in"] == ["cosmic"] and z["editions"] == ["pengyos-desktop"]
    assert z["key"] == "RUSTSEC-2026-0001|cargo:zip@8.6.0"
    assert found[1]["severity"] == "MODERATE" and found[1]["in"] == ["podman"]
    assert list(errors) == ["sbom pengyos-core"], "an SBOM from before 0.2.0 is a source error"
    # Cached by `modified`: a second night fetches nothing it already has.
    calls.clear()
    REAL_OBSERVE(build, targets, tmp_path, post=_post, get=_get(calls))
    assert calls == []


def _args(tmp_path, **kw):
    a = dict(repo=str(tmp_path), build_root=str(tmp_path), state_dir=str(tmp_path / "state"),
             no_email=True, force_email=False, to=None, mark_reported=True)
    a.update(kw)
    return argparse.Namespace(**a)


def _obs(tmp_path, found):
    sd = tmp_path / "state"
    sd.mkdir(exist_ok=True)
    (sd / "observations.json").write_text(json.dumps(
        {"date": "2026-11-12", "kernel": "", "buildroot": "", "packages": {}, "findings": [],
         "kev": {}, "epss": {}, "errors": {}, "gate": None, "bundled": found}))


def test_report_leads_with_new_high_and_reports_once(tmp_path):
    build, targets = _tree(tmp_path)
    found, _ = REAL_OBSERVE(build, targets, tmp_path, post=_post, get=_get([]))
    _obs(tmp_path, found)
    tue = dt.date(2026, 11, 10)  # not a digest day
    r = cli.do_report(_args(tmp_path), today=tue)
    assert "1 bundled high" in r["subject"]
    html = Path(r["report"]).read_text()
    assert "RUSTSEC-2026-0001" in html and "fixed in 8.6.1" in html
    assert "podman 1 moderate" in html, "the rest counted per package"
    st = state.load(tmp_path / "state" / "state.json")
    assert set(st["bundled"]) == {b["key"] for b in found}
    r2 = cli.do_report(_args(tmp_path), today=tue)
    assert "bundled" not in r2["subject"], "reported once, not every night"
    # The dependency moved past it: forgotten, so a regression would be new again.
    _obs(tmp_path, found[1:])
    cli.do_report(_args(tmp_path), today=tue)
    assert set(state.load(tmp_path / "state" / "state.json")["bundled"]) == {found[1]["key"]}
    # A night the scan did not run forgets nothing.
    _obs(tmp_path, None)
    cli.do_report(_args(tmp_path), today=tue)
    assert set(state.load(tmp_path / "state" / "state.json")["bundled"]) == {found[1]["key"]}


def test_subject_counts_bundled_high():
    assert "2 bundled high" in report.subject("2026-11-12", [], [], {}, False, bundled_serious=2)


def test_cvss3_vector_gives_the_severity():
    # Published base scores from NVD for these vectors.
    assert bundled.cvss3_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == 9.8
    assert bundled.cvss3_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H") == 7.5
    assert bundled.cvss3_score("CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H") == 7.8
    assert bundled.cvss3_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N") == 6.1
    assert bundled.cvss3_score("CVSS:3.0/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:N/A:N") == 5.9
    assert bundled.cvss3_score("garbage") is None
    rec = {"id": "RUSTSEC-1", "severity": [{"type": "CVSS_V3",
                                            "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H"}]}
    assert bundled._severity(rec) == "HIGH"
