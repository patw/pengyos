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


def test_a_404_on_the_target_artifact_is_named_as_an_unreachable_artifact():
    """urllib's HTTPError is an OSError. Left unlabelled it arrives at the planner's
    recipe-read handler and is reported as "could not read the recipe to edit", which is
    a tree defect pointing away from the real cause: the release does not exist upstream.
    Measured 2026-10-19 on bolt 1.3.1 and rtkit 0.14 (no such upstream tag) and on
    libqalculate 5.13.2 (the release ships only Windows assets).
    """
    import urllib.error

    def dead(url):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    with pytest.raises(E.ArtifactUnreachable) as exc:
        E.download_hash("https://github.com/heftig/rtkit/archive/refs/tags/v0.14.tar.gz",
                        "v0.14.tar.gz", get=dead)
    assert exc.value.url.endswith("v0.14.tar.gz")
    assert exc.value.filename == "v0.14.tar.gz"
    assert "404" in str(exc.value)


def test_license_hashes_travel_with_the_tarball_hash():
    hash_text = ("sha256  aaa  alsa-ucm-conf-1.2.16.tar.gz\n"
                 "sha256  bbb  LICENSE\n")
    assert E.carry_license_hashes(hash_text, "1.2.16") == ["sha256  bbb  LICENSE"]


def test_the_hash_anchor_is_a_real_slice_of_the_file():
    """uutils-coreutils documents its hash in a comment paragraph - the cargo re-pack
    note - and only two of those lines name the version. Joining the matching lines
    produced an anchor with the paragraph's middle lines missing, i.e. text that does
    not occur in the file, and `apply_edits` raised: the act lane bailed the whole
    batch on 2026-10-16 and in rehearsal on 2026-10-19, and the message blamed the
    recipe. Fixture is the real .hash, copied from the tree on 2026-10-19.
    """
    mk = (FIX / "uutils-coreutils.mk").read_text()
    hash_text = (FIX / "uutils-coreutils.hash").read_text()
    edits, hi = E.recipe_bump(Path("/repo"), "uutils-coreutils", mk, hash_text, "0.12.0",
                              get=lambda url: b"tarball bytes")
    hash_edit = next(e for e in edits if e.path.endswith(".hash"))
    assert hash_edit.old in hash_text, "the anchor must be a real slice of the file"
    assert "coreutils-0.11.0.tar.gz" in hash_edit.old
    assert "raw upstream one" in hash_edit.old, "the paragraph about that hash travels"

    # The replacement leaves exactly one LICENSE line: the extras are the licence
    # hashes, and the anchor must not stop short of the tarball line they pair with.
    updated = hash_text.replace(hash_edit.old, hash_edit.new, 1)
    assert updated.count("LICENSE") == 1, updated
    assert "coreutils-0.11.0" not in updated
    assert "coreutils-0.12.0.tar.gz" in updated


def test_a_bump_does_not_write_the_licence_hash_twice():
    """The plain shape, as the control for the paragraph case above.

    `_hash_block` re-emits the licence lines, so the anchor has to consume them; an
    anchor that stops at the tarball line leaves the old line in place and the new block
    adds another. That is not hypothetical: alsa-ucm-conf and libmtp each carry two
    identical LICENSE / COPYING lines in the tree, written by the loop's own bumps on
    2026-10-05 and 2026-10-06.
    """
    mk = (FIX / "alsa-ucm-conf.mk").read_text()
    hash_text = (FIX / "alsa-ucm-conf.hash").read_text()
    edits, hi = E.recipe_bump(Path("/repo"), "alsa-ucm-conf", mk, hash_text, "1.2.16.1",
                              get=lambda url: b"tarball bytes")
    hash_edit = next(e for e in edits if e.path.endswith(".hash"))
    updated = hash_text.replace(hash_edit.old, hash_edit.new, 1)
    assert updated.count("LICENSE") == 1, updated
    assert updated.count("  alsa-ucm-conf-1.2.16.1.tar.gz") == 1, \
        "one hash line (the `# From` comment naming the URL is not a hash line)"
    assert "1.2.16.tar.gz" not in updated


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


# --- the tree move (phase 4) -------------------------------------------------

def test_tree_move_moves_every_edition_and_names_the_retirement_check(tmp_path):
    repo = tmp_path
    (repo / "targets.toml").write_text(
        '[[targets]]\nname = "pengyos-desktop"\nbr_version = "2026.08"\n'
        '[[targets]]\nname = "pengyos-server"\nbr_version = "2026.08"\n')
    edits, = (E.tree_move(repo, "2026.11"),)
    edit = edits[0]
    assert edit.path == "targets.toml" and edit.kind == "replace"
    assert edit.new.count('br_version = "2026.11"') == 2
    assert "2026.08" not in edit.new
    assert "retired if that line already carries the fix" in edit.note


def test_tree_move_refuses_a_targets_file_that_names_no_line(tmp_path):
    (tmp_path / "targets.toml").write_text('[[targets]]\nname = "pengyos-desktop"\n')
    with pytest.raises(ValueError, match="no br_version"):
        E.tree_move(tmp_path, "2026.11")


def test_the_user_agent_names_the_version_actually_running():
    """Upstream sees this string on every fetch. It read 0.4 in edits.py and 0.1 in
    sources.py while the tool was 0.8.0 — a claim about ourselves that was simply false."""
    from pengyos_nightly import __version__, sources as S
    assert E.UA == f"pengyos-nightly/{__version__} (+https://pengyos.catbee.ca)", E.UA
    assert S.USER_AGENT == f"pengyos-nightly/{__version__} (+https://pengyos.catbee.ca)", \
        S.USER_AGENT


# --- declaring a carried fix to pkg-stats ------------------------------------

CARRIED = [{"path": "buildroot-external/patches/libxml2/2.15.3/0001-a.patch",
            "cves": ["CVE-2026-86137"], "fix_url": "u1"},
           {"path": "buildroot-external/patches/libxml2/2.15.3/0002-b.patch",
            "cves": ["CVE-2026-86139", "CVE-2026-86138"], "fix_url": "u2"}]


def _decl_repo(tmp_path, external_mk="# overrides\n"):
    (tmp_path / "buildroot-external" / "patches").mkdir(parents=True, exist_ok=True)
    (tmp_path / "buildroot-external" / "external.mk").write_text(external_mk)
    return tmp_path


def test_a_carried_patch_is_declared_with_a_comment_naming_the_file(tmp_path):
    """Buildroot's convention: a comment naming the patch before each entry. pkg-stats
    cannot see a BR2_GLOBAL_PATCH_DIR patch, so this is the only way the tree can say
    the fix is carried."""
    repo = _decl_repo(tmp_path)
    e = E.cve_declaration_edit(repo, "libxml2", CARRIED)
    assert e is not None and e.kind == "create" and e.path == E.CVE_DECLARATIONS
    for line in ("# 0001-a.patch", "LIBXML2_IGNORE_CVES += CVE-2026-86137",
                 "# 0002-b.patch", "LIBXML2_IGNORE_CVES += CVE-2026-86138 CVE-2026-86139"):
        assert line in e.new, e.new
    E.apply_edits(repo, [e])
    assert "LIBXML2_IGNORE_CVES += CVE-2026-86137" in (repo / E.CVE_DECLARATIONS).read_text()


def test_the_block_is_regenerated_from_what_survived_not_appended(tmp_path):
    """Twice the same carried set must not double the block, and a fix Buildroot
    rejected must lose its declaration: an ignore for a fix that did not land would
    hide a real exposure."""
    repo = _decl_repo(tmp_path)
    E.apply_edits(repo, [E.cve_declaration_edit(repo, "libxml2", CARRIED)])
    assert E.cve_declaration_edit(repo, "libxml2", CARRIED) is None, "idempotent"

    survived = [c for c in CARRIED if c["path"].endswith("0002-b.patch")]
    e = E.cve_declaration_edit(repo, "libxml2", survived)
    assert e is not None and e.kind == "replace"
    E.apply_edits(repo, [e])
    text = (repo / E.CVE_DECLARATIONS).read_text()
    assert "CVE-2026-86137" not in text, "the dropped patch's CVE is gone"
    assert "CVE-2026-86138" in text and "CVE-2026-86139" in text
    assert text.count(">>> libxml2") == 1, text


def test_a_package_left_with_nothing_to_declare_loses_its_block(tmp_path):
    repo = _decl_repo(tmp_path)
    E.apply_edits(repo, [E.cve_declaration_edit(repo, "libxml2", CARRIED)])
    e = E.cve_declaration_edit(repo, "libxml2", [])
    assert e is not None and "retire" in e.note, e
    E.apply_edits(repo, [e])
    assert "LIBXML2_IGNORE_CVES" not in (repo / E.CVE_DECLARATIONS).read_text()


def test_a_hand_declaration_is_left_where_it_is(tmp_path):
    """external.mk and the generated file are different things. A CVE a human declared
    is not duplicated into the generated block; the rest still lands."""
    repo = _decl_repo(tmp_path, "LIBXML2_IGNORE_CVES += CVE-2026-86137\n")
    e = E.cve_declaration_edit(repo, "libxml2", CARRIED)
    assert "CVE-2026-86137" not in e.new, e.new
    assert "CVE-2026-86138" in e.new and "CVE-2026-86139" in e.new


def test_dashed_component_uses_buildroots_variable_prefix(tmp_path):
    repo = _decl_repo(tmp_path)
    carried = [{"path": "buildroot-external/patches/gst1-plugins-good/1.24.13/0001-q.patch",
                "cves": ["CVE-2026-73433"]}]
    e = E.cve_declaration_edit(repo, "gst1-plugins-good", carried)
    assert "GST1_PLUGINS_GOOD_IGNORE_CVES += CVE-2026-73433" in e.new, e.new


def test_declared_cves_sees_the_generated_file_and_the_hand_overrides(tmp_path):
    repo = _decl_repo(tmp_path, "PODMAN_IGNORE_CVES += CVE-2026-33414\n")
    E.apply_edits(repo, [E.cve_declaration_edit(repo, "libxml2", CARRIED)])
    got = E.declared_cves(repo)
    assert got["LIBXML2"] == {"CVE-2026-86137", "CVE-2026-86138", "CVE-2026-86139"}
    assert got["PODMAN"] == {"CVE-2026-33414"}


def test_carried_patch_files_lists_the_loops_own_security_maintenance(tmp_path):
    root = tmp_path / "buildroot-external" / "patches"
    (root / "libxml2" / "2.15.3").mkdir(parents=True)
    (root / "ffmpeg" / "6.1.5").mkdir(parents=True)
    (root / "libxml2" / "2.15.3" / "0001-a.patch").write_text("x")
    (root / "ffmpeg" / "6.1.5" / "0002-b.patch").write_text("x")
    (root / "README").write_text("not a patch")
    assert E.carried_patch_files(tmp_path) == [
        "buildroot-external/patches/ffmpeg/6.1.5/0002-b.patch",
        "buildroot-external/patches/libxml2/2.15.3/0001-a.patch"]
    assert E.carried_patch_files(tmp_path / "nowhere") == []


# --- the archive Buildroot builds itself (2026.10.14) -------------------------
# A golang/cargo recipe is not downloaded: Buildroot clones the repo and re-tars it, and
# names the result with its own suffix (pkg-utils.mk:51 pkg_source_ext, pkg-download.mk:12-15
# -git4/-svn5/-go2/-cargo6, pkg-golang.mk:101 go, pkg-cargo.mk:190 cargo). A hash keyed to
# `<name>-<version>.tar.gz` names a file the build never produces: check-hash exits 3, the
# download step dies, and the component is blacklisted. That is how fzf 0.74.5 was lost on
# 2026-10-12. No fixture on golang/cargo infra existed before this, which is why it survived.

def test_a_golang_recipe_names_the_archive_buildroot_actually_builds():
    """The positive control: the suffix Buildroot adds is part of the hash key."""
    mk = (FIX / "fzf.mk").read_text()
    coords = E.expand(E.read_recipe(mk, "FZF"), "FZF", "0.74.5", "fzf", mk)
    assert coords["SOURCE"] == "fzf-0.74.5-go2.tar.gz", coords["SOURCE"]
    mk = (FIX / "starship.mk").read_text()
    coords = E.expand(E.read_recipe(mk, "STARSHIP"), "STARSHIP", "1.26.1", "starship", mk)
    assert coords["SOURCE"] == "starship-1.26.1-cargo6.tar.gz", coords["SOURCE"]


def test_a_plain_tarball_recipe_still_names_a_plain_tarball():
    """Negative control: no infra suffix for an ordinary https download.

    Without this the fix would be a carpet-bomb that appends a suffix everywhere.
    alsa-ucm-conf uses the same github SITE macro as fzf but the generic infra, so it
    must keep its suffix-free name - the suffix comes from the infra, not from github.
    """
    mk = (FIX / "alsa-ucm-conf.mk").read_text()
    coords = E.expand(E.read_recipe(mk, "ALSA_UCM_CONF"), "ALSA_UCM_CONF", "1.26.1",
                      "alsa-ucm-conf", mk)
    assert coords["SOURCE"] == "alsa-ucm-conf-1.26.1.tar.gz", coords["SOURCE"]
    mk = (FIX / "libxml2.mk").read_text()
    coords = E.expand(E.read_recipe(mk, "LIBXML2"), "LIBXML2", "2.15.4", "libxml2", mk)
    assert coords["SOURCE"] == "libxml2-2.15.4.tar.xz", coords["SOURCE"]


def test_expand_without_the_recipe_keeps_the_bare_default():
    """The mk_text argument is optional: existing callers keep their behaviour."""
    mk = (FIX / "fzf.mk").read_text()
    coords = E.expand(E.read_recipe(mk, "FZF"), "FZF", "0.74.5", "fzf")
    assert coords["SOURCE"] == "fzf-0.74.5.tar.gz", coords["SOURCE"]


def test_a_recipe_names_the_infra_suffix_from_its_own_eval_line():
    assert E.infra_post_process("$(eval $(golang-package))\n") == "go"
    assert E.infra_post_process("$(eval $(cargo-package))\n") == "cargo"
    assert E.infra_post_process("$(eval $(autotools-package))\n") == ""
    assert E.infra_post_process("$(eval $(python-package))\n") == ""


def test_a_generated_archive_gets_no_hash_the_plan_cannot_prove(tmp_path):
    """The lane must not write a hash it cannot compute.

    recipe_bump on a golang recipe returns the .mk edit alone and a HashInfo that says
    where the digest must come from. A sha256 of the SITE archive is a true digest of the
    wrong file - measured 2026-10-12: fzf 0.74.5's generated archive is 45fa3fb8...,
    GitHub's own tarball at the SITE URL is 5529d088...
    """
    mk = (FIX / "fzf.mk").read_text()
    hash_text = "sha256  aaa  fzf-0.74.4-go2.tar.gz\n"
    e, hi = E.recipe_bump(tmp_path, "buildroot-external/package/fzf", mk, hash_text,
                          "0.74.5", get=lambda url: b"not-the-archive-buildroot-builds")
    assert [x.path for x in e] == ["buildroot-external/package/fzf/fzf.mk"], \
        "no .hash edit: the digest is not knowable at plan time"
    assert hi.sha256 == "", "an empty digest, not a plausible wrong one"
    assert hi.filename == "fzf-0.74.5-go2.tar.gz"
    assert hi.source.startswith("generated by Buildroot"), hi.source


def test_a_recipe_that_names_a_real_file_is_still_downloadable(tmp_path):
    """Negative control: an explicit *_SOURCE names a file that exists, so keep hashing it.

    cosmic-portal is cargo infra (so the infra test above would catch it if the rule were
    written as "all cargo is generated") but its SOURCE is explicit, so Buildroot downloads
    that file and a download-and-hash is correct.
    """
    mk = (FIX / "cosmic-portal.mk").read_text()
    hash_text = "sha256  aaa  epoch-1.7.0.tar.gz\n"
    e, hi = E.recipe_bump(tmp_path, "buildroot-external/package/cosmic-portal", mk, hash_text,
                          "epoch-1.8.0",
                          get=lambda url: b"epoch-1.8.0.tar.gz bytes")
    assert [x.path for x in e] == ["buildroot-external/package/cosmic-portal/cosmic-portal.mk",
                                   "buildroot-external/package/cosmic-portal/cosmic-portal.hash"]
    assert hi.sha256 == E.sha256_of(b"epoch-1.8.0.tar.gz bytes")
    assert hi.filename == "epoch-1.8.0.tar.gz"
    assert hi.source == "self-attested (downloaded here)", hi.source


class _Make:
    """A fake subprocess.run that records the make invocation."""

    def __init__(self, rc=0, out="", err=""):
        self.rc, self.out, self.err, self.calls = rc, out, err, []

    def __call__(self, argv, **kw):
        import subprocess
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.rc, stdout=self.out, stderr=self.err)


def _gen_tree(tmp_path, filename="fzf-0.74.5-go2.tar.gz", payload=b"go archive"):
    src = tmp_path / "buildroot-2026.08"
    (src / "dl" / "fzf").mkdir(parents=True)
    (src / "dl" / "fzf" / filename).write_bytes(payload)
    return src, payload


def test_generated_hash_hashes_the_file_buildroot_produced(tmp_path):
    """The positive control, and the reason this is the only honest way to hash it."""
    src, payload = _gen_tree(tmp_path)
    run = _Make(rc=0)
    hi = E.generated_hash(run, tmp_path, "2026.08", "/repo/buildroot-external",
                          "pengyos-server", "fzf", "fzf-0.74.5-go2.tar.gz")
    assert hi.sha256 == E.sha256_of(payload)
    assert hi.filename == "fzf-0.74.5-go2.tar.gz"
    assert "make fzf-source" in hi.source
    argv = run.calls[0]
    assert argv[-1] == "fzf-source" and argv[:2] == ["make", "-C"], argv
    assert any(a.startswith("O=") for a in argv), "the per-edition output dir must be passed"
    assert any(a.startswith("BR2_EXTERNAL=") for a in argv), "the br2-external tree must be passed"


def test_generated_hash_survives_the_chicken_and_egg(tmp_path):
    """The bootstrap pass is EXPECTED to fail, and the archive is still usable.

    `make <pkg>-source` ends at check-hash, which cannot find a hash for a version whose
    hash is exactly what we are computing. support/download/dl-wrapper cats the generated
    archive to its final path before it exits with the hash verdict, so the bytes are on
    disk. Measured 2026-10-14 on the live tree: make exited 2 with "ERROR: No hash found
    for fzf-0.74.5-go2.tar.gz" and dl/fzf/fzf-0.74.5-go2.tar.gz was there. Treating that
    failure as fatal is what made the first version of this fix bail.
    """
    src, payload = _gen_tree(tmp_path)
    run = _Make(rc=2, err="ERROR: No hash found for fzf-0.74.5-go2.tar.gz\n"
                          "make[1]: *** [package/pkg-generic.mk:179: .../.stamp_downloaded] Error 1\n"
                          "make: *** [Makefile:83: _all] Error 2")
    hi = E.generated_hash(run, tmp_path, "2026.08", "/repo/buildroot-external",
                          "pengyos-server", "fzf", "fzf-0.74.5-go2.tar.gz")
    assert hi.sha256 == E.sha256_of(payload), "the archive Buildroot generated is the hash source"


def test_generated_hash_refuses_when_the_build_failed_for_a_real_reason(tmp_path):
    """Negative control: a failure that is NOT the missing hash must raise.

    Only the missing-hash verdict is expected. A clone that failed, a bad GOMOD, a
    network error - none of those produced an archive, and hashing whatever is lying in
    dl/ is the original defect in a new costume.
    """
    src, _ = _gen_tree(tmp_path)
    with pytest.raises(E.GeneratedHashError, match="without reaching the hash check"):
        E.generated_hash(_Make(rc=2, err="make: *** [package/pkg-generic.mk:179] Error 1"),
                         tmp_path, "2026.08", "/repo/buildroot-external", "pengyos-server",
                         "fzf", "fzf-0.74.5-go2.tar.gz")


def test_generated_hash_refuses_when_the_archive_is_not_where_buildroot_named(tmp_path):
    """Negative control: the file Buildroot names is not there - say so, do not guess."""
    src, _ = _gen_tree(tmp_path, filename="fzf-0.74.4-go2.tar.gz")
    with pytest.raises(E.GeneratedHashError, match="fzf-0.74.5-go2.tar.gz"):
        E.generated_hash(_Make(rc=0), tmp_path, "2026.08", "/repo/buildroot-external",
                         "pengyos-server", "fzf", "fzf-0.74.5-go2.tar.gz")
    with pytest.raises(E.GeneratedHashError, match="nothing to hash"):
        E.generated_hash(_Make(rc=2, err="ERROR: No hash found for fzf-0.74.5-go2.tar.gz"),
                         tmp_path, "2026.08", "/repo/buildroot-external", "pengyos-server",
                         "fzf", "fzf-0.74.5-go2.tar.gz")


def test_verify_source_passes_when_make_succeeds(tmp_path):
    _gen_tree(tmp_path)
    run = _Make(rc=0)
    assert E.verify_source(run, tmp_path, "2026.08", "/repo/buildroot-external",
                           "pengyos-server", "fzf") is None
    assert run.calls, "it must actually re-run the download step"
    assert run.calls[0][-1] == "fzf-source"


def test_verify_source_refuses_a_hash_buildroot_does_not_accept(tmp_path):
    """The proof step. If the download step still fails with the hash written, the hash is wrong."""
    _gen_tree(tmp_path)
    with pytest.raises(E.GeneratedHashError, match="not the one Buildroot accepts"):
        E.verify_source(_Make(rc=2, err="fzf-0.74.5-go2.tar.gz: 1 of 1 hashes FAILED!"),
                        tmp_path, "2026.08", "/repo/buildroot-external", "pengyos-server",
                        "fzf")


def test_generated_hash_edit_keys_the_hash_to_the_file_the_build_produces(tmp_path):
    """The .hash line must name the archive check-hash will look for, and say its source."""
    hi = E.HashInfo(sha256="45fa3fb8", filename="fzf-0.74.5-go2.tar.gz",
                    source="generated by Buildroot (make fzf-source, pengyos-server)")
    hash_text = ("# Locally generated\n"
                 "sha256  aaa  fzf-0.74.4-go2.tar.gz\n"
                 "sha512  bbb  LICENSE\n")
    e = E.generated_hash_edit("fzf", hash_text, "0.74.4", hi)
    assert e.kind == "replace" and e.path.endswith("fzf/fzf.hash")
    assert "fzf-0.74.4-go2.tar.gz" not in e.new, "the old version's line must go"
    assert "sha256  45fa3fb8  fzf-0.74.5-go2.tar.gz" in e.new, e.new
    assert "sha512  bbb  LICENSE" in e.new, "a license hash travels unchanged"
    assert "Generated by Buildroot" in e.new, e.new


# --- writing the divergence down ---------------------------------------------

LEDGER = E.LEDGER

_REQ = ("id", "kind", "component", "carried_since", "expiry", "observe_source",
        "upstream_status")


def _ledger_repo(tmp_path, ledger=""):
    (tmp_path / "buildroot-external" / "patches").mkdir(parents=True, exist_ok=True)
    if ledger:
        (tmp_path / LEDGER).write_text(ledger)
    return tmp_path


def _entries(repo):
    import tomllib
    return tomllib.loads((repo / LEDGER).read_text()).get("divergence", [])


FF = [{"path": "buildroot-external/patches/ffmpeg/6.1.5/0001-a.patch",
       "cves": ["CVE-2026-70628"]},
      {"path": "buildroot-external/patches/ffmpeg/6.1.5/0002-b.patch",
       "cves": ["CVE-2026-64834", "CVE-2026-64835"]}]


def test_a_new_patch_set_is_written_down_and_parses(tmp_path):
    """The ledger's rule is that every divergence gets an entry before it lands. An
    unattended lane cannot ask a human, so it writes the entry: kind, component, an
    expiry, an observe source - every field the gate requires."""
    repo = _ledger_repo(tmp_path)
    e = E.ledger_edit(repo, "ffmpeg", FF, ["pengyos-desktop", "pengyos-desktop-nvidia"],
                      "26.10.31", "2026-10-18")
    assert e is not None and e.kind == "create" and e.path == LEDGER, e
    E.apply_edits(repo, [e])
    (entry,) = _entries(repo)
    for field in _REQ:
        assert str(entry.get(field) or "").strip(), field
    assert entry["kind"] == "patch-set" and entry["component"] == "ffmpeg"
    assert entry["patches_dir"] == "buildroot-external/patches/ffmpeg/6.1.5"
    assert entry["pinned_version"] == "6.1.5"
    assert entry["carried_in"] == ["26.10.31"]
    assert entry["editions"] == ["pengyos-desktop", "pengyos-desktop-nvidia"]
    assert entry["closes"] == ["CVE-2026-70628", "CVE-2026-64834", "CVE-2026-64835"]

    text = (repo / LEDGER).read_text()
    assert "# 0001-a.patch" in text and "# 0002-b.patch" in text, (
        "the ledger says which patch closes which CVE, or the reader has to cross-read "
        "cve-declarations.mk")


def test_the_lane_owns_closes_and_leaves_the_prose_alone(tmp_path):
    """A hand-written entry carries what only a human knows - cost, natural fix version,
    the note about why the patch shape was admissible. The lane must not flatten that;
    `closes` is the part it owns, because it is the part that follows the tree."""
    hand = '''[[divergence]]
id        = "ffmpeg-6.1.5-cve-2026"
kind      = "patch-set"
component = "ffmpeg"
pinned_version = "6.1.5"
carried_since = "2026-10-10"
carried_in    = ["26.10.21"]
patches_dir   = "buildroot-external/patches/ffmpeg/6.1.5"
# Twelve upstream fix backports. The prose here is the operator's.
closes = [
  "CVE-2026-70628",  # 0001-a.patch
]
natural_fix = "7.1"
expiry = "Buildroot pins an ffmpeg outside the NVD range; the patch set is deleted then"
observe_source = "pkg-stats"
upstream_status = "merged"
'''
    repo = _ledger_repo(tmp_path, hand)
    e = E.ledger_edit(repo, "ffmpeg", FF, ["pengyos-desktop"], "26.10.31", "2026-10-18")
    assert e is not None and e.kind == "replace", e
    E.apply_edits(repo, [e])
    (entry,) = _entries(repo)
    assert entry["id"] == "ffmpeg-6.1.5-cve-2026", "the human's id stays"
    assert entry["natural_fix"] == "7.1", "the human's research stays"
    assert entry["carried_in"] == ["26.10.21"], "when it was first carried stays"
    assert entry["closes"] == ["CVE-2026-70628", "CVE-2026-64834", "CVE-2026-64835"]
    assert "The prose here is the operator's." in (repo / LEDGER).read_text()


def test_writing_the_same_set_twice_changes_nothing(tmp_path):
    repo = _ledger_repo(tmp_path)
    E.apply_edits(repo, [E.ledger_edit(repo, "ffmpeg", FF, ["pengyos-desktop"],
                                       "26.10.31", "2026-10-18")])
    assert E.ledger_edit(repo, "ffmpeg", FF, ["pengyos-desktop"], "26.10.31",
                         "2026-10-18") is None, "idempotent"
    assert len(_entries(repo)) == 1


def test_a_dropped_patch_takes_its_cve_out_of_the_ledger(tmp_path):
    """What survived the pre-flight is what the tree carries, and the ledger follows the
    tree: a CVE that stays listed after its patch is gone reads as closed when it is not."""
    repo = _ledger_repo(tmp_path)
    E.apply_edits(repo, [E.ledger_edit(repo, "ffmpeg", FF, ["pengyos-desktop"],
                                       "26.10.31", "2026-10-18")])
    survived = [c for c in FF if c["path"].endswith("0001-a.patch")]
    e = E.ledger_edit(repo, "ffmpeg", survived, ["pengyos-desktop"], "26.10.31",
                      "2026-10-18")
    assert e is not None
    E.apply_edits(repo, [e])
    (entry,) = _entries(repo)
    assert entry["closes"] == ["CVE-2026-70628"]


def test_an_entry_whose_patches_are_gone_is_retired(tmp_path):
    """A ledger entry pointing at a directory with no patches is worse than none: the gate
    fails on it, and it reads as a divergence still being carried."""
    repo = _ledger_repo(tmp_path)
    E.apply_edits(repo, [E.ledger_edit(repo, "ffmpeg", FF, ["pengyos-desktop"],
                                       "26.10.31", "2026-10-18")])
    e = E.ledger_edit(repo, "ffmpeg", [], ["pengyos-desktop"], "26.10.31", "2026-10-18")
    assert e is not None and "retire" in e.note, e
    E.apply_edits(repo, [e])
    assert _entries(repo) == [], (repo / LEDGER).read_text()


def test_the_entry_the_lane_writes_passes_the_repo_gate(tmp_path):
    """The two halves have to agree, or the lane writes entries the gate then refuses.
    Built as a real repo layout: patches on disk, the entry written by this code, then the
    gate's own check run over it."""
    import sys
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        import pengyos_gate as gate
    finally:
        sys.path.remove(str(scripts))

    repo = _ledger_repo(tmp_path)
    patch = repo / FF[0]["path"]
    patch.parent.mkdir(parents=True)
    patch.write_text("From 02fc47e13f90\n")
    for c in FF:
        p = repo / c["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("patch")

    E.apply_edits(repo, [E.ledger_edit(repo, "ffmpeg", FF, ["pengyos-desktop"],
                                       "26.10.31", "2026-10-18")])
    checks = gate.check_ledger_covers_patch_sets(repo)
    assert all(c.ok for c in checks), [c.detail for c in checks if not c.ok]

    # negative control: take the entry out and the same layout fails, by name
    (repo / LEDGER).write_text("")
    checks = gate.check_ledger_covers_patch_sets(repo)
    assert any(c.name == "patch-set-ledgered:buildroot-external/patches/ffmpeg/6.1.5"
               and not c.ok for c in checks), [c.name for c in checks]


def test_editing_one_entry_leaves_the_next_one_alone(tmp_path):
    """Found by rehearsal against main's real ledger, not by a unit test: `_ledger_blocks`
    returned every block as `text[start:]`, so the ffmpeg entry's text held the gst entry
    after it, and writing the block back appended that entry a second time. Five patch sets
    became twenty-five entries and every one of them parsed."""
    one = '''[[divergence]]
id        = "{comp}-entry"
kind      = "patch-set"
component = "{comp}"
carried_since = "2026-10-10"
patches_dir   = "buildroot-external/patches/{comp}/{ver}"
closes = ["CVE-2099-0001"]
expiry = "when the pin moves past the fix"
observe_source = "pkg-stats"
upstream_status = "merged"
'''
    two = (one.format(comp="libxml2", ver="2.15.3")
           + "\n" + one.format(comp="ffmpeg", ver="6.1.5"))
    repo = _ledger_repo(tmp_path, two)
    lib = [{"path": "buildroot-external/patches/libxml2/2.15.3/0001-a.patch",
            "cves": ["CVE-2026-86137"]}]
    e = E.ledger_edit(repo, "libxml2", lib, ["pengyos-desktop"], "26.10.31", "2026-10-18")
    assert e is not None
    E.apply_edits(repo, [e])
    entries = _entries(repo)
    assert len(entries) == 2, [x["id"] for x in entries]
    assert sorted(x["component"] for x in entries) == ["ffmpeg", "libxml2"]


def test_a_night_carrying_the_same_set_does_not_reformat_the_entry(tmp_path):
    """The generated closes line carries a patch filename. A comment a human wrote there
    says more (`0009 vf_swaprect: size the temp row buffer for the widest plane`), so an
    unchanged set must be left exactly as it is - otherwise every nightly commit rewrites
    the ledger to say the same thing in a poorer way."""
    hand = '''[[divergence]]
id        = "ffmpeg-6.1.5-cve-2026"
kind      = "patch-set"
component = "ffmpeg"
carried_since = "2026-10-10"
patches_dir   = "buildroot-external/patches/ffmpeg/6.1.5"
closes = [
  "CVE-2026-70628",  # 0001 dvbsub parser: signed overflow in the capacity computation
  "CVE-2026-64834",  # 0002 rtpdec_asf: reject undersized ASF objects
]
expiry = "Buildroot pins an ffmpeg outside the NVD range; the patch set is deleted then"
observe_source = "pkg-stats"
upstream_status = "merged"
'''
    repo = _ledger_repo(tmp_path, hand)
    same = [{"path": "buildroot-external/patches/ffmpeg/6.1.5/0001-a.patch",
             "cves": ["CVE-2026-70628"]},
            {"path": "buildroot-external/patches/ffmpeg/6.1.5/0002-b.patch",
             "cves": ["CVE-2026-64834"]}]
    assert E.ledger_edit(repo, "ffmpeg", same, ["pengyos-desktop"], "26.10.31",
                         "2026-10-18") is None
    assert (repo / LEDGER).read_text() == hand, "the file was rewritten for nothing"
