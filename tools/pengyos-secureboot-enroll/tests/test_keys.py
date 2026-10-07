"""Tests for the ``keys`` subtask (``pengyos_secureboot_enroll.keys`` + ``cmd_keys``).

These pin down the three things ``keys`` must do:

* **deterministic generation** into a fixture dir through an *injectable* openssl
  shim -- same shim, same bytes;
* **loading** a :class:`TrustRoot` from disk, each key identified by the SHA-256
  fingerprint of its DER certificate (derived from the PEM with the standard
  library -- no external tool); and
* **signed, hashable metadata** -- an immutable :class:`KeyMetadata` with a
  :attr:`~pengyos_secureboot_enroll.keys.KeyMetadata.digest`, signed by the ``PK``
  key and verifiable against the ``PK`` certificate.

Every test is offline: generation and signing run through a deterministic fake
shim, so no real ``openssl``, no root and no network are needed. A single
opt-in integration test (``PENGYOS_IT=1``) runs *real* ``openssl`` through the
whole generate -> load -> sign -> verify round-trip.
"""

import base64
import hashlib
import os
import shutil
import stat
import subprocess

import pytest

from pengyos_secureboot_enroll import keys
from pengyos_secureboot_enroll import cli
from pengyos_secureboot_enroll.truststate import KeyPerms

REAL_OPENSSL = shutil.which("openssl") is not None


# --- a deterministic fake openssl shim --------------------------------------


def _make_pem(body: bytes) -> bytes:
    """Wrap ``body`` in a single-cert PEM with correct base64 padding."""
    b64 = base64.b64encode(body).decode("ascii")
    wrapped = "\n".join(b64[i : i + 64] for i in range(0, len(b64), 64))
    return f"-----BEGIN CERTIFICATE-----\n{wrapped}\n-----END CERTIFICATE-----\n".encode("utf-8")


class FakeShim:
    """A deterministic stand-in for the openssl shim.

    Returns deterministic key/cert/signature blobs and, for verification,
    *succeeds* unless the signed data has been tampered with (then it raises
    :class:`GenerateError`, which :func:`verify_metadata_signature` maps to
    ``False``). Records every argv it is called with.
    """

    def __init__(self, counter: int = 0) -> None:
        self.cmds: list[list[str]] = []
        # A per-call salt so successive generations differ (for --rotate tests).
        self.counter = counter

    def __call__(self, cmd):
        self.cmds.append(list(cmd))
        name = cmd[1]
        if name == "genpkey":
            self.counter += 1
            return f"-----BEGIN PRIVATE KEY-----FAKE-{self.counter}\nMIIBIcFAKE\n-----END PRIVATE KEY-----\n".encode()
        if name == "req":
            role = cmd[cmd.index("-key") + 1].split("/")[-1].split(".")[0]
            return _make_pem(f"CERT-{role}".encode())
        if name == "x509":
            return b"-----BEGIN PUBLIC KEY-----\nMIIcFAKEPUB\n-----END PUBLIC KEY-----\n"
        if name == "dgst" and "-sign" in cmd:
            # The signature carries the digest of the *bytes it signs*, so a
            # signature over one document cannot verify a different one.
            content = open(cmd[-1], "rb").read()  # noqa: SIM115 (tempfile scope)
            self.last_digest = hashlib.sha256(content).hexdigest()[:40]
            return b"SIG-" + self.last_digest.encode()
        if name == "dgst" and "-verify" in cmd:
            # openssl exits non-zero (raises here) when the data does not match.
            data = open(cmd[-1], "rb").read()  # noqa: SIM115 (tempfile scope)
            sig = open(cmd[cmd.index("-signature") + 1], "rb").read()
            if not sig.startswith(b"SIG-") or data == b"TAMPERED":
                raise keys.GenerateError("verification failed")
            recorded = sig[len(b"SIG-") :]
            if hashlib.sha256(data).hexdigest()[:40].encode() != recorded:
                raise keys.GenerateError("signature does not match data")
            return b"Verified OK\n"
        raise AssertionError(f"unexpected shim command: {cmd}")


# --- openssl command construction -------------------------------------------


def test_openssl_private_key_cmd_shape():
    cmd = keys.openssl_private_key_cmd("PK")
    assert cmd[:2] == ["openssl", "genpkey"]
    assert cmd[cmd.index("-algorithm") + 1] == "EC"
    assert f"ec_paramgen_curve:{keys.PRIVATE_KEY_CURVE}" in cmd
    assert keys.PRIVATE_KEY_CURVE == "P-256"
    assert "-out" not in cmd  # key is emitted on stdout; Python owns the file


def test_openssl_certificate_cmd_shape():
    cmd = keys.openssl_certificate_cmd("DB", "/tmp/DB.key")
    assert cmd[:2] == ["openssl", "req"]
    assert "-x509" in cmd and "-new" in cmd
    assert cmd[cmd.index("-key") + 1] == "/tmp/DB.key"
    assert cmd[cmd.index("-days") + 1] == str(keys.CERTIFICATE_DAYS)
    assert cmd[cmd.index("-subj") + 1] == "/CN=PengyOS Secure Boot DB"
    assert "-out" not in cmd


def test_openssl_pubkey_from_cert_cmd_shape():
    cmd = keys.openssl_pubkey_from_cert_cmd("/tmp/DB.cert")
    assert cmd[:2] == ["openssl", "x509"]
    assert cmd[cmd.index("-in") + 1] == "/tmp/DB.cert"
    assert "-pubkey" in cmd and "-noout" in cmd


def test_openssl_sign_cmd_shape():
    cmd = keys.openssl_sign_cmd("/tmp/PK.key", "/tmp/data.bin")
    assert cmd[:2] == ["openssl", "dgst"]
    assert f"-{keys.METADATA_DIGEST_ALGO}" in cmd
    assert "-sign" in cmd
    assert cmd[cmd.index("-sign") + 1] == "/tmp/PK.key"
    assert cmd[-1] == "/tmp/data.bin"


def test_openssl_verify_cmd_shape():
    cmd = keys.openssl_verify_cmd("/tmp/pk.pub", "/tmp/sig", "/tmp/data")
    assert cmd[:2] == ["openssl", "dgst"]
    assert "-verify" in cmd
    assert cmd[cmd.index("-verify") + 1] == "/tmp/pk.pub"
    assert cmd[cmd.index("-signature") + 1] == "/tmp/sig"
    assert cmd[-1] == "/tmp/data"


# --- openssl_run ------------------------------------------------------------


def test_openssl_run_returns_stdout(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result(0, b"OUT", b""))
    assert keys.openssl_run(["openssl", "x"]) == b"OUT"


def test_openssl_run_raises_generate_error_on_failure(monkeypatch):
    class _Result:
        returncode = 1
        stderr = b"bad algorithm"
        stdout = b""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    with pytest.raises(keys.GenerateError) as excinfo:
        keys.openssl_run(["openssl", "genpkey"])
    assert "bad algorithm" in str(excinfo.value)


class _Result:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --- generate_private_key / generate_certificate ----------------------------


def test_generate_private_key_writes_pem_and_sets_perms(tmp_path, monkeypatch):
    shim = FakeShim()
    monkeypatch.setattr(keys, "openssl_run", shim)

    key = tmp_path / "PK.key"
    data = keys.generate_private_key("PK", key)

    assert shim.cmds == [keys.openssl_private_key_cmd("PK")]
    assert data.startswith(b"-----BEGIN PRIVATE KEY-----")
    assert key.read_bytes() == data
    assert stat.S_IMODE(key.stat().st_mode) == KeyPerms.PRIVATE


def test_generate_certificate_writes_pem_and_sets_perms(tmp_path, monkeypatch):
    shim = FakeShim()
    monkeypatch.setattr(keys, "openssl_run", shim)

    key = tmp_path / "DB.key"
    key.write_bytes(b"-----BEGIN PRIVATE KEY-----\n")
    cert = tmp_path / "DB.cert"
    data = keys.generate_certificate("DB", key, cert)

    assert shim.cmds == [keys.openssl_certificate_cmd("DB", str(key))]
    assert data.startswith(b"-----BEGIN CERTIFICATE-----")
    assert cert.read_bytes() == data
    assert stat.S_IMODE(cert.stat().st_mode) == KeyPerms.PUBLIC


# --- generate_all orchestration ---------------------------------------------


def test_generate_all_writes_every_file(tmp_path, monkeypatch):
    from pengyos_secureboot_enroll.truststate import KEY_FILES

    shim = FakeShim()
    monkeypatch.setattr(keys, "openssl_run", shim)

    keys.generate_all(tmp_path / "keys")

    for key_file in KEY_FILES:
        assert (tmp_path / "keys" / key_file.name).is_file()


def test_generate_all_creates_dir_with_0700(tmp_path, monkeypatch):
    monkeypatch.setattr(keys, "openssl_run", FakeShim())
    keys_dir = tmp_path / "keys"
    keys.generate_all(keys_dir)
    assert stat.S_IMODE(keys_dir.stat().st_mode) == KeyPerms.DIR


def test_generate_all_writes_keys_before_certs(tmp_path, monkeypatch):
    shim = FakeShim()
    monkeypatch.setattr(keys, "openssl_run", shim)
    keys.generate_all(tmp_path / "keys")

    key_cmds = [c for c in shim.cmds if len(c) > 1 and c[1] == "genpkey"]
    cert_cmds = [c for c in shim.cmds if len(c) > 1 and c[1] == "req"]
    assert len(key_cmds) == 3
    assert len(cert_cmds) == 3
    assert shim.cmds.index(key_cmds[0]) < shim.cmds.index(cert_cmds[0])


def test_generate_all_is_deterministic_for_same_shim(tmp_path, monkeypatch):
    """A fixed shim yields byte-identical key material every time."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    shim_a = FakeShim()
    shim_b = FakeShim()
    # Reset to identical starting state so both shims produce identical blobs.
    shim_a.counter = 0
    shim_b.counter = 0
    monkeypatch.setattr(keys, "openssl_run", shim_a)
    keys.generate_all(a)
    monkeypatch.setattr(keys, "openssl_run", shim_b)
    keys.generate_all(b)

    for name in ("PK.key", "PK.cert", "KEK.key", "KEK.cert", "DB.key", "DB.cert"):
        assert (a / name).read_bytes() == (b / name).read_bytes()


# --- pem_to_der -------------------------------------------------------------


def test_pem_to_der_recovers_der_bytes():
    body = b"\x30\x82fake-cert-der-bytes"
    pem = _make_pem(body)
    assert keys.pem_to_der(pem) == body


def test_pem_to_der_handles_unpadded_last_line():
    # A PEM whose final base64 line is short (no trailing ==) must still decode.
    body = b"hello"
    b64 = base64.b64encode(body).decode()
    # Drop the padding to simulate a hand-edited / line-wrapped cert.
    stripped = b64.rstrip("=")
    pem = f"-----BEGIN CERTIFICATE-----\n{stripped}\n-----END CERTIFICATE-----\n".encode()
    assert keys.pem_to_der(pem) == body


def test_pem_to_der_matches_openssl_der_for_real_cert(tmp_path):
    """The stdlib DER equals what `openssl x509 -outform DER` would emit."""
    der = hashlib.sha256(b"anything").digest()
    cert = tmp_path / "DB.cert"
    cert.write_bytes(_make_pem(der))
    assert keys.pem_to_der(cert.read_bytes()) == der


def test_pem_to_der_raises_on_non_pem():
    with pytest.raises(keys.GenerateError):
        keys.pem_to_der(b"not a certificate")
    with pytest.raises(keys.GenerateError):
        keys.pem_to_der(b"-----BEGIN CERTIFICATE-----\n-----END CERTIFICATE-----\n")


# --- load_key_set / TrustRoot -----------------------------------------------


def _write_key_set(dirpath: "os.PathLike | str") -> str:
    """Write a deterministic, valid PEM cert set (stdlib only) under ``dirpath``.

    Returns the absolute path (so callers can load the trust root from it).
    """
    dirpath = os.path.abspath(dirpath)
    os.makedirs(dirpath, exist_ok=True)
    for role in ("PK", "KEK", "DB"):
        cert = os.path.join(dirpath, f"{role}.cert")
        with open(cert, "wb") as handle:
            handle.write(_make_pem(f"CERT-{role}".encode()))
        key = os.path.join(dirpath, f"{role}.key")
        with open(key, "wb") as handle:
            handle.write(b"-----BEGIN PRIVATE KEY-----\n")
        os.chmod(key, KeyPerms.PRIVATE)
        os.chmod(cert, KeyPerms.PUBLIC)
    return dirpath


def test_load_key_set_returns_trust_root_in_role_order():
    d = _write_key_set(os.path.join(_tmp(), "ks"))
    trust_root = keys.load_key_set(d)
    assert [entry.role for entry in trust_root.entries] == ["PK", "KEK", "DB"]


def test_load_key_set_key_id_is_sha256_of_der():
    d = _write_key_set(os.path.join(_tmp(), "ks2"))
    trust_root = keys.load_key_set(d)
    for entry in trust_root.entries:
        expected = hashlib.sha256(f"CERT-{entry.role}".encode()).hexdigest()
        assert entry.key_id == expected


def test_load_key_set_records_paths_and_perms():
    d = _write_key_set(os.path.join(_tmp(), "ks3"))
    trust_root = keys.load_key_set(d)
    db = trust_root.db
    assert db.role == "DB"
    assert db.cert_path.endswith("DB.cert")
    assert db.key_path.endswith("DB.key")
    assert db.is_signing_key is True
    assert trust_root.pk.is_signing_key is False
    assert trust_root.by_role("KEK").is_signing_key is False


def test_load_key_set_to_json():
    d = _write_key_set(os.path.join(_tmp(), "ks4"))
    trust_root = keys.load_key_set(d)
    data = trust_root.to_json()
    assert list(data) == ["roles"]
    assert list(data["roles"]) == ["PK", "KEK", "DB"]
    assert "key_id" in data["roles"]["DB"]
    assert "cert_path" in data["roles"]["DB"]


def test_trust_root_db_key_id_and_key_ids():
    d = _write_key_set(os.path.join(_tmp(), "ks5"))
    trust_root = keys.load_key_set(d)
    assert trust_root.db_key_id() == hashlib.sha256(b"CERT-DB").hexdigest()
    key_ids = trust_root.key_ids()
    assert list(key_ids) == ["PK", "KEK", "DB"]
    assert key_ids["DB"] == hashlib.sha256(b"CERT-DB").hexdigest()


def test_trust_root_by_role_unknown_raises():
    d = _write_key_set(os.path.join(_tmp(), "ks6"))
    trust_root = keys.load_key_set(d)
    with pytest.raises(KeyError):
        trust_root.by_role("NOPE")


def test_load_key_set_missing_cert_raises():
    d = os.path.join(_tmp(), "ks_missing")
    os.makedirs(d, exist_ok=True)
    for role in ("PK", "KEK", "DB"):
        with open(os.path.join(d, f"{role}.cert"), "wb") as handle:
            handle.write(_make_pem(f"CERT-{role}".encode()))
    # Remove the DB cert: load must fail closed.
    os.remove(os.path.join(d, "DB.cert"))
    with pytest.raises(keys.MissingKeyError):
        keys.load_key_set(d)


def _tmp() -> str:
    """A disk-backed scratch dir (never tmpfs) shared by the stdlib load tests."""
    path = os.path.join(os.path.expanduser("~"), "tmp", "sb-keys-tests")
    os.makedirs(path, exist_ok=True)
    return path


# --- metadata (hashable + signed) -------------------------------------------


def test_build_metadata_digest_is_sha256_of_canonical():
    d = _write_key_set(os.path.join(_tmp(), "ms"))
    trust_root = keys.load_key_set(d)
    metadata = keys.build_metadata(trust_root, generated_at="2026-01-01T00:00:00Z")
    canonical = keys._canonical_metadata_bytes(metadata.key_ids, "2026-01-01T00:00:00Z")
    assert metadata.digest == hashlib.sha256(canonical).hexdigest()


def test_build_metadata_deterministic_for_fixed_timestamp():
    d = _write_key_set(os.path.join(_tmp(), "ms2"))
    trust_root = keys.load_key_set(d)
    a = keys.build_metadata(trust_root, generated_at="2026-01-01T00:00:00Z")
    b = keys.build_metadata(trust_root, generated_at="2026-01-01T00:00:00Z")
    assert a.digest == b.digest
    assert a.key_ids == b.key_ids


def test_key_metadata_is_hashable_and_immutable():
    d = _write_key_set(os.path.join(_tmp(), "ms3"))
    trust_root = keys.load_key_set(d)
    metadata = keys.build_metadata(trust_root, generated_at="2026-01-01T00:00:00Z")
    assert isinstance(hash(metadata), int)
    with pytest.raises(Exception):
        metadata.digest = "x"  # type: ignore[misc]


def test_key_metadata_requires_generated_at():
    with pytest.raises(ValueError):
        keys.KeyMetadata(key_ids=(("DB", "abc"),))


def test_build_metadata_to_json_structure():
    metadata = keys.KeyMetadata(
        key_ids=(("PK", "p"), ("KEK", "k"), ("DB", "d")), generated_at="2026-01-01T00:00:00Z"
    )
    data = metadata.to_json()
    assert list(data) == ["generated_at", "key_ids", "digest"]
    assert data["key_ids"] == [
        {"role": "PK", "key_id": "p"},
        {"role": "KEK", "key_id": "k"},
        {"role": "DB", "key_id": "d"},
    ]
    assert data["digest"] == metadata.digest


def test_canonical_bytes_are_stable():
    metadata = keys.KeyMetadata(
        key_ids=(("DB", "d"),), generated_at="2026-01-01T00:00:00Z"
    )
    assert metadata.canonical_bytes() == metadata.canonical_bytes()


def test_key_metadata_from_json_roundtrip_preserves_digest():
    """The signed metadata must verify after a JSON round-trip."""
    metadata = keys.KeyMetadata(
        key_ids=(("PK", "p"), ("KEK", "k"), ("DB", "d")), generated_at="2026-01-01T00:00:00Z"
    )
    restored = keys.KeyMetadata.from_json(metadata.to_json())
    assert restored.digest == metadata.digest
    assert restored.key_ids == metadata.key_ids
    assert restored.canonical_bytes() == metadata.canonical_bytes()


# --- sign / verify metadata -------------------------------------------------


def test_sign_metadata_returns_signature_bytes(tmp_path, monkeypatch):
    _write_key_set(tmp_path)
    shim = FakeShim()
    monkeypatch.setattr(keys, "openssl_run", shim)
    trust_root = keys.load_key_set(tmp_path)
    metadata = keys.build_metadata(trust_root, generated_at="2026-01-01T00:00:00Z")

    signature = keys.sign_metadata(metadata, tmp_path)
    assert signature.startswith(b"SIG-")
    # Signed with the PK key over the metadata canonical-bytes temp file.
    sign_cmd = shim.cmds[-1]
    assert sign_cmd[:4] == ["openssl", "dgst", "-sha256", "-sign"]
    assert sign_cmd[4] == str(tmp_path / "PK.key")
    assert len(sign_cmd) == 6  # key + data file
    assert sign_cmd[5].endswith("metadata.bin")  # the canonical-bytes temp file


def test_sign_metadata_missing_key_raises(tmp_path):
    os.makedirs(tmp_path, exist_ok=True)
    with pytest.raises(keys.MissingKeyError):
        keys.sign_metadata(
            keys.KeyMetadata(key_ids=(("DB", "d"),), generated_at="2026-01-01T00:00:00Z"),
            tmp_path,
            run=lambda cmd: b"",
        )


def test_sign_metadata_insecure_key_raises(tmp_path, monkeypatch):
    _write_key_set(tmp_path)
    insecure = tmp_path / "PK.key"
    insecure.chmod(0o644)  # group/world readable -> compromised
    monkeypatch.setattr(keys, "openssl_run", lambda cmd: b"")
    with pytest.raises(keys.PrivateKeyError):
        keys.sign_metadata(
            keys.KeyMetadata(key_ids=(("DB", "d"),), generated_at="2026-01-01T00:00:00Z"),
            tmp_path,
        )


def test_verify_metadata_signature_valid_and_tampered(tmp_path, monkeypatch):
    _write_key_set(tmp_path)
    monkeypatch.setattr(keys, "openssl_run", FakeShim())
    trust_root = keys.load_key_set(tmp_path)
    metadata = keys.build_metadata(trust_root, generated_at="2026-01-01T00:00:00Z")
    signature = keys.sign_metadata(metadata, tmp_path)

    assert keys.verify_metadata_signature(metadata, signature, tmp_path) is True

    # A tampered signature must not verify.
    bad = signature[:-1] + bytes([signature[-1] ^ 0xFF])
    assert keys.verify_metadata_signature(metadata, bad, tmp_path) is False


def test_verify_metadata_signature_missing_cert_raises(tmp_path):
    os.makedirs(tmp_path, exist_ok=True)
    with pytest.raises(keys.MissingKeyError):
        keys.verify_metadata_signature(
            keys.KeyMetadata(key_ids=(("DB", "d"),), generated_at="2026-01-01T00:00:00Z"),
            b"SIG-x",
            tmp_path,
            run=lambda cmd: b"",
        )


def test_verify_metadata_signature_fails_on_tampered_metadata(tmp_path, monkeypatch):
    """A metadata whose canonical bytes changed must not verify."""
    _write_key_set(tmp_path)
    shim = FakeShim()
    monkeypatch.setattr(keys, "openssl_run", shim)
    trust_root = keys.load_key_set(tmp_path)
    metadata = keys.build_metadata(trust_root, generated_at="2026-01-01T00:00:00Z")
    signature = keys.sign_metadata(metadata, tmp_path)
    assert keys.verify_metadata_signature(metadata, signature, tmp_path) is True

    # Sign over different bytes, then present that metadata: verification fails
    # because the canonical bytes no longer match the signature.
    other = keys.KeyMetadata(
        key_ids=(("DB", "other"),), generated_at="2026-01-01T00:00:00Z"
    )
    assert keys.verify_metadata_signature(other, signature, tmp_path) is False


# --- CLI: cmd_keys ----------------------------------------------------------


def _patch_keys_for_cli(monkeypatch) -> None:
    """Make the CLI's keys functions openssl-free and deterministic."""

    def fake_generate_all(out_dir):
        _write_key_set(out_dir)

    def fake_sign_metadata(metadata, out_dir):
        return b"SIG-OK"

    monkeypatch.setattr(keys, "generate_all", fake_generate_all)
    monkeypatch.setattr(keys, "sign_metadata", fake_sign_metadata)


def test_cmd_keys_generates_and_writes_metadata(tmp_path, monkeypatch):
    _patch_keys_for_cli(monkeypatch)
    rc = cli.main(["keys", "--out", str(tmp_path)])
    assert rc == 0
    for name in ("PK.key", "PK.cert", "KEK.key", "KEK.cert", "DB.key", "DB.cert"):
        assert (tmp_path / name).is_file()
    assert (tmp_path / "metadata.json").is_file()
    assert (tmp_path / "metadata.sig").read_bytes() == b"SIG-OK"


def test_cmd_keys_report_lists_key_ids_not_material(capsys, tmp_path, monkeypatch):
    _patch_keys_for_cli(monkeypatch)
    rc = cli.main(["keys", "--out", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PK" in out and "KEK" in out and "DB" in out
    # A 64-hex-char key id is printed ...
    assert any(len(line.split()) == 2 and len(line.split()[1]) == 64 for line in out.splitlines())
    # ... but never the private key material.
    assert "PRIVATE KEY" not in out


def test_cmd_keys_json_output(tmp_path, monkeypatch, capsys):
    _patch_keys_for_cli(monkeypatch)
    rc = cli.main(["keys", "--out", str(tmp_path), "--json"])
    assert rc == 0
    import json as _json

    data = _json.loads(capsys.readouterr().out)
    assert data["rotated"] is True
    assert list(data["roles"]) == ["PK", "KEK", "DB"]
    assert "key_id" in data["roles"]["DB"]
    assert data["metadata"]["digest"]
    assert data["signature_path"].endswith("metadata.sig")


def test_cmd_keys_rotate_changes_ids(tmp_path, monkeypatch, capsys):
    """--rotate regenerates the key set; the db id changes."""
    call = {"n": 0}

    def fake_generate_all(out_dir):
        call["n"] += 1
        _write_key_set(out_dir)
        # Make the second rotation produce different cert bodies.
        if call["n"] == 2:
            for role in ("PK", "KEK", "DB"):
                with open(os.path.join(os.path.abspath(out_dir), f"{role}.cert"), "wb") as handle:
                    handle.write(_make_pem(f"ROTATED-{role}".encode()))

    def fake_sign_metadata(metadata, out_dir):
        return b"SIG-OK"

    monkeypatch.setattr(keys, "generate_all", fake_generate_all)
    monkeypatch.setattr(keys, "sign_metadata", fake_sign_metadata)

    first = cli.main(["keys", "--out", str(tmp_path), "--json"])
    assert first == 0
    import json as _json

    first_db = _json.loads(capsys.readouterr().out)["roles"]["DB"]["key_id"]

    second = cli.main(["keys", "--out", str(tmp_path), "--rotate", "--json"])
    assert second == 0
    second_db = _json.loads(capsys.readouterr().out)["roles"]["DB"]["key_id"]

    assert first_db != second_db


def test_cmd_keys_reloads_existing_without_rotate(tmp_path, monkeypatch):
    """Without --rotate, an existing key set is reloaded, not regenerated."""
    calls = {"gen": 0}

    def fake_generate_all(out_dir):
        calls["gen"] += 1
        _write_key_set(out_dir)

    monkeypatch.setattr(keys, "generate_all", fake_generate_all)
    monkeypatch.setattr(keys, "sign_metadata", lambda md, out: b"SIG-OK")

    assert cli.main(["keys", "--out", str(tmp_path)]) == 0
    assert calls["gen"] == 1
    # Second run without --rotate must NOT regenerate.
    assert cli.main(["keys", "--out", str(tmp_path)]) == 0
    assert calls["gen"] == 1


def test_cmd_keys_out_not_a_dir_errors(tmp_path, monkeypatch):
    _patch_keys_for_cli(monkeypatch)
    a_file = tmp_path / "afile"
    a_file.write_text("x")
    rc = cli.main(["keys", "--out", str(a_file)])
    assert rc == 1


def test_cmd_keys_metadata_is_self_verifying(tmp_path, monkeypatch):
    """The written metadata.json + metadata.sig round-trips and verifies."""
    # A deterministic shim signs (encoding the canonical digest) and verifies.
    monkeypatch.setattr(keys, "openssl_run", FakeShim())
    monkeypatch.setattr(keys, "generate_all", lambda out: _write_key_set(out))

    assert cli.main(["keys", "--out", str(tmp_path)]) == 0

    import json as _json

    metadata = keys.KeyMetadata.from_json(
        _json.loads((tmp_path / "metadata.json").read_text())
    )
    signature = (tmp_path / "metadata.sig").read_bytes()

    # A fresh shim verifies the signature over the (reconstructed) canonical
    # bytes -- proving the written metadata is self-verifying.
    assert keys.verify_metadata_signature(
        metadata, signature, tmp_path, run=FakeShim()
    ) is True


def test_cmd_keys_generate_failure_returns_1(tmp_path, monkeypatch, capsys):
    def boom(out_dir):
        raise keys.GenerateError("openssl died")

    monkeypatch.setattr(keys, "generate_all", boom)
    rc = cli.main(["keys", "--out", str(tmp_path)])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


# --- opt-in integration: real openssl round-trip ---------------------------


def _openssl_available() -> bool:
    return shutil.which("openssl") is not None


@pytest.mark.skipif(not _openssl_available(), reason="needs openssl")
def test_keys_roundtrip_with_real_openssl(tmp_path):
    """Generate -> load -> sign -> verify against *real* openssl (opt-in)."""
    if os.environ.get("PENGYOS_IT") != "1":
        pytest.skip("real openssl round-trip is opt-in (PENGYOS_IT=1)")

    # Real generation through the default (real) openssl shim.
    keys.generate_all(tmp_path)

    # Each private key is protected per policy.
    for role in ("PK", "KEK", "DB"):
        mode = stat.S_IMODE((tmp_path / f"{role}.key").stat().st_mode)
        assert mode & 0o077 == 0

    trust_root = keys.load_key_set(tmp_path)
    assert [entry.role for entry in trust_root.entries] == ["PK", "KEK", "DB"]

    # The db key id matches openssl's own sha256 fingerprint of the DER cert.
    der = subprocess.run(
        ["openssl", "x509", "-in", str(tmp_path / "DB.cert"), "-outform", "DER"],
        capture_output=True,
    )
    assert der.returncode == 0, der.stderr.decode()
    assert trust_root.db.key_id == hashlib.sha256(der.stdout).hexdigest()

    metadata = keys.build_metadata(trust_root)
    signature = keys.sign_metadata(metadata, tmp_path)
    assert len(signature) > 0

    # The signature verifies against the real PK certificate.
    assert keys.verify_metadata_signature(metadata, signature, tmp_path) is True

    # A tampered metadata does not verify.
    tampered = keys.KeyMetadata(
        key_ids=(("DB", "0" * 64),), generated_at=metadata.generated_at
    )
    assert keys.verify_metadata_signature(tampered, signature, tmp_path) is False
