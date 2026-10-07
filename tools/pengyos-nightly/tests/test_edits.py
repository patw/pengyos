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
    edits = E.patch_dir(tmp_path, "libxml2", "2.15.3", cves, ["pengyos-desktop"], get=get)
    paths = [e.path for e in edits if e.path.endswith(".patch")]
    assert paths == ["buildroot-external/patches/libxml2/2.15.3/"
                     "0001-xmlregexp-prevent-out-of-bounds-read-in-nxt-macro.patch",
                     "buildroot-external/patches/libxml2/2.15.3/"
                     "0002-xmlregexp-prevent-out-of-bounds-read-in-nxt-macro.patch"], \
        "two CVEs, two commits, numbered; the duplicate commit is not carried twice"
    assert edits[-1].kind == "ensure" and "BR2_GLOBAL_PATCH_DIR" in edits[-1].new


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
