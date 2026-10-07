"""Hash-file tests.

Asserts that ``package/dbus-broker/dbus-broker.hash`` is the Buildroot hash
file for the *pinned* source:

* it exists and is non-empty;
* every recorded entry is a ``sha256  <digest>  <path>`` line whose digest is a
  64-char lowercase hex string (the Buildroot ``.hash`` line format);
* the tarball entry is named exactly ``dbus-broker-<VERSION>.tar.xz`` where
  ``VERSION`` is the ``DBUS_BROKER_VERSION`` pinned in ``dbus-broker.mk`` — so
  the hash is bound to the pinned upstream version;
* it records the ``LICENSE`` digest and every subproject file named in
  ``DBUS_BROKER_LICENSE_FILES`` (the license/dependency provenance);
* the *real* sha256 of every recorded file equals the digest — verified
  offline against a committed fixture tarball (no network, no root).

The fixture tarball lives in ``tests/fixtures/`` so the whole suite runs with
no network access (spec: "use committed fixtures").
"""

from __future__ import annotations

import hashlib
import re
import tarfile
from pathlib import Path

from tests import recipe_check

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "package" / "dbus-broker"
MK_FILE = PACKAGE_DIR / "dbus-broker.mk"
HASH_FILE = PACKAGE_DIR / "dbus-broker.hash"
FIXTURE_TARBALL = REPO_ROOT / "tests" / "fixtures" / "dbus-broker-37.tar.xz"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _mk_text() -> str:
    return MK_FILE.read_text(encoding="utf-8")


def _hash_entries(text: str) -> dict[str, str]:
    """Parse a Buildroot ``.hash`` body into ``{filename: digest}``.

    Comment (``#``) and blank lines are skipped. Each remaining line must be
    exactly three whitespace-separated fields: ``sha256`` algorithm, a 64-char
    hex digest, and the path. Raises ``AssertionError`` on any malformed line
    so a corrupt ``.hash`` fails loudly instead of silently.
    """
    entries: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        assert len(parts) == 3, f"malformed .hash line (want 3 fields): {line!r}"
        algo, digest, name = parts
        assert algo == "sha256", f"unsupported hash algorithm: {algo!r}"
        assert _HEX64.match(digest), f"digest is not a 64-hex sha256: {digest!r}"
        assert name, f".hash line missing a path: {line!r}"
        entries[name] = digest
    return entries


def _extracted_digests(tarball_path: Path) -> dict[str, str]:
    """Return ``{relative_path: sha256}`` for every regular file in the tarball.

    The single top-level source directory (``dbus-broker-37/``) is stripped so
    the paths match the relative paths recorded in the ``.hash`` file.
    """
    with tarfile.open(tarball_path, "r:*") as tf:
        members = tf.getmembers()
        top_dirs = [m.name for m in members if m.isdir() and "/" not in m.name]
        digests: dict[str, str] = {}
        for m in members:
            if not m.isfile():
                continue
            name = m.name
            for d in top_dirs:
                if name.startswith(d + "/"):
                    name = name[len(d) + 1 :]
                    break
            f = tf.extractfile(m)
            if f is None:
                continue
            digests[name] = hashlib.sha256(f.read()).hexdigest()
    return digests


def test_hash_file_exists_and_nonempty():
    assert HASH_FILE.is_file(), "package/dbus-broker/dbus-broker.hash must exist"
    assert HASH_FILE.read_text(encoding="utf-8").strip(), "dbus-broker.hash is empty"


def test_hash_entries_are_sha256_format():
    entries = _hash_entries(HASH_FILE.read_text(encoding="utf-8"))
    assert entries, "no hash entries parsed from dbus-broker.hash"
    for name, digest in entries.items():
        assert _HEX64.match(digest), f"{name}: digest {digest!r} is not sha256 hex"


def test_tarball_hash_matches_pinned_version():
    """The .hash tarball entry must match the version pinned in the .mk."""
    version = recipe_check.mk_variable(_mk_text(), "DBUS_BROKER_VERSION")
    assert version, "DBUS_BROKER_VERSION is not pinned in dbus-broker.mk"
    source = recipe_check.mk_variable(_mk_text(), "DBUS_BROKER_SOURCE")
    assert source, "DBUS_BROKER_SOURCE is not set in dbus-broker.mk"
    # expand the $(DBUS_BROKER_VERSION) reference the way make would
    expanded = source.replace("$(DBUS_BROKER_VERSION)", version)
    entries = _hash_entries(HASH_FILE.read_text(encoding="utf-8"))
    assert expanded in entries, (
        f".hash has no entry for pinned source {expanded!r} (version {version})"
    )


def test_license_and_subproject_files_hashed():
    """LICENSE + every DBUS_BROKER_LICENSE_FILES entry must be in the .hash."""
    entries = _hash_entries(HASH_FILE.read_text(encoding="utf-8"))
    assert "LICENSE" in entries, ".hash must record the LICENSE digest"
    license_files = [
        p.strip()
        for p in recipe_check.mk_variable(_mk_text(), "DBUS_BROKER_LICENSE_FILES").split()
        if p.strip()
    ]
    assert license_files, "DBUS_BROKER_LICENSE_FILES is empty in dbus-broker.mk"
    for path in license_files:
        assert path in entries, f".hash missing subproject file {path!r}"


def test_tarball_hash_is_correct_against_fixture():
    """The recorded sha256 must equal the real digest of the committed fixture."""
    assert FIXTURE_TARBALL.is_file(), "fixture tarball missing (commit tests/fixtures/)"
    entries = _hash_entries(HASH_FILE.read_text(encoding="utf-8"))
    tarball = recipe_check.mk_variable(_mk_text(), "DBUS_BROKER_SOURCE").replace(
        "$(DBUS_BROKER_VERSION)", recipe_check.mk_variable(_mk_text(), "DBUS_BROKER_VERSION")
    )
    recorded = entries.get(tarball)
    assert recorded is not None, f".hash has no entry for {tarball!r}"
    actual = hashlib.sha256(FIXTURE_TARBALL.read_bytes()).hexdigest()
    assert recorded == actual, (
        f"hash mismatch for {tarball!r}: .hash says {recorded}, file is {actual}"
    )


def test_all_license_digests_match_extracted_tarball():
    """Every recorded LICENSE/subproject digest must match the tarball contents."""
    assert FIXTURE_TARBALL.is_file(), "fixture tarball missing (commit tests/fixtures/)"
    entries = _hash_entries(HASH_FILE.read_text(encoding="utf-8"))
    actual = _extracted_digests(FIXTURE_TARBALL)
    for name, digest in entries.items():
        if name.endswith(".tar.xz"):
            # the archive itself is not a member of itself; covered by the
            # tarball-digest test above
            continue
        assert name in actual, f".hash lists {name!r} but it is absent from the tarball"
        assert actual[name] == digest, (
            f"hash mismatch for {name!r}: .hash={digest}, tarball={actual[name]}"
        )
