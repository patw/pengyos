"""Tests for the guards — including the two walls that are not planner rules.

`channel_locked` is the one that lets the rest of this tool exist: an unattended
loop that signs and publishes. It is tested as a wall (it must raise on the argv,
not merely decline in the plan), because a later edit that assembles a different
command is exactly how such a guard gets lost.
"""

import datetime as dt

import pytest

from pengyos_nightly import guards as G


# --- the wall: stable is unreachable -----------------------------------------

def test_the_publish_channel_is_a_constant():
    assert G.CHANNEL == "testing"


def test_assert_locked_accepts_the_command_act_really_runs():
    argv = ["scripts/release.sh", "--publish", "--channel", "testing",
            "--push", "pengyos-desktop"]
    assert G.assert_locked(argv) == argv


@pytest.mark.parametrize("argv", [
    ["python3", "-m", "pengyos_release", "update-promote", "--site", "s",
     "--channel", "stable"],
    ["scripts/release.sh", "--publish", "--channel", "stable"],
    ["scripts/release.sh", "--publish", "--channel=stable"],
    ["scripts/release.sh", "--publish", "--channel", "testing", "update-promote"],
])
def test_assert_locked_refuses_anything_that_touches_stable(argv):
    with pytest.raises(G.GuardError):
        G.assert_locked(argv)


def test_assert_locked_refuses_a_channel_that_is_not_testing():
    with pytest.raises(G.GuardError):
        G.assert_locked(["scripts/release.sh", "--channel", "beta"])


# --- each guard, by name -----------------------------------------------------

def _patch_action(**kw):
    a = {"lane": "security", "component": "libxml2", "shape": "patch_dir",
         "current": "2.15.3", "target": "2.15.3", "bump_kind": "none",
         "editions": ["pengyos-desktop"],
         "cves": [{"cve": "CVE-2026-86137", "affected": True, "covered": False,
                   "fix_commit": {"url": "https://github.com/GNOME/libxml2/commit/76fe08d",
                                  "sha": "76fe08d", "host": "github.com"}}],
         "patches": ["buildroot-external/patches/libxml2/2.15.3/0001.patch"]}
    return {**a, **kw}


def test_major_bump_is_never_acted_on():
    a = _patch_action(shape="recipe_bump", current="6.1.5", target="9.0.2",
                      bump_kind="major")
    ok, why = G.major_never(a)
    assert not ok and "changes the series" in why


def test_a_patch_shape_is_not_a_bump_and_says_so():
    ok, why = G.major_never(_patch_action())
    assert ok and "does not move" in why


def test_the_routine_lane_cannot_bump_a_stock_package():
    ok, why = G.stock_version_immovable(_patch_action(lane="routine"))
    assert not ok and "stock_version_immovable" in why


def test_security_lane_may_patch_a_stock_package():
    assert G.stock_version_immovable(_patch_action())[0]


def test_evidence_required_needs_the_exposure_proof_for_a_patch():
    a = _patch_action(cves=[{"cve": "CVE-2026-1", "affected": False, "covered": False}])
    ok, why = G.evidence_required(a)
    assert not ok and "inside any affected range" in why
    assert G.evidence_required(_patch_action())[0]


def test_evidence_required_needs_the_fix_proof_for_a_bump():
    a = _patch_action(shape="recipe_bump", current="1.2.16", target="1.2.16.1",
                      bump_kind="routine",
                      cves=[{"cve": "CVE-2026-1", "affected": True, "covered": False}])
    assert not G.evidence_required(a)[0]
    a["cves"] = [{"cve": "CVE-2026-1", "affected": True, "covered": True}]
    assert G.evidence_required(a)[0]


def test_no_upstream_commit_means_no_action():
    a = _patch_action(cves=[{"cve": "CVE-2026-18477", "affected": True,
                             "fix_commit": None}])
    ok, why = G.fix_commits_available(a)
    assert not ok and "no_fix_commit" in why


def test_a_mix_of_fetchable_and_unfetchable_is_actionable_for_the_fetchable_part():
    """The veto used to be per component: one CVE with no named commit refused the
    whole package. Measured 2026-10-06, that held 28 fetchable patches behind 17
    CVEs with nothing to fetch — ffmpeg 18 of 21, gstreamer1 10 of 24."""
    a = _patch_action(cves=[
        {"cve": "CVE-2026-86137", "affected": True,
         "fix_commit": {"url": "https://github.com/GNOME/libxml2/commit/76fe08d",
                        "sha": "76fe08d", "host": "github.com"}},
        {"cve": "CVE-2026-18477", "affected": True, "fix_commit": None}])
    ok, why = G.fix_commits_available(a)
    assert ok, why
    assert "1 of 2" in why and "CVE-2026-18477" in why, why


def test_a_commit_on_a_host_that_serves_no_patch_is_not_a_fix_to_carry():
    """The guard may not allow what fetch_patch cannot fetch."""
    a = _patch_action(cves=[{"cve": "CVE-2026-1", "affected": True, "fix_commit": {
        "url": "https://code.videolan.org/videolan/x/commit/deadbeef", "sha": "deadbeef",
        "host": "code.videolan.org"}}])
    ok, why = G.fix_commits_available(a)
    assert not ok and "no_fix_commit" in why, why


def test_a_review_request_counts_as_an_available_fix():
    a = _patch_action(cves=[{"cve": "CVE-2026-40962", "affected": True, "fix_commit": {
        "url": "https://code.ffmpeg.org/FFmpeg/FFmpeg/pulls/22348", "sha": "",
        "host": "code.ffmpeg.org", "kind": "pull"}}])
    ok, why = G.fix_commits_available(a)
    assert ok and "1 upstream fix" in why, why


def test_a_known_bad_fix_blocks_that_fix_and_not_the_whole_package():
    """The record is per fix. A commit authored against ffmpeg 8.x failing on 6.1.5 says
    nothing about a fix written for 6.1.5, so the package stays open to it."""
    st = {"patch_failures": {"ffmpeg": {
        "date": "2026-10-06", "where": "libavcodec/pngenc.c",
        "fixes": ["https://code.ffmpeg.org/FFmpeg/FFmpeg/commit/aaaaaaaaaaaa"]}}}
    ok, why = G.patch_does_not_apply("ffmpeg", st, [
        "https://code.ffmpeg.org/FFmpeg/FFmpeg/commit/aaaaaaaaaaaa"])
    assert not ok and "known-bad" in why, why
    assert G.patch_does_not_apply("ffmpeg", st, [
        "https://code.ffmpeg.org/FFmpeg/FFmpeg/commit/bbbbbbbbbbbb"])[0], \
        "a different fix for the same package is still tryable"
    assert G.patch_does_not_apply("ffmpeg", st, [])[0], "no named fix, nothing known-bad"


def test_a_failure_recorded_before_per_fix_tracking_still_blocks_the_component():
    """Refusing something for the old reason in the open beats un-blocking it by accident."""
    st = {"patch_failures": {"podman": {"date": "2026-10-05", "where": "libpod/x.go"}}}
    ok, why = G.patch_does_not_apply("podman", st,
                                     ["https://github.com/containers/podman/commit/abc1234"])
    assert not ok and "before per-fix tracking" in why, why


def test_unsure_matches_are_never_actionable():
    a = _patch_action(cves=[{"cve": "CVE-2026-1", "affected": True, "unsure": True,
                             "fix_commit": {"url": "u", "sha": "s", "host": "github.com"}}])
    ok, why = G.confirmed_only(a)
    assert not ok and "unsure" in why


def test_a_build_host_package_is_not_a_security_update():
    assert not G.not_host_only({"host_only": True, "editions": []})[0]
    assert not G.not_host_only({"host_only": False, "editions": []})[0], \
        "in no edition means it is not in the image either"
    assert G.not_host_only({"host_only": False, "editions": ["pengyos-server"]})[0]


@pytest.mark.parametrize("component", ["linux", "nvidia-open", "buildroot", "cosmic"])
def test_the_denied_components_are_report_only(component):
    ok, why = G.denied_component(component)
    assert not ok and "report-only" in why and component in why


def test_cooldown_blocks_a_second_touch_but_only_after_one():
    today = dt.date(2026, 10, 11)
    assert G.cooldown("libxml2", {}, 7, today)[0]
    ok, why = G.cooldown("libxml2", {"libxml2": "2026-10-05"}, 7, today)
    assert not ok and "6d" in why and "needs 7d" in why
    assert G.cooldown("libxml2", {"libxml2": "2026-10-04"}, 7, today)[0], "7d is the gap"


def test_cap_limits_the_night():
    assert G.cap(0, 1)[0]
    ok, why = G.cap(1, 1)
    assert not ok and "cap" in why


def test_a_dirty_tree_or_a_missing_key_stops_act():
    assert not G.key_present(None)[0]
    assert not G.key_present("/definitely/not/here")[0]
    assert G.key_present(__file__)[0]


def test_a_patch_proved_not_to_apply_is_not_retried_every_night():
    """The pre-flight is cheap but it spends the night's one action slot, so a
    known-bad candidate would block a good one forever."""
    st = {"patch_failures": {"podman": {"date": "2026-10-05",
                                        "where": "test/e2e/run_working_dir_test.go"}}}
    ok, why = G.patch_does_not_apply("podman", st)
    assert not ok and "patch_does_not_apply" in why
    assert "test/e2e/run_working_dir_test.go" in why, "say which file, for the human"
    assert G.patch_does_not_apply("libxml2", st)[0]
    assert G.patch_does_not_apply("libxml2", {})[0]


def test_lane_disabled_is_named_as_such():
    ok, why = G.lane_allowed({"lane": "major"}, {"lanes": ["security"]})
    assert not ok and "major_never" in why
    ok, why = G.lane_allowed({"lane": "routine"}, {"lanes": ["security"]})
    assert not ok and "lane_disabled" in why


# --- the Buildroot release-bump lane -----------------------------------------

def test_buildroot_is_report_until_the_operator_schedules_the_event():
    ok, why = G.denied_component("buildroot", {"bump_buildroot": ""})
    assert not ok and "report-only" in why, why
    ok, why = G.denied_component("buildroot", {"bump_buildroot": "2026.11"})
    assert ok and "operator scheduled it" in why, why
    assert not G.denied_component("linux", {"bump_buildroot": "2026.11"})[0], \
        "scheduling a Buildroot bump does not unlock the kernel"


def test_a_new_release_series_is_a_finding_not_an_action():
    ok, why = G.bump_event_approved("buildroot", "2026.11", {"bump_buildroot": ""})
    assert not ok and "no Buildroot bump is scheduled" in why, why


def test_a_scheduled_bump_matches_what_was_scheduled():
    ok, why = G.bump_event_approved("buildroot", "2027.02", {"bump_buildroot": "2026.11"})
    assert not ok and "scheduled bump is to 2026.11, not 2027.02" in why, why
    assert G.bump_event_approved("buildroot", "2026.11", {"bump_buildroot": "2026.11"})[0]
    assert G.bump_event_approved("libxml2", "2.15.4", {"bump_buildroot": ""})[0], \
        "not every candidate is a bump"


def test_the_bump_lane_exists_only_with_the_schedule():
    ok, why = G.lane_allowed({"lane": "bump"}, {"lanes": ["security"], "bump_buildroot": ""})
    assert not ok and "only when bump_buildroot is set" in why, why
    assert G.lane_allowed({"lane": "bump"}, {"lanes": ["security"],
                                             "bump_buildroot": "2026.11"})[0]
    ok, _ = G.lane_allowed({"lane": "major"}, {"lanes": ["major"], "bump_buildroot": "2026.11"})
    assert not ok, "the major lane stays closed even with a bump scheduled"


def test_a_tree_move_is_refused_until_the_act_lane_can_rebuild():
    ok, why = G.tree_move_executable("tree_move")
    assert not ok and "no full-rebuild path" in why, why
    assert G.tree_move_executable("patch_dir")[0]
    assert G.tree_move_executable("recipe_bump")[0]


def test_a_tree_move_crossing_the_series_is_not_major_never():
    """Crossing to a new release series *is* the event; major_never guards package bumps."""
    ok, why = G.major_never({"shape": "tree_move", "current": "2026.08", "target": "2026.11",
                             "bump_kind": "major"})
    assert ok and "the event itself" in why, why
    assert not G.major_never({"shape": "recipe_bump", "current": "2.15.3", "target": "3.0.0",
                              "bump_kind": "major"})[0]


# --- the known-bad record: one reader, pinned to the version it was proved against

_FFH = "https://code.ffmpeg.org/FFmpeg/FFmpeg/commit/"
_BAD = [_FFH + f"{i:07x}" for i in (1, 2)]
_GOOD = [_FFH + f"{i:07x}" for i in (9, 10)]


def _ffmpeg_st(version="6.1.5", fixes=_BAD):
    return {"patch_failures": {"ffmpeg": {"date": "2026-10-08", "where": "libavcodec/tiff.c",
                                          "version": version, "fixes": list(fixes)}}}


def _ffmpeg_cves(n):
    return [{"cve": f"CVE-2026-7000{i}", "affected": True,
             "fix_commit": {"url": _FFH + f"{i:07x}", "sha": f"{i:07x}",
                            "host": "code.ffmpeg.org"}} for i in range(1, n + 1)]


def test_known_bad_fixes_is_the_one_reader_of_the_record():
    assert G.known_bad_fixes("ffmpeg", _ffmpeg_st()) == set(_BAD)
    assert G.known_bad_fixes("libxml2", _ffmpeg_st()) == set()
    assert G.known_bad_fixes("ffmpeg", {}) == set()
    assert G.known_bad_fixes("ffmpeg", {"patch_failures": {"ffmpeg": {"date": "2026-10-05"}}}) \
        == set(), "a record with no fix list is a component block, not a known-bad fix"


def test_a_known_bad_verdict_is_about_the_pin_it_was_recorded_against():
    st = _ffmpeg_st(version="6.1.5")
    assert G.known_bad_fixes("ffmpeg", st, "6.1.5") == set(_BAD)
    assert G.known_bad_fixes("ffmpeg", st, "6.1.6") == set(), \
        "a verdict on 6.1.5 says nothing about 6.1.6"
    assert G.known_bad_fixes("ffmpeg", st) == set(_BAD), \
        "an unknown pin keeps honouring the record: dropping one by accident is worse"


def test_a_record_without_a_version_binds_at_any_pin():
    st = {"patch_failures": {"ffmpeg": {"date": "2026-10-08", "where": "x.c", "fixes": _BAD}}}
    assert G.known_bad_fixes("ffmpeg", st, "6.1.6") == set(_BAD)
