"""Observers against recorded upstream formats (fixtures/, captured 2026-10-03)."""
import json
from pathlib import Path

from pengyos_nightly import sources
from pengyos_nightly.versions import bump_kind

FIX = Path(__file__).parent / "fixtures"


def lanes(findings):
    return sorted((f["lane"], f["component"], f["available"]) for f in findings)


def test_bump_kind():
    assert bump_kind("7.2.5", "7.2.9") == "routine"
    assert bump_kind("1.2.16", "1.2.16.1") == "routine"
    assert bump_kind("1.22.1", "1.23.0") == "major"
    assert bump_kind("0.9.11", "1.3.1") == "major"
    assert bump_kind("epoch-1.9.0", "1.9.0") == "none"
    assert bump_kind("7.2.5", "6.19.14") == "none"        # release-monitoring lags
    assert bump_kind("2.44-36-g2d5421f", "2.44") == "none"  # git snapshot after 2.44
    assert bump_kind("7.2", "7.2-rc5") == "none"


def test_kernel_same_series_and_new_series():
    rel = json.loads((FIX / "kernel-releases.json").read_text())  # stable 7.2.9, mainline 7.3-rc5
    f = sources.kernel_findings(rel, "7.2.5")
    assert ("routine", "linux", "7.2.9") in lanes(f)
    assert not [x for x in f if x["lane"] == "major"]          # 7.3 is only an rc


def test_kernel_new_stable_series_and_eol():
    rel = {"releases": [{"moniker": "stable", "version": "7.3.1", "iseol": False},
                        {"moniker": "stable", "version": "7.2.12", "iseol": True}]}
    f = sources.kernel_findings(rel, "7.2.5")
    notes = " ".join(x["note"] for x in f)
    assert ("routine", "linux", "7.2.12") in lanes(f)
    assert "end of life" in notes and "new stable series Linux 7.3" in notes


def test_kernel_series_gone():
    rel = {"releases": [{"moniker": "stable", "version": "7.4.2", "iseol": False}]}
    notes = [x["note"] for x in sources.kernel_findings(rel, "7.2.5")]
    assert any("no longer listed" in n for n in notes)


def test_ls_remote_prefers_peeled_commit():
    tags = sources.parse_ls_remote((FIX / "ls-remote-cosmic-comp.txt").read_text())
    assert tags["epoch-1.8.0"] == "a55785993e8ef6aad38862cb1a9e1ccaad3c340d"
    assert not any(t.endswith("^{}") for t in tags)


def test_cosmic_behind_newest_epoch():
    tags = sources.parse_ls_remote((FIX / "ls-remote-cosmic-comp.txt").read_text())
    comps = [{"name": "cosmic-comp", "url": "u", "commit": "a55785993e8ef6aad38862cb1a9e1ccaad3c340d"}]
    f = sources.cosmic_findings(comps, {"cosmic-comp": tags})
    assert len(f) == 1 and f[0]["lane"] == "major" and f[0]["available"] == "epoch-1.9.0"
    assert f[0]["current"] == "epoch-1.8.0"
    older = {"name": "cosmic-bg", "url": "u", "commit": tags["epoch-1.0.10"]} if "epoch-1.0.10" in tags else None
    if older:
        f2 = sources.cosmic_findings(comps + [older, {**comps[0], "name": "x"}],
                                     {"cosmic-comp": tags, "cosmic-bg": tags, "x": tags})
        assert f2[0]["current"] == "epoch-1.8.0 (2 of 3 tagged repos)"
    comps[0]["commit"] = tags["epoch-1.9.0"]
    assert sources.cosmic_findings(comps, {"cosmic-comp": tags}) == []


def test_nvidia_new_branch_is_major():
    tags = sources.parse_ls_remote((FIX / "ls-remote-nvidia.txt").read_text())
    f = sources.nvidia_findings(tags, "595.104.02")
    assert f and f[0]["lane"] == "major" and f[0]["available"] == "615.71.09"
    assert "branch 615" in f[0]["note"]
    assert sources.nvidia_findings(tags, "615.71.09") == []


# --- PengyR: the agent the images ship (authored pin, own upstream) ---------

def test_pengyr_minor_bump_is_a_major_finding_from_the_real_tags():
    """Captured 2026-10-05 from github.com/patw/PengyR. The pin was 1.10.0 while
    1.11.0 and 1.11.1 were out — the exact drift this source exists to catch.

    The fixture is deliberately frozen, not refreshed. PengyR 1.11.2 landed three
    hours after this capture, and a test that re-derived its expectation from
    whatever upstream did last would change meaning every time a release ships.
    The point being tested is the comparison, so the capture stays put and the
    live repo is exercised by hand (see the note in spec/CHANGELOG)."""
    tags = sources.parse_ls_remote((FIX / "ls-remote-pengyr.txt").read_text())
    f = sources.pengyr_findings(tags, "1.10.0")
    assert lanes(f) == [("major", "pengy", "1.11.1")]
    assert "1.10 -> 1.11" in f[0]["note"]


def test_pengyr_patch_bump_is_routine():
    f = sources.pengyr_findings(["v1.11.0", "v1.11.1", "v1.10.0"], "1.11.0")
    assert lanes(f) == [("routine", "pengy", "1.11.1")]
    assert "patch" in f[0]["note"]


def test_pengyr_silent_when_the_pin_is_current():
    tags = sources.parse_ls_remote((FIX / "ls-remote-pengyr.txt").read_text())
    assert sources.pengyr_findings(tags, "1.11.1") == []


def test_pengyr_ignores_non_version_tags_and_prereleases():
    tags = ["v1.11.1", "v1.12.0-rc1", "epoch-1.9", "refs/heads/main", "latest"]
    f = sources.pengyr_findings(tags, "1.11.0")
    # The rc is not a release; 1.11.1 is the answer.
    assert lanes(f) == [("routine", "pengy", "1.11.1")]


def test_pengyr_handles_tags_without_the_v_prefix():
    """Early PengyR tags are bare numbers (1.0.0), later ones are v1.9.0."""
    tags = sources.parse_ls_remote((FIX / "ls-remote-pengyr.txt").read_text())
    assert "1.0.0" in tags
    f = sources.pengyr_findings(["1.0.0", "1.0.1", "v1.11.1"], "1.0.0")
    assert f and f[0]["available"] == "1.11.1"

def test_buildroot_no_point_release_yet():
    tags = sources.parse_ls_remote((FIX / "ls-remote-buildroot.txt").read_text())
    assert sources.buildroot_findings(tags, "2026.08") == []   # newest tag is 2026.08


def test_buildroot_point_and_lts():
    tags = ["2026.08", "2026.08.1", "2026.08.2", "2026.11", "2027.02", "2027.02-rc1"]
    f = sources.buildroot_findings(tags, "2026.08")
    assert ("routine", "buildroot", "2026.08.2") in lanes(f)
    major = [x for x in f if x["lane"] == "major"][0]
    assert major["available"] == "2027.02" and "long-term support: 2027.02" in major["note"]


def test_pkgstats_host_only_and_merge():
    core = sources.parse_pkgstats(json.loads((FIX / "pkgstats-core.json").read_text()), "pengyos-core")
    desk = sources.parse_pkgstats(json.loads((FIX / "pkgstats-desktop.json").read_text()), "pengyos-desktop")
    assert core["binutils"]["host_only"] is True
    assert core["gcc-final"]["host_only"] is False          # libgcc ships in the image
    assert core["busybox"]["cves"]
    merged = sources.merge_packages([core, desk])
    assert merged["busybox"]["editions"] == ["pengyos-core", "pengyos-desktop"]


def test_package_findings_only_authored_recipes():
    desk = sources.parse_pkgstats(json.loads((FIX / "pkgstats-desktop.json").read_text()), "pengyos-desktop")
    core = sources.parse_pkgstats(json.loads((FIX / "pkgstats-core.json").read_text()), "pengyos-core")
    f = lanes(sources.package_findings(sources.merge_packages([core, desk])))
    assert ("routine", "alsa-ucm-conf", "1.2.16.1") in f
    assert ("major", "bolt", "1.3.1") in f
    names = {c for _, c, _ in f}
    assert "cosmic-sound-theme" not in names and "oo7" not in names   # epoch prefix / beta
    assert "binutils" not in names and "linux" not in names            # Buildroot's own
    pk = {"nvidia-open": {"current": "595.104.02", "latest": "615.71.09", "tree": "PENGYOS"}}
    assert sources.package_findings(pk) == []                          # its own observer


def test_kev_epss_parse():
    kev = sources.parse_kev(json.loads((FIX / "kev.json").read_text()))
    assert "CVE-2021-44228" in kev
    epss = sources.parse_epss(json.loads((FIX / "epss.json").read_text()))
    assert epss["CVE-2021-44228"]["epss"] > 0.9


def test_fetch_epss_batches():
    calls = []

    def get(url):
        calls.append(url)
        ids = url.split("cve=")[1].split(",")
        return json.dumps({"data": [{"cve": c, "epss": "0.1", "percentile": "0.5"} for c in ids]})
    out = sources.fetch_epss([f"CVE-2026-{i}" for i in range(5)], get=get, batch=2)
    assert len(calls) == 3 and len(out) == 5


def test_nvd_record():
    r = sources.nvd_record(FIX / "nvd", "CVE-2026-38754")
    assert "ifsbreakup" in r["description"] and r["cvss"] == 5.1 and "AV:L" in r["vector"]
    assert sources.nvd_record(FIX / "nvd", "CVE-2026-1") == {}


def test_pins_from_repo_files():
    assert sources.defconfig_value('BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE="7.2.5"\n',
                                   "BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE") == "7.2.5"
    assert sources.mk_value("NVIDIA_OPEN_VERSION = 595.104.02\n", "NVIDIA_OPEN_VERSION") == "595.104.02"
    rows = sources.read_cosmic_components("# c\ncosmic-comp\thttps://x\tabc\tbuild\n")
    assert rows == [{"name": "cosmic-comp", "url": "https://x", "commit": "abc"}]


# --- uutils/findutils (package/uutils-findutils): no release-monitoring project ---

def test_uutils_findutils_silent_at_the_newest_real_tag():
    tags = sources.parse_ls_remote((FIX / "ls-remote-uutils-findutils.txt").read_text())
    assert "latest-commit" in tags            # the moving tag upstream keeps
    assert sources.uutils_findutils_findings(tags, "0.10.0") == []


def test_uutils_findutils_older_pin_from_the_real_tags():
    tags = sources.parse_ls_remote((FIX / "ls-remote-uutils-findutils.txt").read_text())
    (f,) = sources.uutils_findutils_findings(tags, "0.9.1")
    assert f["component"] == "uutils-findutils"   # the Buildroot package name
    assert (f["current"], f["available"], f["lane"]) == ("0.9.1", "0.10.0", "major")


def test_uutils_findutils_patch_release_is_routine():
    (f,) = sources.uutils_findutils_findings(["0.10.0", "0.10.1", "latest-commit"], "0.10.0")
    assert (f["available"], f["lane"]) == ("0.10.1", "routine")


def test_uutils_findutils_ignores_latest_commit_and_prereleases():
    assert sources.uutils_findutils_findings(["latest-commit", "0.11.0-rc1"], "0.10.0") == []
