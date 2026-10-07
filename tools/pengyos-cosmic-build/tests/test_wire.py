"""Tests for the pengyos-cosmic-build wire engine (:mod:`wire`).

:func:`wire` is the *assembly* step that closes the gap between the emitted
recipes (see :mod:`.emit`) and a buildable ``BR2_EXTERNAL`` tree: it installs the
emitted COSMIC cargo recipes + the sibling native-dep recipes into the tree's
``package/`` dir, installs the desktop defconfig(s) and board overlay(s), wires
the tree's ``Config.in`` (a ``source`` line per installed package), and
registers the ``pengyos-desktop`` target in ``targets.toml`` — every step
idempotently.

The two pure helpers (:func:`add_config_in_sources` / :func:`register_target`)
are unit-tested directly; :func:`wire` is tested end-to-end into a temp tree
(emitting real recipes via :mod:`.emit`, then wiring them in); the CLI path is
exercised as a subprocess (with the sibling lanes on ``PYTHONPATH``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pengyos_cosmic_build.emit import emit_group
from pengyos_cosmic_build.records import VendorRecord, write_record
from pengyos_cosmic_build.wire import (
    _is_section_comment,
    add_config_in_sources,
    register_target,
    wire,
)

# --- fixtures: a cosmic-recipes manifest + vendor records (from test_emit) ----


def _manifest(tmp_path: Path) -> Path:
    """A cosmic-recipes-shape manifest (matches the committed fixture shape)."""
    text = """
[[component]]
name = "cosmic-comp"
group = "core"
git_url = "https://github.com/pop-os/libcosmic/cosmic-comp"
tag = "v0.6.0"
license = "MIT OR Apache-2.0"
features = []
binaries = ["cosmic-comp"]
deps = []

[[component]]
name = "cosmic-protocols"
group = "core"
git_url = "https://github.com/pop-os/cosmic-protocols"
tag = "v0.6.0"
license = "MIT"
features = []
binaries = []
deps = []
"""
    path = tmp_path / "cosmic.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _write_vendor_records(vendor_dir: Path, *records: VendorRecord) -> None:
    """Write exactly the passed records (real sha256 + real SITE)."""
    vendor_dir.mkdir(parents=True, exist_ok=True)
    for rec in records:
        write_record(rec, vendor_dir / f"{rec.name}.json")


def _record(
    name: str,
    *,
    version: str = "v0.6.0",
    sha256: str = "b" * 64,
    site: str = "file:///home/ops/dl",
) -> VendorRecord:
    """A valid VendorRecord, overridable per-test."""
    return VendorRecord(
        name=name,
        version=version,
        tarball=f"{name}-{version}.tar.gz",
        sha256=sha256,
        site=site,
    )


# --- a staging source tree (package/ configs/ board/) -----------------------


def _sources_tree(tmp_path: Path, manifest: Path) -> Path:
    """Build a ``--sources`` staging tree: emit recipes + a defconfig + a board.

    * ``package/`` — the emitted COSMIC cargo recipes (via :func:`emit_group`).
    * ``configs/`` — the Desktop defconfig.
    * ``board/`` — a board overlay with a ``post-image.sh``.

    Returns the staging tree root.
    """
    sources = tmp_path / "sources"
    (sources / "configs").mkdir(parents=True)
    (sources / "board" / "desktop" / "rootfs-overlay").mkdir(parents=True)

    # Emit the cargo recipes into <sources>/package/.
    from pengyos_cosmic_recipes.manifest import load_manifest

    vendor = tmp_path / "vendor"
    _write_vendor_records(
        vendor,
        _record("cosmic-comp", site="file:///home/ops/dl/cosmic-comp"),
        _record("cosmic-protocols", site="file:///home/ops/dl/cosmic-protocols"),
    )
    emit_group(load_manifest(manifest), "core", vendor, sources)

    (sources / "configs" / "pengyos_desktop_defconfig").write_text(
        "BR2_PACKAGE_COSMIC_PORTAL=y\n", encoding="utf-8"
    )
    (sources / "board" / "desktop" / "post-image.sh").write_text(
        "#!/bin/sh\necho hi\n", encoding="utf-8"
    )
    return sources


def _repo_with_config_in(tmp_path: Path, config_in: str) -> Path:
    """A fresh ``BR2_EXTERNAL`` tree whose ``Config.in`` is *config_in*."""
    repo = tmp_path / "repo"
    (repo / "package").mkdir(parents=True)
    (repo / "Config.in").write_text(config_in, encoding="utf-8")
    return repo


# the real-tree Config.in native-dep section opener (matched by phrase)
_NATIVE_COMMENT = "\t# COSMIC native (non-Rust) dependencies Buildroot 2026.08 does not ship."
_CARGO_PLACEHOLDER = (
    "\t# COSMIC cargo recipes (pengyos-cosmic-pkg / -recipes) are emitted here next;\n"
    "\t# their `source` lines are added once the vendored digests are pinned.\n"
)


def _real_tree_config_in() -> str:
    """The ``buildroot-external/Config.in`` shape (native deps + placeholder)."""
    return (
        'menu "PengyOS (external tree)"\n'
        "\n"
        + _NATIVE_COMMENT
        + "\n"
        "\t# Emitted by pengyos-cosmic-native-deps (committed under that tool) and\n"
        "\t# installed here so the Desktop target's `BR2_PACKAGE_*` symbols resolve.\n"
        '\tsource "$BR2_EXTERNAL_PENGYOS_PATH/package/greetd/Config.in"\n'
        '\tsource "$BR2_EXTERNAL_PENGYOS_PATH/package/xdg-desktop-portal/Config.in"\n'
        '\tsource "$BR2_EXTERNAL_PENGYOS_PATH/package/cosmic-portal/Config.in"\n'
        '\tsource "$BR2_EXTERNAL_PENGYOS_PATH/package/fonts/Config.in"\n'
        "\n"
        + _CARGO_PLACEHOLDER
        + "\n"
        "endmenu\n"
    )


# --- add_config_in_sources --------------------------------------------------


def test_add_config_in_sources_adds_missing_lines():
    config_in = 'menu "X"\n\nendmenu\n'
    out, changed = add_config_in_sources(config_in, ["cosmic-comp", "cosmic-protocols"])
    assert changed
    assert '\tsource "$BR2_EXTERNAL_PENGYOS_PATH/package/cosmic-comp/Config.in"' in out
    assert '\tsource "$BR2_EXTERNAL_PENGYOS_PATH/package/cosmic-protocols/Config.in"' in out
    # The lines live in a single dedicated section, just before endmenu.
    assert "COSMIC cargo recipes" in out
    assert out.rstrip("\n").endswith("endmenu")


def test_add_config_in_sources_is_idempotent():
    config_in = _real_tree_config_in()
    out1, changed1 = add_config_in_sources(
        config_in, ["cosmic-comp", "cosmic-protocols"]
    )
    assert changed1
    out2, changed2 = add_config_in_sources(out1, ["cosmic-comp", "cosmic-protocols"])
    # Re-running with the same package set is a byte-identical no-op.
    assert not changed2
    assert out1 == out2


def test_add_config_in_sources_does_not_duplicate_present_lines():
    """A package already sourced (e.g. a native dep) is not sourced again.

    Sourcing a Config.in twice redefines its kconfig ``config`` entries, which
    fails the Buildroot parse — so a line already present is left where it is.
    """
    config_in = _real_tree_config_in()
    out, _ = add_config_in_sources(config_in, ["cosmic-comp", "greetd"])
    # greetd was already sourced in the native-dep section — it must not appear
    # a second time in the cargo section.
    assert out.count("package/greetd/Config.in") == 1
    # cosmic-comp is new — it is added exactly once.
    assert out.count("package/cosmic-comp/Config.in") == 1


def test_add_config_in_sources_reuses_existing_section_not_stacks():
    """An incremental run rewrites the existing section instead of stacking."""
    config_in = _real_tree_config_in()
    out1, _ = add_config_in_sources(config_in, ["cosmic-comp"])
    # Add a second package to the same tree — the section is rewritten in place.
    out2, _ = add_config_in_sources(out1, ["cosmic-comp", "cosmic-protocols"])
    # Exactly one section opener comment — no second stacked section.
    assert out2.count("COSMIC cargo recipes") == 1
    assert out2.count("package/cosmic-comp/Config.in") == 1
    assert out2.count("package/cosmic-protocols/Config.in") == 1


def test_add_config_in_sources_preserves_trailing_newline():
    with_nl, changed = add_config_in_sources('menu "X"\n\nendmenu\n', ["p"])
    assert changed
    assert with_nl.endswith("\n")
    assert not with_nl.endswith("\n\n")


def test_add_config_in_sources_preserves_no_trailing_newline():
    without_nl, _ = add_config_in_sources('menu "X"\n\nendmenu', ["p"])
    assert not without_nl.endswith("\n")


def test_add_config_in_sources_requires_endmenu():
    with pytest.raises(AssertionError):
        add_config_in_sources('menu "X"\n\n', ["p"])


def test_add_config_in_sources_empty_packages_is_noop():
    config_in = _real_tree_config_in()
    out, changed = add_config_in_sources(config_in, [])
    assert not changed
    assert out == config_in


# --- _is_section_comment ----------------------------------------------------


def test_is_section_comment_matches_by_phrase():
    # A real tree may carry the recipes lane's *different* placeholder comment;
    # it must still be recognised as the section opener.
    assert _is_section_comment("\t# COSMIC cargo recipes (pengyos-cosmic-pkg / -recipes) are emitted here next;")
    assert _is_section_comment("# COSMIC cargo recipes")
    # Not a section comment.
    assert not _is_section_comment("\t# some other comment")
    assert not _is_section_comment('\tsource "$BR2_EXTERNAL_PENGYOS_PATH/package/p/Config.in"')
    assert not _is_section_comment("endmenu")


# --- register_target --------------------------------------------------------


def test_register_target_appends_block():
    targets = '[[targets]]\nname = "pengyos-server"\n'
    out, changed = register_target(targets)
    assert changed
    assert 'name = "pengyos-desktop"' in out
    assert 'br_version = "2026.08"' in out
    assert 'defconfig = "pengyos_desktop_defconfig"' in out
    # The existing target is preserved.
    assert 'name = "pengyos-server"' in out


def test_register_target_is_idempotent():
    targets = '[[targets]]\nname = "pengyos-desktop"\n'
    out1, changed1 = register_target(targets)
    assert not changed1
    out2, changed2 = register_target(out1)
    assert not changed2
    assert out1 == out2
    # Exactly one registration.
    assert out2.count('name = "pengyos-desktop"') == 1


def test_register_target_normalises_trailing_newline():
    no_nl = '[[targets]]\nname = "a"'
    out, _ = register_target(no_nl)
    assert out.endswith("\n")


# --- wire: full integration -------------------------------------------------


def test_wire_installs_everything_and_registers_target(tmp_path: Path):
    manifest = _manifest(tmp_path)
    sources = _sources_tree(tmp_path, manifest)
    repo = _repo_with_config_in(tmp_path, _real_tree_config_in())
    targets = tmp_path / "targets.toml"
    targets.write_text(
        '[[targets]]\nname = "pengyos-server"\nbr_version = "2026.08"\n',
        encoding="utf-8",
    )

    report = wire(repo, sources, targets=targets)

    # The emitted cargo recipes landed in the tree's package/.
    assert (repo / "package" / "cosmic-comp" / "cosmic-comp.mk").is_file()
    assert (repo / "package" / "cosmic-protocols" / "cosmic-protocols.mk").is_file()
    # The sibling native-dep recipes were installed too.
    for dep in ("greetd", "xdg-desktop-portal", "cosmic-portal", "fonts"):
        assert (repo / "package" / dep).is_dir(), f"{dep} native dep missing"
    # The defconfig + board overlay were installed.
    assert (repo / "configs" / "pengyos_desktop_defconfig").is_file()
    assert (repo / "board" / "desktop" / "post-image.sh").is_file()
    # The target was registered.
    assert report.target_registered
    assert "pengyos-desktop" in targets.read_text()


def test_wire_wires_config_in_without_duplicating(tmp_path: Path):
    manifest = _manifest(tmp_path)
    sources = _sources_tree(tmp_path, manifest)
    repo = _repo_with_config_in(tmp_path, _real_tree_config_in())

    wire(repo, sources, targets=None)
    config_in = (repo / "Config.in").read_text()

    # The new cargo recipes are sourced...
    assert 'package/cosmic-comp/Config.in' in config_in
    assert 'package/cosmic-protocols/Config.in' in config_in
    # ...but the already-present native-dep lines are not duplicated.
    assert config_in.count("package/greetd/Config.in") == 1
    assert config_in.count("package/cosmic-portal/Config.in") == 1
    # Exactly one COSMIC-recipes section (no stacking).
    assert config_in.count("COSMIC cargo recipes") == 1


def test_wire_is_idempotent(tmp_path: Path):
    manifest = _manifest(tmp_path)
    sources = _sources_tree(tmp_path, manifest)
    repo = _repo_with_config_in(tmp_path, _real_tree_config_in())
    targets = tmp_path / "targets.toml"
    targets.write_text(
        '[[targets]]\nname = "pengyos-server"\nbr_version = "2026.08"\n',
        encoding="utf-8",
    )

    first = wire(repo, sources, targets=targets)
    assert first.config_in_changed
    assert first.target_registered

    # Capture the wired state, then wire again.
    config_after_first = (repo / "Config.in").read_text()
    targets_after_first = targets.read_text()

    second = wire(repo, sources, targets=targets)
    assert not second.config_in_changed
    assert not second.target_registered
    # Nothing changed on the second run.
    assert (repo / "Config.in").read_text() == config_after_first
    assert targets.read_text() == targets_after_first


def test_wire_missing_sources_raises(tmp_path: Path):
    from pengyos_cosmic_build.wire import WireError

    with pytest.raises(WireError):
        wire(tmp_path / "repo", tmp_path / "does-not-exist", targets=None)


def test_wire_missing_targets_file_raises(tmp_path: Path):
    from pengyos_cosmic_build.wire import WireError

    manifest = _manifest(tmp_path)
    sources = _sources_tree(tmp_path, manifest)
    repo = _repo_with_config_in(tmp_path, _real_tree_config_in())

    with pytest.raises(WireError):
        wire(repo, sources, targets=tmp_path / "nope.toml")


def test_wire_without_targets_still_wires_tree(tmp_path: Path):
    manifest = _manifest(tmp_path)
    sources = _sources_tree(tmp_path, manifest)
    repo = _repo_with_config_in(tmp_path, _real_tree_config_in())

    report = wire(repo, sources, targets=None)
    # The tree is wired (recipes + Config.in) even though no target is registered.
    assert (repo / "package" / "cosmic-comp" / "cosmic-comp.mk").is_file()
    assert report.config_in_changed
    assert not report.target_registered


# --- CLI --------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    """Run ``python -m pengyos_cosmic_build`` as a subprocess (siblings on path)."""
    tools_dir = Path(__file__).resolve().parent.parent.parent
    env = dict(os.environ)
    siblings = ("pengyos-cosmic-pkg", "pengyos-cosmic-recipes", "pengyos-cosmic-native-deps")
    parts = [str(tools_dir / name) for name in siblings]
    env["PYTHONPATH"] = os.pathsep.join(parts) + (
        os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else ""
    )
    return subprocess.run(
        [sys.executable, "-m", "pengyos_cosmic_build", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_cli_wire_success(tmp_path: Path):
    manifest = _manifest(tmp_path)
    sources = _sources_tree(tmp_path, manifest)
    repo = _repo_with_config_in(tmp_path, _real_tree_config_in())
    targets = tmp_path / "targets.toml"
    targets.write_text(
        '[[targets]]\nname = "pengyos-server"\nbr_version = "2026.08"\n',
        encoding="utf-8",
    )

    proc = _run(
        "wire",
        "--repo",
        str(repo),
        "--sources",
        str(sources),
        "--targets",
        str(targets),
    )
    assert proc.returncode == 0, proc.stderr
    assert "pengyos-desktop" in proc.stdout
    assert (repo / "package" / "cosmic-comp" / "cosmic-comp.mk").is_file()


def test_cli_wire_json_mode(tmp_path: Path):
    manifest = _manifest(tmp_path)
    sources = _sources_tree(tmp_path, manifest)
    repo = _repo_with_config_in(tmp_path, _real_tree_config_in())
    targets = tmp_path / "targets.toml"
    targets.write_text("[[targets]]\n", encoding="utf-8")

    proc = _run(
        "wire",
        "--repo",
        str(repo),
        "--sources",
        str(sources),
        "--targets",
        str(targets),
        "--json",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["command"] == "wire"
    assert payload["ok"] is True
    assert payload["target_registered"] is True
    assert "cosmic-comp" in payload["installed_packages"]
    assert "greetd" in payload["installed_packages"]


def test_cli_wire_missing_sources_reports_cleanly(tmp_path: Path):
    repo = _repo_with_config_in(tmp_path, _real_tree_config_in())

    proc = _run(
        "wire",
        "--repo",
        str(repo),
        "--sources",
        str(tmp_path / "nope"),
        "--targets",
        "",
    )
    assert proc.returncode == 1
    assert "not found" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_cli_wire_missing_sources_json_reports_cleanly(tmp_path: Path):
    repo = _repo_with_config_in(tmp_path, _real_tree_config_in())

    proc = _run(
        "wire",
        "--repo",
        str(repo),
        "--sources",
        str(tmp_path / "nope"),
        "--targets",
        "",
        "--json",
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert "not found" in payload["error"]
    assert "Traceback" not in proc.stderr


def test_cli_wire_without_targets_skips_registration(tmp_path: Path):
    manifest = _manifest(tmp_path)
    sources = _sources_tree(tmp_path, manifest)
    repo = _repo_with_config_in(tmp_path, _real_tree_config_in())

    proc = _run(
        "wire",
        "--repo",
        str(repo),
        "--sources",
        str(sources),
        "--targets",
        "",
    )
    assert proc.returncode == 0, proc.stderr
    # The tree is wired but no target is registered (--targets "" skips it).
    assert (repo / "package" / "cosmic-comp" / "cosmic-comp.mk").is_file()
    # Regression guard: a bare `wire --repo --sources` (or --targets "") must
    # NOT register the desktop target — it must not touch a real targets.toml.
    assert "target: registered" not in proc.stdout
