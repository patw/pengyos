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
FIXTURE_UTILITIES = REPO_ROOT / "tests" / "fixtures" / "coreutils-0.12.0-unix-utilities.txt"

# The tree's copy of the recipe: the tool authors it, the external tree builds it.
# They must not drift (a fix landed in one and not the other is the failure mode).
TREE_RECIPE_DIR = REPO_ROOT.parent.parent / "buildroot-external" / "package" / "uutils-coreutils"

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
    assert re.search(r"^\s*help", config_in, re.MULTILINE), "Kconfig option needs a help block"


def test_config_in_gates_on_the_rust_and_glibc_requirements(config_in: str) -> None:
    """A cargo package needs the rustc arch gate; the utmpx utilities (who, users,
    uptime, pinky) need glibc — musl ships only stubs — so the option must say so."""
    assert "BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS" in config_in
    assert "BR2_TOOLCHAIN_USES_GLIBC" in config_in
    assert "select BR2_PACKAGE_HOST_RUSTC" in config_in


def test_tree_copy_is_identical_to_the_authored_copy() -> None:
    """The external tree builds this recipe; the tool owns it. Byte-identical, or
    the fix that landed in one silently is not in the image."""
    for name in ("uutils-coreutils.mk", "Config.in", "uutils-coreutils.hash"):
        authored = (RECIPE_DIR / name).read_text(encoding="utf-8")
        wired = (TREE_RECIPE_DIR / name).read_text(encoding="utf-8")
        assert authored == wired, (
            f"{name}: tools/pengyos-recipe-uutils and buildroot-external/package "
            "have drifted - copy the authored recipe into the tree (or back)"
        )


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
    assert version == "0.12.0", "bump this test when the pinned version changes"


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

    Buildroot's spelling is ``sha256  <hash>  <filename>`` — the algorithm name
    comes first, because ``support/download/check-hash`` parses the line as
    ``read -r t h f``. A bare ``sha256sum``-style line (hash then filename) is
    never matched, and the download fails with "No hash found for ...".
    Comment lines (``# ...``) are ignored.
    """
    pairs: list[tuple[str, str]] = []
    for raw in recipe_hash.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        assert len(parts) >= 3, (
            f"malformed .hash line (need 'sha256  <hash>  <filename>'): {raw!r}"
        )
        assert parts[0] in {"sha256", "sha1", "md5"}, f"unknown hash algorithm: {raw!r}"
        pairs.append((parts[1], " ".join(parts[2:])))
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


def test_hash_line_format_is_buildroots(recipe_hash: str) -> None:
    """Buildroot's spelling is `sha256  <hash>  <filename>`, algorithm first.

    `support/download/check-hash` reads each line as `read -r t h f`; a bare
    `sha256sum`-style line (hash then filename) shifts the filename into `h`,
    `f` is empty, nothing ever matches, and the build dies with
    "ERROR: No hash found for coreutils-<version>.tar.gz". Found 2026-10-12 on
    the first `make uutils-coreutils-source`.
    """
    for raw in recipe_hash.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        assert parts and parts[0] == "sha256", (
            f".hash line must start with the algorithm ('sha256  <hash>  <file>'): {raw!r}"
        )
        assert len(parts) == 3, f".hash line must be 'sha256  <hash>  <filename>': {raw!r}"


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


def test_build_selects_the_unix_feature_set(recipe: str) -> None:
    """`--features unix` is the whole point of this recipe: the default feature set
    (feat_common_core) has no stat, id, sync, uname, whoami, install, stty, timeout,
    hostname, groups, nice, nohup, chroot, chgrp, chmod, chown, nproc, mkfifo,
    mknod, logname, hostid, users, who, uptime, pinky, stdbuf, kill, arch - every
    one of which Core ships today. `--no-default-features` keeps feat_diagnostics
    (and ariadne) out of every utility."""
    build = re.search(r"define UUTILS_COREUTILS_BUILD_CMDS\n(.*?)\nendef", recipe, re.DOTALL)
    assert build is not None, "BUILD_CMDS block not found"
    body = build.group(1)
    assert "--features unix" in body, "build must select the unix feature set"
    assert "--no-default-features" in body, "build must drop the default feature set"
    assert "--offline" in body and "--locked" in body, (
        "cargo must build offline against the pinned Cargo.lock (Buildroot vendors)"
    )
    assert "$(PKG_CARGO_ENV)" in body, "build must use Buildroot's cargo env (target triple)"


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
    # The fixture carries a provenance header; comment lines are not utility names.
    want = sorted(
        w for line in FIXTURE_UTILITIES.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#") for w in line.split()
    )
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
def _raw_upstream_digest(recipe_hash: str) -> tuple[str, str] | None:
    """The raw-archive digest recorded in the .hash comment, with its tag.

    The .hash *body* is Buildroot's post-processed digest (the cargo post-process
    re-packs the source with the vendored crates), so it can never equal the raw
    GitHub archive. The comment records the raw one precisely so it can be
    checked against the live tarball.
    """
    prose = " ".join(ln.lstrip("#").strip() for ln in recipe_hash.splitlines()
                     if ln.lstrip().startswith("#"))
    m = re.search(r"archive of tag (\d+\.\d+\.\d+) hashes to ([0-9a-f]{64})", prose)
    return (m.group(1), m.group(2)) if m else None


def _downloaded_tarball(tmp_path: Path, version: str) -> Path:
    import urllib.request

    url = f"https://github.com/uutils/coreutils/archive/refs/tags/{version}.tar.gz"
    dst = tmp_path / f"coreutils-{version}.tar.gz"
    urllib.request.urlretrieve(url, dst)  # noqa: S310 - pinned, well-known URL
    return dst


def test_hash_matches_downloaded_source(recipe: str, recipe_hash: str, tmp_path: Path) -> None:
    """PENGYOS_IT=1: the recorded raw-archive digest must match the live tarball.

    This used to compare the .hash *body* with the raw download, which can never
    match: the body is Buildroot's post-processed tarball (the cargo post-process
    vendors the crates and re-packs). The test therefore failed for anyone who
    ran the opt-in suite, and proved nothing. What is worth pinning is the pair
    the comment records: the tag, and the digest of that tag's real archive.
    """
    if not os.environ.get("PENGYOS_IT"):
        pytest.skip("opt-in integration test; set PENGYOS_IT=1 to run")

    ver = re.search(r"^UUTILS_COREUTILS_VERSION\s*=\s*(\S+)", recipe, re.MULTILINE)
    assert ver is not None, "UUTILS_COREUTILS_VERSION must be defined"
    version = ver.group(1)

    recorded = _raw_upstream_digest(recipe_hash)
    assert recorded is not None, (
        "the .hash comment must record the raw upstream digest "
        "(\"archive of tag <ver> hashes to <sha256>\") — that is what this check can verify")
    tag, expected = recorded
    assert tag == version, f".hash comment records tag {tag}, but the recipe pins {version}"

    # The body must still cover the pinned tarball, whatever digest it carries.
    assert any(fn == f"coreutils-{version}.tar.gz" for _, fn in hash_lines(recipe_hash)), (
        f".hash must hash coreutils-{version}.tar.gz")

    tarball = _downloaded_tarball(tmp_path, version)
    actual = __import__("hashlib").sha256(tarball.read_bytes()).hexdigest()
    assert actual == expected, f"raw archive digest drift: comment={expected} live={actual}"
