"""Tests for the non-cargo Buildroot recipe lane
(``pengyos_cosmic_recipes.non_cargo_recipe``).

``cosmic-workspaces`` is a JavaScript GNOME Shell extension, not a Rust cargo
component, so it is authored here — in a *separate* non-cargo lane, out of scope
for the cargo recipes lane. This suite pins the recipe the lane authors: a GNOME
shell-extension **install path** that installs the extension files under the
shell extension directory (keyed by the extension's UUID) and the gschema under
glib's schema directory, compiling the schema cache at target finalisation.

The recipe is grounded against the real ``pop-os/cosmic-workspaces`` repo (UUID
``cosmic-workspaces@system76.com``, gschema
``org.gnome.shell.extensions.cosmic-workspaces.gschema.xml``, GPLv3): the tests
assert the on-disk recipe matches that real layout, not a guess.

All checks are stdlib and need no network, root, display, or hardware.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from pengyos_cosmic_recipes import load_manifest
from pengyos_cosmic_recipes.cli import main
from pengyos_cosmic_recipes.non_cargo_recipe import (
    EXTENSION_FILES,
    GNOME_EXTENSION_UUID,
    GSCHEMA_NAME,
    GSCHEMA_RELATIVE,
    LICENSE,
    NonCargoRecipeResult,
    PLACEHOLDER_HASH,
    RECIPE_NAME,
    RECIPE_SYMBOL,
    TARBALL_NAME,
    VERSION,
    emit_cosmic_workspaces_recipe,
    render_config_in,
    render_pkg_mk,
)
from pengyos_cosmic_pkg.lint import lint_dir

#: The extension directory the files install under (the GNOME shell extension
#: path, keyed by the UUID).
_EXTENSION_DIR = "/usr/share/gnome-shell/extensions/cosmic-workspaces@system76.com"
#: The glib schema directory the gschema installs under.
_SCHEMA_DIR = "/usr/share/glib-2.0/schemas"


def _emit(tmp_path: Path, **kwargs) -> NonCargoRecipeResult:
    """Emit the cosmic-workspaces recipe into a fresh dir, forwarding kwargs."""
    return emit_cosmic_workspaces_recipe(tmp_path, **kwargs)


def _read_mk(pkg_dir: Path) -> dict[str, str]:
    """Parse the ``<pkg>.mk`` ``KEY = value`` lines into a dict."""
    mk = pkg_dir / f"{pkg_dir.name}.mk"
    values: dict[str, str] = {}
    for line in mk.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _mk_values(tmp_path: Path, **kwargs) -> dict[str, str]:
    """Emit the recipe (with kwargs) and parse its ``<pkg>.mk``."""
    _emit(tmp_path, **kwargs)
    return _read_mk(tmp_path / RECIPE_NAME)


def _mk_text(tmp_path: Path, **kwargs) -> str:
    """Emit the recipe (with kwargs) and return its ``<pkg>.mk`` text."""
    _emit(tmp_path, **kwargs)
    return (tmp_path / RECIPE_NAME / f"{RECIPE_NAME}.mk").read_text()


def _run(*args: str) -> int:
    """Invoke the CLI in-process and return its exit code."""
    return main(list(args))


def _capture(*args: str) -> str:
    """Run a CLI subcommand and return its stdout."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        _run(*args)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# identity — the recipe names the real component
# ---------------------------------------------------------------------------


def test_recipe_names_the_real_extension():
    """The recipe names the real debian source package for the extension."""
    assert RECIPE_NAME == "gnome-shell-extension-cosmic-workspaces"
    assert RECIPE_SYMBOL == "BR2_PACKAGE_GNOME_SHELL_EXTENSION_COSMIC_WORKSPACES"
    # The UUID is the real one from the extension's metadata.json.
    assert GNOME_EXTENSION_UUID == "cosmic-workspaces@system76.com"


def test_version_pinned_and_not_floating():
    """The version is a pinned digit-carrying tag, never a floating ref."""
    from pengyos_cosmic_pkg.manifest import is_pinned

    assert VERSION == "0.1.0"
    assert is_pinned(VERSION) is True
    assert "epoch" not in VERSION  # not the epoch-* tag the cargo lane rejects


def test_license_is_gpl3_only():
    """The extension ships GPLv3 (only) — the LICENSE file is Version 3, no 'or later'."""
    assert LICENSE == "GPL-3.0-only"


def test_extension_files_match_real_repo():
    """The installed files match the real extension's runtime files.

    Grounded against ``pop-os/cosmic-workspaces``: ``extension.js`` plus the JS
    modules, ``prefs.js``, ``metadata.json``, ``settings.ui`` and
    ``stylesheet.css`` — everything the debian package installs to the
    extension directory (minus docs / build / schema files).
    """
    assert "extension.js" in EXTENSION_FILES
    assert "metadata.json" in EXTENSION_FILES
    assert "prefs.js" in EXTENSION_FILES
    # The JS modules the extension actually imports.
    for module in (
        "dash.js",
        "gestures.js",
        "overviewControls.js",
        "swipeTracker.js",
        "util.js",
        "workspace.js",
        "workspacesView.js",
        "workspaceSwitcherPopup.js",
        "workspaceThumbnail.js",
    ):
        assert module in EXTENSION_FILES, f"{module} missing from the recipe"
    # settings.ui / stylesheet.css ship with the extension.
    assert "settings.ui" in EXTENSION_FILES
    assert "stylesheet.css" in EXTENSION_FILES
    # The schema is installed separately (not in the extension file list).
    assert not any(name.endswith(".schema.xml") for name in EXTENSION_FILES)


# ---------------------------------------------------------------------------
# the three files
# ---------------------------------------------------------------------------


def test_emit_writes_three_files(tmp_path):
    """The recipe ships Config.in, <pkg>.mk and <pkg>.hash."""
    result = _emit(tmp_path)
    assert isinstance(result, NonCargoRecipeResult)
    pkg_dir = tmp_path / RECIPE_NAME
    assert (pkg_dir / "Config.in").is_file()
    assert (pkg_dir / f"{RECIPE_NAME}.mk").is_file()
    assert (pkg_dir / f"{RECIPE_NAME}.hash").is_file()
    # The result names exactly those three files, in order.
    assert tuple(result.files()) == (
        pkg_dir / "Config.in",
        pkg_dir / f"{RECIPE_NAME}.mk",
        pkg_dir / f"{RECIPE_NAME}.hash",
    )


def test_hash_file_format(tmp_path):
    """The .hash file is a single 'sha256  <digest>  <tarball>' line."""
    _emit(tmp_path)
    hash_text = (tmp_path / RECIPE_NAME / f"{RECIPE_NAME}.hash").read_text()
    assert hash_text.count("\n") == 1
    assert hash_text.startswith(f"sha256  {PLACEHOLDER_HASH}  {TARBALL_NAME}\n")


# ---------------------------------------------------------------------------
# the .mk recipe — pinned reference, license, install path, schema
# ---------------------------------------------------------------------------


def test_mk_pins_reference_and_license(tmp_path):
    """The .mk pins the version, source, license and its license file."""
    values = _mk_values(tmp_path)
    assert values[f"{RECIPE_SYMBOL}_VERSION"] == VERSION
    assert values[f"{RECIPE_SYMBOL}_SOURCE"] == TARBALL_NAME
    assert values[f"{RECIPE_SYMBOL}_LICENSE"] == LICENSE
    assert values[f"{RECIPE_SYMBOL}_LICENSE_FILES"] == "COPYING"


def test_mk_references_vendored_site_placeholder(tmp_path):
    """The .mk references the documented vendored-site placeholder, not a git ref."""
    values = _mk_values(tmp_path)
    assert values[f"{RECIPE_SYMBOL}_SITE"] == "file://<VENDORED_SITE>"


def test_mk_declares_uuid_variable(tmp_path):
    """The .mk pins the extension UUID in a variable, keyed into the install path."""
    values = _mk_values(tmp_path)
    assert values[f"{RECIPE_SYMBOL}_GNOME_EXTENSION_UUID"] == GNOME_EXTENSION_UUID


def test_mk_installs_extension_files_under_uuid(tmp_path):
    """Every extension file is installed under the UUID-keyed shell extension dir."""
    mk = _mk_text(tmp_path)
    uuid_var = f"{RECIPE_SYMBOL}_GNOME_EXTENSION_UUID"
    for name in EXTENSION_FILES:
        # Each file is installed with $(INSTALL) into the UUID-keyed extension dir,
        # keyed by the recipe's own UUID variable (defined once, referenced here).
        assert (
            f"$(INSTALL) -D -m 0644 $(@D)/{name} "
            f"$(TARGET_DIR)/usr/share/gnome-shell/extensions/"
            f"$({uuid_var})/{name}"
        ) in mk


def test_mk_installs_gschema_under_glib_schemas(tmp_path):
    """The gschema is installed under glib's schema directory (not the ext dir)."""
    mk = _mk_text(tmp_path)
    assert GSCHEMA_RELATIVE.startswith("schemas/")
    assert (
        f"$(INSTALL) -D -m 0644 $(@D)/{GSCHEMA_RELATIVE} "
        f"$(TARGET_DIR)/usr/share/glib-2.0/schemas/{GSCHEMA_NAME}"
    ) in mk


def test_gschema_installed_once_not_duplicated(tmp_path):
    """The gschema is installed only under glib's schemas, not inside the ext dir.

    The debian ``override_dh_install`` strips the schema from the extension
    directory, so a faithful recipe installs it once, under glib's schemas.
    """
    mk = _mk_text(tmp_path)
    # No stray copy of the schema inside the extension directory.
    assert f"{_EXTENSION_DIR}/{GSCHEMA_NAME}" not in mk
    # Exactly one install of the schema (under glib's schemas).
    assert mk.count(f"usr/share/glib-2.0/schemas/{GSCHEMA_NAME}") == 1


def test_mk_compiles_schema_at_finalization(tmp_path):
    """The recipe compiles the gschema cache at target finalisation (settings usable)."""
    mk = _mk_text(tmp_path)
    # glib-compile-schemas runs at target finalisation, against the target dir.
    assert "$(HOST_DIR)/bin/glib-compile-schemas" in mk
    assert "--targetdir=$(TARGET_DIR)/usr/share/glib-2.0/schemas" in mk
    assert f"{RECIPE_SYMBOL}_TARGET_FINALIZE_HOOKS" in mk


def test_mk_uses_generic_package_infra(tmp_path):
    """The recipe hands off to Buildroot's generic-package infrastructure."""
    mk = _mk_text(tmp_path)
    assert "$(eval $(generic-package))" in mk


# ---------------------------------------------------------------------------
# the Config.in — valid Kconfig entry that selects the schema tool
# ---------------------------------------------------------------------------


def test_config_in_has_symbol_and_select():
    """The Config.in declares the symbol and selects libglib2 (the schema tool)."""
    text = render_config_in()
    assert f"config {RECIPE_SYMBOL}" in text
    assert f'\tbool "{RECIPE_NAME}"' in text
    # libglib2 provides the host glib-compile-schemas the finalisation hook runs.
    assert "\tselect BR2_PACKAGE_LIBGLIB2" in text


def test_config_in_has_help_and_url():
    """The Config.in has a help body and the source URL."""
    text = render_config_in()
    assert "\thelp" in text
    assert "https://github.com/pop-os/cosmic-workspaces" in text


def test_config_in_is_tab_indented():
    """Kconfig menu entries are tab-indented (not spaces)."""
    text = render_config_in()
    lines = text.splitlines()
    # The bool / select / help lines are indented with a single leading tab.
    assert lines[1].startswith("\t")
    assert lines[2].startswith("\t")
    assert lines[3].startswith("\t")


# ---------------------------------------------------------------------------
# the digest seam — placeholder by default, real when supplied
# ---------------------------------------------------------------------------


def test_emit_placeholder_hash_by_default(tmp_path):
    """Without a digest, the recipe gets the placeholder and stays lint-clean."""
    result = _emit(tmp_path)
    hash_text = result.hash_path.read_text()
    assert hash_text.startswith(f"sha256  {PLACEHOLDER_HASH}  ")
    # The placeholder digest is well-formed and lint-clean.
    assert lint_dir(result.config_path.parent).is_clean is True


def test_emit_real_hash_supplied(tmp_path):
    """A supplied 64-hex digest is pinned verbatim."""
    digest = "ab" * 32
    result = _emit(tmp_path, source_hash=digest)
    hash_text = result.hash_path.read_text()
    assert hash_text.startswith(f"sha256  {digest}  ")


def test_emit_bad_hash_raises(tmp_path):
    """A non-64-hex digest is rejected, not silently pinned."""
    with pytest.raises(ValueError):
        emit_cosmic_workspaces_recipe(tmp_path, source_hash="not-a-sha256")


def test_emit_site_override(tmp_path):
    """A supplied site overrides the vendored-site placeholder."""
    values = _mk_values(tmp_path, site="file:///custom/vendor")
    assert values[f"{RECIPE_SYMBOL}_SITE"] == "file:///custom/vendor"


# ---------------------------------------------------------------------------
# determinism — reproducible emission
# ---------------------------------------------------------------------------


def test_emit_is_deterministic(tmp_path):
    """Emitting twice yields byte-identical recipe files."""
    out1 = tmp_path / "a"
    out2 = tmp_path / "b"
    emit_cosmic_workspaces_recipe(out1)
    emit_cosmic_workspaces_recipe(out2)
    for suffix in ("Config.in", f"{RECIPE_NAME}.mk", f"{RECIPE_NAME}.hash"):
        assert (out1 / RECIPE_NAME / suffix).read_bytes() == (
            out2 / RECIPE_NAME / suffix
        ).read_bytes()


def test_emit_is_clean(tmp_path):
    """The emitted recipe is lint-clean (hash present, version pinned, license)."""
    result = _emit(tmp_path)
    report = lint_dir(result.config_path.parent)
    assert report.is_clean is True
    assert report.violations == ()


# ---------------------------------------------------------------------------
# out of scope for the cargo lane — the cargo manifest never sees it
# ---------------------------------------------------------------------------


def test_cargo_manifest_excludes_cosmic_workspaces():
    """The committed cargo manifest excludes cosmic-workspaces (guard passes)."""
    manifest = load_manifest()
    assert "cosmic-workspaces" not in manifest.names()


def test_non_cargo_recipe_is_separate_lane():
    """The recipe is authored by the non-cargo lane, not the cargo emit lane."""
    # The non-cargo recipe module is distinct from the cargo emit module.
    import pengyos_cosmic_recipes.emit as cargo_emit
    import pengyos_cosmic_recipes.non_cargo_recipe as nc

    assert nc is not cargo_emit
    assert hasattr(nc, "emit_cosmic_workspaces_recipe")
    assert not hasattr(cargo_emit, "emit_cosmic_workspaces_recipe")


# ---------------------------------------------------------------------------
# CLI — non_cargo emit
# ---------------------------------------------------------------------------


def test_cli_non_cargo_emit_human(tmp_path, capsys):
    """``non_cargo emit`` authors the recipe into --out and reports it."""
    rc = _run("non_cargo", "emit", "--out", str(tmp_path))
    assert rc == 0
    emitted = capsys.readouterr().out
    assert RECIPE_NAME in emitted
    assert GNOME_EXTENSION_UUID in emitted
    assert (tmp_path / RECIPE_NAME / "Config.in").is_file()


def test_cli_non_cargo_emit_json(tmp_path):
    """``non_cargo emit --json`` emits a machine-readable summary."""
    out = _capture("non_cargo", "emit", "--out", str(tmp_path), "--json")
    payload = json.loads(out)
    assert payload["command"] == "non_cargo"
    assert payload["action"] == "emit"
    assert payload["ok"] is True
    assert payload["recipe"] == RECIPE_NAME
    assert payload["symbol"] == RECIPE_SYMBOL
    assert payload["uuid"] == GNOME_EXTENSION_UUID
    assert payload["version"] == VERSION
    assert payload["license"] == LICENSE
    assert len(payload["files"]) == 3


def test_cli_non_cargo_emit_missing_out_errors():
    """``non_cargo emit`` without --out exits non-zero (the arg is required)."""
    assert _run("non_cargo", "emit") == 2


def test_cli_non_cargo_list_still_defaults():
    """``non_cargo`` with no action still lists (default 'list')."""
    assert _run("non_cargo") == 0
    out = _capture("non_cargo")
    assert "cosmic-workspaces" in out


def test_cli_non_cargo_emit_real_hash_json(tmp_path):
    """``non_cargo emit --hash <digest>`` pins the supplied digest."""
    digest = "cd" * 32
    out = _capture(
        "non_cargo", "emit", "--out", str(tmp_path), "--hash", digest, "--json"
    )
    payload = json.loads(out)
    assert payload["hash"] == digest
