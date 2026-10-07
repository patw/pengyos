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
