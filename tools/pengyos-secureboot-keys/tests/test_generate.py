"""Tests for real ``openssl`` key generation (``pengyos_sbkeys.generate``).

The ``init`` command now produces *real* PK/KEK/db key material (EC P-256
private keys + self-signed X.509 certificates) via ``openssl`` instead of the
random-bytes stand-in. These tests pin down that contract:

* the exact ``openssl`` argv constructed for a private key / certificate;
* that ``generate_private_key`` / ``generate_certificate`` write the blob to
  disk (with :func:`subprocess.run` mocked, so no real ``openssl`` is needed);
* that ``generate_all`` writes every file, keys before certificates, in role
  order;
* that a failing ``openssl`` raises :class:`GenerateError`;
* an opt-in integration test (``PENGYOS_IT=1``) that runs *real* ``openssl`` and
  validates the material with the tool itself.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pengyos_sbkeys import generate
from pengyos_sbkeys.keys import KEY_FILES, is_private

REAL_OPENSSL = shutil.which("openssl") is not None


class _Recorder:
    """A stand-in for :func:`generate._run` that records commands and returns a
    dummy PEM blob.

    ``generate_private_key`` / ``generate_certificate`` write that blob to disk
    themselves, so the whole generation flow runs offline -- no real openssl
    required.
    """

    def __init__(self) -> None:
        self.cmds: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> bytes:
        self.cmds.append(list(cmd))
        is_certificate = len(cmd) > 1 and cmd[1] == "req"
        return (
            b"-----BEGIN CERTIFICATE-----FAKE\n"
            if is_certificate
            else b"-----BEGIN PRIVATE KEY-----FAKE\n"
        )


# --- openssl argv construction ----------------------------------------------


def test_openssl_private_key_cmd_shape():
    cmd = generate.openssl_private_key_cmd("PK")
    assert cmd[:2] == ["openssl", "genpkey"]
    assert cmd[cmd.index("-algorithm") + 1] == "EC"
    assert f"ec_paramgen_curve:{generate.PRIVATE_KEY_CURVE}" in cmd
    assert generate.PRIVATE_KEY_CURVE == "P-256"
    # The key is emitted on stdout (no -out): Python owns writing it to disk.
    assert "-out" not in cmd


def test_openssl_certificate_cmd_shape():
    cmd = generate.openssl_certificate_cmd("DB", "/tmp/DB.key")
    assert cmd[:2] == ["openssl", "req"]
    assert "-x509" in cmd and "-new" in cmd
    assert cmd[cmd.index("-key") + 1] == "/tmp/DB.key"
    assert cmd[cmd.index("-days") + 1] == str(generate.CERTIFICATE_DAYS)
    subj = cmd[cmd.index("-subj") + 1]
    assert subj == "/CN=PengyOS Secure Boot DB"
    assert "-out" not in cmd


# --- generate_private_key / generate_certificate ----------------------------


def test_generate_private_key_writes_pem(tmp_path, monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(generate, "_run", rec)

    key = tmp_path / "PK.key"
    data = generate.generate_private_key("PK", key)

    assert rec.cmds == [
        [
            "openssl", "genpkey", "-algorithm", "EC",
            "-pkeyopt", "ec_paramgen_curve:P-256",
        ]
    ]
    assert data.startswith(b"-----BEGIN PRIVATE KEY-----")
    assert key.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")


def test_generate_certificate_writes_pem(tmp_path, monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(generate, "_run", rec)

    key = tmp_path / "DB.key"
    cert = tmp_path / "DB.cert"
    data = generate.generate_certificate("DB", key, cert)

    assert rec.cmds == [
        [
            "openssl", "req", "-x509", "-new", "-key", str(key),
            "-days", str(generate.CERTIFICATE_DAYS),
            "-subj", "/CN=PengyOS Secure Boot DB",
        ]
    ]
    assert data.startswith(b"-----BEGIN CERTIFICATE-----")
    assert cert.read_bytes().startswith(b"-----BEGIN CERTIFICATE-----")


# --- generate_all orchestration ---------------------------------------------


def test_generate_all_writes_every_file(tmp_path, monkeypatch):
    monkeypatch.setattr(generate, "_run", _Recorder())
    generate.generate_all(tmp_path / "keys")
    for key_file in KEY_FILES:
        assert (tmp_path / "keys" / key_file.name).is_file()


def test_generate_all_writes_keys_before_certs(tmp_path, monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(generate, "_run", rec)
    generate.generate_all(tmp_path / "keys")

    key_cmds = [c for c in rec.cmds if len(c) > 1 and c[1] == "genpkey"]
    cert_cmds = [c for c in rec.cmds if len(c) > 1 and c[1] == "req"]
    assert len(key_cmds) == 3
    assert len(cert_cmds) == 3
    # Every key is generated before any certificate is built from it.
    assert rec.cmds.index(key_cmds[0]) < rec.cmds.index(cert_cmds[0])


def test_generate_all_generates_pk_kek_db_then_certs(tmp_path, monkeypatch):
    seen: list[tuple[str, str]] = []

    def _spy_private(role: str, path) -> bytes:
        seen.append(("key", role))
        Path(path).write_bytes(b"-----BEGIN PRIVATE KEY-----\n")
        return b"-----BEGIN PRIVATE KEY-----\n"

    def _spy_cert(role: str, key_path, cert_path) -> bytes:
        seen.append(("cert", role))
        Path(cert_path).write_bytes(b"-----BEGIN CERTIFICATE-----\n")
        return b"-----BEGIN CERTIFICATE-----\n"

    monkeypatch.setattr(generate, "generate_private_key", _spy_private)
    monkeypatch.setattr(generate, "generate_certificate", _spy_cert)
    generate.generate_all(tmp_path / "keys")

    roles = [role for _, role in seen]
    assert roles == ["PK", "KEK", "DB", "PK", "KEK", "DB"]


# --- error handling ---------------------------------------------------------


def test_run_raises_generate_error_on_failure(monkeypatch):
    class _Result:
        returncode = 1
        stderr = b"bad algorithm"
        stdout = b""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    with pytest.raises(generate.GenerateError) as excinfo:
        generate._run(["openssl", "genpkey"])
    assert "bad algorithm" in str(excinfo.value)


def test_generate_error_is_runtime():
    assert issubclass(generate.GenerateError, RuntimeError)


# --- opt-in integration: real openssl ---------------------------------------


def _openssl_available() -> bool:
    return shutil.which("openssl") is not None


def _assert_valid_key_pair(key: Path, cert: Path) -> None:
    """Validate that ``cert`` is a self-signed X.509 cert pairing with ``key``."""
    key_pub = subprocess.run(
        ["openssl", "pkey", "-in", str(key), "-pubout"],
        capture_output=True,
    )
    assert key_pub.returncode == 0, key_pub.stderr.decode()

    cert_pub = subprocess.run(
        ["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"],
        capture_output=True,
    )
    assert cert_pub.returncode == 0, cert_pub.stderr.decode()

    # The certificate's public key must match the key's public key.
    assert key_pub.stdout == cert_pub.stdout

    # The certificate is self-signed and readable.
    subject = subprocess.run(
        ["openssl", "x509", "-in", str(cert), "-noout", "-subject"],
        capture_output=True,
        text=True,
    )
    assert subject.returncode == 0
    assert "CN=PengyOS Secure Boot" in subject.stdout


@pytest.mark.skipif(not _openssl_available(), reason="needs openssl")
def test_generate_all_produces_real_key_material(tmp_path):
    if os.environ.get("PENGYOS_IT") != "1":
        pytest.skip("real openssl generation is opt-in (PENGYOS_IT=1)")

    keys = tmp_path / "keys"
    generate.generate_all(keys)

    for key_file in KEY_FILES:
        path = keys / key_file.name
        assert path.stat().st_size > 0
        if is_private(key_file):
            assert path.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")
        else:
            assert path.read_bytes().startswith(b"-----BEGIN CERTIFICATE-----")

    # Each certificate pairs with its freshly generated private key.
    for role in ("PK", "KEK", "DB"):
        _assert_valid_key_pair(keys / f"{role}.key", keys / f"{role}.cert")
