"""Tests for the Buildroot cargo recipe emitter (``pengyos_cosmic_pkg.recipe``).

These assert the golden shape of the three emitted files (``Config.in``,
``<pkg>.mk``, ``<pkg>.hash``) for the fixture component, the helpers that build
them (Kconfig symbol transform, SPDX -> license-file mapping, hash line), the
``read_source_hash`` reader, and the ``recipe`` CLI end to end. A
vendor -> recipe cross-check ties the two subcommands together: the recipe pins
the digest of the tarball the vendored tree actually produced.

No network, root, display, or hardware.
"""

from __future__ import annotations

import json
from pathlib import Path

from pengyos_cosmic_pkg.manifest import component_table, load_manifest, parse_manifest
from pengyos_cosmic_pkg.recipe import (
    VENDORED_SITE_PLACEHOLDER,
    build_recipe,
    hash_line,
    license_files,
    read_source_hash,
    render_config_in,
    render_pkg_mk,
    recipe_symbol,
)
from pengyos_cosmic_pkg.vendor import TAR_SUFFIX, vend

FIXTURES = Path(__file__).resolve().parent / "fixtures"
COMPONENT = FIXTURES / "component.toml"
WORKSPACE = FIXTURES / "workspace"
FIXTURE_LOCK = WORKSPACE / "Cargo.lock"
FIXTURE_GIT_SOURCES = WORKSPACE / "git-sources"

# The fixture component's derived identity, shared by every golden assertion.
NAME = "cosmic-demo"
VERSION = "v0.1.0"
SYMBOL = "BR2_PACKAGE_COSMIC_DEMO"
TARBALL = f"{NAME}-{VERSION}.tar.gz"


# --------------------------------------------------------------------------- #
# recipe_symbol — the Kconfig symbol transform
# --------------------------------------------------------------------------- #


def test_recipe_symbol_uppercases_and_underscores_dashes() -> None:
    assert recipe_symbol("cosmic-demo") == "BR2_PACKAGE_COSMIC_DEMO"


def test_recipe_symbol_passthrough_for_single_token() -> None:
    assert recipe_symbol("winit") == "BR2_PACKAGE_WINIT"


def test_recipe_symbol_leaves_existing_underscores() -> None:
    assert recipe_symbol("gtk_layering_shell") == "BR2_PACKAGE_GTK_LAYERING_SHELL"


# --------------------------------------------------------------------------- #
# license_files — SPDX expression -> license filenames
# --------------------------------------------------------------------------- #


def test_license_files_single_mit() -> None:
    assert license_files("MIT") == ["LICENSE-MIT"]


def test_license_files_two_license_expression() -> None:
    assert license_files("MIT OR Apache-2.0") == ["LICENSE-MIT", "LICENSE-APACHE"]


def test_license_files_gpl_maps_to_copying() -> None:
    assert license_files("GPL-3.0-only") == ["COPYING"]


def test_license_files_unknown_operand_falls_back_to_license() -> None:
    assert license_files("SomeLicense") == ["LICENSE"]


def test_license_files_trims_operand_whitespace() -> None:
    assert license_files("MIT OR  Apache-2.0") == ["LICENSE-MIT", "LICENSE-APACHE"]


# --------------------------------------------------------------------------- #
# hash_line — the Buildroot hash line format
# --------------------------------------------------------------------------- #


def test_hash_line_format() -> None:
    assert hash_line("abc123", TARBALL) == f"sha256  abc123  {TARBALL}\n"


# --------------------------------------------------------------------------- #
# read_source_hash — read the digest out of a Buildroot .hash file
# --------------------------------------------------------------------------- #


def test_read_source_hash_reads_digest(tmp_path: Path) -> None:
    hash_file = tmp_path / "cosmic-demo.hash"
    hash_file.write_text(f"sha256  {'d' * 64}  {TARBALL}\n", encoding="utf-8")
    assert read_source_hash(hash_file) == "d" * 64


def test_read_source_hash_skips_comments_and_blanks(tmp_path: Path) -> None:
    hash_file = tmp_path / "cosmic-demo.hash"
    hash_file.write_text(
        "# a comment\n"
        "\n"
        f"sha256  {'a' * 64}  {TARBALL}\n",
        encoding="utf-8",
    )
    assert read_source_hash(hash_file) == "a" * 64


def test_read_source_hash_rejects_missing_file() -> None:
    import pytest

    with pytest.raises(ValueError, match="hash file not found"):
        read_source_hash("/tmp/does-not-exist-cosmic-demo.hash")


def test_read_source_hash_rejects_bad_type(tmp_path: Path) -> None:
    hash_file = tmp_path / "bad.hash"
    hash_file.write_text("md5  deadbeef  foo\n", encoding="utf-8")
    try:
        read_source_hash(hash_file)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for a non-sha256 hash line")


def test_read_source_hash_rejects_empty_file(tmp_path: Path) -> None:
    hash_file = tmp_path / "empty.hash"
    hash_file.write_text("# only comments\n", encoding="utf-8")
    try:
        read_source_hash(hash_file)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for a hash file with no digest")


# --------------------------------------------------------------------------- #
# render_config_in — golden Kconfig menu entry
# --------------------------------------------------------------------------- #


def test_render_config_in_golden() -> None:
    manifest = load_manifest(COMPONENT)
    expected = (
        f"config {SYMBOL}\n"
        '\tbool "cosmic-demo"\n'
        "\tdepends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS\n"
        "\tselect BR2_PACKAGE_HOST_RUSTC\n"
        "help\n"
        "  cosmic-demo — a COSMIC git component.\n"
        "  \n"
        "  https://github.com/pop-os/cosmic-demo\n"
    )
    assert render_config_in(manifest) == expected


# --------------------------------------------------------------------------- #
# render_pkg_mk — golden cargo package recipe
# --------------------------------------------------------------------------- #


def test_render_pkg_mk_golden() -> None:
    manifest = load_manifest(COMPONENT)
    expected = (
        "################################################################################\n"
        "#\n"
        "# cosmic-demo\n"
        "#\n"
        "################################################################################\n"
        "\n"
        f"{SYMBOL}_VERSION = {VERSION}\n"
        f"{SYMBOL}_SOURCE = {TARBALL}\n"
        f"{SYMBOL}_SITE = {VENDORED_SITE_PLACEHOLDER}\n"
        f"{SYMBOL}_LICENSE = MIT OR Apache-2.0\n"
        f"{SYMBOL}_LICENSE_FILES = LICENSE-MIT LICENSE-APACHE\n"
        f"{SYMBOL}_CARGO_FEATURES = default\n"
        f"{SYMBOL}_CARGO_BINARIES = cosmic-demo\n"
        "\n"
        "$(eval $(cargo-package))\n"
    )
    assert render_pkg_mk(manifest, tarball_name=TARBALL) == expected


def test_render_pkg_mk_omits_features_and_binaries_when_unset() -> None:
    # A minimal manifest with no features/binaries should omit those lines.
    minimal = parse_manifest(component_table(name="bare", tag="v1.0.0", license="MIT"))
    out = render_pkg_mk(minimal, tarball_name="bare-v1.0.0.tar.gz")
    assert "CARGO_FEATURES" not in out
    assert "CARGO_BINARIES" not in out
    assert f"{SYMBOL}" not in out  # symbol is BR2_PACKAGE_BARE, not COSMIC_DEMO
    assert "BR2_PACKAGE_BARE_VERSION = v1.0.0" in out


def test_render_pkg_mk_site_override() -> None:
    manifest = load_manifest(COMPONENT)
    out = render_pkg_mk(
        manifest, tarball_name=TARBALL, site="file:///opt/vendored"
    )
    assert f"{SYMBOL}_SITE = file:///opt/vendored" in out


# --------------------------------------------------------------------------- #
# build_recipe — emit all three files, golden-checked
# --------------------------------------------------------------------------- #


def test_build_recipe_emits_three_files(tmp_path: Path) -> None:
    manifest = load_manifest(COMPONENT)
    result = build_recipe(
        manifest, source_hash="h" * 64, out_dir=tmp_path / "package"
    )
    assert result.symbol == SYMBOL
    assert result.tarball_name == TARBALL
    assert result.config_path == tmp_path / "package" / NAME / "Config.in"
    assert result.mk_path == tmp_path / "package" / NAME / f"{NAME}.mk"
    assert result.hash_path == tmp_path / "package" / NAME / f"{NAME}.hash"
    assert result.config_path.is_file()
    assert result.mk_path.is_file()
    assert result.hash_path.is_file()


def test_build_recipe_hash_matches_tarball_name(tmp_path: Path) -> None:
    manifest = load_manifest(COMPONENT)
    digest = "9" * 64
    result = build_recipe(
        manifest, source_hash=digest, out_dir=tmp_path / "package"
    )
    assert result.hash_path.read_text() == hash_line(digest, TARBALL)


def test_build_recipe_end_to_end_golden(tmp_path: Path) -> None:
    manifest = load_manifest(COMPONENT)
    result = build_recipe(
        manifest, source_hash="0" * 64, out_dir=tmp_path / "package"
    )
    # Config.in: symbol, bool, rust deps, select, help + url.
    config = result.config_path.read_text()
    assert f"config {SYMBOL}" in config
    assert '\tbool "cosmic-demo"' in config
    assert "\tdepends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS" in config
    assert "\tselect BR2_PACKAGE_HOST_RUSTC" in config
    assert "https://github.com/pop-os/cosmic-demo" in config

    # .mk: version + tarball pinned, license + files, cargo infra, features/bins.
    mk = result.mk_path.read_text()
    assert f"{SYMBOL}_VERSION = {VERSION}" in mk
    assert f"{SYMBOL}_SOURCE = {TARBALL}" in mk
    assert f"{SYMBOL}_LICENSE = MIT OR Apache-2.0" in mk
    assert f"{SYMBOL}_LICENSE_FILES = LICENSE-MIT LICENSE-APACHE" in mk
    assert f"{SYMBOL}_CARGO_FEATURES = default" in mk
    assert f"{SYMBOL}_CARGO_BINARIES = cosmic-demo" in mk
    assert "$(eval $(cargo-package))" in mk

    # .hash: single sha256 line naming the vendored tarball.
    hash_text = result.hash_path.read_text()
    assert hash_text == f"sha256  {'0' * 64}  {TARBALL}\n"


# --------------------------------------------------------------------------- #
# vendor -> recipe cross-check: the recipe pins the tarball's real digest
# --------------------------------------------------------------------------- #


def _vend_into(tmp_path: Path) -> Path:
    manifest = load_manifest(COMPONENT)
    lock_path = tmp_path / "Cargo.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(FIXTURE_LOCK.read_text(), encoding="utf-8")
    from pengyos_cosmic_pkg.cargo import load_lock

    lock = load_lock(lock_path)
    return vend(manifest, lock, FIXTURE_GIT_SOURCES, tmp_path)


def test_recipe_pins_vendored_tarball_digest(tmp_path: Path) -> None:
    import hashlib

    vend_dir = tmp_path / "vend"
    result = _vend_into(vend_dir)

    # The recipe consumes the .hash the vendored tree produced...
    source_hash = read_source_hash(result.hash_path)
    assert source_hash == result.hash_value
    assert len(source_hash) == 64

    # ...and pins that digest into the emitted recipe, naming the vendored
    # tarball the vendor command actually wrote.
    manifest = load_manifest(COMPONENT)
    recipe = build_recipe(
        manifest, source_hash=source_hash, out_dir=tmp_path / "package"
    )
    mk_text = recipe.mk_path.read_text()
    assert f"{SYMBOL}_SOURCE = {result.tarball_path.name}" in mk_text
    assert f"sha256  {source_hash}  {result.tarball_path.name}" in recipe.hash_path.read_text()
    # The .mk references the tarball by the exact name in the .hash line.
    assert f"{SYMBOL}_SOURCE = {result.tarball_path.name}" in mk_text


def test_recipe_tarball_is_buildroot_vendored_detected(tmp_path: Path) -> None:
    """The recipe's tarball is self-recognising to Buildroot's cargo check."""
    import re

    vend_dir = tmp_path / "vend"
    result = _vend_into(vend_dir)

    manifest = load_manifest(COMPONENT)
    recipe = build_recipe(
        manifest, source_hash=result.hash_value, out_dir=tmp_path / "package"
    )

    # The .mk names the vendored tarball that build_recipe pinned...
    mk_text = recipe.mk_path.read_text()
    tarball_name = result.tarball_path.name
    assert f"{SYMBOL}_SOURCE = {tarball_name}" in mk_text

    # ...and that tarball, when listed, has a VENDOR/ dir nested under exactly
    # one top-level component (Buildroot's "^[^/]*/VENDOR" detection), so the
    # download post-process skips re-vendoring instead of failing offline.
    import tarfile

    with tarfile.open(result.tarball_path) as tar:
        names = tar.getnames()
    assert any(re.match(r"^[^/]*/VENDOR", n) for n in names)


# --------------------------------------------------------------------------- #
# CLI: recipe end to end
# --------------------------------------------------------------------------- #


def test_cli_recipe_success(run_cli, tmp_path) -> None:
    vend_dir = tmp_path / "vend"
    vend_result = _vend_into(vend_dir)
    pkg_dir = tmp_path / "package"
    rc = run_cli(
        "recipe",
        "--manifest",
        str(COMPONENT),
        "--out",
        str(pkg_dir),
        "--hash-file",
        str(vend_result.hash_path),
    )
    assert rc == 0
    emitted = pkg_dir / NAME
    assert (emitted / "Config.in").is_file()
    assert (emitted / f"{NAME}.mk").is_file()
    assert (emitted / f"{NAME}.hash").is_file()
    mk_text = (emitted / f"{NAME}.mk").read_text()
    assert f"{SYMBOL}_VERSION = {VERSION}" in mk_text
    assert VENDORED_SITE_PLACEHOLDER in mk_text


def test_cli_recipe_json_mode(run_cli, tmp_path, capsys) -> None:
    vend_dir = tmp_path / "vend"
    vend_result = _vend_into(vend_dir)
    rc = run_cli(
        "recipe",
        "--manifest",
        str(COMPONENT),
        "--out",
        str(tmp_path / "package"),
        "--hash-file",
        str(vend_result.hash_path),
        "--json",
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "recipe"
    assert payload["ok"] is True
    assert payload["symbol"] == SYMBOL
    assert payload["version"] == VERSION
    assert payload["tarball"] == TARBALL
    assert any(p.endswith(f"{NAME}.mk") for p in payload["files"])


def test_cli_recipe_site_override(run_cli, tmp_path) -> None:
    vend_dir = tmp_path / "vend"
    vend_result = _vend_into(vend_dir)
    rc = run_cli(
        "recipe",
        "--manifest",
        str(COMPONENT),
        "--out",
        str(tmp_path / "package"),
        "--hash-file",
        str(vend_result.hash_path),
        "--site",
        "file:///opt/vendored",
    )
    assert rc == 0
    mk_text = (tmp_path / "package" / NAME / f"{NAME}.mk").read_text()
    assert f"{SYMBOL}_SITE = file:///opt/vendored" in mk_text


def test_cli_recipe_missing_hash_file(run_cli, capsys, tmp_path) -> None:
    rc = run_cli(
        "recipe",
        "--manifest",
        str(COMPONENT),
        "--out",
        str(tmp_path / "package"),
        "--hash-file",
        "/tmp/definitely-not-here-cosmic-demo.hash",
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "file not found" in err
    assert "definitely-not-here" in err


def test_cli_recipe_malformed_hash_file(run_cli, capsys, tmp_path) -> None:
    bad_hash = tmp_path / "bad.hash"
    bad_hash.write_text("not a hash line\n", encoding="utf-8")
    rc = run_cli(
        "recipe",
        "--manifest",
        str(COMPONENT),
        "--out",
        str(tmp_path / "package"),
        "--hash-file",
        str(bad_hash),
    )
    assert rc == 1
    err = capsys.readouterr().err
    # The malformed hash file is a ValueError, reported as
    # "pengyos-cosmic-pkg: recipe: <detail>".
    assert "recipe" in err
    assert "hash" in err.lower()
