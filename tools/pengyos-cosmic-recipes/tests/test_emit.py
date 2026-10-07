"""Tests for the recipes-lane emit (``pengyos_cosmic_recipes.emit``).

Exercise :func:`emit_group` over the committed core group: the three recipe
files per component, the pinned reference / license / binaries forwarded from
the manifest, the placeholder-vs-supplied digest seam, determinism and the
empty-group error. All checks are stdlib and need no network, root, display, or
hardware.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_cosmic_recipes import emit as E
from pengyos_cosmic_recipes import manifest as M
from pengyos_cosmic_recipes.manifest import CosmicManifest, load_manifest

# Repo root = parent of this tests/ dir.


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


def _core_manifest() -> CosmicManifest:
    return load_manifest()


def test_emit_writes_three_files_per_component(tmp_path):
    """Every emitted component ships Config.in, <pkg>.mk and <pkg>.hash."""
    report = E.emit_group(_core_manifest(), "core", out_dir=tmp_path)
    assert report.emitted_names() == (
        "cosmic-protocols",
        "libcosmic",
        "cosmic-comp",
        "cosmic-session",
    )
    for name in report.emitted_names():
        pkg_dir = tmp_path / name
        assert (pkg_dir / "Config.in").is_file()
        assert (pkg_dir / f"{name}.mk").is_file()
        assert (pkg_dir / f"{name}.hash").is_file()


def test_emit_pins_manifest_reference(tmp_path):
    """Each recipe's VERSION is the manifest's pinned tag for that component."""
    manifest = _core_manifest()
    report = E.emit_group(manifest, "core", out_dir=tmp_path)
    by = manifest.by_name()
    for result in report.results:
        values = _read_mk(tmp_path / result.name)
        symbol = f"BR2_PACKAGE_{result.name.upper().replace('-', '_')}"
        assert values[f"{symbol}_VERSION"] == by[result.name].tag
        # The emitted version matches what the report claims.
        assert result.version == by[result.name].tag


def test_emit_declares_manifest_license(tmp_path):
    """Each recipe's LICENSE is the manifest's license for that component."""
    manifest = _core_manifest()
    report = E.emit_group(manifest, "core", out_dir=tmp_path)
    by = manifest.by_name()
    for result in report.results:
        values = _read_mk(tmp_path / result.name)
        symbol = f"BR2_PACKAGE_{result.name.upper().replace('-', '_')}"
        assert values[f"{symbol}_LICENSE"] == by[result.name].license


def test_emit_forwards_binaries(tmp_path):
    """A component with binaries forwards CARGO_BINARIES; one without omits it."""
    report = E.emit_group(_core_manifest(), "core", out_dir=tmp_path)
    comp_values = _read_mk(tmp_path / "cosmic-comp")
    assert comp_values["BR2_PACKAGE_COSMIC_COMP_CARGO_BINARIES"] == "cosmic-comp"
    # cosmic-protocols ships no binary target.
    proto_values = _read_mk(tmp_path / "cosmic-protocols")
    assert "BR2_PACKAGE_COSMIC_PROTOCOLS_CARGO_BINARIES" not in proto_values


def test_emit_placeholder_hash_by_default(tmp_path):
    """Without a digest, every recipe gets the placeholder and is flagged."""
    report = E.emit_group(_core_manifest(), "core", out_dir=tmp_path)
    assert report.missing_digest == report.emitted_names()
    for result in report.results:
        assert result.placeholder_hash is True
        hash_text = (tmp_path / result.name / f"{result.name}.hash").read_text()
        assert hash_text.startswith(f"sha256  {E.PLACEHOLDER_HASH}  ")


def test_emit_real_hash_supplied(tmp_path):
    """A supplied digest is pinned verbatim and not flagged as missing."""
    digest = "a" * 64
    report = E.emit_group(
        _core_manifest(), "core", out_dir=tmp_path, source_hash=digest
    )
    assert report.missing_digest == ()
    assert report.is_complete is True
    for result in report.results:
        assert result.placeholder_hash is False
        hash_text = (tmp_path / result.name / f"{result.name}.hash").read_text()
        assert hash_text.startswith(f"sha256  {digest}  ")


def test_emit_is_deterministic(tmp_path):
    """Emitting the same group twice yields byte-identical recipe files."""
    out1 = tmp_path / "a"
    out2 = tmp_path / "b"
    E.emit_group(_core_manifest(), "core", out_dir=out1)
    E.emit_group(_core_manifest(), "core", out_dir=out2)
    for name in ("cosmic-protocols", "libcosmic", "cosmic-comp", "cosmic-session"):
        for suffix in ("Config.in", f"{name}.mk", f"{name}.hash"):
            assert (out1 / name / suffix).read_bytes() == (
                out2 / name / suffix
            ).read_bytes()


def test_emit_empty_group_raises(tmp_path):
    """Emitting a group with no components raises EmitError."""
    from pengyos_cosmic_recipes.emit import EmitError

    # Every real group (core/login/shell/apps) is now authored, so a
    # nonexistent group (which has no components) exercises the empty-group path.
    with pytest.raises(EmitError):
        E.emit_group(_core_manifest(), "nope", out_dir=tmp_path)


def test_emit_missing_digest_reports_placeholder_components(tmp_path):
    """Without a digest, the report lists every component left on the placeholder."""
    report = E.emit_group(_core_manifest(), "core", out_dir=tmp_path)
    # Every component is flagged as missing a real digest.
    assert set(report.missing_digest) == set(report.emitted_names())
    assert report.is_complete is False


def test_emit_login_group_writes_recipe(tmp_path):
    """Emitting the login group writes cosmic-greeter's three recipe files."""
    report = E.emit_group(_core_manifest(), "login", out_dir=tmp_path)
    assert report.emitted_names() == ("cosmic-greeter",)
    pkg_dir = tmp_path / "cosmic-greeter"
    assert (pkg_dir / "Config.in").is_file()
    assert (pkg_dir / "cosmic-greeter.mk").is_file()
    assert (pkg_dir / "cosmic-greeter.hash").is_file()


def test_emit_login_pins_reference_and_license(tmp_path):
    """The login recipe pins the greeter's commit SHA and GPL-3.0-only license."""
    manifest = _core_manifest()
    report = E.emit_group(manifest, "login", out_dir=tmp_path)
    greeter = manifest.by_name()["cosmic-greeter"]
    values = _read_mk(tmp_path / "cosmic-greeter")
    symbol = "BR2_PACKAGE_COSMIC_GREETER"
    assert values[f"{symbol}_VERSION"] == greeter.tag
    assert not greeter.tag.startswith("epoch-")  # pinned to the commit, not the epoch tag
    assert report.results[0].version == greeter.tag
    assert values[f"{symbol}_LICENSE"] == "GPL-3.0-only"


def test_emit_login_forwards_binary(tmp_path):
    """The login recipe forwards the `cosmic-greeter` cargo binary target."""
    E.emit_group(_core_manifest(), "login", out_dir=tmp_path)
    values = _read_mk(tmp_path / "cosmic-greeter")
    assert values["BR2_PACKAGE_COSMIC_GREETER_CARGO_BINARIES"] == "cosmic-greeter"


def test_emit_login_dep_expands_for_engine(tmp_path):
    """cosmic-greeter's name-only dep (libcosmic) expands for the engine."""
    manifest = _core_manifest()
    by = manifest.by_name()
    table = M.component_manifest(by["cosmic-greeter"], by)
    assert "group" not in table
    assert table["name"] == "cosmic-greeter"
    assert table["tag"] == by["cosmic-greeter"].tag
    dep_names = {d["name"] for d in table["deps"]}
    assert dep_names == {"libcosmic"}
    for dep in table["deps"]:
        target = by[dep["name"]]
        assert dep["git_url"] == target.git_url
        assert dep["tag"] == target.tag



