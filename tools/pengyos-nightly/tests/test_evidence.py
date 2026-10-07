"""Tests for the evidence rules — what the act lane is allowed to believe.

These matter more than any other test in the tool: the guards decide whether an
unattended run edits a package that every image ships. The fixtures are real NVD
records copied from the live fkie-cad clone on 2026-10-05, not invented shapes,
because the whole design rests on what NVD's `configurations` actually say.
"""

import json
from pathlib import Path

from pengyos_nightly import evidence as EV

FIX = Path(__file__).parent / "fixtures" / "nvd"


def record(cve: str) -> dict:
    return json.loads((FIX / "git" / f"CVE-{cve.split('-')[1]}"
                       / f"CVE-{cve.split('-')[1]}-{int(cve.split('-')[2]) // 100}xx"
                       / f"{cve}.json").read_text())


# --- the two readings of the same range --------------------------------------

def test_end_excluding_proves_both_exposure_and_the_fix():
    """libxml2 CVE-2026-86137: 'In libxml2 before 2.15.4' -> cpeMatch with
    versionEndExcluding 2.15.4. 2.15.3 is exposed; 2.15.4 closes it."""
    bound = EV.bounds(record("CVE-2026-86137"), "libxml2")
    assert bound, "NVD states a range for the package we ship"
    assert EV.affected(bound, "2.15.3")[0]
    assert EV.covers(bound, "2.15.4")[0]
    assert not EV.affected(bound, "2.15.4")[0], "2.15.4 is not affected, so no fix is needed"


def test_end_including_closes_only_above_the_named_version():
    """CVE-2026-11979 names versionEndIncluding 2.15.3: the last affected release
    is 2.15.3 itself, so 2.15.3 is exposed and only something above it closes it."""
    rec = {"configurations": [{"nodes": [{"cpeMatch": [
        {"criteria": "cpe:2.3:a:xmlsoft:libxml2:*:*:*:*:*:*:*:*",
         "versionEndIncluding": "2.15.3"}]}]}]}
    bound = EV.bounds(rec, "libxml2")
    assert EV.affected(bound, "2.15.3")[0]
    assert EV.covers(bound, "2.15.4")[0]
    assert not EV.covers(bound, "2.15.3")[0], "staying on 2.15.3 does not close it"


def test_exact_version_match_proves_exposure_but_names_no_fix():
    """tar CVE-2026-18477 matches `cpe:2.3:a:gnu:tar:1.35:*...` with no end field:
    the version we ship is named, but nothing says what closes it. That is exposure
    without a fix, and the patch lane must then find a commit or refuse."""
    bound = EV.bounds(record("CVE-2026-18477"), "tar")
    assert bound and EV.affected(bound, "1.35")[0]
    ok, why = EV.covers(bound, "1.35")
    assert not ok and "no version that closes it" in why


def test_a_version_past_the_range_is_not_affected():
    """The other direction matters too: acting on a CVE that does not apply to the
    version we ship would put a pointless change in front of every machine."""
    bound = EV.bounds(record("CVE-2026-86137"), "libxml2")
    ok, why = EV.affected(bound, "2.16.0")
    assert not ok and "does not list it as affected" in why


def test_no_range_means_no_evidence():
    assert EV.bounds({"descriptions": []}, "libxml2") is None
    ev = EV.evidence("CVE-2026-00000", "libxml2", {}, "2.15.3", "2.15.4")
    assert ev["kind"] == "none" and not ev["affected"] and not ev["covered"]


# --- package identity --------------------------------------------------------

def test_cpeid_decides_the_product_not_the_recipe_name():
    """pkg-stats calls it gst1-plugins-good; NVD calls it gst-plugins-good. The
    cpeid pkg-stats reports is the authoritative bridge, and without it the alias
    table is the fallback."""
    rec = {"configurations": [{"nodes": [{"cpeMatch": [
        {"criteria": "cpe:2.3:a:freedesktop:gst-plugins-good:*:*:*:*:*:*:*:*",
         "versionEndExcluding": "1.24.14"}]}]}]}
    assert EV.bounds(rec, "gst1-plugins-good",
                     "cpe:2.3:a:freedesktop:gst-plugins-good:1.24.13:-:*:*:*:*:*:*")
    assert EV.bounds(rec, "gst1-plugins-good")          # alias table
    assert EV.bounds(rec, "totally-unrelated") is None  # not this package


def test_a_record_about_another_product_never_matches():
    rec = {"configurations": [{"nodes": [{"cpeMatch": [
        {"criteria": "cpe:2.3:a:redhat:openshift_container_platform:4.0:*:*:*:*:*:*:*"}]}]}]}
    assert EV.bounds(rec, "podman", "cpe:2.3:a:redhat:podman:5.7.0:-:*:*:*:*:*:*") is None


# --- the fix commit ----------------------------------------------------------

def test_fix_commit_is_the_upstream_commit_nvd_names():
    c = EV.fix_commit(record("CVE-2026-86137"))
    assert c == {"url": "https://github.com/GNOME/libxml2/commit/"
                       "76fe08d97de88bfaef2f7d5cd27f11954cc5bee2",
                 "sha": "76fe08d97de88bfaef2f7d5cd27f11954cc5bee2", "host": "github.com",
                 "kind": "commit"}


def test_a_review_request_is_a_fix_the_loop_can_carry():
    """FFmpeg and GStreamer publish a fix as a review request and NVD links the
    request, not the commit. Both hosts serve the request itself as a git
    format-patch (verified live on 2026-10-06), so this is a fix to carry — the old
    rule read it as "no fix", which is how ffmpeg CVE-2026-40962 (CVSS 9.8) sat
    unactioned while its patch was one HTTP GET away."""
    ffmpeg = EV.fix_commit({"references": [
        {"url": "https://code.ffmpeg.org/FFmpeg/FFmpeg/pulls/22348"}]})
    assert ffmpeg == {"url": "https://code.ffmpeg.org/FFmpeg/FFmpeg/pulls/22348",
                      "sha": "", "host": "code.ffmpeg.org", "kind": "pull",
                      "request": "22348"}
    gst = EV.fix_commit({"references": [
        {"url": "https://gitlab.freedesktop.org/gstreamer/gstreamer"
                "/-/merge_requests/11243.patch"}]})
    assert gst and gst["kind"] == "pull" and gst["request"] == "11243"
    gh = EV.fix_commit({"references": [{"url": "https://github.com/o/r/pull/42"}]})
    assert gh and gh["kind"] == "pull" and gh["sha"] == ""


def test_a_named_commit_beats_a_review_request_on_the_same_record():
    """A commit is the exact change; a request is the fallback, never the preference."""
    rec = {"references": [{"url": "https://code.ffmpeg.org/FFmpeg/FFmpeg/pulls/22348"},
                          {"url": "https://code.ffmpeg.org/FFmpeg/FFmpeg/commit/"
                                   "dbd495f066a85ba96b17433f4306582aa37c3951"}]}
    c = EV.fix_commit(rec)
    assert c["kind"] == "commit" and c["sha"].startswith("dbd495f0")


def test_an_issue_or_an_advisory_page_is_not_a_review_request():
    """`/issues/4854` and a security advisory name a problem, not a fix."""
    assert EV.fix_commit({"references": [
        {"url": "https://gitlab.freedesktop.org/gstreamer/gstreamer/-/issues/4854"}]}) is None
    assert EV.fix_commit({"references": [
        {"url": "https://gstreamer.freedesktop.org/security/sa-2026-0018.html"}]}) is None


def test_vendor_errata_are_not_a_fix_to_carry():
    """libsndfile CVE-2026-37555 points at a gist, an issue and a commit; a record
    that names only errata gives the loop nothing to fetch, and it must say so."""
    assert EV.fix_commit({"references": [
        {"url": "https://access.redhat.com/errata/RHSA-2026:49361"}]}) is None
    c = EV.fix_commit(record("CVE-2026-37555"))
    assert c and c["host"] == "github.com"
