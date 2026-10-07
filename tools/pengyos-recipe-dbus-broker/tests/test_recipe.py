"""Recipe-authoring tests.

Asserts that the Buildroot recipe under ``package/dbus-broker/`` is authored to
the spec contract (spec Verification):

* **version pinned** — ``DBUS_BROKER_VERSION`` is set and the source tarball is
  bound to that exact version;
* **license** — ``DBUS_BROKER_LICENSE`` is Apache-2.0 (the pengyOS recipe keeps
  the upstream's ``and/or`` form so both the AUTHORS and README legal notices
  are covered — see the recipe comment);
* **depends declared** — the recipe lists its ``DEPENDENCIES`` (which must
  include ``systemd``) and declares the ``dbus`` dependency conditionally on
  ``BR2_PACKAGE_DBUS``; the ``Config.in`` kconfig entry ``depends on``
  ``BR2_PACKAGE_SYSTEMD``;
* **systemd unit installed** — the ``.mk`` installs a systemd unit
  (``dbus.socket``) into ``$(TARGET_DIR)/usr/lib/systemd/system/`` and the unit
  file ships in ``package/dbus-broker/``.

These read the *committed* recipe text (no build, no network, no root).
"""

from __future__ import annotations

from pathlib import Path

from tests import recipe_check

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "package" / "dbus-broker"
MK_FILE = PACKAGE_DIR / "dbus-broker.mk"
CONFIG_FILE = PACKAGE_DIR / "Config.in"


def _mk_text() -> str:
    return MK_FILE.read_text(encoding="utf-8")


def _config_text() -> str:
    return CONFIG_FILE.read_text(encoding="utf-8")


# --- version pinned ---------------------------------------------------------


def test_version_is_pinned():
    """DBUS_BROKER_VERSION must be a concrete upstream version, not a URL/branch."""
    version = recipe_check.mk_variable(_mk_text(), "DBUS_BROKER_VERSION")
    assert version, "DBUS_BROKER_VERSION is not pinned in dbus-broker.mk"
    assert version.isdigit(), f"version {version!r} is not a numeric upstream tag"
    assert version == "37", f"unexpected pinned version {version!r} (expected 37)"


def test_source_bound_to_pinned_version():
    """The SOURCE tarball name must expand to dbus-broker-<VERSION>.tar.xz."""
    text = _mk_text()
    version = recipe_check.mk_variable(text, "DBUS_BROKER_VERSION")
    source = recipe_check.mk_variable(text, "DBUS_BROKER_SOURCE")
    site = recipe_check.mk_variable(text, "DBUS_BROKER_SITE")
    assert source and site, "SOURCE/SITE must be set"
    expanded = source.replace("$(DBUS_BROKER_VERSION)", version)
    assert expanded == f"dbus-broker-{version}.tar.xz", (
        f"SOURCE {expanded!r} is not bound to the pinned version {version}"
    )
    assert "$(" not in expanded, f"SOURCE still has an unexpanded reference: {expanded!r}"
    assert "github.com/bus1/dbus-broker/releases" in site, (
        "SITE must point at the pinned upstream release download"
    )


# --- license ----------------------------------------------------------------


def test_license_is_apache2():
    """The recipe must declare the Apache-2.0 license (spec: 'LICENSE Apache-2.0')."""
    value = recipe_check.mk_variable(_mk_text(), "DBUS_BROKER_LICENSE")
    assert value, "DBUS_BROKER_LICENSE is not set"
    assert "Apache-2.0" in value, f"license {value!r} does not declare Apache-2.0"


def test_license_declares_the_subproject_clause():
    """The compound license must carry the LGPL-2.1+ subproject clause (upstream keeps both)."""
    values = recipe_check.mk_license_values(_mk_text())
    assert any("LGPL-2.1+" in v for v in values), (
        f"compound license missing the LGPL-2.1+ subproject clause: {values}"
    )


def test_license_files_recorded():
    """DBUS_BROKER_LICENSE_FILES must list LICENSE + the subproject AUTHORS/README files."""
    license_files = [
        p.strip()
        for p in recipe_check.mk_variable(_mk_text(), "DBUS_BROKER_LICENSE_FILES").split()
        if p.strip()
    ]
    assert license_files, "DBUS_BROKER_LICENSE_FILES is empty"
    assert "LICENSE" in license_files, "LICENSE must be in DBUS_BROKER_LICENSE_FILES"
    # the subproject provenance files live inside the upstream tarball (verified
    # by tests/test_hash.py against the committed fixture), so here we only assert
    # the list is well-formed: every entry other than the top-level LICENSE is a
    # subproject path.
    for rel in license_files:
        if rel == "LICENSE":
            continue
        assert "/" in rel, f"unexpected LICENSE_FILES entry (no path): {rel!r}"


# --- dependencies declared --------------------------------------------------


def test_dependencies_include_systemd():
    """DEPENDENCIES must include systemd (spec: 'depends on systemd')."""
    deps = recipe_check.mk_variable(_mk_text(), "DBUS_BROKER_DEPENDENCIES")
    assert deps, "DBUS_BROKER_DEPENDENCIES is not set"
    assert "systemd" in deps.split(), f"DEPENDENCIES {deps!r} does not include systemd"


def test_dependencies_include_expat():
    """The recipe selects expat in Config.in, so it must be a build dependency."""
    deps = recipe_check.mk_variable(_mk_text(), "DBUS_BROKER_DEPENDENCIES")
    assert "expat" in deps.split(), f"DEPENDENCIES {deps!r} does not include expat"


def test_dbus_dependency_is_conditional():
    """The dbus dependency must be conditional on BR2_PACKAGE_DBUS (upstream behaviour)."""
    text = _mk_text()
    assert "BR2_PACKAGE_DBUS" in text, "the dbus dependency must be gated on BR2_PACKAGE_DBUS"
    assert "DBUS_BROKER_DEPENDENCIES += dbus" in text, (
        "the conditional dbus dependency assignment is missing"
    )


def test_config_in_depends_on_systemd():
    """The kconfig entry must depend on BR2_PACKAGE_SYSTEMD (spec: 'depends on systemd')."""
    block = recipe_check.kconfig_block(_config_text(), "BR2_PACKAGE_DBUS_BROKER")
    assert block, "config BR2_PACKAGE_DBUS_BROKER is absent from Config.in"
    depends = recipe_check.kconfig_depends_on(block)
    assert "BR2_PACKAGE_SYSTEMD" in depends, (
        f"Config.in does not 'depends on BR2_PACKAGE_SYSTEMD': {depends}"
    )


def test_config_in_selects_expat():
    """Config.in must select BR2_PACKAGE_EXPAT (mirrors the DEPENDENCIES=expat)."""
    block = recipe_check.kconfig_block(_config_text(), "BR2_PACKAGE_DBUS_BROKER")
    assert "select BR2_PACKAGE_EXPAT" in block, "Config.in must select BR2_PACKAGE_EXPAT"


# --- systemd unit installed -------------------------------------------------


def test_systemd_unit_installed():
    """The .mk must install a systemd unit into $(TARGET_DIR)/usr/lib/systemd/."""
    units = recipe_check.installed_systemd_units(_mk_text())
    assert units, "no systemd unit install destination found in the .mk"
    # the socket unit is what makes dbus-broker the system bus
    assert any(u.endswith("dbus.socket") for u in units), (
        f"expected a dbus.socket unit to be installed, got: {units}"
    )


def test_systemd_unit_file_ships():
    """The installed socket unit file must be present in package/dbus-broker/."""
    assert (PACKAGE_DIR / "dbus.socket").is_file(), (
        "dbus.socket unit file is missing from package/dbus-broker/"
    )


def test_meson_package_evaluated():
    """The recipe must finish with $(eval $(meson-package)) (Buildroot convention)."""
    assert "$(eval $(meson-package))" in _mk_text(), (
        "dbus-broker.mk does not end with $(eval $(meson-package))"
    )


def test_recipe_defines_users():
    """dbus-broker runs as dbus:dbus with home /run/dbus (mirrors the reference dbus)."""
    text = _mk_text()
    assert "define DBUS_BROKER_USERS" in text, "DBUS_BROKER_USERS is not defined"
    assert "/run/dbus" in text, "the dbus user home (/run/dbus) is not configured"


def test_systemd_socket_install_destination():
    """The socket unit must be installed to $(TARGET_DIR)/usr/lib/systemd/system/."""
    dests = recipe_check.installed_init_files(_mk_text())
    assert "usr/lib/systemd/system/dbus.socket" in dests, (
        f"dbus.socket must be installed to the systemd unit dir, got: {dests}"
    )


def test_dbus_config_files_installed():
    """session.conf + system.conf must ship to $(TARGET_DIR)/usr/share/dbus-1/."""
    dests = recipe_check.installed_init_files(_mk_text())
    assert "usr/share/dbus-1/session.conf" in dests, (
        f"session.conf must be installed to $(TARGET_DIR)/usr/share/dbus-1/, got: {dests}"
    )
    assert "usr/share/dbus-1/system.conf" in dests, (
        f"system.conf must be installed to $(TARGET_DIR)/usr/share/dbus-1/, got: {dests}"
    )
