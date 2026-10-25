"""Recipe-authoring tests.

Asserts that the Buildroot recipe under ``package/pengy/pengy.mk`` is authored to
the spec contract (spec Verification):

* **version pinned** — ``PENGY_VERSION`` is a concrete upstream version (1.9.0),
  not a branch/alias or a build-time make expansion;
* **source bound to the pin** — ``PENGY_SOURCE`` expands to
  ``PengyR-1.9.0.tar.gz`` with no unexpanded reference left behind;
* **verified https site** — ``PENGY_SITE`` is an absolute ``https`` URL at the
  pinned PengyR repo (so ``pengyos-recipe-lint``'s ``no_unverified_downloads``
  passes);
* **license declared** — ``PENGY_LICENSE`` is MIT and ``PENGY_LICENSE_FILES``
  points at the LICENSE shipped in the tarball;
* **headless build only** — the recipe builds *only* the two Rust binaries
  (``pengy-cli`` + ``pengy-web``) and installs exactly those two to
  ``$(TARGET_DIR)/usr/bin``; the build/install command bodies never reference
  Qt / C++ / CMake / the ``gui`` crate (Core is headless — spec Non-goals).
* **cargo-package** — the recipe is a cargo package (ends with
  ``$(eval $(cargo-package))``) and depends only on ``host-rustc``.

These read the *committed* recipe text (no build, no network, no root).
"""

from __future__ import annotations

import re
from pathlib import Path

from tests import recipe_check

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "package" / "pengy"
MK_FILE = PACKAGE_DIR / "pengy.mk"

# The two headless Rust front-ends Core ships. Everything else — the Qt6 GUI,
# which is a separate CMake target outside the Cargo workspace — is out of scope.
EXPECTED_BINS = ("pengy-cli", "pengy-web")

# Tokens that would pull a GUI / non-Rust toolchain into the build. The recipe
# *mentions* CMake and `gui/` in its prose to explain why they are excluded, so
# this assertion is scoped to the build/install command bodies (see
# _define_body below), never the whole file.
_FORBIDDEN_BUILD_TOKENS = ("qt", "cmake", "c++", "g++", "gcc", "make-package", "meson")


def _mk_text() -> str:
    return MK_FILE.read_text(encoding="utf-8")


def _define_body(text: str, name: str) -> str:
    """Return the lines of a ``define NAME ... endef`` block (joined).

    ``define``/``endef`` blocks are how the recipe overrides
    ``<PKG>_BUILD_CMDS`` / ``<PKG>_INSTALL_TARGET_CMDS``; the body is what the
    build actually runs, so it is the right place to assert on the toolchain.
    Returns ``""`` when the block is absent.
    """
    start = None
    for i, line in enumerate(text.splitlines()):
        if re.match(r"^\s*define\s+" + re.escape(name) + r"\s*$", line):
            start = i
            break
    if start is None:
        return ""
    body = []
    for line in text.splitlines()[start + 1 :]:
        if re.match(r"^\s*endef\s*$", line):
            break
        body.append(line)
    return "\n".join(body)


# --- version pinned ---------------------------------------------------------


def test_version_is_pinned():
    """PENGY_VERSION must be a concrete upstream version, not a URL/branch/expansion."""
    version = recipe_check.mk_variable(_mk_text(), "PENGY_VERSION")
    assert version, "PENGY_VERSION is not pinned in pengy.mk"
    assert version == "1.9.0", f"unexpected pinned version {version!r} (expected 1.9.0)"
    # not a branch/alias, not a build-time make expansion (pengyos-recipe-lint)
    assert not re.fullmatch(r"(?i)(git|head|auto|master|main|latest|trunk|stable)", version)
    assert "$(" not in version


def test_source_bound_to_pinned_version():
    """SOURCE is the literal pinned tarball name, matching the .hash filename.

    SOURCE is a *literal* filename (not a ``$(PENGY_VERSION)`` expansion) on
    purpose: the Buildroot download URL is SITE + "/" + SOURCE, and Buildroot
    still expands the version in SITE. SOURCE stays literal so the ``.hash``
    filename matches it and ``pengyos-recipe-lint``'s ``hash_present`` rule can
    verify the digest against the exact file Buildroot fetches.
    """
    text = _mk_text()
    version = recipe_check.mk_variable(text, "PENGY_VERSION")
    source = recipe_check.mk_variable(text, "PENGY_SOURCE")
    assert source and version, "SOURCE/VERSION must be set"
    assert source == f"PengyR-{version}.tar.gz", (
        f"SOURCE {source!r} is not the pinned tarball for version {version}"
    )
    assert "$(" not in source, f"SOURCE must be literal for hash verification: {source!r}"


def test_site_is_verified_https():
    """PENGY_SITE must be an absolute https URL at the pinned PengyR repo."""
    site = recipe_check.mk_variable(_mk_text(), "PENGY_SITE")
    assert site, "PENGY_SITE is not set"
    assert site.startswith("https://"), f"PENGY_SITE {site!r} is not https"
    assert "github.com/patw/PengyR" in site, (
        f"PENGY_SITE {site!r} does not point at the pinned PengyR repo"
    )
    # the site is pinned to the exact release tag, not a moving HEAD: expand the
    # make variable the way make would and confirm the tag is present
    version = recipe_check.mk_variable(_mk_text(), "PENGY_VERSION")
    expanded = site.replace("$(PENGY_VERSION)", version)
    assert f"v{version}" in expanded, (
        f"PENGY_SITE {expanded!r} is not pinned to the v{version} release tag"
    )


# --- license ----------------------------------------------------------------


def test_license_is_mit():
    """The recipe must declare the MIT license (spec: 'LICENSE=MIT')."""
    value = recipe_check.mk_variable(_mk_text(), "PENGY_LICENSE")
    assert value, "PENGY_LICENSE is not set"
    assert "MIT" in value, f"license {value!r} does not declare MIT"


def test_license_values_are_mit_only():
    """The declared license must resolve to MIT and nothing else."""
    values = recipe_check.mk_license_values(_mk_text())
    assert values == ["MIT"], f"unexpected license values: {values}"


def test_license_files_recorded():
    """PENGY_LICENSE_FILES must point at the LICENSE shipped in the tarball."""
    license_files = [
        p.strip()
        for p in recipe_check.mk_variable(_mk_text(), "PENGY_LICENSE_FILES").split()
        if p.strip()
    ]
    assert license_files, "PENGY_LICENSE_FILES is empty"
    assert "LICENSE" in license_files, "LICENSE must be in PENGY_LICENSE_FILES"


# --- headless build only ----------------------------------------------------


def test_build_command_builds_both_bins():
    """The build must compile exactly the two headless Rust binaries."""
    body = _define_body(_mk_text(), "PENGY_BUILD_CMDS")
    assert body, "PENGY_BUILD_CMDS is not defined"
    for bin_name in EXPECTED_BINS:
        assert f"--bin {bin_name}" in body, (
            f"build does not compile the {bin_name} target"
        )


def test_build_uses_locked_offline_cargo():
    """The build must be reproducible: locked, offline cargo."""
    body = _define_body(_mk_text(), "PENGY_BUILD_CMDS")
    assert "--locked" in body, "build must use --locked (reproducible dependency set)"
    assert "--offline" in body, "build must use --offline (no network at build time)"


def test_install_destinations_include_the_two_bins():
    """The recipe installs the two headless binaries to $(TARGET_DIR)/usr/bin."""
    dests = recipe_check.installed_init_files(_mk_text())
    for bin_name in EXPECTED_BINS:
        assert f"usr/bin/{bin_name}" in dests, (
            f"recipe does not install {bin_name} to $(TARGET_DIR)/usr/bin"
        )


def test_installs_default_settings():
    """The recipe ships the default config at $(TARGET_DIR)/etc/pengy/settings.json."""
    dests = recipe_check.installed_init_files(_mk_text())
    assert "etc/pengy/settings.json" in dests, (
        f"recipe does not install settings.json; got {dests}"
    )


def test_installs_no_systemd_unit():
    """The recipe ships no unit: running the UI is a per-user, opt-in act.

    The unit this repo used to carry was written for PengyR and wrong for this
    image (`--config`, `DynamicUser`, `ProtectHome`, root), and the live recipe
    never installed it. Serving `pengy-web` is now the system template
    `board/server/rootfs-overlay/etc/systemd/system/pengy-web@.service`, enabled
    per user by `pengyos-webui on`; a recipe-installed unit would be enabled on
    every machine that builds the recipe.
    """
    text = _mk_text()
    assert "pengy-web.service" not in text, "the recipe must not install a unit"
    assert "PENGY_INSTALL_INIT_SYSTEMD" not in text, "no unit to install"
    units = recipe_check.installed_systemd_units(text)
    assert units == [], f"recipe installs systemd units: {units}"
    assert not (PACKAGE_DIR / "pengy-web.service").exists(), (
        "the package directory must not carry a pengy-web unit"
    )


def test_installs_nothing_beyond_bins_and_config():
    """The recipe installs exactly the two bins and the default config."""
    dests = recipe_check.installed_init_files(_mk_text())
    expected = {
        "usr/bin/pengy-cli",
        "usr/bin/pengy-web",
        "etc/pengy/settings.json",
    }
    assert set(dests) == set(expected), (
        f"install destinations {dests} are not exactly {sorted(expected)}"
    )


def test_no_gui_toolchain_in_build_commands():
    """The build/install bodies must never pull Qt / C++ / CMake (Core is headless)."""
    for name in ("PENGY_BUILD_CMDS", "PENGY_INSTALL_TARGET_CMDS"):
        body = _define_body(_mk_text(), name)
        low = body.lower()
        for token in _FORBIDDEN_BUILD_TOKENS:
            assert token not in low, (
                f"{name} references {token!r} — Core is headless, no GUI toolchain"
            )


def test_no_cmake_or_meson_package_macro():
    """The recipe must use the cargo-package macro, never cmake/meson."""
    text = _mk_text()
    assert "$(eval $(cargo-package))" in text, "recipe must end with $(eval $(cargo-package))"
    assert "cmake-package" not in text, "recipe must not use cmake-package"
    assert "meson-package" not in text, "recipe must not use meson-package"


# Target (non-host) dependencies the headless recipe is allowed to declare. A
# headless cargo build needs none: the cargo macro supplies host-rustc, and the
# recipe ships no unit (see test_installs_no_systemd_unit). Anything else
# (``qt6``/``libgtk``/C++) would pull the GUI toolchain into Core, which is
# explicitly out of scope.
_ALLOWED_SYSTEM_DEPS = ("systemd",)


def test_no_gui_system_dependency_declared():
    """No Qt/C++ system dependency is declared (the cargo macro supplies host-rustc).

    A headless cargo build needs no GUI system libraries. If the recipe declares
    a ``PENGY_DEPENDENCIES`` line, every entry must be either ``host-rustc``
    (supplied by the cargo-package macro) or an allowed non-GUI system dep. Any
    ``qt6``/``libgtk``/C++ dep would pull the GUI toolchain into Core, which is
    explicitly out of scope. The recipe declares none: it installs no unit.
    """
    deps = recipe_check.mk_variable(_mk_text(), "PENGY_DEPENDENCIES")
    if not deps:
        # the cargo-package macro already adds host-rustc; nothing to declare
        return
    for dep in deps.split():
        assert dep == "host-rustc" or dep.startswith(_ALLOWED_SYSTEM_DEPS), (
            f"unexpected dependency {dep!r} "
            f"(only host-rustc or {_ALLOWED_SYSTEM_DEPS} are allowed for a headless build)"
        )


def test_installs_the_two_binaries_from_target_dir():
    """The install copies exactly the two binary artifacts out of the cargo target dir.

    The recipe also ships a default config and a systemd unit (installed from
    ``$(PENGY_PKGDIR)``), so the *binary* installs — the ones that read the cargo
    target dir — must be exactly two, one per headless front-end.
    """
    body = _define_body(_mk_text(), "PENGY_INSTALL_TARGET_CMDS")
    bin_installs = re.findall(r"target/\$\(RUSTC_TARGET_NAME\)/release/\w+", body)
    assert len(bin_installs) == 2, (
        f"expected exactly two binary installs from the cargo target dir, got {bin_installs}"
    )
    for bin_name in EXPECTED_BINS:
        assert f"target/$(RUSTC_TARGET_NAME)/release/{bin_name}" in body, (
            f"install does not copy the {bin_name} artifact"
        )
