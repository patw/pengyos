"""Tests for the recipes-lane shell group (``cosmic.toml`` + emit + closure).

Exercise the committed ``shell`` group: the eleven components, their pinned
references / licenses / inter-component deps, recipe emission (three files per
component, the multi-binary ``cosmic-applets``, the binary-less
``cosmic-icons``), and the cross-group closure (shell closes only once the core
group's recipes are present). All checks are stdlib and need no network, root,
display, or hardware.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_cosmic_recipes import closure_lint, emit_group, load_manifest
from pengyos_cosmic_recipes.manifest import is_pinned
from pins import shipping_pins

# Repo root = parent of this tests/ dir.
ROOT = Path(__file__).resolve().parent.parent


# The eleven shell components, in file order.
SHELL_NAMES = (
    "cosmic-panel",
    "cosmic-launcher",
    "cosmic-applets",
    "cosmic-bg",
    "cosmic-randr",
    "cosmic-settings",
    "cosmic-settings-daemon",
    "cosmic-osd",
    "cosmic-notifications",
    "cosmic-idle",
    "cosmic-icons",
)

# name -> expected license (from each component's Cargo.toml / LICENSE.md).
SHELL_LICENSES = {
    "cosmic-panel": "GPL-3.0-only",
    "cosmic-launcher": "GPL-3.0-only",
    "cosmic-applets": "GPL-3.0-only",
    "cosmic-bg": "MPL-2.0",
    "cosmic-randr": "MPL-2.0",
    "cosmic-settings": "GPL-3.0-only",
    "cosmic-settings-daemon": "GPL-3.0-or-later",
    "cosmic-osd": "GPL-3.0-or-later",
    "cosmic-notifications": "GPL-3.0-only",
    "cosmic-idle": "GPL-3.0-only",
    "cosmic-icons": "CC-BY-SA-4.0",
}

# name -> expected inter-component deps (declared by name in cosmic.toml).
SHELL_DEPS = {
    "cosmic-panel": ("libcosmic", "cosmic-protocols"),
    "cosmic-launcher": ("libcosmic",),
    "cosmic-applets": ("libcosmic", "cosmic-protocols"),
    "cosmic-bg": ("libcosmic",),
    "cosmic-randr": (),
    "cosmic-settings": (
        "libcosmic",
        "cosmic-randr",
        "cosmic-bg",
        "cosmic-comp",
        "cosmic-idle",
        "cosmic-panel",
    ),
    "cosmic-settings-daemon": ("libcosmic", "cosmic-comp"),
    "cosmic-osd": (
        "libcosmic",
        "cosmic-comp",
        "cosmic-randr",
        "cosmic-settings-daemon",
    ),
    "cosmic-notifications": ("libcosmic", "cosmic-panel"),
    "cosmic-idle": ("libcosmic", "cosmic-settings-daemon"),
    "cosmic-icons": (),
}

# name -> expected cargo binaries (forwarded into CARGO_BINARIES).
SHELL_BINARIES = {
    "cosmic-panel": ("cosmic-panel",),
    "cosmic-launcher": ("cosmic-launcher",),
    "cosmic-applets": (
        "cosmic-applets",
        "cosmic-applet-audio",
        "cosmic-applet-battery",
        "cosmic-applet-bluetooth",
        "cosmic-applet-minimize",
        "cosmic-applet-network",
        "cosmic-applet-notifications",
        "cosmic-applet-power",
        "cosmic-applet-status-area",
        "cosmic-applet-tiling",
        "cosmic-applet-time",
        "cosmic-applet-workspaces",
        "cosmic-panel-button",
    ),
    "cosmic-bg": ("cosmic-bg",),
    "cosmic-randr": ("cosmic-randr",),
    "cosmic-settings": ("cosmic-settings",),
    "cosmic-settings-daemon": ("cosmic-settings-daemon",),
    "cosmic-osd": ("cosmic-osd",),
    "cosmic-notifications": ("cosmic-notifications",),
    "cosmic-idle": ("cosmic-idle",),
    "cosmic-icons": (),
}


def _read_mk(pkg_dir: Path) -> dict[str, str]:
    """Parse the ``<pkg>.mk`` KEY = value lines into a dict."""
    mk = pkg_dir / f"{pkg_dir.name}.mk"
    values: dict[str, str] = {}
    for line in mk.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _symbol(pkg: str) -> str:
    return f"BR2_PACKAGE_{pkg.upper().replace('-', '_')}"


def test_shell_group_has_eleven_components():
    """The shell group holds exactly the eleven shell components, in order."""
    manifest = load_manifest()
    assert manifest.names("shell") == SHELL_NAMES


def test_shell_components_are_pinned_and_licensed():
    """Every shell component carries a pinned tag and a declared license."""
    manifest = load_manifest()
    for entry in manifest.entries("shell"):
        assert is_pinned(entry.tag), f"{entry.name}: {entry.tag!r} is not pinned"
        assert entry.license.strip(), f"{entry.name}: license is empty"


def test_shell_licenses_declared(tmp_path):
    """Each shell recipe's LICENSE line is the manifest's license."""
    manifest = load_manifest()
    emit_group(manifest, "shell", out_dir=tmp_path)
    for name, license_ in SHELL_LICENSES.items():
        values = _read_mk(tmp_path / name)
        assert values[f"{_symbol(name)}_LICENSE"] == license_


def test_shell_pins_match_the_shipping_component_set(tmp_path):
    """Each shell recipe pins what `scripts/cosmic-components.tsv` pins today.

    COSMIC has two pin sets: the TSV the host-built path builds from, and this
    manifest for the recipes lane. They must agree or the tree ships one COSMIC and
    generates recipes for another. An `epoch-*` tag is never the pin (it is a release
    snapshot, and `is_pinned` rejects it), so both sides carry commit SHAs.
    """
    manifest = load_manifest()
    emit_group(manifest, "shell", out_dir=tmp_path)
    by = manifest.by_name()
    shipping = shipping_pins()
    for name in SHELL_NAMES:
        values = _read_mk(tmp_path / name)
        assert values[f"{_symbol(name)}_VERSION"] == by[name].tag
        assert not by[name].tag.startswith("epoch-")
        if name in shipping:
            assert by[name].tag == shipping[name], (
                f"{name}: cosmic.toml pins {by[name].tag[:12]} but the shipping TSV "
                f"pins {shipping[name][:12]} - the two COSMIC pin sets disagree"
            )


def test_shell_forwards_multiple_binaries(tmp_path):
    """cosmic-applets forwards its many cargo binaries; cosmic-icons none."""
    manifest = load_manifest()
    emit_group(manifest, "shell", out_dir=tmp_path)
    applets = _read_mk(tmp_path / "cosmic-applets")
    assert (
        applets["BR2_PACKAGE_COSMIC_APPLETS_CARGO_BINARIES"]
        == "cosmic-applets cosmic-applet-audio cosmic-applet-battery "
        "cosmic-applet-bluetooth cosmic-applet-minimize cosmic-applet-network "
        "cosmic-applet-notifications cosmic-applet-power "
        "cosmic-applet-status-area cosmic-applet-tiling cosmic-applet-time "
        "cosmic-applet-workspaces cosmic-panel-button"
    )
    # cosmic-icons is a data package: no CARGO_BINARIES line at all.
    icons = _read_mk(tmp_path / "cosmic-icons")
    assert "BR2_PACKAGE_COSMIC_ICONS_CARGO_BINARIES" not in icons


def test_shell_forwards_single_binary(tmp_path):
    """Each binary-carrying shell component forwards exactly its own binary."""
    manifest = load_manifest()
    emit_group(manifest, "shell", out_dir=tmp_path)
    for name, binaries in SHELL_BINARIES.items():
        if not binaries:
            continue
        values = _read_mk(tmp_path / name)
        assert values[f"{_symbol(name)}_CARGO_BINARIES"] == " ".join(binaries)


def test_shell_three_files_per_component(tmp_path):
    """Every shell component ships Config.in, <pkg>.mk and <pkg>.hash."""
    report = emit_group(load_manifest(), "shell", out_dir=tmp_path)
    assert report.emitted_names() == SHELL_NAMES
    for name in SHELL_NAMES:
        pkg_dir = tmp_path / name
        assert (pkg_dir / "Config.in").is_file()
        assert (pkg_dir / f"{name}.mk").is_file()
        assert (pkg_dir / f"{name}.hash").is_file()


def test_shell_dep_graph():
    """The shell dependency graph is the COSMIC shell chain."""
    manifest = load_manifest()
    by = manifest.by_name()
    for name, deps in SHELL_DEPS.items():
        assert by[name].deps == deps, f"{name}: {by[name].deps} != {deps}"


def test_shell_deps_reference_real_components():
    """Every shell dep references a real component in the manifest."""
    manifest = load_manifest()
    all_names = set(manifest.names())
    for name in SHELL_NAMES:
        for dep in SHELL_DEPS[name]:
            assert dep in all_names, f"{name}: dep '{dep}' is not a component"


def test_shell_alone_is_unclosed(tmp_path):
    """Emitting the shell group alone leaves its libcosmic dep unclosed."""
    root = tmp_path / "package"
    emit_group(load_manifest(), "shell", out_dir=root)
    report = closure_lint(load_manifest(), root)
    assert report.is_closed is False
    rules = [v.rule for v in report.violations]
    # Every shell component depends on libcosmic, which has no recipe here.
    assert "unclosed-dep" in rules
    assert any("libcosmic" in str(v) for v in report.violations)


def test_shell_closes_with_core(tmp_path):
    """The whole manifest closes once core + login + shell + apps are emitted.

    The closure linter checks the *whole* manifest, so the shell group (which
    builds on libcosmic, cosmic-protocols and the core compositor) closes only
    once the core and login recipes are present too. The apps group (which
    builds on libcosmic / cosmic-protocols / the shell's cosmic-panel) closes
    only once those are present as well.
    """
    root = tmp_path / "package"
    emit_group(load_manifest(), "shell", out_dir=root)
    assert closure_lint(load_manifest(), root).is_closed is False  # shell alone
    emit_group(load_manifest(), "core", out_dir=root)
    emit_group(load_manifest(), "login", out_dir=root)
    assert closure_lint(load_manifest(), root).is_closed is False  # apps still unemitted
    emit_group(load_manifest(), "apps", out_dir=root)
    report = closure_lint(load_manifest(), root)
    assert report.is_closed is True
    assert report.violations == ()


def test_shell_emit_is_deterministic(tmp_path):
    """Emitting the shell group twice yields byte-identical recipe files."""
    out1 = tmp_path / "a"
    out2 = tmp_path / "b"
    emit_group(load_manifest(), "shell", out_dir=out1)
    emit_group(load_manifest(), "shell", out_dir=out2)
    for name in SHELL_NAMES:
        for suffix in ("Config.in", f"{name}.mk", f"{name}.hash"):
            assert (out1 / name / suffix).read_bytes() == (
                out2 / name / suffix
            ).read_bytes()
