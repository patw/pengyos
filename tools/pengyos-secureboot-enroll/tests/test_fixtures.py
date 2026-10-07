"""Ground the round-trip in a *committed* signed fixture.

These tests pin that a signed tree lints clean and each mutation (unsigned
binary, wrong key, removed db entry) fires its rule:

* a **signed tree lints clean** (the committed fixture), by both
  :func:`pengyos_secureboot_enroll.lint.lint_esp` and the ``lint`` CLI; and
* **each mutation fires exactly its rule** -- ``unsigned``, ``untrusted_signer``
  (wrong key), ``untrusted_signer`` (db entry removed), ``signature_invalid``
  (tampered bytes) and ``missing`` -- applied to a *copy* of the committed
  fixture so the checked-in tree is never dirtied.

A determinism guard rebuilds the fixture from the library and checks it matches
the committed tree byte-for-byte, so the ground truth cannot silently drift from
the code.

Everything runs offline: the stdlib Ed25519 signer needs no external tool, and
the fixtures are committed, so no real ``openssl``, no root and no network are
needed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

from pengyos_secureboot_enroll import cli
from pengyos_secureboot_enroll import keys
from pengyos_secureboot_enroll import lint as lint_module
from pengyos_secureboot_enroll import sign_chain as sc
from pengyos_secureboot_enroll.sign_chain import SIGNATURES_JSON

from conftest import FIXTURE_ESP, FIXTURE_KEYS, FIXTURES_ROOT, SignedFixture, foreign_key_set

# The pinned boot-chain binaries, in manifest order.
SYSTEMD_BOOT = "/EFI/systemd/systemd-bootx64.efi"
FALLBACK = "/EFI/BOOT/BOOTX64.EFI"
UKI = "/EFI/Linux/pengyos-server+1.0.0-a.efi"


def _load_build_fixtures() -> object:
    """Load ``tests/fixtures/build_fixtures.py`` as a module (no package needed)."""
    spec = importlib.util.spec_from_file_location(
        "build_fixtures_under_test", str(Path(__file__).parent / "fixtures" / "build_fixtures.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree_sha(root: Path) -> str:
    """Return a SHA-256 over every file under ``root`` (path + bytes).

    Deterministic and *order-independent*: files are hashed in sorted
    relative-path order. ``os.walk`` yields directories in filesystem order
    (inode/creation order), which differs between a committed checkout and a
    freshly built tree, so iterating the walk directly made this digest
    non-reproducible (same bytes, different hash) -- and made the drift guard
    flaky across checkouts. Sort by relpath to pin it to content only.
    """
    digest = hashlib.sha256()
    files = [Path(dirpath) / name
             for dirpath, _dirs, names in os.walk(root)
             for name in names]
    for path in sorted(files, key=lambda p: str(p.relative_to(root))):
        rel = path.relative_to(root)
        digest.update(str(rel).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _edit_bindings(bindings_path: Path, mutate):
    """Rewrite ``signatures.json`` in place; return the mutated binding list.

    ``mutate`` is a ``list -> list`` transformer over the raw binding dicts, so
    the on-disk ``signatures.json`` and the returned list stay in sync.
    """
    data = json.loads(bindings_path.read_text(encoding="utf-8"))
    data["bindings"] = mutate(data["bindings"])
    bindings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return sc.load_bindings(bindings_path)


# --- the committed fixture lints clean --------------------------------------


def test_committed_fixture_lints_clean(signed_fixture: SignedFixture):
    """A signed tree lints clean: every expected binary is verified."""
    trust_root = keys.load_key_set(signed_fixture.keys_dir)
    bindings = sc.load_bindings(signed_fixture.signatures)
    result = lint_module.lint_esp(
        signed_fixture.esp_dir, sc.build_manifest(), trust_root, bindings=bindings
    )
    assert result.verified is True
    assert result.binaries_checked == 3
    assert result.violations == []


def test_committed_fixture_lints_clean_via_cli(signed_fixture: SignedFixture, capsys):
    """The ``lint`` CLI reports OK and exits 0 for the committed fixture."""
    rc = cli.main(
        ["lint", "--esp", str(signed_fixture.esp_dir), "--keys", str(signed_fixture.keys_dir)]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "lint OK" in out
    assert "3 binaries" in out


def test_committed_fixture_reports_the_db_key_id(signed_fixture: SignedFixture, capsys):
    """The CLI reports the db key id the fixture was signed with."""
    trust_root = keys.load_key_set(signed_fixture.keys_dir)
    cli.main(
        ["lint", "--esp", str(signed_fixture.esp_dir), "--keys", str(signed_fixture.keys_dir)]
    )
    assert trust_root.db.key_id in capsys.readouterr().out


def test_committed_fixture_no_key_material_on_stdout(signed_fixture: SignedFixture, capsys):
    """Lint never prints private key material."""
    cli.main(
        ["lint", "--esp", str(signed_fixture.esp_dir), "--keys", str(signed_fixture.keys_dir)]
    )
    out = capsys.readouterr().out
    assert "PRIVATE KEY" not in out


def test_committed_fixture_signatures_shape(signed_fixture: SignedFixture):
    """The committed ``signatures.json`` has the expected version + 3 bindings."""
    data = json.loads(signed_fixture.signatures.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert len(data["bindings"]) == 3
    for binding in data["bindings"]:
        assert set(binding) == {"path", "sha256", "key_id", "signature", "signer"}
        assert binding["signer"] == "stdlib-ed25519"


# --- mutation: unsigned -----------------------------------------------------


def test_mutation_unsigned_fires_rule(signed_fixture: SignedFixture):
    """Removing a binding makes that binary ``unsigned`` (others still verified)."""
    trust_root = keys.load_key_set(signed_fixture.keys_dir)
    bindings = _edit_bindings(
        signed_fixture.signatures,
        lambda bs: [b for b in bs if b["path"] != FALLBACK],
    )
    result = lint_module.lint_esp(
        signed_fixture.esp_dir, sc.build_manifest(), trust_root, bindings=bindings
    )
    assert result.verified is False
    assert result.binaries_checked == 2
    assert [v.path for v in result.violations] == [FALLBACK]
    assert result.violations[0].reason == "unsigned"


# --- mutation: wrong key (untrusted signer) ---------------------------------


def test_mutation_wrong_key_fires_rule(signed_fixture: SignedFixture, tmp_path):
    """A binary signed by a key NOT in db is ``untrusted_signer``."""
    # A foreign key set with a distinct db key id.
    foreign_dir = foreign_key_set(tmp_path, "WRONGKEY")
    foreign_root = keys.load_key_set(foreign_dir)

    # Re-sign the fallback binary with the foreign db key.
    esp_bytes = (signed_fixture.esp_dir / FALLBACK.lstrip("/")).read_bytes()
    foreign_sig = sc.sign_bytes(esp_bytes, foreign_dir / "DB.key")
    sc.emit_verification_key(foreign_dir / "DB.cert")

    trust_root = keys.load_key_set(signed_fixture.keys_dir)
    bindings = _edit_bindings(
        signed_fixture.signatures,
        lambda bs: [
            {**b, "key_id": foreign_root.db.key_id, "signature": foreign_sig.hex()}
            if b["path"] == FALLBACK
            else b
            for b in bs
        ],
    )
    result = lint_module.lint_esp(
        signed_fixture.esp_dir, sc.build_manifest(), trust_root, bindings=bindings
    )
    assert result.verified is False
    assert result.violations[0].reason == "untrusted_signer"
    assert result.violations[0].path == FALLBACK


def test_mutation_removed_db_entry_fires_rule(signed_fixture: SignedFixture, tmp_path):
    """If the signing key is no longer in db, the binary is ``untrusted_signer``.

    The committed fixture's bindings are signed by the primary db key; linting
    them against a *second* trust root whose db key id differs models a db that
    no longer contains the signer.
    """
    foreign_dir = foreign_key_set(tmp_path, "REMOVED")
    foreign_root = keys.load_key_set(foreign_dir)
    bindings = sc.load_bindings(signed_fixture.signatures)
    result = lint_module.lint_esp(
        signed_fixture.esp_dir, sc.build_manifest(), foreign_root, bindings=bindings
    )
    assert result.verified is False
    assert result.violations[0].reason == "untrusted_signer"
    assert foreign_root.db.key_id != keys.load_key_set(signed_fixture.keys_dir).db.key_id


# --- mutation: tampered bytes (signature invalid) ---------------------------


def test_mutation_tampered_fires_rule(signed_fixture: SignedFixture):
    """Tampering a signed binary's bytes makes its signature ``signature_invalid``."""
    trust_root = keys.load_key_set(signed_fixture.keys_dir)
    bindings = sc.load_bindings(signed_fixture.signatures)
    (signed_fixture.esp_dir / FALLBACK.lstrip("/")).write_bytes(b"TAMPERED")
    result = lint_module.lint_esp(
        signed_fixture.esp_dir, sc.build_manifest(), trust_root, bindings=bindings
    )
    assert result.verified is False
    assert result.binaries_checked == 2
    tampered = [v for v in result.violations if v.path == FALLBACK]
    assert len(tampered) == 1
    assert tampered[0].reason == "signature_invalid"


# --- mutation: missing binary -----------------------------------------------


def test_mutation_missing_fires_rule(signed_fixture: SignedFixture):
    """An expected binary absent from the ESP is ``missing``."""
    trust_root = keys.load_key_set(signed_fixture.keys_dir)
    bindings = sc.load_bindings(signed_fixture.signatures)
    os.remove(signed_fixture.esp_dir / FALLBACK.lstrip("/"))
    result = lint_module.lint_esp(
        signed_fixture.esp_dir, sc.build_manifest(), trust_root, bindings=bindings
    )
    assert result.verified is False
    missing = [v for v in result.violations if v.path == FALLBACK]
    assert len(missing) == 1
    assert missing[0].reason == "missing"


# --- determinism guard ------------------------------------------------------


def _assert_fresh_matches_committed(build, fresh: Path) -> None:
    """The freshly built ``keys``/``signed_esp`` match the committed ones, byte-for-byte."""
    assert _tree_sha(fresh / "keys") == _tree_sha(FIXTURES_ROOT / "keys")
    assert _tree_sha(fresh / "signed_esp") == _tree_sha(FIXTURES_ROOT / "signed_esp")


def test_fixture_is_reproducible_from_library(tmp_path):
    """The committed fixture matches a fresh library build, byte-for-byte.

    This is the drift guard: if the signing/key logic changes, the committed
    fixtures must be regenerated (and this test fails) rather than silently
    going stale.
    """
    build = _load_build_fixtures()
    fresh = tmp_path / "fresh"
    build.build(fresh)
    _assert_fresh_matches_committed(build, fresh)


def test_committed_fixture_matches_its_generator(tmp_path):
    """The committed tree sha is what the current generator produces."""
    build = _load_build_fixtures()
    fresh = tmp_path / "fresh2"
    build.build(fresh)
    _assert_fresh_matches_committed(build, fresh)


# --- the committed key set is a complete trust root -------------------------


def test_committed_keys_are_a_complete_trust_root():
    """The committed key set loads as a full PK/KEK/db trust root."""
    trust_root = keys.load_key_set(FIXTURE_KEYS)
    assert trust_root.key_ids() == {
        "PK": trust_root.by_role("PK").key_id,
        "KEK": trust_root.by_role("KEK").key_id,
        "DB": trust_root.by_role("DB").key_id,
    }
    assert trust_root.db.is_signing_key


def test_committed_fixture_paths_are_pinned():
    """The committed ESP carries exactly the pinned boot-chain binaries."""
    names = sorted(p.name for p in FIXTURE_ESP.rglob("*") if p.is_file())
    assert names == sorted(
        [
            "systemd-bootx64.efi",
            "BOOTX64.EFI",
            "pengyos-server+1.0.0-a.efi",
        ]
    )


def test_committed_key_perms_are_strict(tmp_path):
    """Private keys are written ``0600`` and the key dir ``0700``.

    The strict perms are a property of the fixture *generator*
    (``tests/fixtures/build_fixtures.py``) and of the ``signed_fixture``
    workspace -- not of the checked-out files, since git only tracks the exec
    bit and the committed private keys read back as ``0644``. Assert on a fresh
    generator build so a regression to world-readable private keys still fails
    here (the ``signed_fixture`` fixture restores these bits for the other
    tests; see ``conftest.py``).
    """
    build = _load_build_fixtures()
    fresh = tmp_path / "perms"
    build.build(fresh)
    keys_dir = fresh / "keys"
    assert stat.S_IMODE(keys_dir.stat().st_mode) == 0o700
    for role in ("PK", "KEK", "DB"):
        assert stat.S_IMODE((keys_dir / f"{role}.key").stat().st_mode) == 0o600
        assert stat.S_IMODE((keys_dir / f"{role}.cert").stat().st_mode) == 0o644
