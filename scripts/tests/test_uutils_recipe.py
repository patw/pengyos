"""Recipe rules for uutils-coreutils (SPEC §2.1: the Rust coreutils).

Why these checks exist. The recipe is the only thing standing between the image and
a silently degraded Core: build it with the default cargo feature set and `stat`,
`id`, `sync`, `uname`, `whoami`, `install`, `stty` and `timeout` are simply not in
the image; drop a symlink and one utility disappears from PATH; write the `.hash`
line in `sha256sum` order and the download dies with "No hash found".

`tools/pengyos-recipe-uutils` held these rules while the recipe was authored there
and copied into the tree. The recipe now lives in one place
(`buildroot-external/package/uutils-coreutils`), so the rules live with the repo's
other wiring checks. `test_core_userland.py` covers the wiring around it — the
layers select it, every defconfig carries it, Config.in sources it, the utility set
covers what GNU coreutils put in Core, the BusyBox applets are compiled out. This
file covers the shape of the recipe itself.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "buildroot-external"
CONFIG_IN = EXT / "Config.in"
RECIPE = EXT / "package" / "uutils-coreutils"
MK = RECIPE / "uutils-coreutils.mk"
HASH = RECIPE / "uutils-coreutils.hash"

# The authoritative utility set for the pinned version: the names the multicall
# `coreutils` binary dispatches on, generated from upstream's pinned Cargo.toml
# feature sets. Committed so the suite needs no network.
FIXTURE_UTILITIES = REPO / "scripts" / "tests" / "fixtures" / "coreutils-0.12.0-unix-utilities.txt"

PINNED_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
MOVING_REF_RE = re.compile(r"\b(HEAD|master|main|latest|v?next|stable|dev)\b", re.IGNORECASE)


def _mk() -> str:
    return MK.read_text(encoding="utf-8")


def _block(name: str) -> str:
    """The body of `define UUTILS_COREUTILS_<name> ... endef`."""
    m = re.search(rf"define UUTILS_COREUTILS_{name}\n(.*?)\nendef", _mk(), re.S)
    assert m is not None, f"UUTILS_COREUTILS_{name} block not found"
    return m.group(1)


def _value(key: str) -> str:
    m = re.search(rf"^UUTILS_COREUTILS_{key}\s*=\s*(.+)$", _mk(), re.M)
    assert m is not None, f"UUTILS_COREUTILS_{key} must be defined"
    return m.group(1)


def _utilities() -> list[str]:
    """Parse the multi-line UUTILS_COREUTILS_UTILITIES assignment into names."""
    lines = _mk().splitlines()
    start = next(i for i, ln in enumerate(lines)
                 if ln.startswith("UUTILS_COREUTILS_UTILITIES"))
    names: list[str] = []
    for raw in lines[start:]:
        line = raw.split("=", 1)[1] if raw.startswith("UUTILS_COREUTILS_UTILITIES") else raw
        if line.rstrip().endswith("\\"):
            names += line.rstrip()[:-1].split()
        else:
            names += line.split()
            break
    return names


# --- the Kconfig option is gated on what a cargo package actually needs -------
def test_config_in_is_gated_on_the_rust_and_glibc_requirements():
    """The utmpx utilities (who, users, uptime, pinky) need glibc — musl ships
    stubs — and a cargo package needs the rustc arch gate. An ungated option
    builds on a toolchain that cannot produce those binaries."""
    cin = (RECIPE / "Config.in").read_text()
    assert "config BR2_PACKAGE_UUTILS_COREUTILS" in cin
    assert "depends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS" in cin
    assert "depends on BR2_TOOLCHAIN_USES_GLIBC" in cin
    assert "depends on BR2_USE_MMU" in cin
    assert "select BR2_PACKAGE_HOST_RUSTC" in cin
    assert re.search(r"^\s*help", cin, re.M), "the option needs a help block"
    assert "comment" in cin, "an ungated toolchain shows a dead option, not a reason"


def test_the_recipe_is_wired_into_the_external_tree():
    assert 'source "$BR2_EXTERNAL_PENGYOS_PATH/package/uutils-coreutils/Config.in"' \
        in CONFIG_IN.read_text()


# --- pinned source, licence, site -------------------------------------------
def test_version_is_a_pinned_release_and_not_a_moving_ref():
    version = _value("VERSION")
    assert PINNED_VERSION_RE.match(version), f"VERSION '{version}' is not a pinned release"
    assert not MOVING_REF_RE.search(version), "VERSION must not be a moving ref"


def test_source_and_site_track_the_pinned_version():
    version = _value("VERSION")
    source = _value("SOURCE")
    assert source in (f"coreutils-{version}.tar.gz",
                      "coreutils-$(UUTILS_COREUTILS_VERSION).tar.gz"), \
        "SOURCE must track the pinned VERSION"
    site = _value("SITE")
    assert "$(call github,uutils,coreutils" in site, \
        "SITE must use the Buildroot github macro for uutils/coreutils"
    assert not MOVING_REF_RE.search(site), "SITE must not reference a moving ref"


def test_the_licence_is_declared_as_mit():
    assert "MIT" in _value("LICENSE"), "the base system's licence stays MIT/Apache-2.0"
    assert _value("LICENSE_FILES"), "LICENSE_FILES must point at the licence file"


def test_hash_lines_are_buildroots_own_spelling():
    """`sha256  <64-hex>  <filename>`, algorithm first.

    `support/download/check-hash` reads each line as `read -r t h f`; a
    `sha256sum`-style line (hash then filename) shifts the filename into `h`, `f`
    is empty, nothing matches, and the build dies with "No hash found for
    coreutils-<version>.tar.gz". Found 2026-10-12 on the first
    `make uutils-coreutils-source`.
    """
    version = _value("VERSION")
    pairs = []
    for raw in HASH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        assert len(parts) == 3 and parts[0] == "sha256", \
            f".hash line must be 'sha256  <hash>  <filename>': {raw!r}"
        assert re.fullmatch(r"[0-9a-f]{64}", parts[1]), \
            f"expected a 64-hex sha256, got {parts[1]!r}"
        pairs.append(parts[2])
    assert f"coreutils-{version}.tar.gz" in pairs, \
        "the .hash must cover the pinned SOURCE"
    assert "LICENSE" in pairs, "the cargo post-process ships LICENSE alongside; hash it"


# --- the build is the unix feature set, offline and locked ------------------
def test_build_selects_the_unix_feature_set_offline_and_locked():
    """The whole point of this recipe. The default feature set (feat_common_core)
    has no stat, id, sync, uname, whoami, install, stty, timeout, hostname,
    groups, nice, nohup, chroot, chgrp, chmod, chown, nproc, mkfifo, mknod,
    logname, hostid, users, who, uptime, pinky, stdbuf, kill, arch — every one of
    which Core ships today. `--no-default-features` also keeps feat_diagnostics
    (and ariadne) out of every utility."""
    body = _block("BUILD_CMDS")
    assert "--features unix" in body, "build must select the unix feature set"
    assert "--no-default-features" in body, "build must drop the default feature set"
    assert "--offline" in body and "--locked" in body, \
        "cargo must build offline against the pinned Cargo.lock (Buildroot vendors)"
    assert "$(PKG_CARGO_ENV)" in body, "build must use Buildroot's cargo env (target triple)"
    assert "--bin coreutils" in body, "build the multicall binary, not every bin"


def test_install_target_is_enabled():
    assert re.search(r"^UUTILS_COREUTILS_INSTALL_TARGET\s*=\s*YES", _mk(), re.M), \
        "UUTILS_COREUTILS_INSTALL_TARGET must be YES"


def test_the_multicall_binary_is_installed_and_every_utility_is_a_symlink():
    """Upstream ships one binary that dispatches on argv[0]. The image needs
    `ls`, `cp`, `stat` as ordinary /usr/bin entries, so the install step places
    the binary with $(INSTALL) and symlinks every name in the utility list."""
    install = _block("INSTALL_TARGET_CMDS")
    assert "$(INSTALL)" in install, "the multicall binary must be installed with $(INSTALL)"
    assert re.search(r"ln\s+-sf", install), "the symlink plan must create utility symlinks"
    assert "UUTILS_COREUTILS_UTILITIES" in install, \
        "the symlink loop must walk the same list the recipe declares"
    assert "coreutils" in install


# --- the shipped set is exactly upstream's, for the pinned version ----------
def test_the_utility_list_is_the_authoritative_set_for_the_pinned_version():
    """A name missing from the list is a command with no file on PATH; a name in
    the list that upstream does not build is a dangling symlink. The fixture is
    upstream's feat_os_unix set for the pinned version, so a bump that regenerates
    one side and not the other fails here."""
    assert FIXTURE_UTILITIES.is_file(), "the authoritative utilities fixture is missing"
    want = sorted(
        w for line in FIXTURE_UTILITIES.read_text().splitlines()
        if not line.lstrip().startswith("#") for w in line.split()
    )
    got = _utilities()
    assert len(got) == len(set(got)), "the utility list contains duplicates"
    assert sorted(got) == want, (
        f"utility list drift: missing={set(want) - set(got)}, extra={set(got) - set(want)}"
    )
