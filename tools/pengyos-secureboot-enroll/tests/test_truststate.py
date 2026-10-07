"""Tests for the trust-state data model (``pengyos_secureboot_enroll.truststate``).

These pin down the two things the tool reasons about before it signs or lints
anything:

* the PK/KEK/db key set and how each key is *identified* (the SHA-256
  fingerprint of its DER certificate -- the same identifier MOK/mokutil uses),
  which is why rotating a key changes its id; and
* the set of EFI binaries the ESP is expected to ship, each signed by a key
  (:class:`EspManifest` / :class:`EspBinary`).

The model is pure data (no I/O, no crypto at import time), so the tests only
exercise Python -- no openssl, no root, no network. A single opt-in integration
test (``PENGYOS_IT=1``) checks the fingerprint scheme against a *real* openssl
certificate.
"""

import hashlib
import os
import shutil
import subprocess

import pytest

from pengyos_secureboot_enroll import truststate as ts

REAL_OPENSSL = shutil.which("openssl") is not None


# --- key-set definition -----------------------------------------------------


def test_key_roles_in_enrollment_order():
    assert ts.KEY_ROLES == ("PK", "KEK", "DB")


def test_key_files_have_key_and_cert_per_role():
    roles = {key.role for key in ts.KEY_FILES}
    assert roles == {"PK", "KEK", "DB"}
    # Each role has a private key and a public certificate.
    names = {key.name for key in ts.KEY_FILES}
    for role in ts.KEY_ROLES:
        assert f"{role}.key" in names
        assert f"{role}.cert" in names


def test_is_private_and_perms():
    for key_file in ts.KEY_FILES:
        if key_file.name.endswith(".key"):
            assert ts.is_private(key_file) is True
            assert ts.perms_for(key_file) == 0o600
        else:
            assert ts.is_private(key_file) is False
            assert ts.perms_for(key_file) == 0o644


def test_role_of_and_key_file_for_name():
    pk = ts.key_file_for_name("PK.key")
    assert pk is not None
    assert ts.role_of(pk) == "PK"
    # An unmanaged filename resolves to None (used by the perm guard elsewhere).
    assert ts.key_file_for_name("NotOurs.key") is None


def test_key_file_name_property_is_filename_only():
    assert ts.KeyFile("DB.key", ts.KeyKind.PRIVATE, "DB", "").path == "DB.key"


# --- key fingerprint (the key id) -------------------------------------------


def test_key_fingerprint_is_sha256_hex_of_der():
    der = b"-----BEGIN CERTIFICATE-----not-a-real-cert-----END-----"
    assert ts.key_fingerprint(der) == hashlib.sha256(der).hexdigest()


def test_key_fingerprint_is_lowercase_hex_and_deterministic():
    fp = ts.key_fingerprint(b"abc")
    assert fp == fp.lower()
    assert all(c in "0123456789abcdef" for c in fp)
    assert len(fp) == 64  # SHA-256 -> 32 bytes -> 64 hex chars
    # Deterministic: same input -> same fingerprint.
    assert ts.key_fingerprint(b"abc") == ts.key_fingerprint(b"abc")


def test_key_fingerprint_changes_with_material():
    # This is what makes `keys --rotate` change the key id.
    assert ts.key_fingerprint(b"key-v1") != ts.key_fingerprint(b"key-v2")


def test_key_entry_signing_key_is_only_db():
    db = ts.KeyEntry(role="DB", key_id="deadbeef", cert_path="DB.cert", key_path="DB.key")
    assert db.is_signing_key is True

    pk = ts.KeyEntry(role="PK", key_id="aa", cert_path="PK.cert")
    assert pk.is_signing_key is False

    kek = ts.KeyEntry(role="KEK", key_id="bb", cert_path="KEK.cert")
    assert kek.is_signing_key is False


def test_key_entry_defaults():
    entry = ts.KeyEntry(role="DB", key_id="cc", cert_path="DB.cert")
    assert entry.key_path is None
    assert entry.perms == 0o644
    # frozen dataclass -> immutable
    with pytest.raises(Exception):
        entry.key_id = "other"  # type: ignore[misc]


# --- EspBinary --------------------------------------------------------------


def test_esp_binary_path_normalised():
    binary = ts.EspBinary(path="///EFI//systemd/../systemd/systemd-boot.efi/")
    assert binary.normalized_path() == "/EFI/systemd/systemd-boot.efi"


def test_esp_binary_path_collapses_and_drops_trailing_slash():
    assert ts.EspBinary(path="/EFI/PengyOS/linux.efi/").normalized_path() == "/EFI/PengyOS/linux.efi"
    assert ts.EspBinary(path="EFI//boot/BOOTx64.efi").normalized_path() == "/EFI/boot/BOOTx64.efi"


def test_esp_binary_path_resolves_dotdot_guard():
    # A path that escapes the ESP root cannot be a real boot entry.
    assert ts.EspBinary(path="/EFI/../../etc/passwd").normalized_path() == "/etc/passwd"


def test_esp_binary_normalized_entry_keeps_fields():
    binary = ts.EspBinary(path="///EFI//a/../b/x.efi", key_id="k", description="d")
    entry = binary.normalized_entry()
    assert entry.path == "/EFI/b/x.efi"
    assert entry.key_id == "k"
    assert entry.description == "d"
    # original is untouched (immutable copy)
    assert binary.path == "///EFI//a/../b/x.efi"


def test_esp_relative_path():
    assert ts.esp_relative_path("EFI//boot/BOOTx64.efi") == "/EFI/boot/BOOTx64.efi"
    assert ts.esp_relative_path("/EFI/PengyOS/linux.efi") == "/EFI/PengyOS/linux.efi"


def test_esp_key_id_accessor():
    assert ts.esp_key_id(ts.EspBinary(path="/x.efi", key_id="k")) == "k"
    assert ts.esp_key_id(ts.EspBinary(path="/x.efi")) is None


# --- EspManifest ------------------------------------------------------------


def test_manifest_add_is_insertion_ordered():
    m = ts.EspManifest().add(ts.EspBinary(path="/EFI/a.efi")).add(ts.EspBinary(path="/EFI/b.efi"))
    assert [b.path for b in m] == ["/EFI/a.efi", "/EFI/b.efi"]


def test_manifest_add_dedupes_by_path():
    m = (
        ts.EspManifest()
        .add(ts.EspBinary(path="/EFI/PengyOS/linux.efi", key_id="abc"))
        .add(ts.EspBinary(path="/EFI/systemd/BOOTx64.efi"))
        .add(ts.EspBinary(path="///EFI//PengyOS/../PengyOS/linux.efi", key_id="zzz"))
    )
    assert len(m) == 2
    # The first occurrence wins on a duplicate path.
    assert m.get("/EFI/PengyOS/linux.efi").key_id == "abc"


def test_manifest_get_and_contains():
    m = ts.EspManifest().add(ts.EspBinary(path="/EFI/PengyOS/linux.efi"))
    assert m.get("/EFI/PengyOS/linux.efi") is not None
    assert m.get("/EFI/missing.efi") is None
    assert "/EFI/PengyOS/linux.efi" in m
    assert "/EFI/missing.efi" not in m


def test_manifest_get_is_case_sensitive_and_canonical():
    m = ts.EspManifest().add(ts.EspBinary(path="/EFI/PengyOS/linux.efi"))
    # A differently-formed but equivalent path still resolves.
    assert m.get("///EFI//PengyOS/../PengyOS/linux.efi") is not None


def test_manifest_key_ids_are_distinct_and_ordered():
    m = (
        ts.EspManifest()
        .add(ts.EspBinary(path="/EFI/a.efi", key_id="first"))
        .add(ts.EspBinary(path="/EFI/b.efi"))  # no pinned key -> skipped
        .add(ts.EspBinary(path="/EFI/c.efi", key_id="second"))
        .add(ts.EspBinary(path="/EFI/d.efi", key_id="first"))  # dup -> skipped
    )
    assert m.key_ids() == ["first", "second"]


def test_manifest_iteration_and_len():
    m = ts.EspManifest().add(ts.EspBinary(path="/EFI/a.efi")).add(ts.EspBinary(path="/EFI/b.efi"))
    assert len(m) == 2
    assert list(m) == list(m)  # stable, repeatable iteration


def test_manifest_to_json_structure():
    m = ts.EspManifest().add(
        ts.EspBinary(path="/EFI/PengyOS/linux.efi", key_id="abc", description="UKI")
    )
    data = m.to_json()
    assert list(data) == ["binaries"]
    entry = data["binaries"][0]
    assert entry == {
        "path": "/EFI/PengyOS/linux.efi",
        "key_id": "abc",
        "description": "UKI",
    }


def test_manifest_normalises_paths_on_construction():
    m = ts.EspManifest(binaries=[ts.EspBinary(path="///EFI//a/../b/x.efi")])
    assert m.binaries[0].path == "/EFI/b/x.efi"


def test_manifest_empty_is_valid():
    m = ts.EspManifest()
    assert len(m) == 0
    assert m.key_ids() == []
    assert m.to_json() == {"binaries": []}


# --- load_expected_binaries -------------------------------------------------


def test_load_expected_binaries_builds_manifest():
    manifest = ts.load_expected_binaries(["/EFI/a.efi", "/EFI/b.efi"])
    assert len(manifest) == 2
    assert [b.path for b in manifest] == ["/EFI/a.efi", "/EFI/b.efi"]


def test_load_expected_binaries_aligns_key_ids_and_descriptions():
    manifest = ts.load_expected_binaries(
        ["/EFI/PengyOS/linux.efi", "/EFI/systemd/BOOTx64.efi"],
        key_ids=["abc", None],
        descriptions=["UKI", "bootloader"],
    )
    uki = manifest.get("/EFI/PengyOS/linux.efi")
    assert uki.key_id == "abc"
    assert uki.description == "UKI"
    boot = manifest.get("/EFI/systemd/BOOTx64.efi")
    assert boot.key_id is None
    assert boot.description == "bootloader"


def test_load_expected_binaries_normalises_paths():
    manifest = ts.load_expected_binaries(["EFI//boot/../boot/BOOTx64.efi"])
    assert manifest.binaries[0].path == "/EFI/boot/BOOTx64.efi"


def test_load_expected_binaries_dedupes():
    manifest = ts.load_expected_binaries(
        ["/EFI/PengyOS/linux.efi", "///EFI//PengyOS/../PengyOS/linux.efi"]
    )
    assert len(manifest) == 1


# --- opt-in integration: real openssl certificate ---------------------------


def _openssl_available() -> bool:
    return shutil.which("openssl") is not None


@pytest.mark.skipif(not _openssl_available(), reason="needs openssl")
def test_key_fingerprint_matches_real_certificate_der(tmp_path):
    """The key id is the SHA-256 of the *DER* certificate -- the identifier MOK
    uses. Prove it against a real openssl-generated self-signed cert."""
    if os.environ.get("PENGYOS_IT") != "1":
        pytest.skip("real-certificate fingerprint check is opt-in (PENGYOS_IT=1)")

    key = tmp_path / "DB.key"
    cert = tmp_path / "DB.cert"
    gen = subprocess.run(
        [
            "openssl", "genpkey",
            "-algorithm", "EC",
            "-pkeyopt", "ec_paramgen_curve:P-256",
            "-out", str(key),
        ],
        capture_output=True,
    )
    assert gen.returncode == 0, gen.stderr.decode()

    req = subprocess.run(
        [
            "openssl", "req", "-x509", "-new", "-key", str(key),
            "-days", "3650", "-subj", "/CN=PengyOS Secure Boot DB",
            "-out", str(cert),
        ],
        capture_output=True,
    )
    assert req.returncode == 0, req.stderr.decode()

    # openssl can export the DER of the certificate.
    der_proc = subprocess.run(
        ["openssl", "x509", "-in", str(cert), "-outform", "DER"],
        capture_output=True,
    )
    assert der_proc.returncode == 0, der_proc.stderr.decode()
    der = der_proc.stdout

    fingerprint = ts.key_fingerprint(der)
    # The fingerprint must be the SHA-256 of the DER bytes.
    assert fingerprint == hashlib.sha256(der).hexdigest()
    # And it must be what openssl reports as the cert digest.
    digest_proc = subprocess.run(
        ["openssl", "x509", "-in", str(cert), "-noout", "-fingerprint", "-sha256"],
        capture_output=True,
        text=True,
    )
    assert digest_proc.returncode == 0, digest_proc.stderr.decode()
    # openssl prints "fingerprint=SHA256=AB:CD:..." (uppercase, colon-separated).
    reported = digest_proc.stdout.split("=", 1)[1].strip().replace(":", "").lower()
    assert reported == fingerprint
