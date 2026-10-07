"""Hash-file authoring tests.

Asserts that the Buildroot ``.hash`` sibling under ``package/pengy/`` records a
sha256 for the *exact* source the recipe downloads, and — the heart of this
item — that the pinned version string is **consistent** between ``pengy.mk`` and
the hash file:

* the ``.hash`` file exists and carries a single ``sha256`` row;
* that row's digest is a well-formed 64-hex sha256;
* its filename equals the recipe's expanded ``PENGY_SOURCE`` (so the hash covers
  the file Buildroot will actually fetch);
* the version embedded in that filename equals ``PENGY_VERSION`` in ``pengy.mk``
  (the version-consistency contract of this item);
* the digest matches the real tarball — an opt-in ``PENGYOS_IT=1`` network check
  that downloads the pinned release and recomputes the sha256.

The opt-in check that the recipe passes the real ``pengyos-recipe-lint`` tool
lives in :mod:`tests.test_recipe_compliance` (the "Recipe tests" item owns the
compliance half of the contract); this module owns the hash-file half.

These read the committed files only (no build, no network, no root) except for
the opt-in network check.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import pytest

from tests import recipe_check

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "package" / "pengy"
MK_FILE = PACKAGE_DIR / "pengy.mk"
HASH_FILE = PACKAGE_DIR / "pengy.hash"

# 64 lowercase/uppercase hex digits — the shape of a sha256 digest.
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _mk_text() -> str:
    return MK_FILE.read_text(encoding="utf-8")


def _hash_text() -> str:
    return HASH_FILE.read_text(encoding="utf-8")


def _expanded_source(text: str) -> str:
    """Expand ``$(PENGY_VERSION)`` in ``PENGY_SOURCE`` the way make would."""
    version = recipe_check.mk_variable(text, "PENGY_VERSION")
    source = recipe_check.mk_variable(text, "PENGY_SOURCE")
    return source.replace("$(PENGY_VERSION)", version)


# --- existence / shape ------------------------------------------------------


def test_hash_file_exists():
    assert HASH_FILE.is_file(), "package/pengy/pengy.hash must exist"


def test_hash_has_sha256_entry():
    entries = recipe_check.hash_sha256_entries(_hash_text())
    assert entries, "pengy.hash has no sha256 entry"


def test_hash_single_sha256_entry():
    """Exactly one sha256 row — the single pinned source the recipe fetches."""
    entries = recipe_check.hash_sha256_entries(_hash_text())
    assert len(entries) == 1, f"expected exactly one sha256 row, got {len(entries)}"


def test_hash_digest_is_64_hex_sha256():
    (entry,) = recipe_check.hash_sha256_entries(_hash_text())
    assert _SHA256_RE.match(entry["digest"]), (
        f"digest {entry['digest']!r} is not a 64-hex sha256"
    )


# --- version consistency: hash <-> pengy.mk ---------------------------------


def test_hash_filename_matches_expanded_source():
    """The hash row's filename must equal the recipe's expanded PENGY_SOURCE.

    This ties the hash to the *exact* file Buildroot will download: if the
    recipe's SOURCE or VERSION drifts, the hash filename no longer matches and
    the recipe fails.
    """
    expanded = _expanded_source(_mk_text())
    entries = recipe_check.hash_sha256_entries(_hash_text())
    filenames = [e["filename"] for e in entries]
    assert expanded in filenames, (
        f"hash filename {filenames} does not cover the recipe's SOURCE {expanded}"
    )


def test_hash_version_matches_mk_version():
    """The version in the hash filename must equal PENGY_VERSION in pengy.mk.

    This is the version-consistency contract of this item: the tarball the hash
    covers is the same upstream release the recipe pins.
    """
    version = recipe_check.mk_variable(_mk_text(), "PENGY_VERSION")
    assert version, "PENGY_VERSION is not pinned in pengy.mk"
    expanded = _expanded_source(_mk_text())
    # PengyR-1.9.0.tar.gz -> 1.9.0
    match = re.search(r"PengyR-(\d+\.\d+\.\d+)\.tar\.gz$", expanded)
    assert match, f"expanded SOURCE {expanded!r} has no parseable PengyR version"
    assert match.group(1) == version, (
        f"hash SOURCE version {match.group(1)!r} != PENGY_VERSION {version!r}"
    )


def test_hash_digest_matches_recipe_source():
    """The single sha256 row must be for the recipe's SOURCE (lint's hash_present)."""
    expanded = _expanded_source(_mk_text())
    entries = recipe_check.hash_sha256_entries(_hash_text())
    assert any(e["filename"] == expanded for e in entries), (
        f"sha256 row does not cover {expanded}"
    )


# --- opt-in network check: digest is the real tarball hash ------------------


@pytest.mark.skipif(
    not os.environ.get("PENGYOS_IT"),
    reason="downloads the pinned tarball (opt-in integration check)",
)
def test_hash_digest_matches_downloaded_tarball(tmp_path):
    """Download the pinned release and confirm the sha256 matches pengy.hash.

    This is the only test that touches the network, and it is opt-in behind
    ``PENGYOS_IT=1`` so the ordinary suite stays offline. It re-derives the
    download URL from the recipe (SITE + "/" + SOURCE) and recomputes the sha256,
    locking the committed digest to the real upstream artifact.
    """
    import urllib.request

    version = recipe_check.mk_variable(_mk_text(), "PENGY_VERSION")
    site = recipe_check.mk_variable(_mk_text(), "PENGY_SITE").replace(
        "$(PENGY_VERSION)", version
    )
    expanded = _expanded_source(_mk_text())

    url = f"{site.rstrip('/')}/{expanded}"
    target = tmp_path / expanded
    # GitHub serves archive downloads only with a browser-like User-Agent;
    # a bare request gets a 404.
    req = urllib.request.Request(url, headers={"User-Agent": "pengy-agent-package"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - opt-in
        data = resp.read()
    target.write_bytes(data)

    digest = hashlib.sha256(data).hexdigest()
    entries = recipe_check.hash_sha256_entries(_hash_text())
    (entry,) = entries
    assert entry["filename"] == expanded
    assert entry["digest"] == digest, (
        f"pengy.hash digest {entry['digest']} != sha256 of downloaded tarball {digest}"
    )
