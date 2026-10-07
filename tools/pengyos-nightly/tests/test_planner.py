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
from pathlib import Path

from pengyos_nightly import planner, sources

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
