"""Tests for per-fix known-bad evidence: act.py `_per_fix` and planner.py `_fix_evidence`.

The defect, from the 2026-10-19 security report: all ten ffmpeg "known-bad" entries
carried one identical `failed_on` string. It was `patch_failures.where`, the union of
every file named anywhere in one `make` output — a component-wide aggregate. Ten CVEs
quoting the same string is not evidence about any of them, and the real answer turned out
to be five different shapes (three fixes already in 6.1.5, one that applies in isolation
and fails in series, three partial hunks, two new-file feature commits, one whose file
does not exist in 6.1.5). Getting that answer cost re-testing every fix by hand against
the real tree, while the per-fix evidence sat in `dropped` and was thrown away by the
aggregation.

Run: python3 -m pytest tests -q   (from tools/pengyos-nightly)
"""

import datetime as dt
from pathlib import Path

from pengyos_nightly import act, planner

TODAY = dt.date(2026, 10, 19)

URL_A = "https://code.ffmpeg.org/FFmpeg/FFmpeg/commit/4c6217477fc6"
URL_B = "https://code.ffmpeg.org/FFmpeg/FFmpeg/commit/b506fafec9a1"

DROPPED = [
    {"patch": "buildroot-external/patches/ffmpeg/6.1.5/0005-nvdec.patch",
     "cves": ["CVE-2026-64832"], "fix_url": URL_A,
     "reason": "does not apply to the pinned version",
     "where": "libavcodec/nvdec.c"},
    {"patch": "buildroot-external/patches/ffmpeg/6.1.5/0008-codecview.patch",
     "cves": ["CVE-2024-31582"], "fix_url": URL_B,
     "reason": "already present in the pinned version",
     "where": "libavfilter/vf_codecview.c"},
]


# --- act._per_fix ---------------------------------------------------------------------

def test_per_fix_keeps_each_fix_on_its_own_evidence():
    """Positive control: two dropped fixes, two distinct records."""
    per = act._per_fix(DROPPED)
    assert set(per) == {URL_A, URL_B}
    assert per[URL_A]["where"] == "libavcodec/nvdec.c"
    assert per[URL_B]["where"] == "libavfilter/vf_codecview.c"
    assert per[URL_A]["reason"] == "does not apply to the pinned version"
    assert per[URL_B]["reason"] == "already present in the pinned version"
    assert per[URL_B]["patch"] == "0008-codecview.patch"


def test_per_fix_drops_entries_with_no_fix_url():
    """A fix NVD does not name has no handle to key on; it must not key the map."""
    per = act._per_fix(DROPPED + [{"patch": "x/0009-local.patch", "cves": ["CVE-1"],
                                   "fix_url": "", "reason": "r", "where": "w"}])
    assert set(per) == {URL_A, URL_B}


def test_per_fix_empty_when_nothing_was_dropped():
    assert act._per_fix([]) == {}


# --- planner._fix_evidence ------------------------------------------------------------

AGGREGATE = {"date": "2026-10-10", "where": "libavcodec/adxdec.c, libavcodec/tiff.c",
             "detail": "0005-nvdec.patch: does not apply; 0008-codecview.patch: present",
             "fixes": [URL_A, URL_B], "version": "6.1.5"}

PER_FIX_RECORD = {**AGGREGATE, "per_fix": act._per_fix(DROPPED)}


def test_per_fix_record_names_this_fixes_own_files():
    """The positive control: the two CVEs now read differently."""
    a = planner._fix_evidence(PER_FIX_RECORD, URL_A)
    b = planner._fix_evidence(PER_FIX_RECORD, URL_B)
    assert a["evidence"] == "per-fix" and b["evidence"] == "per-fix"
    assert a["failed_on"] == "libavcodec/nvdec.c"
    assert b["failed_on"] == "libavfilter/vf_codecview.c"
    assert a["failed_on"] != b["failed_on"], "the whole point: the two must not read alike"
    assert b["reason"] == "already present in the pinned version"


def test_aggregate_record_says_it_is_an_aggregate():
    """The negative control, reproduced: the record the repo has today.

    The old behaviour was to hand back the aggregate string as if it were about the fix.
    The string is still shown (it is the only evidence there is) and is labelled.
    """
    a = planner._fix_evidence(AGGREGATE, URL_A)
    assert a["failed_on"] == AGGREGATE["where"]
    assert "component-wide aggregate" in a["evidence"]
    assert "not evidence about this one" in a["evidence"]


def test_a_fix_the_record_does_not_name_is_labelled_not_invented():
    """No per-fix entry for this URL must not silently borrow another fix's files."""
    a = planner._fix_evidence(PER_FIX_RECORD, "https://code.ffmpeg.org/FFmpeg/FFmpeg/commit/zzz")
    assert "aggregate" in a["evidence"]
    assert a["failed_on"] == AGGREGATE["where"]


def test_empty_record_yields_empty_fields_not_a_crash():
    e = planner._fix_evidence({}, URL_A)
    assert e["failed_on"] == "" and e["patch"] == ""
    assert "aggregate" in e["evidence"]


# --- the planner wires it into the plan ----------------------------------------------

# --- the batch lane merges per-fix evidence across nights ---------------------------

def _batch_paths(tmp_path):
    (tmp_path / "actions.json").write_text('{"actions": []}')
    return tmp_path


def test_per_fix_evidence_accumulates_across_nights(tmp_path):
    """Night two must not forget what night one proved.

    The merge is the difference between a known-bad set that grows and one that resets
    every night and spends a pre-flight rediscovering the same bad cherry-picks.
    """
    url_c = "https://code.ffmpeg.org/FFmpeg/FFmpeg/commit/533a6198505e"
    st = {"cves": {}, "findings": {}, "actions": {}, "last_report": "",
          "last_delivery_error": "",
          "patch_failures": {"ffmpeg": {"date": "2026-10-10", "where": "aggregate files",
                                        "fixes": [URL_A],
                                        "per_fix": act._per_fix(DROPPED[:1])}}}
    night_two = {"applied_actions": [], "editions": [], "dropped": [],
                 "patch_failures": {"ffmpeg": {
                     "date": TODAY.isoformat(), "where": "libavcodec/rscc.c",
                     "fixes": [url_c],
                     "per_fix": {url_c: {"patch": "0020-rscc.patch",
                                         "reason": "does not apply to the pinned version",
                                         "where": "libavcodec/rscc.c"}}}}}
    act.record_batch(night_two, [], _batch_paths(tmp_path), st, TODAY)
    rec = st["patch_failures"]["ffmpeg"]
    assert set(rec["per_fix"]) == {URL_A, url_c}, rec["per_fix"]
    assert rec["per_fix"][URL_A]["where"] == "libavcodec/nvdec.c"
    assert set(rec["fixes"]) == {URL_A, url_c}


def test_a_night_with_no_per_fix_does_not_wipe_the_older_evidence(tmp_path):
    """The single-action lane writes aggregate-only records. Merging must not erase.

    This is the shape that would silently turn ten per-fix records back into one
    aggregate string, which is the defect the change exists to remove.
    """
    st = {"cves": {}, "findings": {}, "actions": {}, "last_report": "",
          "last_delivery_error": "",
          "patch_failures": {"ffmpeg": {"date": "2026-10-10", "where": "old aggregate",
                                        "fixes": [URL_A],
                                        "per_fix": act._per_fix(DROPPED)}}}
    night_two = {"applied_actions": [], "editions": [], "dropped": [],
                 "patch_failures": {"ffmpeg": {"date": TODAY.isoformat(),
                                               "where": "new aggregate", "detail": "x",
                                               "fixes": [], "version": "6.1.5"}}}
    act.record_batch(night_two, [], _batch_paths(tmp_path), st, TODAY)
    rec = st["patch_failures"]["ffmpeg"]
    assert set(rec["per_fix"]) == {URL_A, URL_B}, rec["per_fix"]
    assert rec["where"] == "new aggregate"


def test_empty_per_fix_record_still_yields_an_empty_map(tmp_path):
    st = {"cves": {}, "findings": {}, "actions": {}, "last_report": "",
          "last_delivery_error": "", "patch_failures": {}}
    night = {"applied_actions": [], "editions": [], "dropped": [],
             "patch_failures": {"ffmpeg": {"date": TODAY.isoformat(), "where": "w",
                                           "fixes": [], "per_fix": {}}}}
    act.record_batch(night, [], _batch_paths(tmp_path), st, TODAY)
    assert st["patch_failures"]["ffmpeg"]["per_fix"] == {}


def test_skipped_known_bad_entries_are_distinct_in_the_plan():
    """The plan's `skipped_known_bad` is what a human reads; it must not be uniform.

    Built through the planner's own record shape rather than by asserting on the helper.
    """
    st = {"cves": {}, "findings": {}, "actions": {}, "patch_failures":
          {"ffmpeg": PER_FIX_RECORD}, "last_report": "", "last_delivery_error": ""}
    cves = [{"cve": "CVE-2026-64832", "priority": "P1", "affected": True,
             "fix_commit": {"url": URL_A}},
            {"cve": "CVE-2024-31582", "priority": "P1", "affected": True,
             "fix_commit": {"url": URL_B}}]
    known_bad = {URL_A, URL_B}
    skipped = [{"cve": c["cve"], "fix": c["fix_commit"]["url"],
                "recorded": str(PER_FIX_RECORD.get("date") or ""),
                **planner._fix_evidence(PER_FIX_RECORD, c["fix_commit"]["url"])}
               for c in cves if c["fix_commit"]["url"] in known_bad]
    assert len(skipped) == 2
    assert skipped[0]["failed_on"] != skipped[1]["failed_on"], \
        "two skipped fixes quoting one string is the defect this change removes"
    assert {s["evidence"] for s in skipped} == {"per-fix"}
