"""Tests for the ``sign-chain`` subtask (``sign_chain`` + ``cmd_sign_chain``).

These pin down the four things ``sign-chain`` must do:

* **sign each EFI binary** present in the ESP with the ``db`` key;
* **record the signature binding** -- the binary's SHA-256, the signing key id
  (the ``db`` fingerprint) and the signature;
* **verify every signature** against the ``db`` certificate (a tampered binary
  or a signature over a different key fails); and
* **persist the bindings** (``signatures.json`` + the Ed25519 verification-key
  sidecar) so ``lint`` can check them later.

Every test is offline: the stdlib signer needs no external tool, and the openssl
shim is a deterministic fake in tests, so no real ``openssl``, no root and no
network are needed. A single opt-in integration test (``PENGYOS_IT=1``) runs
*real* ``openssl`` through the whole sign -> verify round-trip.
"""

import base64
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from pengyos_secureboot_enroll import cli
from pengyos_secureboot_enroll import keys
from pengyos_secureboot_enroll import sign_chain as sc
from pengyos_secureboot_enroll import signer as signer_module
from pengyos_secureboot_enroll.sign_chain import SIGNATURES_JSON
from pengyos_secureboot_enroll.truststate import KeyPerms

REAL_OPENSSL = shutil.which("openssl") is not None


# --- fixtures --------------------------------------------------------------


def _make_pem(body: bytes) -> bytes:
    """Wrap ``body`` in a single-cert PEM with correct base64 padding."""
    b64 = base64.b64encode(body).decode("ascii")
    wrapped = "\n".join(b64[i : i + 64] for i in range(0, len(b64), 64))
    return f"-----BEGIN CERTIFICATE-----\n{wrapped}\n-----END CERTIFICATE-----\n".encode("utf-8")


def _write_key_set(dirpath: "os.PathLike | str") -> Path:
    """Write a deterministic, valid PEM key set (PK/KEK/db) under ``dirpath``.

    Returns the absolute path. The db private key is a fixed 42-byte blob (the
    stdlib signer derives a 32-byte Ed25519 seed from its SHA-256).
    """
    dirpath = Path(os.path.abspath(dirpath))
    dirpath.mkdir(parents=True, exist_ok=True)
    for role in ("PK", "KEK", "DB"):
        cert = dirpath / f"{role}.cert"
        cert.write_bytes(_make_pem(f"CERT-{role}-DER-BYTES-0123456789".encode()))
        key = dirpath / f"{role}.key"
        key.write_bytes(f"PRIVATE-KEY-{role}-BYTES-0123456789abcdef".encode())
        key.chmod(KeyPerms.PRIVATE)
        cert.chmod(KeyPerms.PUBLIC)
    return dirpath


def _write_esp(esp_dir: "os.PathLike | str", paths: dict) -> Path:
    """Write the given ``{esp_relative_path: bytes}`` into ``esp_dir``."""
    esp_dir = Path(os.path.abspath(esp_dir))
    for rel, data in paths.items():
        target = esp_dir / rel.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return esp_dir


# --- a deterministic fake openssl shim -------------------------------------


class FakeOpenShim:
    """A deterministic stand-in for the openssl shim that models ECDSA.

    ``-sign`` returns ``b"SIG-" + sha256(data)``; ``-verify`` succeeds iff the
    signature matches the data's digest (raising :class:`GenerateError`
    otherwise); ``x509 -pubkey`` returns a deterministic fake public key. This
    makes :class:`signer.OpenSSLSigner` fully testable without real openssl.
    """

    def __init__(self) -> None:
        self.cmds: list[list[str]] = []

    def __call__(self, cmd):
        self.cmds.append(list(cmd))
        name = cmd[1]
        if name == "x509":  # x509 -pubkey -noout
            return b"-----BEGIN PUBLIC KEY-----\nMIIcFAKEPUB\n-----END PUBLIC KEY-----\n"
        if name == "dgst" and "-sign" in cmd:
            data = open(cmd[-1], "rb").read()  # noqa: SIM115 (tempfile scope)
            return b"SIG-" + hashlib.sha256(data).hexdigest().encode()
        if name == "dgst" and "-verify" in cmd:
            data = open(cmd[-1], "rb").read()  # noqa: SIM115 (tempfile scope)
            sig = open(cmd[cmd.index("-signature") + 1], "rb").read()
            expected = b"SIG-" + hashlib.sha256(data).hexdigest().encode()
            if sig != expected:
                raise signer_module.SignError("verification failed")
            return b"Verified OK\n"
        raise AssertionError(f"unexpected shim command: {cmd}")


# --- build_manifest --------------------------------------------------------


def test_build_manifest_default_is_pinned_boot_chain():
    manifest = sc.build_manifest()
    paths = [b.path for b in manifest]
    assert paths == [
        "/EFI/systemd/systemd-bootx64.efi",
        "/EFI/BOOT/BOOTX64.EFI",
        "/EFI/Linux/pengyos-server+1.0.0-a.efi",
    ]


def test_build_manifest_custom_slots_and_edition():
    manifest = sc.build_manifest(edition="desktop", version="2.3.1", slots=["a", "b"])
    paths = [b.path for b in manifest]
    assert "/EFI/systemd/systemd-bootx64.efi" in paths
    assert "/EFI/BOOT/BOOTX64.EFI" in paths
    assert "/EFI/Linux/pengyos-desktop+2.3.1-a.efi" in paths
    assert "/EFI/Linux/pengyos-desktop+2.3.1-b.efi" in paths


def test_build_manifest_explicit_paths():
    manifest = sc.build_manifest(paths=["/EFI/BOOT/BOOTX64.EFI", "/EFI/Linux/custom.efi"])
    assert [b.path for b in manifest] == [
        "/EFI/BOOT/BOOTX64.EFI",
        "/EFI/Linux/custom.efi",
    ]


# --- StdlibSigner ----------------------------------------------------------


def test_stdlib_signer_sign_verify_roundtrip(tmp_path):
    _write_key_set(tmp_path)
    trust_root = keys.load_key_set(tmp_path)
    data = b"EFI binary bytes"
    signer = signer_module.StdlibSigner()
    sig = signer.sign(data, tmp_path / "DB.key")
    assert len(sig) == 64
    assert signer.verify(sig, data, tmp_path / "DB.cert") is False  # no sidecar yet
    sc.emit_verification_key(tmp_path / "DB.cert", signer)
    assert signer.verify(sig, data, tmp_path / "DB.cert") is True


def test_stdlib_signer_tampered_fails(tmp_path):
    _write_key_set(tmp_path)
    signer = signer_module.StdlibSigner()
    sc.emit_verification_key(tmp_path / "DB.cert", signer)
    sig = signer.sign(b"original", tmp_path / "DB.key")
    assert signer.verify(sig, b"tampered", tmp_path / "DB.cert") is False


def test_stdlib_signer_wrong_key_fails(tmp_path):
    keys_a = _write_key_set(os.path.join(str(tmp_path), "a"))
    keys_b = _write_key_set(os.path.join(str(tmp_path), "b"))
    signer = signer_module.StdlibSigner()
    sc.emit_verification_key(os.path.join(keys_a, "DB.cert"), signer)
    sig = signer.sign(b"data", os.path.join(keys_a, "DB.key"))
    # Signed by key A must not verify against key B's cert/sidecar.
    assert signer.verify(sig, b"data", os.path.join(keys_b, "DB.cert")) is False


def test_stdlib_signer_missing_key_raises(tmp_path):
    os.makedirs(tmp_path, exist_ok=True)
    with pytest.raises(signer_module.SignError):
        signer_module.StdlibSigner().sign(b"data", tmp_path / "NOPE.key")


def test_seed_for_private_key_is_32_bytes():
    seed = signer_module.seed_for_private_key(b"some private key bytes")
    assert len(seed) == 32


# --- OpenSSLSigner (fake shim) ---------------------------------------------


def test_openssl_signer_roundtrip(tmp_path, monkeypatch):
    _write_key_set(tmp_path)
    shim = FakeOpenShim()
    monkeypatch.setattr(signer_module, "openssl_run", shim)
    signer = signer_module.OpenSSLSigner()
    data = b"EFI binary bytes"
    sig = signer.sign(data, tmp_path / "DB.key")
    assert sig.startswith(b"SIG-")
    assert signer.verify(sig, data, tmp_path / "DB.cert") is True


def test_openssl_signer_tampered_fails(tmp_path, monkeypatch):
    _write_key_set(tmp_path)
    monkeypatch.setattr(signer_module, "openssl_run", FakeOpenShim())
    signer = signer_module.OpenSSLSigner()
    sig = signer.sign(b"original", tmp_path / "DB.key")
    assert signer.verify(sig, b"tampered", tmp_path / "DB.cert") is False


def test_openssl_signer_missing_key_raises(tmp_path, monkeypatch):
    os.makedirs(tmp_path, exist_ok=True)
    monkeypatch.setattr(signer_module, "openssl_run", FakeOpenShim())
    with pytest.raises(signer_module.SignError):
        signer_module.OpenSSLSigner().sign(b"data", tmp_path / "NOPE.key")


# --- select_signer ---------------------------------------------------------


def test_select_signer_defaults_to_stdlib():
    assert isinstance(signer_module.select_signer(), signer_module.StdlibSigner)
    assert isinstance(signer_module.select_signer(None), signer_module.StdlibSigner)


def test_select_signer_openssl():
    assert isinstance(
        signer_module.select_signer("openssl"), signer_module.OpenSSLSigner
    )


def test_select_signer_from_env(monkeypatch):
    monkeypatch.setenv("PENGYOS_SIGNER", "openssl")
    assert isinstance(signer_module.select_signer(), signer_module.OpenSSLSigner)


def test_select_signer_unknown_raises():
    with pytest.raises(signer_module.SignError):
        signer_module.select_signer("bogus")


# --- sign_binaries ---------------------------------------------------------


def _signed_chain(tmp_path):
    keys_dir = _write_key_set(os.path.join(str(tmp_path), "keys"))
    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {
            "/EFI/systemd/systemd-bootx64.efi": b"systemd-boot-bytes",
            "/EFI/BOOT/BOOTX64.EFI": b"boot-fallback-bytes",
            "/EFI/Linux/pengyos-server+1.0.0-a.efi": b"uki-bytes",
        },
    )
    trust_root = keys.load_key_set(keys_dir)
    manifest = sc.build_manifest()
    return sc.sign_binaries(esp, manifest, trust_root), keys_dir, esp


def test_sign_binaries_roundtrip(tmp_path):
    signed, keys_dir, esp = _signed_chain(tmp_path)
    assert signed.verified
    assert len(signed.bindings) == 3
    # Each binding records the sha256 of the binary, the db key id, and signer.
    for binding in signed.bindings:
        assert binding.signer == "stdlib-ed25519"
        assert binding.key_id == keys.load_key_set(keys_dir).db.key_id
        assert binding.sha256 == hashlib.sha256(
            (esp / binding.path.lstrip("/")).read_bytes()
        ).hexdigest()


def test_sign_binaries_emits_verification_key_sidecar(tmp_path):
    signed, keys_dir, _ = _signed_chain(tmp_path)
    assert signed.verified
    sidecar = os.path.join(keys_dir, "DB.cert.ed25519.pub")
    assert os.path.isfile(sidecar)
    assert stat.S_IMODE(os.stat(sidecar).st_mode) == 0o644


def test_sign_binaries_deterministic(tmp_path):
    keys_dir = _write_key_set(os.path.join(str(tmp_path), "k"))
    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {"/EFI/BOOT/BOOTX64.EFI": b"same-bytes"},
    )
    trust_root = keys.load_key_set(keys_dir)
    first = sc.sign_binaries(esp, sc.build_manifest(paths=["/EFI/BOOT/BOOTX64.EFI"]), trust_root)
    # Re-sign the same bytes with the same key -> identical signature.
    second = sc.sign_binaries(esp, sc.build_manifest(paths=["/EFI/BOOT/BOOTX64.EFI"]), trust_root)
    assert first.bindings[0].signature == second.bindings[0].signature


def test_sign_binaries_missing_binary_raises(tmp_path):
    keys_dir = _write_key_set(os.path.join(str(tmp_path), "keys"))
    # ESP has only two of the three expected binaries.
    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {
            "/EFI/systemd/systemd-bootx64.efi": b"a",
            "/EFI/BOOT/BOOTX64.EFI": b"b",
        },
    )
    trust_root = keys.load_key_set(keys_dir)
    with pytest.raises(sc.SignChainError):
        sc.sign_binaries(esp, sc.build_manifest(), trust_root)


def test_sign_binaries_unverifiable_raises(tmp_path, monkeypatch):
    """A signer that verifies nothing must fail closed."""
    keys_dir = _write_key_set(os.path.join(str(tmp_path), "keys"))
    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {"/EFI/BOOT/BOOTX64.EFI": b"x"},
    )
    monkeypatch.setattr(
        signer_module, "StdlibSigner", _AlwaysFailSigner
    )
    trust_root = keys.load_key_set(keys_dir)
    with pytest.raises(sc.SignChainError):
        sc.sign_binaries(esp, sc.build_manifest(paths=["/EFI/BOOT/BOOTX64.EFI"]), trust_root)


class _AlwaysFailSigner:
    """A signer whose verify always returns False (models a broken signer)."""

    name = "broken"

    def sign(self, data, key_path):
        return b"SIG-broken"

    def verify(self, signature, data, cert_path):
        return False


# --- save / load bindings --------------------------------------------------


def test_save_and_load_bindings_roundtrip(tmp_path):
    signed, keys_dir, _ = _signed_chain(tmp_path)
    path = sc.save_bindings(signed.bindings, os.path.join(keys_dir, SIGNATURES_JSON))
    assert os.path.isfile(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644
    loaded = sc.load_bindings(path)
    assert [b.path for b in loaded] == [b.path for b in signed.bindings]
    assert loaded[0].signature == signed.bindings[0].signature


def test_load_bindings_missing_file_returns_empty(tmp_path):
    assert sc.load_bindings(os.path.join(str(tmp_path), "nope.json")) == []


def test_signatures_json_has_expected_shape(tmp_path):
    signed, keys_dir, _ = _signed_chain(tmp_path)
    path = sc.save_bindings(signed.bindings, os.path.join(keys_dir, SIGNATURES_JSON))
    data = json.loads(open(path).read())  # noqa: SIM115
    assert data["version"] == 1
    assert len(data["bindings"]) == 3
    binding = data["bindings"][0]
    assert set(binding) == {"path", "sha256", "key_id", "signature", "signer"}


# --- verify_bindings -------------------------------------------------------


def test_verify_bindings_clean(tmp_path):
    signed, keys_dir, esp = _signed_chain(tmp_path)
    path = sc.save_bindings(signed.bindings, os.path.join(keys_dir, SIGNATURES_JSON))
    loaded = sc.load_bindings(path)
    assert sc.verify_bindings(loaded, esp, os.path.join(keys_dir, "DB.cert")) is True


def test_verify_bindings_tampered_fails(tmp_path):
    signed, keys_dir, esp = _signed_chain(tmp_path)
    path = sc.save_bindings(signed.bindings, os.path.join(keys_dir, SIGNATURES_JSON))
    loaded = sc.load_bindings(path)
    # Tamper the fallback bootloader on disk.
    with open(os.path.join(esp, "EFI/BOOT/BOOTX64.EFI"), "wb") as handle:
        handle.write(b"TAMPERED")
    assert sc.verify_bindings(loaded, esp, os.path.join(keys_dir, "DB.cert")) is False


def test_verify_bindings_missing_binary_fails(tmp_path):
    signed, keys_dir, esp = _signed_chain(tmp_path)
    path = sc.save_bindings(signed.bindings, os.path.join(keys_dir, SIGNATURES_JSON))
    loaded = sc.load_bindings(path)
    os.remove(os.path.join(esp, "EFI/BOOT/BOOTX64.EFI"))
    assert sc.verify_bindings(loaded, esp, os.path.join(keys_dir, "DB.cert")) is False


def test_verify_bindings_no_sidecar_fails_closed(tmp_path):
    """Without the verification-key sidecar, stdlib verify returns False."""
    signed, keys_dir, esp = _signed_chain(tmp_path)
    os.remove(os.path.join(keys_dir, "DB.cert.ed25519.pub"))
    loaded = signed.bindings
    assert sc.verify_bindings(loaded, esp, os.path.join(keys_dir, "DB.cert")) is False


# --- CLI: cmd_sign_chain ---------------------------------------------------


def _esp_fixture(tmp_path):
    keys_dir = _write_key_set(os.path.join(str(tmp_path), "keys"))
    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {
            "/EFI/systemd/systemd-bootx64.efi": b"systemd-boot-bytes",
            "/EFI/BOOT/BOOTX64.EFI": b"boot-fallback-bytes",
            "/EFI/Linux/pengyos-server+1.0.0-a.efi": b"uki-bytes",
        },
    )
    return keys_dir, esp


def test_cmd_sign_chain_signs_and_writes_bindings(tmp_path, capsys):
    keys_dir, esp = _esp_fixture(tmp_path)
    rc = cli.main(["sign-chain", "--esp", str(esp), "--keys", str(keys_dir)])
    assert rc == 0
    bindings_path = os.path.join(keys_dir, SIGNATURES_JSON)
    assert os.path.isfile(bindings_path)
    loaded = sc.load_bindings(bindings_path)
    assert len(loaded) == 3
    # The Ed25519 verification key sidecar is emitted next to the db cert.
    assert os.path.isfile(os.path.join(keys_dir, "DB.cert.ed25519.pub"))


def test_cmd_sign_chain_json_output(tmp_path, capsys):
    keys_dir, esp = _esp_fixture(tmp_path)
    rc = cli.main(["sign-chain", "--esp", str(esp), "--keys", str(keys_dir), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 3
    assert out["verified"] is True
    assert out["signer"] == "stdlib-ed25519"
    assert out["bindings"][0]["key_id"]


def test_cmd_sign_chain_reports_no_key_material(tmp_path, capsys):
    keys_dir, esp = _esp_fixture(tmp_path)
    cli.main(["sign-chain", "--esp", str(esp), "--keys", str(keys_dir)])
    out = capsys.readouterr().out
    assert "PRIVATE KEY" not in out
    # Key material contents never appear on stdout.
    assert "0123456789abcdef" not in out


def test_cmd_sign_chain_missing_esp_errors(tmp_path):
    keys_dir = _write_key_set(os.path.join(str(tmp_path), "keys"))
    rc = cli.main(["sign-chain", "--esp", os.path.join(str(tmp_path), "nope"), "--keys", str(keys_dir)])
    assert rc == 1


def test_cmd_sign_chain_missing_key_set_errors(tmp_path, capsys):
    # An empty keys dir: no cert set to load.
    empty = os.path.join(str(tmp_path), "empty-keys")
    os.makedirs(empty, exist_ok=True)
    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {"/EFI/BOOT/BOOTX64.EFI": b"x"},
    )
    rc = cli.main(["sign-chain", "--esp", str(esp), "--keys", empty])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_cmd_sign_chain_insecure_key_perms_errors(tmp_path):
    keys_dir = _write_key_set(os.path.join(str(tmp_path), "keys"))
    # Loosen the db key perms -> the command must refuse (fail closed).
    os.chmod(os.path.join(keys_dir, "DB.key"), 0o644)
    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {"/EFI/BOOT/BOOTX64.EFI": b"x"},
    )
    rc = cli.main(["sign-chain", "--esp", str(esp), "--keys", str(keys_dir)])
    assert rc == 1


def test_cmd_sign_chain_openssl_signer(tmp_path, monkeypatch, capsys):
    keys_dir, esp = _esp_fixture(tmp_path)
    monkeypatch.setattr(signer_module, "openssl_run", FakeOpenShim())
    rc = cli.main(
        ["sign-chain", "--esp", str(esp), "--keys", str(keys_dir), "--signer", "openssl", "--json"]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["signer"] == "openssl"
    assert out["verified"] is True


# --- opt-in integration: real openssl round-trip ---------------------------


@pytest.mark.skipif(not REAL_OPENSSL, reason="needs openssl")
def test_sign_chain_with_real_openssl(tmp_path):
    """Sign -> verify -> lint against *real* openssl (opt-in)."""
    if os.environ.get("PENGYOS_IT") != "1":
        pytest.skip("real openssl round-trip is opt-in (PENGYOS_IT=1)")

    keys_dir = _write_key_set(str(tmp_path))
    # Real keys replace the fake ones so openssl can actually sign.
    _regenerate_real_openssl_keys(keys_dir)

    esp = _write_esp(
        str(tmp_path / "esp"),
        {"/EFI/BOOT/BOOTX64.EFI": b"real-efi-bytes"},
    )
    trust_root = keys.load_key_set(keys_dir)
    signed = sc.sign_binaries(
        esp, sc.build_manifest(paths=["/EFI/BOOT/BOOTX64.EFI"]), trust_root
    )
    assert signed.verified
    assert sc.verify_bindings(
        signed.bindings, esp, os.path.join(keys_dir, "DB.cert")
    ) is True


def _regenerate_real_openssl_keys(keys_dir: str) -> None:
    """Replace the fake PEM keys with real EC P-256 keys via openssl."""
    import subprocess

    for role in ("PK", "KEK", "DB"):
        key_path = os.path.join(keys_dir, f"{role}.key")
        cert_path = os.path.join(keys_dir, f"{role}.cert")
        subprocess.run(
            [
                "openssl",
                "genpkey",
                "-algorithm",
                "EC",
                "-pkeyopt",
                "ec_paramgen_curve:P-256",
                "-out",
                key_path,
            ],
            check=True,
        )
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-new",
                "-key",
                key_path,
                "-days",
                "2",
                "-subj",
                f"/CN=PengyOS Secure Boot {role}",
                "-out",
                cert_path,
            ],
            check=True,
        )
        os.chmod(key_path, KeyPerms.PRIVATE)
        os.chmod(cert_path, KeyPerms.PUBLIC)
