"""Tests for the Cargo.lock parser (``pengyos_cosmic_pkg.cargo``).

These exercise the pure, hermetic parser that turns a parsed ``Cargo.lock``
into a structured model and answers the one question ``vendor`` cares about:
which packages are git-sourced. No network, disk (beyond the committed
fixture), root, or hardware.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from pengyos_cosmic_pkg.cargo import (
    CargoLock,
    CargoLockError,
    CargoPackage,
    GitSource,
    git_slug,
    load_lock,
    parse_git_source,
    parse_lock,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WORKSPACE = FIXTURES / "workspace"
FIXTURE_LOCK = WORKSPACE / "Cargo.lock"
FIXTURE_GIT_SOURCES = WORKSPACE / "git-sources"


# --------------------------------------------------------------------------- #
# parse_git_source — a git+... source string -> GitSource
# --------------------------------------------------------------------------- #


def test_parse_git_source_with_tag_and_ref() -> None:
    src = parse_git_source(
        "git+https://github.com/pop-os/smithay-clipboard?tag=v0.6.0#6f33b0c1a7b3f2e4d5c6a7b8c9d0e1f2a3b4c5d6"
    )
    assert src.url == "https://github.com/pop-os/smithay-clipboard"
    assert src.query == {"tag": "v0.6.0"}
    assert src.ref == "6f33b0c1a7b3f2e4d5c6a7b8c9d0e1f2a3b4c5d6"
    # The full original string is preserved as the source id.
    assert src.source_id == (
        "git+https://github.com/pop-os/smithay-clipboard?tag=v0.6.0"
        "#6f33b0c1a7b3f2e4d5c6a7b8c9d0e1f2a3b4c5d6"
    )
    assert src.tag == "v0.6.0"
    assert src.branch is None


def test_parse_git_source_with_branch() -> None:
    src = parse_git_source("git+https://example.com/foo?branch=main#abc1234")
    assert src.url == "https://example.com/foo"
    assert src.query == {"branch": "main"}
    assert src.branch == "main"
    assert src.tag is None


def test_parse_git_source_dotsuffix_is_stripped_from_url_not_id() -> None:
    src = parse_git_source("git+https://example.com/foo.git#abc1234")
    # The URL keeps its .git (it is the real repo URL); only git_slug() strips it.
    assert src.url == "https://example.com/foo.git"


def test_parse_git_source_without_ref() -> None:
    src = parse_git_source("git+https://example.com/foo")
    assert src.url == "https://example.com/foo"
    assert src.ref == ""


@pytest.mark.parametrize(
    "source",
    [
        "https://example.com/foo",  # no git+ prefix
        "registry+https://...",  # not a git source
        "git://example.com/foo",  # git source needs the git+ prefix
    ],
)
def test_parse_git_source_rejects_non_git(source: str) -> None:
    with pytest.raises(CargoLockError, match="not a git source"):
        parse_git_source(source)


@pytest.mark.parametrize(
    "source",
    [
        "git+",  # no URL at all
        "git+#abc",  # no URL before the fragment
        "git+://",  # a "://" with no scheme
    ],
)
def test_parse_git_source_rejects_missing_url(source: str) -> None:
    with pytest.raises(CargoLockError, match="missing URL"):
        parse_git_source(source)


def test_parse_git_source_rejects_malformed_query() -> None:
    with pytest.raises(CargoLockError, match="malformed git query"):
        parse_git_source("git+https://example.com/foo?bogus#abc")


# --------------------------------------------------------------------------- #
# git_slug — a git URL -> on-disk directory name
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url,slug",
    [
        ("https://github.com/pop-os/smithay-clipboard", "smithay-clipboard"),
        ("https://github.com/pop-os/smithay-clipboard.git", "smithay-clipboard"),
        ("https://github.com/pop-os/smithay-clipboard/", "smithay-clipboard"),
        ("git@github.com:pop-os/foo.git", "foo"),
        ("http://example.com/a/b-c", "b-c"),
    ],
)
def test_git_slug(url: str, slug: str) -> None:
    assert git_slug(url) == slug


def test_git_slug_rejects_unparseable() -> None:
    with pytest.raises(CargoLockError, match="cannot derive a slug"):
        git_slug("")


# --------------------------------------------------------------------------- #
# parse_lock — a parsed Cargo.lock -> CargoLock
# --------------------------------------------------------------------------- #


def test_parse_lock_fixture() -> None:
    with FIXTURE_LOCK.open("rb") as fh:
        lock = parse_lock(tomllib.load(fh))

    assert lock.version == "3"
    names = [p.name for p in lock.packages]
    assert names == ["cosmic-demo", "smithay-clipboard", "serde"]


def test_parse_lock_classifies_kinds() -> None:
    with FIXTURE_LOCK.open("rb") as fh:
        lock = parse_lock(tomllib.load(fh))

    by_name = {p.name: p for p in lock.packages}

    # The root has no source -> kind "root".
    assert by_name["cosmic-demo"].kind == "root"
    assert by_name["cosmic-demo"].git is None

    # The git dep is classified as git with a parsed GitSource.
    smith = by_name["smithay-clipboard"]
    assert smith.kind == "git"
    assert smith.is_git
    assert smith.git is not None
    assert smith.git.tag == "v0.6.0"
    assert smith.checksum is None

    # The registry dep keeps its index URL and checksum.
    serde = by_name["serde"]
    assert serde.kind == "registry"
    assert not serde.is_git
    assert serde.registry_index == "https://github.com/rust-lang/crates.io-index"
    assert serde.checksum == "6fb04a81a29a1c3c9f008a8ea098b41937e2d3f5c5329f18f6b89a8e32565c1a"


def test_lock_git_and_registry_accessors() -> None:
    with FIXTURE_LOCK.open("rb") as fh:
        lock = parse_lock(tomllib.load(fh))

    assert [p.name for p in lock.git_packages()] == ["smithay-clipboard"]
    assert [p.name for p in lock.registry_packages()] == ["serde"]


def test_parse_lock_defaults_version_to_3() -> None:
    lock = parse_lock({"package": [{"name": "a", "version": "1.0"}]})
    assert lock.version == "3"


def test_parse_lock_missing_package_key_raises() -> None:
    with pytest.raises(CargoLockError, match="no 'package' entries"):
        parse_lock({"version": "3"})


def test_parse_lock_empty_package_array_raises() -> None:
    with pytest.raises(CargoLockError, match="no 'package' entries"):
        parse_lock({"package": []})


def test_parse_lock_package_not_a_list_raises() -> None:
    with pytest.raises(CargoLockError, match="expected an array of tables"):
        parse_lock({"package": {"name": "a", "version": "1.0"}})


def test_parse_lock_missing_name_raises() -> None:
    with pytest.raises(CargoLockError, match="missing required field 'name'"):
        parse_lock({"package": [{"version": "1.0"}]})


def test_parse_lock_missing_version_raises() -> None:
    with pytest.raises(CargoLockError, match="missing required field 'version'"):
        parse_lock({"package": [{"name": "a"}]})


def test_parse_lock_non_string_name_raises() -> None:
    with pytest.raises(CargoLockError, match="must be a non-empty string"):
        parse_lock({"package": [{"name": 5, "version": "1.0"}]})


def test_parse_lock_unknown_source_treated_as_path() -> None:
    lock = parse_lock(
        {"package": [{"name": "a", "version": "1.0", "source": "vendored+foo"}]}
    )
    assert lock.packages[0].kind == "path"
    assert not lock.packages[0].is_git


def test_load_lock_missing_file_raises() -> None:
    with pytest.raises(CargoLockError, match="Cargo.lock file not found"):
        load_lock("/tmp/definitely-not-here-Cargo.lock-xyz")


def test_load_lock_malformed_toml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "Cargo.lock"
    bad.write_text("this is = = not valid toml\n", encoding="utf-8")
    with pytest.raises(CargoLockError, match="malformed TOML"):
        load_lock(bad)


# --------------------------------------------------------------------------- #
# Data-model invariants
# --------------------------------------------------------------------------- #


def test_gitsource_tag_and_branch_properties() -> None:
    gs = GitSource(url="u", query={}, ref="", source_id="git+u")
    assert gs.tag is None
    assert gs.branch is None
    gs2 = GitSource(url="u", query={"tag": "v1", "branch": "main"}, ref="s", source_id="x")
    assert gs2.tag == "v1"
    assert gs2.branch == "main"
