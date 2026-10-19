"""Tests for the planner: what becomes an action, and what is refused by name.

The refusals are the point. A plan that lists only its actions proves nothing — you
cannot tell "a guard stopped this" from "the guards never looked". Every candidate
here that is not an action must name the guard that stopped it.

Each test writes its own NVD records into the layout `sources.nvd_raw` reads, so a
refusal is tested against the record that causes it rather than against a borrowed
one. The libxml2 record is the real one (copied from the live fkie-cad clone on
2026-10-05); the rest are the smallest shapes that exercise a rule.
"""

import datetime as dt
import json
import urllib.error
from pathlib import Path

from pengyos_nightly import evidence, planner, sources

FIX = Path(__file__).parent / "fixtures"
PATCH_TEXT = (FIX / "patches" / "0001-xmlregexp-prevent-out-of-bounds-read-in-nxt-macro.patch"
              ).read_text()
LIBXML2 = json.loads((FIX / "nvd" / "git" / "CVE-2026" / "CVE-2026-861xx"
                      / "CVE-2026-86137.json").read_text())

TODAY = dt.date(2026, 10, 5)


def fake_get(url: str) -> bytes:
    if url.endswith(".patch"):
        return PATCH_TEXT.encode()
    return b"a fake tarball"


def _pkg(**kw):
    p = {"current": "", "latest": "", "tree": "BUILDROOT", "pkg_path": "", "mk_path": "",
         "cpeid": "", "patch_files": [], "url": "", "host_only": False, "cves": [],
         "unsure_cves": [], "editions": ["pengyos-desktop"]}
    return {**p, **kw}


def _obs(**packages):
    return {"date": "2026-10-05", "buildroot": "2026.08", "kernel": "7.2.5",
            "packages": packages, "findings": [], "errors": {}}


def _triaged(cve, package, priority="P1", **extra):
    return {f"{cve}|{package}": {"priority": priority, "cvss": 7.8,
                                 "first_seen": "2026-10-05", "rationale": "x",
                                 "source": "model", **extra}}


def _st(*matches):
    st = {"cves": {}, "findings": {}, "last_report": "2026-10-05", "actions": {}}
    for m in matches:
        st["cves"].update(m)
    return st


def _cpe(vendor, product, version, **range_):
    return {"cpeMatch": [{"criteria": f"cpe:2.3:a:{vendor}:{product}:{version}:*:*:*:*:*:*:*",
                          **range_}]}


def _rec(vendor, product, version, refs=(), **range_):
    """The smallest NVD record shape that exercises a rule."""
    return {"configurations": [{"nodes": [_cpe(vendor, product, version, **range_)]}],
            "references": [{"url": u} for u in refs]}


def _nvd(tmp_path, *records):
    """Write records into the layout sources.nvd_raw reads."""
    root = tmp_path / "nvd"
    for cve, rec in records:
        year, num = cve.split("-")[1:]
        d = root / "git" / f"CVE-{year}" / f"CVE-{year}-{int(num) // 100}xx"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{cve}.json").write_text(json.dumps(rec))
    return root


def _repo(tmp_path):
    repo = tmp_path / "repo"
    d = repo / "buildroot-external" / "package" / "alsa-ucm-conf"
    d.mkdir(parents=True, exist_ok=True)
    for name in ("alsa-ucm-conf.mk", "alsa-ucm-conf.hash"):
        (d / name).write_text((FIX / "recipes" / name).read_text())
    layer = repo / "buildroot-external" / "configs" / "layers"
    layer.mkdir(parents=True, exist_ok=True)
    (layer / "core.config").write_text('BR2_ROOTFS_OVERLAY="x"\n')
    (repo / "targets.toml").write_text(
        '[[targets]]\nname = "pengyos-desktop"\nbr_version = "2026.08"\n'
        'external_tree = "buildroot-external"\ndefconfig = "pengyos_desktop_defconfig"\n\n'
        '[[targets]]\nname = "pengyos-server"\nbr_version = "2026.08"\n'
        'external_tree = "buildroot-external"\ndefconfig = "pengyos_server_defconfig"\n')
    carried = repo / "buildroot-external" / "patches" / "libxml2" / "2.15.3"
    carried.mkdir(parents=True, exist_ok=True)
    (carried / "0001-xmlregexp.patch").write_text(PATCH_TEXT)
    return repo


def _plan(tmp_path, obs, st, nvd, cfg=None, today=TODAY):
    cfg = cfg or {"act_enabled": True, "push": True, "lanes": ["security", "routine"],
                  "priorities": ["P0", "P1"], "max_actions": 1, "cooldown_days": 7,
                  "allow_patch_shape": True, "skip_cosmic": True}
    return planner.build_plan(obs, st, cfg, today, _repo(tmp_path), tmp_path,
                              lambda cve: sources.nvd_raw(nvd, cve), fake_get)


def _by_id(plan, action_id):
    for a in plan["actions"] + plan["refused"]:
        if a["id"] == action_id:
            return a
    raise AssertionError(f"{action_id} is neither an action nor a refusal: "
                         + ", ".join(a["id"] for a in plan["actions"] + plan["refused"]))


# --- the action tonight actually took ----------------------------------------

def test_libxml2_becomes_a_patch_action_with_named_fixes(tmp_path):
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*",
                            patch_files=["0001-Revert-cmake.patch"],
                            editions=["pengyos-desktop", "pengyos-desktop-nvidia"]))
    nvd = _nvd(tmp_path, ("CVE-2026-86137", LIBXML2),
               ("CVE-2026-86143", _rec("xmlsoft", "libxml2", "*",
                                       ["https://github.com/GNOME/libxml2/commit/90f293ba"],
                                       versionEndExcluding="2.15.4")))
    plan = _plan(tmp_path, obs, _st(_triaged("CVE-2026-86137", "libxml2"),
                                    _triaged("CVE-2026-86143", "libxml2")), nvd)
    a = _by_id(plan, "security-libxml2-2.15.3")
    assert a["allowed"], [g for g in a["guards"] if not g["ok"]]
    assert a["shape"] == "patch_dir" and a["target"] == "2.15.3", "the pin does not move"
    assert a["closes"] == ["CVE-2026-86137", "CVE-2026-86143"]
    assert a["editions"] == ["pengyos-desktop", "pengyos-desktop-nvidia"]
    patches = [e for e in a["edits"] if e["path"].endswith(".patch")]
    assert len(patches) == 2, "one patch per distinct fix commit"
    assert patches[0]["path"] == ("buildroot-external/patches/libxml2/2.15.3/"
                                  "0001-xmlregexp-prevent-out-of-bounds-read-in-nxt-macro.patch")
    assert "BR2_GLOBAL_PATCH_DIR" in a["edits"][-1]["new"]
    assert a["risks"], "the stock recipe's own patches are a risk worth naming"


def test_the_plan_records_the_evidence_next_to_each_cve(tmp_path):
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*"))
    nvd = _nvd(tmp_path, ("CVE-2026-86137", LIBXML2))
    a = _by_id(_plan(tmp_path, obs, _st(_triaged("CVE-2026-86137", "libxml2")), nvd),
               "security-libxml2-2.15.3")
    c = a["cves"][0]
    assert c["affected"] and "2.15.4" in c["affected_note"]
    assert c["fix_commit"]["host"] == "github.com" and c["priority"] == "P1"


def test_one_patch_per_commit_even_when_two_cves_share_it(tmp_path):
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*"))
    same = _rec("xmlsoft", "libxml2", "*",
                ["https://github.com/GNOME/libxml2/commit/76fe08d9"],
                versionEndExcluding="2.15.4")
    nvd = _nvd(tmp_path, ("CVE-2026-86137", same), ("CVE-2026-86143", same))
    a = _by_id(_plan(tmp_path, obs, _st(_triaged("CVE-2026-86137", "libxml2"),
                                        _triaged("CVE-2026-86143", "libxml2")), nvd),
               "security-libxml2-2.15.3")
    assert len([e for e in a["edits"] if e["path"].endswith(".patch")]) == 1
    assert len(a["closes"]) == 2


def test_a_patch_action_claims_only_the_cves_it_actually_carries(tmp_path):
    """The claim and the patch set come from one rule (`edits.carried_cves`), so a
    package with a mix of fetchable fixes and vendor errata ships the fixes and says
    plainly which CVEs are still open. Before this, one unfetchable CVE refused the
    whole component: ffmpeg 18 fetchable patches lost to 3 errata-only CVEs."""
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*"))
    carried = _rec("xmlsoft", "libxml2", "*",
                   ["https://github.com/GNOME/libxml2/commit/76fe08d9"],
                   versionEndExcluding="2.15.4")
    errata = _rec("xmlsoft", "libxml2", "*",
                  ["https://access.redhat.com/errata/RHSA-2026:49361"],
                  versionEndExcluding="2.15.4")
    nvd = _nvd(tmp_path, ("CVE-2026-86137", carried), ("CVE-2026-86144", errata))
    a = _by_id(_plan(tmp_path, obs, _st(_triaged("CVE-2026-86137", "libxml2"),
                                        _triaged("CVE-2026-86144", "libxml2")), nvd),
               "security-libxml2-2.15.3")
    assert a["allowed"], a["guards"]
    assert a["closes"] == ["CVE-2026-86137"], "the claim is the patch set, not the exposure"
    assert a["still_open"] == ["CVE-2026-86144"], "the rest is named, not dropped"
    assert len([e for e in a["edits"] if e["path"].endswith(".patch")]) == 1
    assert len(a["cves"]) == 2, "both matches stay recorded on the action"


def test_a_review_request_makes_a_stock_package_actionable(tmp_path):
    """The ffmpeg case, end to end through the planner: NVD named a pull request on
    a host that serves it as a patch, so the pinned version can carry the fix."""
    obs = _obs(ffmpeg=_pkg(current="6.1.5", latest="8.1",
                           cpeid="cpe:2.3:a:ffmpeg:ffmpeg:6.1.5:-:*:*:*:*:*:*"))
    nvd = _nvd(tmp_path, ("CVE-2026-40962", _rec(
        "ffmpeg", "ffmpeg", "*", ["https://code.ffmpeg.org/FFmpeg/FFmpeg/pulls/22348"],
        versionEndExcluding="8.1")))
    a = _by_id(_plan(tmp_path, obs, _st(_triaged("CVE-2026-40962", "ffmpeg")), nvd),
               "security-ffmpeg-6.1.5")
    assert a["allowed"], a["guards"]
    assert a["closes"] == ["CVE-2026-40962"]
    assert a["cves"][0]["fix_commit"]["kind"] == "pull"
    assert len([e for e in a["edits"] if e["path"].endswith(".patch")]) == 1


# --- the Buildroot release-bump lane (phase 4) -------------------------------

def _bump_cfg(target="2026.11", lanes=("security", "routine")):
    return {"act_enabled": True, "push": True, "lanes": list(lanes),
            "priorities": ["P0", "P1"], "max_actions": 12, "cooldown_days": 7,
            "allow_patch_shape": True, "skip_cosmic": True, "bump_buildroot": target}


def test_a_new_buildroot_release_is_a_finding_and_stays_one(tmp_path):
    """Nothing about a newer tag moves the tree. The lane is inert until the operator
    schedules the event, and an inert lane leaves no candidate at all."""
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*",
                            cves=["CVE-2026-86137"]))
    obs["findings"] = [{"lane": "major", "component": "buildroot", "current": "2026.08",
                        "available": "2026.11", "note": "new Buildroot release series 2026.11"}]
    st = _st(_triaged("CVE-2026-86137", "libxml2"))
    plan = _plan(tmp_path, obs, st, _nvd(tmp_path, ("CVE-2026-86137", _rec(
        "xmlsoft", "libxml2", "*",
        ["https://github.com/GNOME/libxml2/commit/76fe08d97de88bfaef2f7d5cd27f11954cc5bee2"],
        versionEndExcluding="2.15.4"))))
    assert not [a for a in plan["actions"] + plan["refused"] if a["component"] == "buildroot"], \
        "a finding is not a candidate"


def test_a_scheduled_bump_is_planned_refused_by_name_and_says_what_it_retires(tmp_path):
    """`bump_buildroot = "2026.11"` is the operator scheduling the event. The plan then
    shows the whole decision - the edit, every edition it touches, and the patches that
    must be re-checked - and refuses it by name because the act lane cannot rebuild yet."""
    obs = _obs()
    obs["buildroot"] = "2026.08"
    plan = planner.build_plan(obs, _st(), _bump_cfg(), TODAY, _repo(tmp_path), tmp_path,
                              lambda cve: {}, fake_get)
    a = _by_id(plan, "bump-buildroot-2026.11")
    assert a, [x["id"] for x in plan["refused"]]
    assert not a["allowed"] and "tree_move_not_executable" in a["refused_by"], a["guards"]
    assert a["shape"] == "tree_move" and a["current"] == "2026.08" and a["target"] == "2026.11"
    assert a["editions"] == ["pengyos-desktop", "pengyos-server"], "every edition, or none"
    edit = a["edits"][0]
    assert edit["path"] == "targets.toml"
    assert edit["new"].count('br_version = "2026.11"') == 2
    assert 'br_version = "2026.08"' not in edit["new"]
    assert a["retires"] == ["buildroot-external/patches/libxml2/2.15.3/0001-xmlregexp.patch"]
    assert "retired if that line already carries the fix" in a["retire_check"]
    # The guard that stops it is the last one, so the plan still shows everything else.
    assert a["refused_by"] == ["tree_move_not_executable"], a["refused_by"]


def test_scheduling_a_bump_does_not_enable_the_other_major_findings(tmp_path):
    """The bump lane is its own lane. Turning it on for Buildroot must not turn on the
    kernel or the NVIDIA driver, which share the 'new release series' label."""
    obs = _obs()
    obs["buildroot"] = "2026.08"
    obs["findings"] = [{"lane": "routine", "component": "linux", "current": "7.2.5",
                        "available": "7.2.6", "note": "kernel point release"}]
    plan = planner.build_plan(obs, _st(), _bump_cfg(), TODAY, _repo(tmp_path), tmp_path,
                              lambda cve: {}, fake_get)
    bump = _by_id(plan, "bump-buildroot-2026.11")
    assert bump and not bump["allowed"]
    assert bump["refused_by"] == ["tree_move_not_executable"], bump["refused_by"]
    lane_ok = [g for g in bump["guards"] if g["guard"] == "lane_allowed"][0]
    assert lane_ok["ok"] and "bump" in lane_ok["reason"], lane_ok
    kernel = _by_id(plan, "routine-linux-7.2.6")
    assert kernel and kernel["lane"] == "routine", "the kernel is not swept into the bump"
    assert all(a["lane"] != "major" for a in plan["actions"] + plan["refused"]), \
        [a["id"] for a in plan["actions"] + plan["refused"]]


# --- attributing a CVE to the component its fix actually changes -------------

OTHER = (FIX / "patches" / "monorepo-asfdemux-other-component.patch").read_text()
BAD_COMPONENT = """From bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb Mon Sep 17 00:00:00 2001
Subject: [PATCH] dvbsuboverlay: bounds check

--- a/subprojects/gst-plugins-bad/gst/dvbsuboverlay/gst-dvbsuboverlay.c
+++ b/subprojects/gst-plugins-bad/gst/dvbsuboverlay/gst-dvbsuboverlay.c
@@ -10,6 +10,7 @@
+  if (len > MAX) return;
"""


def _gstreamer_obs(bad_version="1.24.13", ship_bad=True, ship_ugly=False):
    pkgs = {"gstreamer1": _pkg(current="1.24.13", latest="1.26.0",
                               cpeid="cpe:2.3:a:gstreamer:gstreamer:1.24.13:-:*:*:*:*:*:*",
                               cves=["CVE-2026-3084", "CVE-2026-3085"]),
            "gst1-plugins-good": _pkg(current="1.24.13", latest="1.26.0",
                                      cpeid="cpe:2.3:a:gst-plugins-good:gst-plugins-good:"
                                            "1.24.13:-:*:*:*:*:*:*")}
    if ship_bad:
        pkgs["gst1-plugins-bad"] = _pkg(current=bad_version, latest="1.26.0",
                                        cpeid="cpe:2.3:a:gst-plugins-bad:gst-plugins-bad:"
                                              f"{bad_version}:-:*:*:*:*:*:*")
    if ship_ugly:
        pkgs["gst1-plugins-ugly"] = _pkg(current="1.24.13", latest="1.26.0",
                                         cpeid="cpe:2.3:a:gst-plugins-ugly:gst-plugins-ugly:"
                                               "1.24.13:-:*:*:*:*:*:*")
    return _obs(**pkgs)


def _gstreamer_nvd(tmp_path):
    return _nvd(tmp_path,
                ("CVE-2026-3084", _rec("gstreamer", "gstreamer", "*",
                                       ["https://gitlab.freedesktop.org/gstreamer/gstreamer"
                                        "/-/merge_requests/11245"], versionEndExcluding="1.26.0")),
                ("CVE-2026-3085", _rec("gstreamer", "gstreamer", "*",
                                       ["https://gitlab.freedesktop.org/gstreamer/gstreamer"
                                        "/-/merge_requests/12000"], versionEndExcluding="1.26.0")))


def _gstreamer_get(url):
    return (BAD_COMPONENT if "12000" in url else OTHER).encode()


def test_a_fix_for_a_sibling_component_is_attributed_to_the_package_that_ships_it(tmp_path):
    """pkg-stats reports a gst-plugins-bad fix against gstreamer1 (NVD's CPE is the
    project) and shows gst1-plugins-bad with zero CVEs. The patch says where the change
    is, so the CVE moves to the package that can actually carry it."""
    obs = _gstreamer_obs()
    st = _st(_triaged("CVE-2026-3084", "gstreamer1"), _triaged("CVE-2026-3085", "gstreamer1"))
    room = {"act_enabled": True, "push": True, "lanes": ["security", "routine"],
            "priorities": ["P0", "P1"], "max_actions": 12, "cooldown_days": 7,
            "allow_patch_shape": True, "skip_cosmic": True}
    plan = _plan_get(tmp_path, obs, st, _gstreamer_nvd(tmp_path), _gstreamer_get, room)

    bad = _by_id(plan, "security-gst1-plugins-bad-1.24.13")
    assert bad, [a["id"] for a in plan["actions"]] + [a["id"] for a in plan["refused"]]
    assert [c["cve"] for c in bad["cves"]] == ["CVE-2026-3085"], \
        "only the fix that changes gst-plugins-bad's files belongs here"
    assert bad["cves"][0]["attributed"]["from"] == "gstreamer1"
    assert bad["cves"][0]["attributed"]["component"] == "gst-plugins-bad"
    # The exposure claim is inherited from the project-wide range, and the plan says so.
    assert bad["cves"][0]["affected"], bad["cves"][0].get("note")

    # asfdemux is gst-plugins-ugly, which no edition ships: that CVE stays on gstreamer1
    # and is refused there, rather than disappearing from the plan.
    core = _by_id(plan, "security-gstreamer1-1.24.13")
    assert [c["cve"] for c in core["cves"]] == ["CVE-2026-3084"]
    assert not core["allowed"] and "no_fix_for_this_package" in core["refused_by"], core["guards"]
    assert plan["counts"]["attributed"] == 2


def test_attribution_only_holds_when_both_components_are_pinned_at_the_same_version(tmp_path):
    """The transfer is a lockstep claim: gstreamer releases core and its plugins at one
    version. If our gst1-plugins-bad is not at that version, the CVE is not its problem."""
    obs = _gstreamer_obs(bad_version="1.22.12")
    st = _st(_triaged("CVE-2026-3084", "gstreamer1"), _triaged("CVE-2026-3085", "gstreamer1"))
    plan = _plan_get(tmp_path, obs, st, _gstreamer_nvd(tmp_path), _gstreamer_get)
    assert all(a["package"] == "" for a in plan["attributed"]), \
        "nothing is moved: no component we ship is pinned at 1.24.13 for that fix"
    assert not [a for a in plan["actions"] + plan["refused"]
                if a["component"] == "gst1-plugins-bad"], \
        "gst1-plugins-bad at 1.22.12 is not the package this fix belongs to"
    core = _by_id(plan, "security-gstreamer1-1.24.13")
    assert {c["cve"] for c in core["cves"]} == {"CVE-2026-3084", "CVE-2026-3085"}


def test_a_component_we_do_not_ship_stays_recorded_where_it_was_reported(tmp_path):
    """gst-plugins-ugly is in no edition. The CVE must not vanish from the plan: it stays
    on gstreamer1 and is refused there, with the reason naming the component."""
    obs = _gstreamer_obs(ship_bad=False)
    st = _st(_triaged("CVE-2026-3084", "gstreamer1"))
    plan = _plan_get(tmp_path, obs, st, _gstreamer_nvd(tmp_path), _gstreamer_get)
    a = _by_id(plan, "security-gstreamer1-1.24.13")
    assert not a["allowed"] and "no_fix_for_this_package" in a["refused_by"], a["guards"]
    assert a["not_carried"][0]["reason"].startswith("the fix is for gst-plugins-ugly")


def test_attribution_does_not_touch_a_package_that_is_not_a_monorepo_member(tmp_path):
    """ffmpeg is its own product: nothing is re-attributed, and no patch is fetched for
    attribution that the edits were not going to fetch anyway."""
    obs = _obs(ffmpeg=_pkg(current="6.1.5", latest="8.1",
                           cpeid="cpe:2.3:a:ffmpeg:ffmpeg:6.1.5:-:*:*:*:*:*:*",
                           cves=["CVE-2026-40962"]))
    st = _st(_triaged("CVE-2026-40962", "ffmpeg"))
    nvd = _nvd(tmp_path, ("CVE-2026-40962", _rec(
        "ffmpeg", "ffmpeg", "*", ["https://code.ffmpeg.org/FFmpeg/FFmpeg/pulls/22348"],
        versionEndExcluding="8.1")))
    plan = _plan_get(tmp_path, obs, st, nvd, lambda url: PATCH_TEXT.encode())
    assert plan["counts"]["attributed"] == 0


def test_attribution_fetches_nothing_for_a_package_that_is_not_in_a_monorepo(tmp_path):
    """Attribution costs a fetch per candidate fix, so it runs only where the question is
    real: a package whose upstream product publishes CVEs project-wide. ffmpeg is its own
    product, and paying for a fetch to learn that would double the plan's network cost."""
    obs = _obs(ffmpeg=_pkg(current="6.1.5", latest="8.1",
                           cpeid="cpe:2.3:a:ffmpeg:ffmpeg:6.1.5:-:*:*:*:*:*:*",
                           cves=["CVE-2026-40962"]),
               gstreamer1=_pkg(current="1.24.13", latest="1.26.0",
                               cpeid="cpe:2.3:a:gstreamer:gstreamer:1.24.13:-:*:*:*:*:*:*",
                               cves=["CVE-2026-3085"]))
    st = _st(_triaged("CVE-2026-40962", "ffmpeg"), _triaged("CVE-2026-3085", "gstreamer1"))
    nvd = _nvd(tmp_path,
               ("CVE-2026-40962", _rec("ffmpeg", "ffmpeg", "*",
                                       ["https://code.ffmpeg.org/FFmpeg/FFmpeg/pulls/22348"],
                                       versionEndExcluding="8.1")),
               ("CVE-2026-3085", _rec("gstreamer", "gstreamer", "*",
                                      ["https://gitlab.freedesktop.org/gstreamer/gstreamer"
                                       "/-/merge_requests/12000"], versionEndExcluding="1.26.0")))
    seen = []

    def get(url):
        seen.append(url)
        return _gstreamer_get(url)

    out = planner._attribution(obs, st, {"priorities": ["P0", "P1"]}, obs["packages"],
                               lambda cve: sources.nvd_raw(nvd, cve), get)
    assert seen == ["https://gitlab.freedesktop.org/gstreamer/gstreamer"
                    "/-/merge_requests/12000.patch"], seen
    assert list(out) == [("CVE-2026-3085", "gstreamer1")]


def test_a_monorepo_product_is_named_as_one(tmp_path):
    assert evidence.is_monorepo_product("gstreamer")
    assert not evidence.is_monorepo_product("ffmpeg")
    assert not evidence.is_monorepo_product("")


# --- a named commit that is not a fix for this package ----------------------

ADVISORY = (FIX / "patches" / "advisory-sa-2026-0009.patch").read_text()
MONOREPO = (FIX / "patches" / "monorepo-qtdemux-series.patch").read_text()


def _plan_get(tmp_path, obs, st, nvd, get, cfg=None):
    cfg = cfg or {"act_enabled": True, "push": True, "lanes": ["security", "routine"],
                  "priorities": ["P0", "P1"], "max_actions": 1, "cooldown_days": 7,
                  "allow_patch_shape": True, "skip_cosmic": True}
    return planner.build_plan(obs, st, cfg, TODAY, _repo(tmp_path), tmp_path,
                              lambda cve: sources.nvd_raw(nvd, cve), get)


def test_an_advisory_commit_is_refused_by_name_not_claimed(tmp_path):
    """NVD named GStreamer's advisory document as the fix for CVE-2026-3086. Carrying it
    would add markdown to the source tree and let the notes claim a CVE was fixed."""
    obs = _obs(gstreamer1=_pkg(current="1.24.13", latest="1.26.0",
                               cpeid="cpe:2.3:a:gstreamer:gstreamer:1.24.13:-:*:*:*:*:*:*"))
    nvd = _nvd(tmp_path, ("CVE-2026-3086", _rec(
        "gstreamer", "gstreamer", "*",
        ["https://gitlab.freedesktop.org/gstreamer/gstreamer/-/commit/025d59cf3459"],
        versionEndExcluding="1.26.0")))
    a = _by_id(_plan_get(tmp_path, obs, _st(_triaged("CVE-2026-3086", "gstreamer1")), nvd,
                         lambda url: ADVISORY.encode()), "security-gstreamer1-1.24.13")
    assert not a["allowed"] and "no_fix_for_this_package" in a["refused_by"], a["guards"]
    assert a["not_carried"][0]["cves"] == ["CVE-2026-3086"]
    assert "adds new files only" in a["not_carried"][0]["reason"]
    assert "security-advisories" in a["not_carried"][0]["subject"], "named, so a human can see it"


def test_a_fix_for_a_sibling_component_does_not_become_a_claim(tmp_path):
    """NVD's product is `gstreamer`, so a gst-plugins-ugly fix is matched against our
    gstreamer1. It is refused rather than written into a tree that has no such files."""
    obs = _obs(gstreamer1=_pkg(current="1.24.13", latest="1.26.0",
                               cpeid="cpe:2.3:a:gstreamer:gstreamer:1.24.13:-:*:*:*:*:*:*"))
    nvd = _nvd(tmp_path, ("CVE-2026-3084", _rec(
        "gstreamer", "gstreamer", "*",
        ["https://gitlab.freedesktop.org/gstreamer/gstreamer/-/merge_requests/11245"],
        versionEndExcluding="1.26.0")))
    other = (FIX / "patches" / "monorepo-asfdemux-other-component.patch").read_text()
    a = _by_id(_plan_get(tmp_path, obs, _st(_triaged("CVE-2026-3084", "gstreamer1")), nvd,
                         lambda url: other.encode()), "security-gstreamer1-1.24.13")
    assert not a["allowed"] and "no_fix_for_this_package" in a["refused_by"], a["guards"]
    assert "gst-plugins-ugly" in a["not_carried"][0]["reason"]


def test_a_monorepo_fix_for_this_component_is_carried_re_rooted_and_claimed(tmp_path):
    obs = _obs(**{"gst1-plugins-good": _pkg(
        current="1.24.13", latest="1.26.0",
        cpeid="cpe:2.3:a:gst-plugins-good:gst-plugins-good:1.24.13:-:*:*:*:*:*:*")})
    nvd = _nvd(tmp_path, ("CVE-2026-46469", _rec(
        "gst-plugins-good", "gst-plugins-good", "*",
        ["https://gitlab.freedesktop.org/gstreamer/gstreamer/-/merge_requests/11243"],
        versionEndExcluding="1.26.0")))
    a = _by_id(_plan_get(tmp_path, obs, _st(_triaged("CVE-2026-46469", "gst1-plugins-good")),
                         nvd, lambda url: MONOREPO.encode()),
               "security-gst1-plugins-good-1.24.13")
    assert a["allowed"], a["guards"]
    assert a["closes"] == ["CVE-2026-46469"]
    patches = [e for e in a["edits"] if e["path"].endswith(".patch")]
    assert len(patches) == 4
    assert all("--- a/gst/isomp4/qtdemux.c" in e["new"] for e in patches)
    assert any("re-rooted" in r for r in a["risks"]), a["risks"]


def test_a_package_keeps_the_real_fix_and_admits_the_advisory_one(tmp_path):
    """The honesty case: one CVE has a real fix, the other's named commit is a document.
    The action ships, claims one CVE, and says the other is still open - with the reason."""
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*"))
    real = _rec("xmlsoft", "libxml2", "*",
                ["https://github.com/GNOME/libxml2/commit/76fe08d97de88bfaef2f7d5cd27f11954cc5bee2"],
                versionEndExcluding="2.15.4")
    doc = _rec("xmlsoft", "libxml2", "*",
               ["https://gitlab.freedesktop.org/gstreamer/gstreamer/-/commit/025d59cf3459"],
               versionEndExcluding="2.15.4")
    nvd = _nvd(tmp_path, ("CVE-2026-86137", real), ("CVE-2026-3086", doc))
    st = _st(_triaged("CVE-2026-86137", "libxml2"), _triaged("CVE-2026-3086", "libxml2"))

    def get(url):
        return ADVISORY.encode() if "025d59cf" in url else (FIX / "patches" /
            "0001-xmlregexp-prevent-out-of-bounds-read-in-nxt-macro.patch").read_bytes()

    a = _by_id(_plan_get(tmp_path, obs, st, nvd, get), "security-libxml2-2.15.3")
    assert a["allowed"], a["guards"]
    assert a["closes"] == ["CVE-2026-86137"], "the claim is the patch set"
    assert a["still_open"] == ["CVE-2026-3086"], "not dropped - named"
    assert a["not_carried"][0]["reason"].startswith("adds new files only")


def test_a_known_bad_fix_is_refused_by_name_and_the_package_stays_open(tmp_path):
    """Tomorrow's plan must refuse the cherry-pick that already failed, and still take a
    different fix for the same package."""
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*"))
    st = _st(_triaged("CVE-2026-86137", "libxml2"))
    st["patch_failures"] = {"libxml2": {"date": "2026-10-05", "where": "parser.c",
                                        "fixes": ["https://github.com/GNOME/libxml2/commit/bad1bad1"]}}
    nvd = _nvd(tmp_path, ("CVE-2026-86137", _rec(
        "xmlsoft", "libxml2", "*", ["https://github.com/GNOME/libxml2/commit/bad1bad1"],
        versionEndExcluding="2.15.4")))
    a = _by_id(_plan(tmp_path, obs, st, nvd), "security-libxml2-2.15.3")
    assert not a["allowed"] and "patch_does_not_apply" in a["refused_by"], a["guards"]

    nvd2 = _nvd(tmp_path, ("CVE-2026-86137", _rec(
        "xmlsoft", "libxml2", "*", ["https://github.com/GNOME/libxml2/commit/beef1cad"],
        versionEndExcluding="2.15.4")))
    b = _by_id(_plan(tmp_path, obs, st, nvd2), "security-libxml2-2.15.3")
    assert b["allowed"], b["guards"]
    assert b["closes"] == ["CVE-2026-86137"]


# --- refusals, each one named ------------------------------------------------

def test_a_cve_with_no_upstream_commit_is_refused_not_guessed(tmp_path):
    """tar CVE-2026-18477 names 1.35 exactly and points at Red Hat errata: exposure
    with nothing to carry."""
    obs = _obs(tar=_pkg(current="1.35", latest="1.35",
                        cpeid="cpe:2.3:a:gnu:tar:1.35:-:*:*:*:*:*:*",
                        editions=["pengyos-core", "pengyos-server"]))
    nvd = _nvd(tmp_path, ("CVE-2026-18477", _rec(
        "gnu", "tar", "1.35", ["https://access.redhat.com/errata/RHSA-2026:49361"])))
    a = _by_id(_plan(tmp_path, obs, _st(_triaged("CVE-2026-18477", "tar")), nvd),
               "security-tar-1.35")
    assert not a["allowed"] and "fix_commits_available" in a["refused_by"]


def test_a_version_nvd_does_not_list_as_affected_is_refused(tmp_path):
    obs = _obs(libxml2=_pkg(current="2.15.4", latest="2.16.0",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.4:-:*:*:*:*:*:*"))
    nvd = _nvd(tmp_path, ("CVE-2026-86137", LIBXML2))
    a = _by_id(_plan(tmp_path, obs, _st(_triaged("CVE-2026-86137", "libxml2")), nvd),
               "security-libxml2-2.15.4")
    assert not a["allowed"] and "evidence_required" in a["refused_by"]


def test_a_series_jump_in_a_recipe_we_own_is_refused(tmp_path):
    """major_never: even where the pin is ours to move, a new series is not a nightly
    decision."""
    obs = _obs(**{"alsa-ucm-conf": _pkg(current="1.2.16", latest="2.0.0", tree="PENGYOS")})
    obs["findings"] = [{"lane": "routine", "component": "alsa-ucm-conf",
                        "current": "1.2.16", "available": "2.0.0", "note": "authored"}]
    a = _by_id(_plan(tmp_path, obs, _st(), _nvd(tmp_path)), "routine-alsa-ucm-conf-2.0.0")
    assert not a["allowed"] and "major_never" in a["refused_by"]


def test_the_kernel_is_report_only(tmp_path):
    obs = _obs(linux=_pkg(current="7.2.5", latest="7.2.9", editions=["pengyos-core"],
                          cpeid="cpe:2.3:a:linuxkernel:linux_kernel:7.2.5:-:*:*:*:*:*:*"))
    nvd = _nvd(tmp_path, ("CVE-2026-11111", _rec(
        "linuxkernel", "linux_kernel", "*",
        ["https://github.com/torvalds/linux/commit/abc1234"],
        versionEndExcluding="7.2.6")))
    a = _by_id(_plan(tmp_path, obs, _st(_triaged("CVE-2026-11111", "linux")), nvd),
               "security-linux-7.2.5")
    assert not a["allowed"] and "denied_component" in a["refused_by"]


def test_a_host_only_package_is_not_an_image_update(tmp_path):
    obs = _obs(make=_pkg(current="4.4", latest="4.5", host_only=True,
                         cpeid="cpe:2.3:a:gnu:make:4.4:-:*:*:*:*:*:*"))
    nvd = _nvd(tmp_path, ("CVE-2026-22222", _rec(
        "gnu", "make", "*", ["https://github.com/make/make/commit/abc1234"],
        versionEndExcluding="4.5")))
    a = _by_id(_plan(tmp_path, obs, _st(_triaged("CVE-2026-22222", "make")), nvd),
               "security-make-4.4")
    assert not a["allowed"] and "not_host_only" in a["refused_by"]


def test_unsure_matches_never_become_an_action(tmp_path):
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*",
                            unsure_cves=["CVE-2026-86137"]))
    nvd = _nvd(tmp_path, ("CVE-2026-86137", LIBXML2))
    a = _by_id(_plan(tmp_path, obs, _st(_triaged("CVE-2026-86137", "libxml2")), nvd),
               "security-libxml2-2.15.3")
    assert not a["allowed"] and "confirmed_only" in a["refused_by"]


def test_only_max_actions_per_night_and_the_rest_say_cap(tmp_path):
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*"),
               libpng=_pkg(current="1.6.40", latest="1.6.44",
                           cpeid="cpe:2.3:a:libpng:libpng:1.6.40:-:*:*:*:*:*:*",
                           editions=["pengyos-server"]))
    nvd = _nvd(tmp_path,
               ("CVE-2026-86137", dict(LIBXML2, references=[{"url": "https://github.com/x/y/commit/76fe08d9"}])),
               ("CVE-2026-33333", _rec("libpng", "libpng", "*",
                                       ["https://github.com/x/y/commit/abc12345"],
                                       versionEndExcluding="1.6.44")))
    plan = _plan(tmp_path, obs, _st(_triaged("CVE-2026-86137", "libxml2"),
                                    _triaged("CVE-2026-33333", "libpng")), nvd)
    assert len(plan["actions"]) == 1 and plan["actions"][0]["component"] == "libpng"
    assert _by_id(plan, "security-libxml2-2.15.3")["refused_by"] == ["cap"]


def test_cooldown_is_honoured_and_named(tmp_path):
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*"))
    nvd = _nvd(tmp_path, ("CVE-2026-86137", LIBXML2))
    st = _st(_triaged("CVE-2026-86137", "libxml2"))
    st["actions"] = {"libxml2": "2026-10-05"}
    a = _by_id(_plan(tmp_path, obs, st, nvd), "security-libxml2-2.15.3")
    assert not a["allowed"] and "cooldown" in a["refused_by"]


def test_a_disabled_lane_is_refused_by_name(tmp_path):
    obs = _obs(**{"alsa-ucm-conf": _pkg(current="1.2.16", latest="1.2.16.1", tree="PENGYOS")})
    obs["findings"] = [{"lane": "routine", "component": "alsa-ucm-conf",
                        "current": "1.2.16", "available": "1.2.16.1", "note": "authored"}]
    cfg = {"act_enabled": True, "push": True, "lanes": ["security"], "priorities": ["P0", "P1"],
           "max_actions": 1, "cooldown_days": 7, "allow_patch_shape": True,
           "skip_cosmic": True}
    a = _by_id(_plan(tmp_path, obs, _st(), _nvd(tmp_path), cfg),
               "routine-alsa-ucm-conf-1.2.16.1")
    assert not a["allowed"] and "lane_allowed" in a["refused_by"]
    assert "lane_disabled" in " ".join(a["refused_reason"])


def test_a_routine_bump_of_a_recipe_we_author_is_an_action(tmp_path):
    obs = _obs(**{"alsa-ucm-conf": _pkg(current="1.2.16", latest="1.2.16.1", tree="PENGYOS")})
    obs["findings"] = [{"lane": "routine", "component": "alsa-ucm-conf",
                        "current": "1.2.16", "available": "1.2.16.1", "note": "authored"}]
    a = _by_id(_plan(tmp_path, obs, _st(), _nvd(tmp_path)), "routine-alsa-ucm-conf-1.2.16.1")
    assert a["allowed"], [g for g in a["guards"] if not g["ok"]]
    assert a["shape"] == "recipe_bump"
    assert [e["path"] for e in a["edits"]] == [
        "buildroot-external/package/alsa-ucm-conf/alsa-ucm-conf.mk",
        "buildroot-external/package/alsa-ucm-conf/alsa-ucm-conf.hash"]
    assert a["edits"][0]["new"] == "ALSA_UCM_CONF_VERSION = 1.2.16.1"


def test_a_stock_package_in_the_routine_lane_is_refused_not_patched(tmp_path):
    """Routine means 'move the pin', and a stock pin is not ours to move."""
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*"))
    obs["findings"] = [{"lane": "routine", "component": "libxml2", "current": "2.15.3",
                        "available": "2.15.4", "note": "stock"}]
    a = _by_id(_plan(tmp_path, obs, _st(), _nvd(tmp_path)), "routine-libxml2-2.15.3")
    assert not a["allowed"] and "stock_version_immovable" in a["refused_by"]


def test_triaged_cves_outside_the_policy_priorities_are_not_candidates(tmp_path):
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*"))
    nvd = _nvd(tmp_path, ("CVE-2026-86137", LIBXML2))
    st = _st(_triaged("CVE-2026-86137", "libxml2", priority="P2"))
    plan = _plan(tmp_path, obs, st, nvd)
    assert plan["actions"] == [] and plan["refused"] == []


def test_resolved_matches_stop_being_candidates(tmp_path):
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*"))
    nvd = _nvd(tmp_path, ("CVE-2026-86137", LIBXML2))
    st = _st(_triaged("CVE-2026-86137", "libxml2", resolved="2026-10-05"))
    assert _plan(tmp_path, obs, st, nvd)["actions"] == []


def test_a_candidate_whose_patch_failed_is_refused_by_name(tmp_path):
    """podman on 2026-10-05: the source hunks applied, upstream's own test hunks did
    not. Without this the loop re-extracts podman in four editions every night and
    spends its one action slot on a candidate that cannot work."""
    obs = _obs(podman=_pkg(current="v5.7.0", latest="v5.7.0",
                           cpeid="cpe:2.3:a:redhat:podman:5.7.0:-:*:*:*:*:*:*",
                           editions=["pengyos-server"]))
    nvd = _nvd(tmp_path, ("CVE-2026-44444", _rec(
        "redhat", "podman", "*", ["https://github.com/containers/podman/commit/abc1234"],
        versionEndExcluding="v5.7.1")))
    st = _st(_triaged("CVE-2026-44444", "podman"))
    st["patch_failures"] = {"podman": {"date": "2026-10-05",
                                       "where": "test/e2e/run_working_dir_test.go"}}
    a = _by_id(_plan(tmp_path, obs, st, nvd), "security-podman-v5.7.0")
    assert not a["allowed"] and "patch_does_not_apply" in a["refused_by"]


def test_a_cve_already_carried_as_a_patch_is_not_a_candidate_again(tmp_path):
    """pkg-stats still flags libxml2 2.15.3 tomorrow, because the version moved nowhere.
    Without this the loop re-proposes the same patch the moment the cooldown expires."""
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*"))
    nvd = _nvd(tmp_path, ("CVE-2026-86137", LIBXML2))
    st = _st(_triaged("CVE-2026-86137", "libxml2"))
    st["cves"]["CVE-2026-86137|libxml2"]["patched_by"] = {
        "date": "2026-10-05", "version": "2.15.3", "release": "26.10.8",
        "patches": ["buildroot-external/patches/libxml2/2.15.3/0001-x.patch"]}
    plan = _plan(tmp_path, obs, st, nvd)
    assert plan["actions"] == [] and plan["refused"] == [], \
        "carried is not open, and not a candidate"


def test_the_plan_is_deterministic(tmp_path):
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*"))
    nvd = _nvd(tmp_path, ("CVE-2026-86137", LIBXML2))
    a = _plan(tmp_path, obs, _st(_triaged("CVE-2026-86137", "libxml2")), nvd)
    b = _plan(tmp_path, obs, _st(_triaged("CVE-2026-86137", "libxml2")), nvd)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# --- known-bad fixes: a filter, not a veto ------------------------------------
#
# `patch_does_not_apply` used to refuse a whole component if the candidate named any
# fix a pre-flight had proved does not apply — the same shape as the `fix_commits_available`
# veto fixed on 2026-10-06. Measured 2026-10-08 it was holding 11 carried ffmpeg patches
# and 6 gst1-plugins-good patches behind 8 and 1 bad fixes, and the batch that shipped
# carried 1 change instead of the 4 it was queued with. These fixtures are the ffmpeg
# shape: N fixes, some known-bad, and the plan must carry the rest.

_FFH = "https://code.ffmpeg.org/FFmpeg/FFmpeg/commit/"


def _fix_url(i: int) -> str:
    """A distinct, fetchable fix-commit URL (hex sha, on a host that serves .patch)."""
    return f"{_FFH}{i:07x}"


def _ffmpeg_obs(current="6.1.5"):
    return _obs(ffmpeg=_pkg(current=current, latest="7.0.2",
                            cpeid=f"cpe:2.3:a:ffmpeg:ffmpeg:{current}:-:*:*:*:*:*:*"))


def _ffmpeg_nvd(tmp_path, n):
    """n CVEs, each naming its own upstream fix, all affected at the pinned version."""
    return _nvd(tmp_path, *[(f"CVE-2026-{70000 + i}", _rec(
        "ffmpeg", "ffmpeg", "*", [_fix_url(i)], versionEndExcluding="6.1.7"))
        for i in range(1, n + 1)])


def _ffmpeg_st(n, bad=(), recorded="6.1.5"):
    """State for n ffmpeg CVEs, with `bad` recorded as known-bad against `recorded`."""
    st = _st(*[_triaged(f"CVE-2026-{70000 + i}", "ffmpeg") for i in range(1, n + 1)])
    st["patch_failures"] = {"ffmpeg": {"date": "2026-10-08", "where": "libavcodec/tiff.c",
                                       "version": recorded,
                                       "fixes": [_fix_url(i) for i in bad]}}
    return st


def test_a_known_bad_fix_drops_its_cve_not_the_component(tmp_path):
    """The unit of failure is a patch. One bad cherry-pick takes its CVE to `still_open`
    and leaves the component actionable for everything else."""
    obs, st, nvd = _ffmpeg_obs(), _ffmpeg_st(3, bad=(2,)), _ffmpeg_nvd(tmp_path, 3)
    a = _by_id(_plan(tmp_path, obs, st, nvd), "security-ffmpeg-6.1.5")
    assert a["allowed"], a["guards"]
    assert a["closes"] == ["CVE-2026-70001", "CVE-2026-70003"], "the claim is the kept set"
    assert a["still_open"] == ["CVE-2026-70002"], "filtering is not forgetting"
    assert len(a["patches"]) == 2
    s = a["skipped_known_bad"][0]
    assert (s["cve"], s["fix"]) == ("CVE-2026-70002", _fix_url(2))
    assert s["recorded"] == "2026-10-08" and "libavcodec/tiff.c" in s["failed_on"], s


def test_a_component_whose_only_fix_is_known_bad_is_still_refused(tmp_path):
    """The libsndfile shape, 2026-10-08: 1 candidate fix, 1 known-bad, 0 carriable.
    Refused, by this guard, and by exactly one guard."""
    obs = _obs(libsndfile=_pkg(current="1.2.2", latest="1.2.3",
                               cpeid="cpe:2.3:a:libsndfile:libsndfile:1.2.2:-:*:*:*:*:*:*"))
    fix = "https://github.com/libsndfile/libsndfile/commit/9a82911"
    nvd = _nvd(tmp_path, ("CVE-2026-05001", _rec("libsndfile", "libsndfile", "*", [fix],
                                                  versionEndExcluding="1.2.3")))
    st = _st(_triaged("CVE-2026-05001", "libsndfile"))
    st["patch_failures"] = {"libsndfile": {"date": "2026-10-08", "where": "src/ALAC/ag_dec.c",
                                           "version": "1.2.2", "fixes": [fix]}}
    a = _by_id(_plan(tmp_path, obs, st, nvd), "security-libsndfile-1.2.2")
    assert not a["allowed"] and "patch_does_not_apply" in a["refused_by"], a["guards"]
    assert "nothing carriable" in a["refused_reason"][0], a["refused_reason"]
    assert a["refused_by"] == ["patch_does_not_apply"], \
        "one guard owns the reason: NVD did name a fix, it is just already proved bad"
    assert a["skipped_known_bad"][0]["cve"] == "CVE-2026-05001", "named even when refused"


def test_a_legacy_failure_record_still_blocks_the_component(tmp_path):
    """The podman shape: a record from before per-fix tracking. It stays a component-wide
    block, with the wording a human already knows, rather than being un-blocked by accident."""
    obs = _obs(podman=_pkg(current="v5.7.0", latest="v5.7.0",
                           cpeid="cpe:2.3:a:redhat:podman:5.7.0:-:*:*:*:*:*:*",
                           editions=["pengyos-server"]))
    nvd = _nvd(tmp_path, ("CVE-2026-44444", _rec(
        "redhat", "podman", "*", ["https://github.com/containers/podman/commit/abc1234"],
        versionEndExcluding="v5.7.1")))
    st = _st(_triaged("CVE-2026-44444", "podman"))
    st["patch_failures"] = {"podman": {"date": "2026-10-05",
                                       "where": "test/e2e/run_working_dir_test.go"}}
    a = _by_id(_plan(tmp_path, obs, st, nvd), "security-podman-v5.7.0")
    assert not a["allowed"] and "patch_does_not_apply" in a["refused_by"]
    assert "before per-fix tracking" in a["refused_reason"][0], a["refused_reason"]
    assert a["skipped_known_bad"] == [], "no fix list to filter: the whole component is held"


def test_the_planner_never_fetches_a_known_bad_fix(tmp_path):
    """Buildroot has already answered this question; asking the network again costs a
    fetch and re-opens a decision the pre-flight made."""
    seen: list[str] = []

    def get(url):
        seen.append(url)
        return PATCH_TEXT.encode() if url.endswith(".patch") else b"a fake tarball"

    obs, st, nvd = _ffmpeg_obs(), _ffmpeg_st(3, bad=(2,)), _ffmpeg_nvd(tmp_path, 3)
    _plan_get(tmp_path, obs, st, nvd, get)
    assert _fix_url(2) + ".patch" not in seen, "the known-bad fix was fetched anyway"
    assert _fix_url(1) + ".patch" in seen and _fix_url(3) + ".patch" in seen, \
        "the kept fixes must still be fetched: seen = " + ", ".join(seen)


def test_a_known_bad_verdict_expires_when_the_pin_moves(tmp_path):
    """"Does not apply to ffmpeg 6.1.5" is a fact about 6.1.5. Recorded against a pin
    we no longer ship, it stops binding — and it still binds at the pin it named."""
    st = _ffmpeg_st(3, bad=(2,), recorded="6.1.5")
    nvd = _ffmpeg_nvd(tmp_path, 3)

    moved = _by_id(_plan(tmp_path, _ffmpeg_obs(current="6.1.6"), st, nvd),
                   "security-ffmpeg-6.1.6")
    assert moved["allowed"] and moved["skipped_known_bad"] == [], moved["guards"]
    assert len(moved["patches"]) == 3, "the fix is tryable again at the new pin"

    same = _by_id(_plan(tmp_path, _ffmpeg_obs(current="6.1.5"), st, nvd),
                  "security-ffmpeg-6.1.5")
    assert [s["cve"] for s in same["skipped_known_bad"]] == ["CVE-2026-70002"], \
        "the negative control: at the pin it was proved against, it still binds"


def test_the_real_ffmpeg_shape_19_fixes_8_known_bad_carries_11(tmp_path):
    """The regression this change exists to fix, stated exactly as measured on
    2026-10-08: 19 candidate fixes, 8 known-bad, 11 that passed pre-flight. Before the
    change the plan refused the component and shipped nothing."""
    obs, st, nvd = _ffmpeg_obs(), _ffmpeg_st(19, bad=tuple(range(1, 9))), _ffmpeg_nvd(tmp_path, 19)
    a = _by_id(_plan(tmp_path, obs, st, nvd), "security-ffmpeg-6.1.5")
    assert a["allowed"], [g for g in a["guards"] if not g["ok"]]
    assert len(a["patches"]) == 11 and len(a["closes"]) == 11
    assert len(a["skipped_known_bad"]) == 8
    assert len(a["still_open"]) == 8, "the dropped CVEs are still open, and still named"


# --- the agent the image ships: pengy -----------------------------------------

def _pengy_obs(available="1.11.6", lane="routine"):
    obs = _obs(pengy=_pkg(current="1.11.5", tree="PENGYOS", pkg_path="package/pengy",
                          mk_path="package/pengy/pengy.mk",
                          url="https://github.com/patw/PengyR",
                          editions=["pengyos-core", "pengyos-desktop", "pengyos-server"]))
    obs["findings"] = [{"lane": lane, "component": "pengy", "current": "1.11.5",
                        "available": available, "note": "PengyR release"}]
    return obs


def _pengy_repo(tmp_path):
    d = _repo(tmp_path) / "buildroot-external" / "package" / "pengy"
    d.mkdir(parents=True, exist_ok=True)
    for name in ("pengy.mk", "pengy.hash"):
        (d / name).write_text((FIX / "recipes" / name).read_text())


def _pengy_plan(tmp_path, obs, decisions=None, cfg=None):
    _pengy_repo(tmp_path)
    cfg = cfg or {"act_enabled": True, "push": True, "lanes": ["security", "routine"],
                  "priorities": ["P0", "P1"], "max_actions": 1, "cooldown_days": 7,
                  "allow_patch_shape": True, "skip_cosmic": True}
    return planner.build_plan(obs, _st(), cfg, TODAY, _repo(tmp_path), tmp_path,
                              lambda cve: {}, fake_get, decisions=decisions)


def test_the_pengy_finding_joins_the_package_entry_it_is_about(tmp_path):
    """The component key is the Buildroot package name, not the upstream repo's name.

    Measured on the live plan of 2026-10-11: the finding said 1.11.2 -> 1.11.5 and the
    plan refused it as `not_host_only: the package is in no configured edition` plus
    `nothing_newer: upstream has no newer release than the pin` — both computed from
    the empty fallback package, because `packages` is keyed `pengy` and the finding
    was keyed `pengyr`. A finding that joins nothing is refused for invented reasons.
    """
    a = _by_id(_pengy_plan(tmp_path, _pengy_obs()), "routine-pengy-1.11.6")
    assert a["current"] == "1.11.5", "the pin came from the package entry, not the fallback"
    assert a["editions"] == ["pengyos-core", "pengyos-desktop", "pengyos-server"]
    assert a["bump_kind"] == "routine", "1.11.5 -> 1.11.6 is a patch inside the pinned minor"


def test_a_pengy_patch_release_is_bumped_with_a_hash_the_act_lane_generates(tmp_path):
    """pengy was report-only until 2026-10-16 because its archive is the cargo-VENDORED
    tarball Buildroot generates (`pengy-<v>-cargo6.tar.gz`), not the SITE archive, and the
    planner cannot download that. The act lane generates and verifies exactly that kind of
    hash since 2026-10-12, so the plan edits only the version and leaves the hash to act.
    """
    a = _by_id(_pengy_plan(tmp_path, _pengy_obs()), "routine-pengy-1.11.6")
    assert a["allowed"], [g for g in a["guards"] if not g["ok"]]
    assert a["hash_generated"] and a["hash"]["sha256"] == ""
    assert [e["path"] for e in a["edits"]] == ["buildroot-external/package/pengy/pengy.mk"]
    assert a["edits"][0]["new"] == "PENGY_VERSION = 1.11.6"


# --- the operator's decisions (decisions.json, from the web page) -------------

def _decided(kind, component, decision, target):
    return {f"{kind}:{component}": {"kind": kind, "component": component,
                                    "decision": decision, "target": target,
                                    "at": "2026-10-16T12:00:00"}}


def test_a_new_series_waits_for_the_operator(tmp_path):
    plan = _pengy_plan(tmp_path, _pengy_obs("1.12.2", lane="major"))
    assert plan["actions"] == [] and plan["refused"] == [], \
        "a major finding is not even a candidate until someone approves it"


def test_an_approved_new_series_becomes_an_action(tmp_path):
    plan = _pengy_plan(tmp_path, _pengy_obs("1.12.2", lane="major"),
                       _decided("update", "pengy", "approved", "1.12.2"))
    a = _by_id(plan, "approved-pengy-1.12.2")
    assert a["allowed"], [g for g in a["guards"] if not g["ok"]]
    assert a["lane"] == "approved" and a["bump_kind"] == "major"
    assert a["edits"][0]["new"] == "PENGY_VERSION = 1.12.2"


def test_an_approval_covers_later_patch_releases_of_the_same_series(tmp_path):
    plan = _pengy_plan(tmp_path, _pengy_obs("1.12.3", lane="major"),
                       _decided("update", "pengy", "approved", "1.12.2"))
    assert _by_id(plan, "approved-pengy-1.12.3")["allowed"]


def test_an_approval_does_not_cover_the_next_series(tmp_path):
    plan = _pengy_plan(tmp_path, _pengy_obs("1.13.0", lane="major"),
                       _decided("update", "pengy", "approved", "1.12.2"))
    assert plan["actions"] == [] and plan["refused"] == []


def test_an_approval_does_not_lift_the_other_guards(tmp_path):
    """An approval stands in for the person the major lane waits for, nothing else."""
    obs = _obs(**{"nvidia-open": _pkg(current="610.57.04", tree="PENGYOS",
                                      editions=["pengyos-desktop-nvidia"])})
    obs["findings"] = [{"lane": "major", "component": "nvidia-open", "current": "610.57.04",
                        "available": "615.78.08", "note": "new driver branch"}]
    plan = _pengy_plan(tmp_path, obs, _decided("update", "nvidia-open", "approved", "615.78.08"))
    a = _by_id(plan, "approved-nvidia-open-615.78.08")
    assert not a["allowed"] and "denied_component" in a["refused_by"]


def test_a_denied_routine_bump_is_not_acted_on_and_says_who_stopped_it(tmp_path):
    plan = _pengy_plan(tmp_path, _pengy_obs("1.11.6"),
                       _decided("update", "pengy", "denied", "1.11.6"))
    assert plan["actions"] == []
    a = _by_id(plan, "routine-pengy-1.11.6")
    assert a["refused_by"] == ["operator_denied"] and "denied" in a["refused_reason"][0]


def test_a_held_security_action_is_not_acted_on(tmp_path):
    obs = _obs(libxml2=_pkg(current="2.15.3", latest="2.15.4",
                            cpeid="cpe:2.3:a:xmlsoft:libxml2:2.15.3:-:*:*:*:*:*:*"))
    nvd = _nvd(tmp_path, ("CVE-2026-86137", LIBXML2))
    st = _st(_triaged("CVE-2026-86137", "libxml2"))
    cfg = {"act_enabled": True, "push": True, "lanes": ["security", "routine"],
           "priorities": ["P0", "P1"], "max_actions": 1, "cooldown_days": 7,
           "allow_patch_shape": True, "skip_cosmic": True}
    plan = planner.build_plan(obs, st, cfg, TODAY, _repo(tmp_path), tmp_path,
                              lambda cve: sources.nvd_raw(nvd, cve), fake_get,
                              decisions=_decided("security", "libxml2", "denied", "2.15.3"))
    assert plan["actions"] == []
    assert _by_id(plan, "security-libxml2-2.15.3")["refused_by"] == ["operator_denied"]

# --- the artifact the recipe points at does not exist -------------------------

def _alsa_obs():
    obs = _obs(**{"alsa-ucm-conf": _pkg(
        current="1.2.16", latest="1.2.16.1", tree="PENGYOS",
        pkg_path="package/alsa-ucm-conf",
        mk_path="package/alsa-ucm-conf/alsa-ucm-conf.mk",
        url="https://github.com/alsa-project/alsa-ucm-conf")})
    obs["findings"] = [{"lane": "routine", "component": "alsa-ucm-conf",
                        "current": "1.2.16", "available": "1.2.16.1",
                        "note": "authored recipe"}]
    return obs


def _alsa_plan(tmp_path, get):
    return planner.build_plan(_alsa_obs(), _st(), {"act_enabled": True, "push": True,
                                                   "lanes": ["security", "routine"],
                                                   "priorities": ["P0", "P1"],
                                                   "max_actions": 1, "cooldown_days": 7,
                                                   "allow_patch_shape": True,
                                                   "skip_cosmic": True},
                              TODAY, _repo(tmp_path), tmp_path,
                              lambda cve: sources.nvd_raw(tmp_path / "nvd", cve), get)


def test_a_bump_whose_tarball_downloads_is_planned(tmp_path):
    """Positive control for the test below: same recipe, same target, working fetch."""
    a = _by_id(_alsa_plan(tmp_path, fake_get), "routine-alsa-ucm-conf-1.2.16.1")
    assert a["allowed"], [g for g in a["guards"] if not g["ok"]]


def test_an_unreachable_tarball_is_named_as_such_not_as_a_broken_recipe(tmp_path):
    """2026-10-19: three approved bumps were refused every night with
    `recipe_unreadable: could not read the recipe to edit: HTTP Error 404`. The recipes
    read fine - the URL the recipe builds for the NEW version is a 404, because
    release-monitoring names releases upstream never tagged. urllib's HTTPError is an
    OSError, so an unlabelled fetch failure landed in the recipe-read handler. The two
    need opposite answers (fix the tree vs. drop the finding), so the plan names them
    apart and records the URL it tried.
    """
    def dead(url):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    a = _by_id(_alsa_plan(tmp_path, dead), "routine-alsa-ucm-conf-1.2.16.1")
    assert not a["allowed"]
    assert a["refused_by"] == ["artifact_unreachable"], a["refused_reason"]
    assert "recipe_unreadable" not in a["refused_by"], "the tree is not the problem"
    assert a["unreachable"]["url"].endswith("alsa-ucm-conf-1.2.16.1.tar.gz"), a["unreachable"]
    assert "404" in a["refused_reason"][0]
