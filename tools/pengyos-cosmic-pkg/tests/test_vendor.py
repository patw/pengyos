"""Tests for the deterministic, git-dependency-aware vendoring engine
(``pengyos_cosmic_pkg.vendor``).

These run the :func:`vend` engine end-to-end against the committed fixture
workspace (a git dep + a registry dep) and assert:

* the git source is rewritten to ``path:VENDOR/<name>`` in the vendored lock;
* the registry source is passed through unchanged;
* the checked-out git crate is copied into ``VENDOR/<name>/``;
* the ``.cargo/config.toml`` points cargo at the vendored tree;
* the source ``.hash`` is present, well-formed, and matches the tarball;
* the tree is byte-for-byte reproducible across runs.

No network, root, display, or hardware — the git crate is shipped on disk.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import pytest

from pengyos_cosmic_pkg.cargo import load_lock
from pengyos_cosmic_pkg.manifest import ManifestError, load_manifest
from pengyos_cosmic_pkg.vendor import (
    CARGO_CONFIG_REL,
    VENDOR_DIRNAME,
    TAR_SUFFIX,
    vend,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
COMPONENT = FIXTURES / "component.toml"
WORKSPACE = FIXTURES / "workspace"
FIXTURE_LOCK = WORKSPACE / "Cargo.lock"
FIXTURE_GIT_SOURCES = WORKSPACE / "git-sources"


def _vend_into(tmp_path: Path, *, extra_lock: str | None = None) -> Path:
    """Run the vend engine against the fixture into ``tmp_path``; return it."""
    manifest = load_manifest(COMPONENT)
    lock_text = extra_lock if extra_lock is not None else FIXTURE_LOCK.read_text()
    lock_path = tmp_path / "Cargo.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(lock_text, encoding="utf-8")
    lock = load_lock(lock_path)
    return vend(manifest, lock, FIXTURE_GIT_SOURCES, tmp_path)


# --------------------------------------------------------------------------- #
# End-to-end: the vendored tree
# --------------------------------------------------------------------------- #


def test_vend_produces_expected_tree(tmp_path: Path) -> None:
    result = _vend_into(tmp_path)

    files = {p.relative_to(tmp_path).as_posix() for p in result._walk()}
    assert "Cargo.lock" in files
    assert ".cargo/config.toml" in files
    assert "VENDOR/smithay-clipboard/Cargo.toml" in files
    assert "VENDOR/smithay-clipboard/src/lib.rs" in files
    # The tarball is named <name>-<version>.tar.gz and the hash <name>.hash
    # (version is the manifest's pinned tag, v0.1.0).
    assert result.tarball_path.name == f"cosmic-demo-v0.1.0{TAR_SUFFIX}"
    assert result.hash_path.name == "cosmic-demo.hash"


def test_vend_rewrites_git_source_to_path(tmp_path: Path) -> None:
    result = _vend_into(tmp_path)
    lock_text = result.lock_path.read_text()

    # The git dep is rewritten to a path into the vendored tree...
    assert 'source = "path:VENDOR/smithay-clipboard"' in lock_text
    # ...and its pinned SHA fragment is dropped.
    assert "6f33b0c1a7b3f2e4d5c6a7b8c9d0e1f2a3b4c5d6" not in lock_text
    # The root package (no source) is preserved.
    assert 'name = "cosmic-demo"' in lock_text
    assert lock_text.index('name = "cosmic-demo"') < lock_text.index(
        'name = "smithay-clipboard"'
    )


def test_vend_preserves_registry_source(tmp_path: Path) -> None:
    result = _vend_into(tmp_path)
    lock_text = result.lock_path.read_text()

    # The registry dep keeps its registry+ index and its checksum.
    assert 'source = "registry+https://github.com/rust-lang/crates.io-index"' in lock_text
    assert 'checksum = "6fb04a81a29a1c3c9f008a8ea098b41937e2d3f5c5329f18f6b89a8e32565c1a"' in lock_text


def test_vend_copies_git_crate_source(tmp_path: Path) -> None:
    result = _vend_into(tmp_path)
    vendored = result.vendored_dir / "smithay-clipboard"

    # The crate's on-disk source is copied verbatim into VENDOR/<name>/.
    assert (vendored / "Cargo.toml").is_file()
    assert (vendored / "src" / "lib.rs").is_file()
    # The copied content matches the fixture source byte-for-byte.
    copied = (vendored / "Cargo.toml").read_bytes()
    original = (FIXTURE_GIT_SOURCES / "smithay-clipboard" / "Cargo.toml").read_bytes()
    assert copied == original


def test_vend_cargo_config_points_at_vendored_tree(tmp_path: Path) -> None:
    result = _vend_into(tmp_path)
    config = result.cargo_config_path.read_text()

    # crates.io is replaced with the vendored sources directory...
    assert "[source.crates-io]" in config
    assert 'replace-with = "vendored-sources"' in config
    assert '[source.vendored-sources]' in config
    assert 'directory = "VENDOR"' in config
    # ...and the git source is mapped to its own VENDOR/<name> directory.
    assert "[source.git+https://github.com/pop-os/smithay-clipboard?tag=v0.6.0#" in config
    assert 'directory = "VENDOR/smithay-clipboard"' in config


def test_vend_hash_file_is_buildroot_formatted(tmp_path: Path) -> None:
    result = _vend_into(tmp_path)
    hash_text = result.hash_path.read_text()

    # Buildroot's .hash format: "<type>  <hash>  <filename>\n" (two spaces).
    expected = f"sha256  {result.hash_value}  {result.hash_filename}\n"
    assert hash_text == expected
    # The hash value is a 64-char hex sha256.
    assert len(result.hash_value) == 64
    assert all(c in "0123456789abcdef" for c in result.hash_value)


def test_vend_hash_matches_tarball(tmp_path: Path) -> None:
    result = _vend_into(tmp_path)
    digest = hashlib.sha256(result.tarball_path.read_bytes()).hexdigest()
    assert digest == result.hash_value


def test_vend_tarball_is_a_valid_gzipped_tar(tmp_path: Path) -> None:
    result = _vend_into(tmp_path)
    with tarfile.open(result.tarball_path) as tar:
        names = tar.getnames()
    # The vendored tree is wrapped in a single top-level <name>-<version>/
    # directory (the Buildroot extraction convention), so every member is rooted
    # under that wrapper — this is what makes the tarball self-recognising to
    # Buildroot's cargo download post-process (which greps for a VENDOR/ dir
    # nested under exactly one top-level component).
    wrapper = result.tarball_path.name[: -len(TAR_SUFFIX)]  # "cosmic-demo-v0.1.0"
    assert f"{wrapper}/VENDOR/smithay-clipboard/src/lib.rs" in names
    assert f"{wrapper}/Cargo.lock" in names
    assert f"{wrapper}/.cargo/config.toml" in names
    # ...but not the tarball itself or the .hash (they are excluded).
    assert not any(n.endswith(TAR_SUFFIX) for n in names)
    assert not any(n.endswith(".hash") for n in names)


def test_vend_tarball_is_wrapped_for_buildroot_detection(tmp_path: Path) -> None:
    """The vendored tarball is self-recognising to Buildroot's cargo post-process.

    Buildroot's ``support/download/cargo-post-process`` re-runs ``cargo vendor``
    unless the tarball already looks vendored: it greps the member list for a
    ``VENDOR/`` dir nested under exactly one top-level component
    (``grep -q "^[^/]*/VENDOR"``). The wrapper test asserts that property so the
    recipe's vendored source cannot silently fall back to re-vendoring (which
    would fail offline and rewrite the tree).
    """
    import re

    result = _vend_into(tmp_path)
    with tarfile.open(result.tarball_path) as tar:
        names = tar.getnames()

    wrapper = result.tarball_path.name[: -len(TAR_SUFFIX)]
    # Exactly one top-level component, and it is the <name>-<version> wrapper.
    assert all(n.split("/", 1)[0] == wrapper for n in names)
    # The vendored crate is nested one level under the wrapper...
    assert any(re.match(rf"^{re.escape(wrapper)}/VENDOR/", n) for n in names)
    # ...so Buildroot's "^[^/]*/VENDOR" detection matches.
    assert any(re.match(r"^[^/]*/VENDOR", n) for n in names)


# --------------------------------------------------------------------------- #
# Determinism — the headline property
# --------------------------------------------------------------------------- #


def test_vend_is_deterministic_across_runs(tmp_path: Path) -> None:
    ra = _vend_into(tmp_path / "a")
    rb = _vend_into(tmp_path / "b")

    # Same hash value for the same inputs...
    assert ra.hash_value == rb.hash_value

    # ...and byte-identical tarballs (gzip mtime is normalised to 0).
    assert ra.tarball_path.read_bytes() == rb.tarball_path.read_bytes()

    # ...and every other file in the tree is byte-identical.
    for rel in ["Cargo.lock", ".cargo/config.toml",
                "VENDOR/smithay-clipboard/Cargo.toml",
                "VENDOR/smithay-clipboard/src/lib.rs"]:
        assert (ra.out_dir / rel).read_bytes() == (rb.out_dir / rel).read_bytes()


def test_vend_is_idempotent_into_same_dir(tmp_path: Path) -> None:
    # Re-running into the *same* output dir must not change the hash: the
    # generated tarball/.hash must not be folded back into the next tree.
    first = _vend_into(tmp_path)
    second = _vend_into(tmp_path)
    assert first.hash_value == second.hash_value
    assert first.tarball_path.read_bytes() == second.tarball_path.read_bytes()


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #


def test_vend_raises_when_no_git_sources(tmp_path: Path) -> None:
    # A lock with only a registry dep has nothing to rewrite. The root is a
    # [[package]] entry (a Cargo.lock has no separate [package] table).
    no_git = (
        'version = "3"\n'
        '[[package]]\nname = "app"\nversion = "0.1.0"\n'
        '\n[[package]]\n'
        'name = "serde"\nversion = "1.0.0"\n'
        'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
        'checksum = "abc"\n'
    )
    with pytest.raises(ManifestError, match="no git-sourced packages"):
        _vend_into(tmp_path, extra_lock=no_git)


def test_vend_raises_when_git_source_missing(tmp_path: Path) -> None:
    # Point at an empty mirror: the git crate is not checked out there.
    manifest = load_manifest(COMPONENT)
    lock = load_lock(FIXTURE_LOCK)
    empty_mirror = tmp_path / "empty-mirror"
    empty_mirror.mkdir()
    with pytest.raises(ManifestError, match="no checked-out source"):
        vend(manifest, lock, empty_mirror, tmp_path / "out")


# --------------------------------------------------------------------------- #
# JSON summary
# --------------------------------------------------------------------------- #


def test_vend_summary_is_json_serialisable(tmp_path: Path) -> None:
    result = _vend_into(tmp_path)
    summary = result.summary()
    # Round-trips through JSON (the contract for --json output).
    payload = json.loads(json.dumps(summary))
    assert payload["git_packages"] == ["smithay-clipboard"]
    assert payload["hash_type"] == "sha256"
    assert payload["hash_value"] == result.hash_value
    assert "VENDOR/smithay-clipboard/src/lib.rs" in payload["files"]


# --------------------------------------------------------------------------- #
# CLI end-to-end
# --------------------------------------------------------------------------- #


def test_cli_vendor_end_to_end(run_cli, tmp_path) -> None:
    out = tmp_path / "vend"
    rc = run_cli(
        "vendor",
        "--manifest",
        str(COMPONENT),
        "--cargo-lock",
        str(FIXTURE_LOCK),
        "--git-sources",
        str(FIXTURE_GIT_SOURCES),
        "--out",
        str(out),
    )
    assert rc == 0
    assert (out / "Cargo.lock").is_file()
    assert (out / ".cargo" / "config.toml").is_file()
    assert (out / "VENDOR" / "smithay-clipboard" / "src" / "lib.rs").is_file()
    assert (out / "cosmic-demo.hash").is_file()


def test_cli_vendor_json_mode(run_cli, capsys, tmp_path) -> None:
    out = tmp_path / "vend"
    rc = run_cli(
        "vendor",
        "--manifest",
        str(COMPONENT),
        "--cargo-lock",
        str(FIXTURE_LOCK),
        "--git-sources",
        str(FIXTURE_GIT_SOURCES),
        "--out",
        str(out),
        "--json",
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "vendor"
    assert payload["ok"] is True
    assert payload["git_packages"] == ["smithay-clipboard"]


def test_cli_vendor_default_git_sources(run_cli, tmp_path) -> None:
    # With no --git-sources, vendor defaults to <--cargo-lock dir>/git-sources.
    # The fixture ships its mirror there, so this exercises the default.
    out = tmp_path / "vend"
    rc = run_cli(
        "vendor",
        "--manifest",
        str(COMPONENT),
        "--cargo-lock",
        str(FIXTURE_LOCK),
        "--out",
        str(out),
    )
    assert rc == 0
    assert (out / "VENDOR" / "smithay-clipboard" / "src" / "lib.rs").is_file()
