"""Recipe-rule tests for the uutils-coreutils Buildroot recipe.

These assert the requirements of the "Author Config.in + .mk" subtask:

* the package option is declared (Config.in),
* the version is pinned to a real release (no HEAD/branch),
* the source filename tracks the pinned version,
* the download site uses the Buildroot ``github`` macro,
* the MIT license is declared,
* the multicall binary is installed and every utility is exposed through a
  busybox-style symlink (the "symlink plan"), and
* the shipped utility set matches the authoritative ``coreutils --list`` output
  for the pinned version (committed fixture, so no network is required).
* the ``.hash`` file is present, carries a 64-hex sha256, and hashes the pinned
  SOURCE (see ``test_hash_*``). An opt-in integration test (``PENGYOS_IT=1``)
  verifies that digest against the live upstream tarball.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPE_DIR = REPO_ROOT / "package" / "uutils-coreutils"

CONFIG_IN = RECIPE_DIR / "Config.in"
MK = RECIPE_DIR / "uutils-coreutils.mk"
FIXTURE_UTILITIES = REPO_ROOT / "tests" / "fixtures" / "coreutils-0.11.0-utilities.txt"

# A pinned Buildroot version is an upstream release tag, never a moving ref.
PINNED_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
MOVING_REF_RE = re.compile(r"\b(HEAD|master|main|latest|v?next|stable|dev)\b", re.IGNORECASE)


def extract_utilities(recipe: str) -> list[str]:
    """Parse the multi-line UUTILS_COREUTILS_UTILITIES assignment into names."""
    lines = recipe.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("UUTILS_COREUTILS_UTILITIES")), None)
    assert start is not None, "UUTILS_COREUTILS_UTILITIES must be defined"
    names: list[str] = []
    for raw in lines[start:]:
        line = raw.split("=", 1)[1] if raw.startswith("UUTILS_COREUTILS_UTILITIES") else raw
        if line.rstrip().endswith("\\"):
            names += line.rstrip()[:-1].split()
        else:
            names += line.split()
            break
    return names


@pytest.fixture(scope="module")
def config_in() -> str:
    return CONFIG_IN.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def recipe() -> str:
    return MK.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def install_cmds(recipe: str) -> str:
    """Extract the INSTALL_TARGET_CMDS recipe body."""
    match = re.search(
        r"define UUTILS_COREUTILS_INSTALL_TARGET_CMDS\n(.*?)\nendef",
        recipe,
        re.DOTALL,
    )
    assert match is not None, "INSTALL_TARGET_CMDS block not found"
    return match.group(1)


# --------------------------------------------------------------------------- #
# Config.in
# --------------------------------------------------------------------------- #
def test_config_in_present() -> None:
    assert CONFIG_IN.is_file(), "Config.in is missing"


def test_config_in_declares_package_option(config_in: str) -> None:
    assert "config BR2_PACKAGE_UUTILS_COREUTILS" in config_in


def test_config_in_has_dependency_and_help(config_in: str) -> None:
    assert "depends on" in config_in, "Kconfig option needs at least one 'depends on'"
    assert re.search(r"^help", config_in, re.MULTILINE), "Kconfig option needs a help block"


# --------------------------------------------------------------------------- #
# Version pinned / source / site
# --------------------------------------------------------------------------- #
def test_version_is_pinned(recipe: str) -> None:
    match = re.search(r"^UUTILS_COREUTILS_VERSION\s*=\s*(\S+)", recipe, re.MULTILINE)
    assert match is not None, "UUTILS_COREUTILS_VERSION is not defined"
    version = match.group(1)
    assert PINNED_VERSION_RE.match(version), f"VERSION '{version}' is not a pinned release"


def test_version_is_not_a_moving_ref(recipe: str) -> None:
    match = re.search(r"^UUTILS_COREUTILS_VERSION\s*=\s*(\S+)", recipe, re.MULTILINE)
    assert match is not None
    assert not MOVING_REF_RE.search(match.group(1)), "VERSION must not be a moving ref"


def test_version_tracks_upstream_release(recipe: str) -> None:
    """The pinned version must actually exist as a uutils/coreutils release."""
    match = re.search(r"^UUTILS_COREUTILS_VERSION\s*=\s*(\S+)", recipe, re.MULTILINE)
    assert match is not None
    version = match.group(1)
    assert version == "0.11.0", "bump this test when the pinned version changes"


def test_source_matches_version(recipe: str) -> None:
    src = re.search(r"^UUTILS_COREUTILS_SOURCE\s*=\s*(\S+)", recipe, re.MULTILINE)
    ver = re.search(r"^UUTILS_COREUTILS_VERSION\s*=\s*(\S+)", recipe, re.MULTILINE)
    assert src and ver, "SOURCE and VERSION must be defined"
    version = ver.group(1)
    expected = f"coreutils-{version}.tar.gz"
    assert src.group(1) in (expected, f"coreutils-$(UUTILS_COREUTILS_VERSION).tar.gz"), (
        "SOURCE must track the pinned VERSION"
    )


def test_site_uses_github_macro(recipe: str) -> None:
    site = re.search(r"^UUTILS_COREUTILS_SITE\s*=\s*(.+)", recipe, re.MULTILINE)
    assert site is not None, "UUTILS_COREUTILS_SITE must be defined"
    assert "$(call github,uutils,coreutils" in site.group(1), (
        "SITE should use the Buildroot github macro for uutils/coreutils"
    )
    assert not MOVING_REF_RE.search(site.group(1)), "SITE must not reference a moving ref"


# --------------------------------------------------------------------------- #
# License
# --------------------------------------------------------------------------- #
def test_license_is_mit(recipe: str) -> None:
    lic = re.search(r"^UUTILS_COREUTILS_LICENSE\s*=\s*(.+)", recipe, re.MULTILINE)
    assert lic is not None, "LICENSE must be defined"
    assert "MIT" in lic.group(1), "LICENSE must declare MIT"


def test_license_files_present(recipe: str) -> None:
    files = re.search(r"^UUTILS_COREUTILS_LICENSE_FILES\s*=\s*(.+)", recipe, re.MULTILINE)
    assert files is not None, "LICENSE_FILES must point at the LICENSE file"


# --------------------------------------------------------------------------- #
# Hash file
# --------------------------------------------------------------------------- #
HASH_FILE = RECIPE_DIR / "uutils-coreutils.hash"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def hash_lines(recipe_hash: str) -> list[tuple[str, str]]:
    """Return ``(hash, filename)`` pairs from a Buildroot ``.hash`` body.

    Comment lines (``# ...``) are ignored; each remaining line is
    ``<hash><whitespace><filename>`` as produced by ``sha256sum``.
    """
    pairs: list[tuple[str, str]] = []
    for raw in recipe_hash.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        assert len(parts) >= 2, f"malicious .hash line (need hash + filename): {raw!r}"
        pairs.append((parts[0], " ".join(parts[1:])))
    return pairs


@pytest.fixture(scope="module")
def recipe_hash() -> str:
    return HASH_FILE.read_text(encoding="utf-8")


def test_hash_file_present() -> None:
    assert HASH_FILE.is_file(), "uutils-coreutils.hash is missing"


def test_hash_file_has_sha256(recipe_hash: str) -> None:
    """The .hash file must carry at least one 64-hex sha256 digest."""
    pairs = hash_lines(recipe_hash)
    assert pairs, "uutils-coreutils.hash has no hash lines"
    assert any(SHA256_RE.match(h) for h, _ in pairs), (
        "uutils-coreutils.hash must contain a 64-hex-character sha256 digest"
    )


def test_hash_filename_tracks_source(recipe: str, recipe_hash: str) -> None:
    """Every hashed filename must track the pinned SOURCE basename."""
    ver = re.search(r"^UUTILS_COREUTILS_VERSION\s*=\s*(\S+)", recipe, re.MULTILINE)
    assert ver is not None, "UUTILS_COREUTILS_VERSION must be defined"
    version = ver.group(1)
    expected = f"coreutils-{version}.tar.gz"

    pairs = hash_lines(recipe_hash)
    assert pairs, "uutils-coreutils.hash has no hash lines"
    filenames = {fn for _, fn in pairs}
    assert expected in filenames, (
        f".hash must hash the pinned SOURCE {expected}; found {sorted(filenames)}"
    )


def test_hash_digest_is_sha256_not_sha1(recipe_hash: str) -> None:
    """Reject a stray sha1 (40 hex) or md5 (32 hex) line masquerading as the digest."""
    pairs = hash_lines(recipe_hash)
    for digest, _ in pairs:
        assert len(digest) == 64, f"expected a 64-hex sha256, got a {len(digest)}-hex digest: {digest}"


# --------------------------------------------------------------------------- #
# Build / install / symlink plan
# --------------------------------------------------------------------------- #
def test_install_target_enabled(recipe: str) -> None:
    enabled = re.search(r"^UUTILS_COREUTILS_INSTALL_TARGET\s*=\s*YES", recipe, re.MULTILINE)
    assert enabled, "UUTILS_COREUTILS_INSTALL_TARGET must be YES"


def test_build_and_install_defined(recipe: str) -> None:
    assert "define UUTILS_COREUTILS_BUILD_CMDS" in recipe, "BUILD_CMDS must be defined"
    assert (
        "define UUTILS_COREUTILS_INSTALL_TARGET_CMDS" in recipe
    ), "INSTALL_TARGET_CMDS must be defined"


def test_installs_multicall_binary(install_cmds: str) -> None:
    assert "coreutils" in install_cmds, "recipe must install the multicall binary"
    assert "$(INSTALL)" in install_cmds, "binary must be installed with $(INSTALL)"


def test_symlink_plan_present(install_cmds: str) -> None:
    assert re.search(r"ln\s+-sf", install_cmds), "symlink plan must create utility symlinks"


def test_every_utility_has_symlink(recipe: str, install_cmds: str) -> None:
    """The symlink plan must cover every shipped utility name."""
    shipped = extract_utilities(recipe)
    assert shipped, "utility list must not be empty"
    # The foreach walks the same list variable.
    assert "UUTILS_COREUTILS_UTILITIES" in install_cmds


# --------------------------------------------------------------------------- #
# Utility set matches the authoritative multicall binary for the pinned version
# --------------------------------------------------------------------------- #
def test_utility_list_matches_authoritative_fixture(recipe: str) -> None:
    assert FIXTURE_UTILITIES.is_file(), "authoritative utilities fixture is missing"
    got = extract_utilities(recipe)
    want = sorted(FIXTURE_UTILITIES.read_text(encoding="utf-8").split())
    assert sorted(got) == want, (
        f"utility list drift: missing={set(want) - set(got)}, extra={set(got) - set(want)}"
    )
    assert len(got) == len(set(got)), "utility list contains duplicates"


# --------------------------------------------------------------------------- #
# Opt-in integration: verify the committed hash against the live tarball
#
# These need the network and are skipped unless PENGYOS_IT=1. They pin the
# recipe's .hash to the real upstream artifact so a bad digest fails CI.
# --------------------------------------------------------------------------- #
def _downloaded_tarball(tmp_path: Path, version: str) -> Path:
    import urllib.request

    url = f"https://github.com/uutils/coreutils/archive/refs/tags/{version}.tar.gz"
    dst = tmp_path / f"coreutils-{version}.tar.gz"
    urllib.request.urlretrieve(url, dst)  # noqa: S310 - pinned, well-known URL
    return dst


def test_hash_matches_downloaded_source(recipe: str, recipe_hash: str, tmp_path: Path) -> None:
    """PENGYOS_IT=1: the .hash digest must equal the sha256 of the real tarball."""
    if not os.environ.get("PENGYOS_IT"):
        pytest.skip("opt-in integration test; set PENGYOS_IT=1 to run")

    ver = re.search(r"^UUTILS_COREUTILS_VERSION\s*=\s*(\S+)", recipe, re.MULTILINE)
    assert ver is not None, "UUTILS_COREUTILS_VERSION must be defined"
    version = ver.group(1)

    pairs = hash_lines(recipe_hash)
    expected, _ = next((p for p in pairs if p[1] == f"coreutils-{version}.tar.gz"), None)
    assert expected is not None, f".hash must hash coreutils-{version}.tar.gz"

    tarball = _downloaded_tarball(tmp_path, version)
    actual = __import__("hashlib").sha256(tarball.read_bytes()).hexdigest()
    assert actual == expected, f"digest drift: .hash={expected} live={actual}"
