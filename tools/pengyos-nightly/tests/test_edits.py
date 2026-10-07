"""Tests for the edit shapes and the recipe reading behind them.

The prefix and the download coordinates are read from the recipe rather than
derived from the package name, because Buildroot's own names do not match its
variables: the `gstreamer1` recipe assigns GSTREAMER_1_VERSION and its
sub-package `gst1-plugins-good` assigns GST1_PLUGINS_GOOD_VERSION. A name-derived
prefix reassigns a variable that nothing reads, and the build silently keeps the
old version.
"""

from pathlib import Path

import pytest

from pengyos_nightly import edits as E

FIX = Path(__file__).parent / "fixtures" / "recipes"
PATCH = (Path(__file__).parent / "fixtures" / "patches"
         / "0001-xmlregexp-prevent-out-of-bounds-read-in-nxt-macro.patch").read_text()


# --- reading a recipe --------------------------------------------------------

def test_the_prefix_comes_from_the_recipe_not_the_package_name():
    assert E.recipe_prefix((FIX / "gst1-plugins-good.mk").read_text(), "1.24.13") \
        == "GST1_PLUGINS_GOOD"
    assert E.recipe_prefix((FIX / "libxml2.mk").read_text(), "2.15.3") == "LIBXML2"
    assert E.recipe_prefix((FIX / "alsa-ucm-conf.mk").read_text(), "1.2.16") == "ALSA_UCM_CONF"


def test_a_recipe_with_no_version_assignment_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        E.recipe_prefix("FOO_SITE = https://example.com\n", "")


def test_continuations_and_nested_variables_expand():
    """LIBXML2_SITE is continued onto the next line and uses $(LIBXML2_VERSION_MAJOR)."""
    mk = (FIX / "libxml2.mk").read_text()
    coords = E.expand(E.read_recipe(mk, "LIBXML2"), "LIBXML2", "2.15.4", "libxml2")
    assert coords["SITE"] == "https://download.gnome.org/sources/libxml2/2.15"
    assert coords["SOURCE"] == "libxml2-2.15.4.tar.xz"


def test_the_github_macro_expands_to_a_real_url():
    mk = (FIX / "alsa-ucm-conf.mk").read_text()
    coords = E.expand(E.read_recipe(mk, "ALSA_UCM_CONF"), "ALSA_UCM_CONF", "1.2.16.1",
                      "alsa-ucm-conf")
    assert coords["SITE"] == ("https://github.com/alsa-project/alsa-ucm-conf"
                              "/archive/v1.2.16.1")
    assert coords["SOURCE"] == "alsa-ucm-conf-1.2.16.1.tar.gz"


# --- hashes ------------------------------------------------------------------

def test_upstream_published_checksum_is_used_when_the_recipe_names_one():
    hash_text = ("# From https://download.gnome.org/sources/libxml2/2.15/"
                 "libxml2-2.15.3.sha256sum\n"
                 "sha256  aaa  libxml2-2.15.3.tar.xz\n")
    got = E.upstream_hash(hash_text, "2.15.3", "2.15.4", "libxml2-2.15.4.tar.xz",
                          get=lambda url: b"98087fd181d9070724f3fbc65c7377db03038eb92bd882374daff44940138821  libxml2-2.15.4.tar.xz\n")
    assert got.sha256 == "98087fd181d9070724f3fbc65c7377db03038eb92bd882374daff44940138821"
    assert got.source == "upstream .sha256sum"
    assert got.url.endswith("libxml2-2.15.4.sha256sum")


def test_a_hash_computed_here_is_labelled_self_attested():
    got = E.download_hash("https://example.com/x.tar.gz", "x.tar.gz", get=lambda u: b"hello")
    assert got.sha256 == E.sha256_of(b"hello")
    assert got.source == "self-attested (downloaded here)"


def test_license_hashes_travel_with_the_tarball_hash():
    hash_text = ("sha256  aaa  alsa-ucm-conf-1.2.16.tar.gz\n"
                 "sha256  bbb  LICENSE\n")
    assert E.carry_license_hashes(hash_text, "1.2.16") == ["sha256  bbb  LICENSE"]


# --- patch naming ------------------------------------------------------------

def test_patch_name_drops_the_git_subject_brackets():
    slug = E.patch_slug(PATCH, "76fe08d97de88bfaef2f7d5cd27f11954cc5bee2")
    assert slug == "xmlregexp-prevent-out-of-bounds-read-in-nxt-macro"


def test_patch_url_is_only_offered_for_hosts_that_serve_one():
    assert E.patch_url("https://github.com/GNOME/libxml2/commit/76fe08d").endswith(
        "76fe08d.patch")
    assert E.patch_url("https://gitlab.gnome.org/GNOME/libxml2/-/commit/c2e233f").endswith(
        "c2e233f.patch")
    assert E.patch_url("https://example.org/commit/abc") is None
    assert E.patch_url("") is None


def test_patch_url_knows_ffmpeg_gitlab_and_a_review_request():
    """16 of the ffmpeg fix references live on code.ffmpeg.org, which the old host
    list silently could not fetch — the guard allowed the action and fetch_patch
    would have raised on it."""
    assert E.patch_url("https://code.ffmpeg.org/FFmpeg/FFmpeg/commit/dbd495f0").endswith(
        "dbd495f0.patch")
    assert E.patch_url("https://code.ffmpeg.org/FFmpeg/FFmpeg/pulls/22348").endswith(
        "pulls/22348.patch")
    assert E.patch_url("https://github.com/o/r/pull/42").endswith("pull/42.patch")


def test_patch_url_passes_a_patch_form_through_instead_of_doubling_it():
    """GStreamer's advisory references already end in `.patch`."""
    url = ("https://gitlab.freedesktop.org/gstreamer/gstreamer"
           "/-/merge_requests/11243.patch")
    assert E.patch_url(url) == url


def test_patch_sha_reads_the_commit_a_format_patch_came_from():
    """A fix NVD named only as a pull request still ends up recorded against a sha."""
    assert E.patch_sha(PATCH) == "76fe08d97de88bfaef2f7d5cd27f11954cc5bee2"
    assert E.patch_sha("not a format-patch") == ""


# --- which CVEs a patch set actually closes ----------------------------------

def test_two_cves_fixed_by_one_commit_are_one_patch_and_two_closed():
    cves = [{"cve": "CVE-2026-86143", "fix_commit": {"url": "https://github.com/o/r/commit/abc",
                                                     "sha": "abc"}},
            {"cve": "CVE-2026-86137", "fix_commit": {"url": "https://github.com/o/r/commit/abc",
                                                     "sha": "abc"}}]
    groups = E.carried_patches(cves)
    assert len(groups) == 1
    assert groups[0]["cves"] == ["CVE-2026-86137", "CVE-2026-86143"]
    assert E.carried_cves(cves) == ["CVE-2026-86137", "CVE-2026-86143"], \
        "deduplicating the patch must not shrink the claim"


def test_a_cve_with_nothing_fetchable_is_carried_by_nothing():
    cves = [{"cve": "CVE-2026-18477", "fix_commit": None},
            {"cve": "CVE-2026-8631", "fix_commit": {"url": "https://support.hp.com/doc",
                                                    "sha": "", "host": "support.hp.com"}},
            {"cve": "CVE-2026-86137", "fix_commit": {"url": "https://github.com/GNOME/libxml2"
                                                      "/commit/76fe08d", "sha": "76fe08d"}}]
    assert E.carried_cves(cves) == ["CVE-2026-86137"]
    assert len(E.carried_patches(cves)) == 1


def test_patch_dir_carries_a_review_request_and_names_the_commit_it_came_from(tmp_path):
    """The ffmpeg case: NVD named a pull request, the host serves it as a patch, and
    the patch's own `From` line supplies the commit the notes cite."""
    cves = [{"cve": "CVE-2026-40962", "priority": "P1", "affected": True,
             "fix_commit": {"url": "https://code.ffmpeg.org/FFmpeg/FFmpeg/pulls/22348",
                            "sha": "", "host": "code.ffmpeg.org", "kind": "pull"}}]
    edits, _ = E.patch_dir(tmp_path, "ffmpeg", "6.1.5", cves, ["pengyos-desktop"],
                           get=lambda url: PATCH.encode())
    patch = next(e for e in edits if e.path.endswith(".patch"))
    assert patch.path.startswith("buildroot-external/patches/ffmpeg/6.1.5/0001-")
    assert "CVE-2026-40962" in patch.note and "pull" in patch.note
    assert "76fe08d97de8" in patch.note, "the sha read back out of the fetched patch"


def test_split_patches_separates_a_review_request_series(tmp_path):
    """A GitLab MR / GitHub PR `.patch` is the whole series concatenated, and Buildroot
    pipes one file to one `patch` invocation. Measured 2026-10-07: gstreamer's MR 11243
    came back as 4 patches in one file, which is what the `|-- |GitLab` garbage in a real
    `gstreamer1-patch` failure was."""
    second = PATCH.replace("76fe08d97de88bfaef2f7d5cd27f11954cc5bee2",
                           "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    parts = E.split_patches(PATCH + second)
    assert len(parts) == 2
    assert all(p.startswith("From ") for p in parts)
    assert E.patch_sha(parts[0]) == "76fe08d97de88bfaef2f7d5cd27f11954cc5bee2"
    assert E.patch_sha(parts[1]) == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    assert len(E.split_patches(PATCH)) == 1, "a single commit is still one patch"
    assert E.split_patches("") == []


def test_a_response_that_is_not_a_format_patch_is_refused(tmp_path):
    """An HTML page must not become a .patch file in the tree."""
    with pytest.raises(ValueError, match="not a git format-patch"):
        E.fetch_patches("https://gitlab.freedesktop.org/gstreamer/gstreamer/-/commit/abc",
                        get=lambda url: b"<!DOCTYPE html><html><body>GitLab</body></html>")


def test_patch_dir_writes_one_file_per_commit_in_a_review_request(tmp_path):
    cves = [{"cve": "CVE-2026-46470", "priority": "P1", "affected": True,
             "fix_commit": {"url": "https://gitlab.freedesktop.org/gstreamer/gstreamer"
                                    "/-/merge_requests/11243", "sha": "",
                            "host": "gitlab.freedesktop.org", "kind": "pull"}}]
    second = PATCH.replace("76fe08d97de88bfaef2f7d5cd27f11954cc5bee2",
                           "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    edits, _ = E.patch_dir(tmp_path, "gst1-plugins-good", "1.24.13", cves,
                           ["pengyos-desktop"], get=lambda url: (PATCH + second).encode())
    patches = [e for e in edits if e.path.endswith(".patch")]
    assert [Path(e.path).name[:4] for e in patches] == ["0001", "0002"], \
        [e.path for e in patches]
    assert len({Path(e.path).name for e in patches}) == 2, "two files, not one"
    assert all(e.cves == ["CVE-2026-46470"] for e in patches), \
        "both commits of the request close the same CVE, so both are claimed by it"
    assert E.patch_sha(patches[1].new)[:12] in patches[1].note, \
        "each file names the commit it actually came from, not just the request"


def test_patch_dir_records_which_cves_each_patch_closes(tmp_path):
    """The act lane re-computes its claim after pre-flight, and it can only do that if
    each edit says which CVEs it closes and which upstream fix it came from."""
    cves = [{"cve": "CVE-2026-86137", "priority": "P1", "affected": True,
             "fix_commit": {"url": "https://github.com/GNOME/libxml2/commit/76fe08d9",
                            "sha": "76fe08d9", "host": "github.com", "kind": "commit"}}]
    edits, _ = E.patch_dir(tmp_path, "libxml2", "2.15.3", cves, ["pengyos-desktop"],
                           get=lambda url: PATCH.encode())
    patch = next(e for e in edits if e.path.endswith(".patch"))
    assert patch.cves == ["CVE-2026-86137"]
    assert patch.fix_url == "https://github.com/GNOME/libxml2/commit/76fe08d9"


def test_patch_dir_carries_one_numbered_patch_per_distinct_commit(tmp_path):
    def get(url):
        return PATCH.encode()
    cves = [{"cve": "CVE-2026-86137", "priority": "P1",
             "fix_commit": {"url": "https://github.com/GNOME/libxml2/commit/76fe08d",
                            "sha": "76fe08d", "host": "github.com"}},
            {"cve": "CVE-2026-86143", "priority": "P1",
             "fix_commit": {"url": "https://github.com/GNOME/libxml2/commit/90f293b",
                            "sha": "90f293b", "host": "github.com"}},
            {"cve": "CVE-2026-99999", "priority": "P1",
             "fix_commit": {"url": "https://github.com/GNOME/libxml2/commit/76fe08d",
                            "sha": "76fe08d", "host": "github.com"}}]
    edits, _ = E.patch_dir(tmp_path, "libxml2", "2.15.3", cves, ["pengyos-desktop"], get=get)
    paths = [e.path for e in edits if e.path.endswith(".patch")]
    assert paths == ["buildroot-external/patches/libxml2/2.15.3/"
                     "0001-xmlregexp-prevent-out-of-bounds-read-in-nxt-macro.patch",
                     "buildroot-external/patches/libxml2/2.15.3/"
                     "0002-xmlregexp-prevent-out-of-bounds-read-in-nxt-macro.patch"], \
        "two CVEs, two commits, numbered; the duplicate commit is not carried twice"
    assert edits[-1].kind == "ensure" and "BR2_GLOBAL_PATCH_DIR" in edits[-1].new


# --- is this patch a fix for THIS package? (real upstream fixtures) ----------
# The three fixtures below were captured from the live plan on 2026-10-07, not invented:
# the advisory commit GStreamer publishes for CVE-2026-3086, the gst-plugins-ugly fix NVD
# matched against our gstreamer1, and the 4-commit qtdemux series for gst-plugins-good.

PATCHES = Path(__file__).parent / "fixtures" / "patches"
ADVISORY = (PATCHES / "advisory-sa-2026-0009.patch").read_text()
OTHER_COMPONENT = (PATCHES / "monorepo-asfdemux-other-component.patch").read_text()
MONOREPO_SERIES = (PATCHES / "monorepo-qtdemux-series.patch").read_text()


def test_patch_paths_and_components_read_the_patch_itself():
    assert E.patch_components(PATCH) == set(), "libxml2 patches are not monorepo-shaped"
    assert E.patch_components(ADVISORY) == set()
    assert E.patch_components(OTHER_COMPONENT) == {"gst-plugins-ugly"}
    assert E.patch_components(MONOREPO_SERIES) == {"gst-plugins-good"}
    assert E.patch_subject(ADVISORY).startswith("security-advisories")


def test_an_advisory_commit_is_not_a_fix():
    """GStreamer publishes the advisory as its own commit and NVD links it as the fix.
    Carrying it adds markdown to the source tree and closes nothing - which is how seven
    of gstreamer1's ten 'fixes' would have claimed eight CVEs in the release notes."""
    v = E.classify_patch(ADVISORY, "gstreamer")
    assert not v["ok"] and "adds new files only" in v["reason"], v


def test_a_fix_for_a_sibling_component_is_not_a_fix_for_this_package():
    """NVD's CPE product is `gstreamer`, so a gst-plugins-ugly fix is matched against our
    gstreamer1. Those files are not in gstreamer's tarball: the patch can only fail."""
    v = E.classify_patch(OTHER_COMPONENT, "gstreamer")
    assert not v["ok"] and "gst-plugins-ugly" in v["reason"], v
    assert E.classify_patch(OTHER_COMPONENT, "gst-plugins-ugly")["ok"], \
        "the same patch is a real fix for the package that owns those files"


def test_a_monorepo_fix_for_this_component_is_re_rooted():
    v = E.classify_patch(next(iter(E.split_patches(MONOREPO_SERIES))), "gst-plugins-good")
    assert v["ok"] and v["prefix"] == "subprojects/gst-plugins-good/", v


def test_reroot_patch_moves_the_paths_and_leaves_the_hunks_alone():
    out = E.reroot_patch(OTHER_COMPONENT, "subprojects/gst-plugins-ugly/")
    assert "--- a/gst/asfdemux/gstasfdemux.c" in out
    assert not [l for l in out.splitlines()
                if l.startswith(E._PATH_LINES) and "subprojects/" in l]
    changed = [l for l in out.splitlines()
               if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
    assert changed == [l for l in OTHER_COMPONENT.splitlines()
                       if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))], \
        "the change itself is upstream's, unchanged"
    assert E.reroot_patch(PATCH, "") is PATCH, "a patch with no prefix is left alone"


def test_patch_dir_refuses_an_advisory_commit_and_says_why(tmp_path):
    cves = [{"cve": "CVE-2026-3086", "priority": "P1", "affected": True,
             "fix_commit": {"url": "https://gitlab.freedesktop.org/gstreamer/gstreamer"
                                    "/-/commit/025d59cf3459", "sha": "025d59cf3459",
                            "host": "gitlab.freedesktop.org", "kind": "commit"}}]
    with pytest.raises(E.NoCarriableFix) as exc:
        E.patch_dir(tmp_path, "gstreamer1", "1.24.13", cves, ["pengyos-desktop"],
                    get=lambda url: ADVISORY.encode(), upstream="gstreamer")
    assert exc.value.refused[0]["cves"] == ["CVE-2026-3086"]
    assert "adds new files only" in exc.value.refused[0]["reason"]


def test_patch_dir_writes_the_re_rooted_patch_not_the_monorepo_form(tmp_path):
    cves = [{"cve": "CVE-2026-46469", "priority": "P1", "affected": True,
             "fix_commit": {"url": "https://gitlab.freedesktop.org/gstreamer/gstreamer"
                                    "/-/merge_requests/11243", "sha": "",
                            "host": "gitlab.freedesktop.org", "kind": "pull"}}]
    edits, refused = E.patch_dir(tmp_path, "gst1-plugins-good", "1.24.13", cves,
                                 ["pengyos-desktop"], get=lambda url: MONOREPO_SERIES.encode(),
                                 upstream="gst-plugins-good")
    patches = [e for e in edits if e.path.endswith(".patch")]
    assert len(patches) == 4, "the request's four commits, one file each"
    assert not refused
    for e in patches:
        assert "--- a/gst/isomp4/qtdemux.c" in e.new
        assert "subprojects/" not in "\n".join(
            l for l in e.new.splitlines() if l.startswith(E._PATH_LINES))
        assert "re-rooted" in e.note
        assert e.cves == ["CVE-2026-46469"]


def test_patch_dir_refuses_when_there_is_nothing_to_carry(tmp_path):
    with pytest.raises(ValueError):
        E.patch_dir(tmp_path, "libxml2", "2.15.3", [], ["pengyos-desktop"],
                    get=lambda u: b"")


# --- applying ----------------------------------------------------------------

def test_apply_edits_creates_replaces_and_is_idempotent(tmp_path):
    (tmp_path / "a").write_text("VERSION = 1.0\n")
    E.apply_edits(tmp_path, [
        E.Edit(path="a", kind="replace", old="VERSION = 1.0", new="VERSION = 1.1"),
        E.Edit(path="b/c.patch", kind="create", new="patch\n"),
        E.Edit(path="layer", kind="ensure", new="BR2_GLOBAL_PATCH_DIR=\"x\""),
    ])
    assert (tmp_path / "a").read_text() == "VERSION = 1.1\n"
    assert (tmp_path / "b/c.patch").read_text() == "patch\n"
    E.apply_edits(tmp_path, [E.Edit(path="layer", kind="ensure", new="BR2_GLOBAL_PATCH_DIR=\"x\"")])
    assert (tmp_path / "layer").read_text().count("BR2_GLOBAL_PATCH_DIR") == 1, \
        "a second stock action must not duplicate the layer line"


def test_a_replace_that_does_not_match_is_an_error(tmp_path):
    """Silently not applying an edit is the failure mode that would publish an
    image with the old pin still in it."""
    (tmp_path / "a").write_text("SOMETHING_ELSE = 1\n")
    with pytest.raises(ValueError, match="did not find the line"):
        E.apply_edits(tmp_path, [E.Edit(path="a", kind="replace", old="VERSION = 1.0",
                                        new="VERSION = 1.1")])
