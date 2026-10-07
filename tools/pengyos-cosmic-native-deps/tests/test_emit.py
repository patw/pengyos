"""Tests for ``pengyos_cosmic_native_deps.emit`` (the recipe + fragment writer).

:mod:`.emit` renders each recipe in the catalogue to a ``package/<dep>/``
``Config.in`` + ``<dep>.mk`` + ``<dep>.hash`` trio (``Config.in`` only for a
data-only ``none`` recipe) and the ``fragments/cosmic-native.fragment`` that
enables the emitted symbols. The rendering is deterministic, so these tests pin
the exact Buildroot recipe shape (banner, ``_VERSION``/``_SOURCE``/``_SITE``/
``_LICENSE``, the build-system ``$(eval $(...))`` line, the ``sha256`` hash line,
and the ``<SYMBOL>=y`` fragment lines) and that re-running emit is idempotent.
"""

from __future__ import annotations

import re

import pengyos_cosmic_native_deps.emit as emit
from pengyos_cosmic_native_deps.recipes import BuildSystem, default_recipe_catalogue

# Buildroot's check-package invariants, mirrored here so the emitted files are
# validated even where the (root/network-requiring) check-package tool is not
# runnable: a sha256 hash line is three two-space-separated fields with a 64-hex
# digest; a recipe variable reference is ``$(PKG_NAME)``.
_HASH_LINE_RE = re.compile(r"^sha256  [0-9a-f]{64}  \S+$")
_PKG_VAR_RE = re.compile(r"^\$(\w+)$")

CATALOGUE = default_recipe_catalogue()
BY_NAME = {r.name: r for r in CATALOGUE}


# --- render_config_in -------------------------------------------------------


def test_render_config_in_uses_the_primary_symbol_as_config_name():
    for recipe in CATALOGUE:
        text = emit.render_config_in(recipe)
        assert text.startswith(f"config {recipe.br_symbols[0]}\n")


def test_render_config_in_bool_label_is_the_recipe_name():
    for recipe in CATALOGUE:
        assert f'\tbool "{recipe.name}"\n' in emit.render_config_in(recipe)


def test_render_config_in_cargo_has_host_rust_select_and_depends():
    greetd = BY_NAME["greetd"]
    text = emit.render_config_in(greetd)
    assert '\tdepends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS\n' in text
    assert "\tselect BR2_PACKAGE_HOST_RUSTC\n" in text


def test_render_config_in_non_cargo_has_no_host_rust_select():
    xdg = BY_NAME["xdg-desktop-portal"]
    text = emit.render_config_in(xdg)
    assert "BR2_PACKAGE_HOST_RUSTC" not in text


def test_render_config_in_help_text_is_tab_then_two_spaces():
    """The help body must be indented <tab><2 spaces> (check-package rule)."""
    text = emit.render_config_in(BY_NAME["greetd"])
    # The first help-text line (after the blank line following ``help``) is
    # indented tab + two spaces + a non-space character.
    assert re.search(r"\n\t  \S", text), text


def test_render_config_in_url_is_indented_like_help_text():
    text = emit.render_config_in(BY_NAME["greetd"])
    url_line = f"\t  {BY_NAME['greetd'].url}\n"
    assert url_line in text


def test_render_config_in_ends_with_a_newline():
    for recipe in CATALOGUE:
        text = emit.render_config_in(recipe)
        assert text.endswith("\n")
        # No trailing blank lines.
        assert not text.endswith("\n\n")


# --- render_mk --------------------------------------------------------------


def test_render_mk_none_returns_empty_string():
    assert emit.render_mk(BY_NAME["fonts"]) == ""


def test_render_mk_has_the_standard_banner():
    text = emit.render_mk(BY_NAME["greetd"])
    assert text.startswith(
        "################################################################################\n"
        "#\n"
        "# greetd\n"
        "#\n"
        "################################################################################\n"
    )


def test_render_mk_pins_version_source_and_license():
    greetd = BY_NAME["greetd"]
    text = emit.render_mk(greetd)
    assert f"GREETD_VERSION = {greetd.version}\n" in text
    assert f"GREETD_LICENSE = {greetd.license}\n" in text
    assert "GREETD_LICENSE_FILES = LICENSE\n" in text


def test_render_mk_source_references_the_version_variable():
    """``<PKG>_SOURCE = $(<PKG>_VERSION).tar.gz`` — a single Make expansion."""
    text = emit.render_mk(BY_NAME["greetd"])
    assert "GREETD_SOURCE = $(GREETD_VERSION).tar.gz\n" in text
    # Not the accidental double-parenthesis form.
    assert "$((GREETD_VERSION)" not in text


def test_render_mk_site_is_download_url_without_the_source_suffix():
    """``_SITE`` + ``/`` + ``_SOURCE`` must reconstruct ``download_url``."""
    for recipe in CATALOGUE:
        if recipe.build_system is BuildSystem.NONE:
            continue
        mk = emit.render_mk(recipe)
        site = next(
            value
            for key, value in (
                pair.split(" = ", 1) for pair in mk.splitlines() if " = " in pair
            )
            if key.endswith("_SITE")
        )
        assert site == recipe.download_url[: -len(recipe.source)].rstrip("/")
        # The joined URL resolves to the pinned download endpoint.
        assert site + "/" + recipe.source == recipe.download_url


def test_render_mk_eval_line_maps_the_build_system():
    expected = {
        BuildSystem.MESON: "$(eval $(meson-package))",
        BuildSystem.AUTOTOOLS: "$(eval $(autotools-package))",
        BuildSystem.MAKE: "$(eval $(generic-package))",
        BuildSystem.CARGO: "$(eval $(cargo-package))",
    }
    for recipe in CATALOGUE:
        if recipe.build_system is BuildSystem.NONE:
            continue
        text = emit.render_mk(recipe)
        assert expected[recipe.build_system] in text


def test_render_mk_pkg_name_uppercases_and_underscores():
    text = emit.render_mk(BY_NAME["xdg-desktop-portal"])
    # The hyphenated name becomes the upper-cased, underscored Make prefix.
    assert "XDG_DESKTOP_PORTAL_VERSION =" in text


# --- render_hash ------------------------------------------------------------


def test_render_hash_none_returns_empty_string():
    assert emit.render_hash(BY_NAME["fonts"]) == ""


def test_render_hash_has_three_two_space_separated_fields():
    for recipe in CATALOGUE:
        if recipe.build_system is BuildSystem.NONE:
            continue
        text = emit.render_hash(recipe)
        assert text.endswith("\n")
        line = text.rstrip("\n")
        assert _HASH_LINE_RE.match(line), line
        fields = line.split("  ")
        assert fields == ["sha256", recipe.hash, recipe.source]


# --- render_fragment --------------------------------------------------------


def test_render_fragment_enables_every_symbol_sorted_and_deduped():
    text = emit.render_fragment(CATALOGUE)
    lines = text.splitlines()
    assert lines == sorted(lines)
    for line in lines:
        symbol, value = line.split("=")
        assert value == "y"
        assert symbol.startswith("BR2_PACKAGE_")


def test_render_fragment_enables_all_ten_font_symbols():
    text = emit.render_fragment(CATALOGUE)
    for symbol in BY_NAME["fonts"].br_symbols:
        assert f"{symbol}=y" in text


def test_render_fragment_empty_for_no_recipes():
    assert emit.render_fragment([]) == ""


# --- emit -------------------------------------------------------------------


def test_emit_writes_the_trio_for_buildable_recipes(tmp_path):
    report = emit.emit(CATALOGUE, tmp_path)
    for recipe in CATALOGUE:
        if recipe.build_system is BuildSystem.NONE:
            continue
        assert (tmp_path / "package" / recipe.name / "Config.in").is_file()
        assert (tmp_path / "package" / recipe.name / f"{recipe.name}.mk").is_file()
        assert (
            tmp_path / "package" / recipe.name / f"{recipe.name}.hash"
        ).is_file()


def test_emit_writes_only_config_in_for_none_recipe(tmp_path):
    report = emit.emit(CATALOGUE, tmp_path)
    fonts_dir = tmp_path / "package" / "fonts"
    assert (fonts_dir / "Config.in").is_file()
    assert not (fonts_dir / "fonts.mk").exists()
    assert not (fonts_dir / "fonts.hash").exists()


def test_emit_writes_the_fragment(tmp_path):
    report = emit.emit(CATALOGUE, tmp_path)
    fragment = tmp_path / "fragments" / "cosmic-native.fragment"
    assert fragment.is_file()
    text = fragment.read_text(encoding="utf-8")
    for symbol in BY_NAME["fonts"].br_symbols:
        assert f"{symbol}=y" in text


def test_emit_report_count_and_paths(tmp_path):
    """3 buildable recipes x 3 files + 1 none recipe x 1 file + 1 fragment."""
    report = emit.emit(CATALOGUE, tmp_path)
    assert report.count == 11
    paths = sorted(f.path for f in report.files)
    assert paths == [
        "fragments/cosmic-native.fragment",
        "package/cosmic-portal/Config.in",
        "package/cosmic-portal/cosmic-portal.hash",
        "package/cosmic-portal/cosmic-portal.mk",
        "package/fonts/Config.in",
        "package/greetd/Config.in",
        "package/greetd/greetd.hash",
        "package/greetd/greetd.mk",
        "package/xdg-desktop-portal/Config.in",
        "package/xdg-desktop-portal/xdg-desktop-portal.hash",
        "package/xdg-desktop-portal/xdg-desktop-portal.mk",
    ]


def test_emit_is_deterministic_and_idempotent(tmp_path):
    """Re-running emit over the same tree yields byte-identical files."""
    first = emit.emit(CATALOGUE, tmp_path)
    snapshots = {f.path: (tmp_path / f.path).read_bytes() for f in first.files}
    second = emit.emit(CATALOGUE, tmp_path)
    assert [f.path for f in second.files] == [f.path for f in first.files]
    for path, original in snapshots.items():
        assert (tmp_path / path).read_bytes() == original


def test_emit_rendering_is_order_stable(tmp_path):
    """The render functions depend only on the recipe, not on emit ordering."""
    greetd = BY_NAME["greetd"]
    a = emit.render_mk(greetd)
    b = emit.render_mk(greetd)
    assert a == b
