"""Tests for the ``enroll`` subtask (``enroll`` + ``cmd_enroll``).

These pin down the three things ``enroll`` must do (emit the MOK/custom-key
enrolment material + the operator instructions, with no NVRAM writes):

* **extract the DER db certificate** from the key set with the standard library
  (no openssl) -- the artefact ``mokutil --import`` consumes;
* **build the enrolment material** in either mode (``mok`` -- the DER cert, or
  ``custom`` -- a self-authenticating JSON bundle that carries the signed key
  metadata) and the operator instructions; and
* **write the material** to disk (public, ``0644``), never reading the private
  keys out, and **write no firmware NVRAM**.

Every test is offline: the DER comes from the standard library and the metadata
signature runs through a deterministic fake openssl shim, so no real ``openssl``,
no root and no network are needed. A single opt-in integration test
(``PENGYOS_IT=1``) builds the material against *real* openssl key material and
checks the DER equals ``openssl x509 -outform DER``.
"""

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess

import pytest

from pengyos_secureboot_enroll import cli
from pengyos_secureboot_enroll import enroll
from pengyos_secureboot_enroll import keys
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

    Returns deterministic key/cert blobs and signs metadata with ``openssl
    dgst -sign`` (returning ``b"SIG-" + sha256(data)``). Records every argv it
    is called with. For enrolment we only need ``req`` (cert body) and ``dgst
    -sign`` (the metadata signature), so the verification branch is untested
    here (covered by ``test_keys``).
    """

    def __init__(self) -> None:
        self.cmds: list[list[str]] = []

    def __call__(self, cmd):
        self.cmds.append(list(cmd))
        name = cmd[1]
        if name == "req":
            role = cmd[cmd.index("-key") + 1].split("/")[-1].split(".")[0]
            return _make_pem(f"CERT-{role}".encode())
        if name == "dgst" and "-sign" in cmd:
            data = open(cmd[-1], "rb").read()  # noqa: SIM115 (tempfile scope)
            return b"SIG-" + hashlib.sha256(data).hexdigest().encode()
        raise AssertionError(f"unexpected shim command: {cmd}")


# --- fixtures --------------------------------------------------------------


def _write_key_set(dirpath: "os.PathLike | str") -> str:
    """Write a deterministic, valid PEM key set (stdlib only) under ``dirpath``.

    Returns the absolute path (so callers can build enrolment material from it).
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


def _tmp() -> str:
    """A disk-backed scratch dir (never tmpfs) shared by the stdlib tests."""
    path = os.path.join(os.path.expanduser("~"), "tmp", "sb-enroll-tests")
    os.makedirs(path, exist_ok=True)
    return path


def _patch_sign_metadata(monkeypatch) -> None:
    """Make the CLI's metadata signing openssl-free and deterministic.

    The fake key set in ``_write_key_set`` is not a real openssl key, so the
    real ``sign_metadata`` (default shim) would fail; replace it with a stub
    that returns a fixed signature.
    """
    monkeypatch.setattr(enroll, "sign_metadata", lambda metadata, out_dir: b"SIG-OK")


# --- db_cert_der -----------------------------------------------------------


def test_db_cert_der_matches_pem_to_der():
    d = _write_key_set(os.path.join(_tmp(), "der"))
    der = enroll.db_cert_der(d)
    with open(os.path.join(d, "DB.cert"), "rb") as handle:
        assert der == keys.pem_to_der(handle.read())


def test_db_cert_der_is_der_of_db_only():
    d = _write_key_set(os.path.join(_tmp(), "der2"))
    # The db DER must differ from the PK/KEK certs (it is the db cert).
    der = enroll.db_cert_der(d)
    assert der != keys.pem_to_der(_make_pem(b"CERT-PK"))
    assert der != keys.pem_to_der(_make_pem(b"CERT-KEK"))


def test_db_cert_der_missing_cert_raises():
    d = os.path.join(_tmp(), "der_missing")
    os.makedirs(d, exist_ok=True)
    with pytest.raises(enroll.EnrollError):
        enroll.db_cert_der(d)


# --- build_enrolment -------------------------------------------------------


def test_build_enrolment_mok_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(keys, "openssl_run", FakeShim())
    d = _write_key_set(str(tmp_path))
    material = enroll.build_enrolment(d, mode="mok")
    assert material.is_mok
    assert material.db_key_id == keys.load_key_set(d).db.key_id
    assert material.db_cert_der == enroll.db_cert_der(d)
    assert material.metadata_digest  # non-empty
    # The metadata signature is the hex of the (shim-signed) signature bytes:
    # the fake shim returns b"SIG-" + sha256(canonical-bytes).
    sig = bytes.fromhex(material.metadata_signature)
    assert sig.startswith(b"SIG-")
    assert len(sig) == 4 + 64  # "SIG-" + 64 hex chars of sha256


def test_build_enrolment_custom_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(keys, "openssl_run", FakeShim())
    d = _write_key_set(str(tmp_path))
    material = enroll.build_enrolment(d, mode="custom")
    assert not material.is_mok
    assert material.db_key_id == keys.load_key_set(d).db.key_id
    assert material.db_cert_der == enroll.db_cert_der(d)
    sig = bytes.fromhex(material.metadata_signature)
    assert sig.startswith(b"SIG-")
    assert len(sig) == 4 + 64


def test_build_enrolment_unknown_mode_raises():
    d = _write_key_set(os.path.join(_tmp(), "bad"))
    with pytest.raises(enroll.EnrollError):
        enroll.build_enrolment(d, mode="bogus")


def test_build_enrolment_missing_key_set_raises():
    d = os.path.join(_tmp(), "empty")
    os.makedirs(d, exist_ok=True)
    with pytest.raises(keys.MissingKeyError):
        enroll.build_enrolment(d, mode="mok")


def test_build_enrolment_insecure_key_raises(tmp_path):
    d = _write_key_set(str(tmp_path))
    os.chmod(os.path.join(d, "DB.key"), 0o644)  # loosened -> compromised
    with pytest.raises(keys.PrivateKeyError):
        enroll.build_enrolment(d, mode="mok")


# --- EnrolmentMaterial JSON ------------------------------------------------


def test_enrolment_material_json_roundtrip(tmp_path, monkeypatch):
    _patch_sign_metadata(monkeypatch)
    d = _write_key_set(str(tmp_path))
    material = enroll.build_enrolment(d, mode="custom")
    data = material.to_json()
    assert data["mode"] == "custom"
    assert data["db_key_id"] == material.db_key_id
    # The DER is base64-encoded in the bundle and decodes back to the same bytes.
    assert base64.b64decode(data["db_cert_der_b64"]) == material.db_cert_der
    assert base64.b64decode(data["metadata_signature_b64"]) == bytes.fromhex(
        material.metadata_signature
    )


# --- instructions_for ------------------------------------------------------


def test_instructions_mok_mentions_mokutil_import(tmp_path, monkeypatch):
    _patch_sign_metadata(monkeypatch)
    d = _write_key_set(str(tmp_path))
    material = enroll.build_enrolment(d, mode="mok")
    text = enroll.instructions_for(material)
    assert "mokutil --import" in text
    assert material.db_key_id in text
    # Never leaks key material.
    assert "PRIVATE KEY" not in text
    assert "0123456789abcdef" not in text


def test_instructions_custom_mentions_enrol_and_key_id(tmp_path, monkeypatch):
    _patch_sign_metadata(monkeypatch)
    d = _write_key_set(str(tmp_path))
    material = enroll.build_enrolment(d, mode="custom")
    text = enroll.instructions_for(material)
    assert "db" in text.lower()
    assert material.db_key_id in text
    assert "PRIVATE KEY" not in text


# --- write_enrolment -------------------------------------------------------


def test_write_enrolment_mok_writes_der_and_instructions_only(tmp_path, monkeypatch):
    _patch_sign_metadata(monkeypatch)
    d = _write_key_set(str(tmp_path / "keys"))
    material = enroll.build_enrolment(d, mode="mok")
    out = enroll.write_enrolment(material, tmp_path / "out")

    assert (out / enroll.CERT_DER).is_file()
    assert (out / enroll.CERT_DER).read_bytes() == material.db_cert_der
    assert (out / enroll.INSTRUCTIONS).is_file()
    # MOK mode has NO JSON bundle.
    assert not (out / enroll.ENROLMENT_JSON).exists()
    # Private keys are never written out.
    assert not any(out.glob("*.key"))
    # All artefacts are world-readable (public enrolment material).
    for name in (enroll.CERT_DER, enroll.INSTRUCTIONS):
        assert stat.S_IMODE((out / name).stat().st_mode) == 0o644
    # The material records the files it wrote.
    assert str(out / enroll.CERT_DER) in material.files


def test_write_enrolment_custom_writes_bundle(tmp_path, monkeypatch):
    _patch_sign_metadata(monkeypatch)
    d = _write_key_set(str(tmp_path / "keys"))
    material = enroll.build_enrolment(d, mode="custom")
    out = enroll.write_enrolment(material, tmp_path / "out")

    assert (out / enroll.CERT_DER).is_file()
    bundle = (out / enroll.ENROLMENT_JSON)
    assert bundle.is_file()
    data = json.loads(bundle.read_text())
    assert data["mode"] == "custom"
    assert data["db_key_id"] == material.db_key_id
    assert base64.b64decode(data["db_cert_der_b64"]) == material.db_cert_der
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o644
    assert not any(out.glob("*.key"))


# --- CLI: cmd_enroll -------------------------------------------------------


def test_cmd_enroll_mok_writes_material(tmp_path, capsys, monkeypatch):
    _patch_sign_metadata(monkeypatch)
    keys_dir = _write_key_set(str(tmp_path / "keys"))
    rc = cli.main(["enroll", "--keys", str(keys_dir), "--mode", "mok", "--out", str(tmp_path / "out")])
    assert rc == 0
    out = tmp_path / "out"
    assert (out / enroll.CERT_DER).is_file()
    assert (out / enroll.INSTRUCTIONS).is_file()
    assert not (out / enroll.ENROLMENT_JSON).exists()


def test_cmd_enroll_custom_writes_bundle(tmp_path, capsys, monkeypatch):
    _patch_sign_metadata(monkeypatch)
    keys_dir = _write_key_set(str(tmp_path / "keys"))
    rc = cli.main(["enroll", "--keys", str(keys_dir), "--mode", "custom", "--out", str(tmp_path / "out")])
    assert rc == 0
    assert (tmp_path / "out" / enroll.ENROLMENT_JSON).is_file()


def test_cmd_enroll_json_output(tmp_path, capsys, monkeypatch):
    _patch_sign_metadata(monkeypatch)
    keys_dir = _write_key_set(str(tmp_path / "keys"))
    rc = cli.main(["enroll", "--keys", str(keys_dir), "--mode", "mok", "--out", str(tmp_path / "out"), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "mok"
    assert data["db_key_id"]
    assert data["db_cert_der_b64"]


def test_cmd_enroll_report_never_prints_key_material(tmp_path, capsys, monkeypatch):
    _patch_sign_metadata(monkeypatch)
    keys_dir = _write_key_set(str(tmp_path / "keys"))
    rc = cli.main(["enroll", "--keys", str(keys_dir), "--mode", "mok", "--out", str(tmp_path / "out")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PRIVATE KEY" not in out
    assert "0123456789abcdef" not in out


def test_cmd_enroll_missing_key_set_errors(tmp_path, capsys):
    empty = os.path.join(str(tmp_path), "empty-keys")
    os.makedirs(empty, exist_ok=True)
    rc = cli.main(["enroll", "--keys", empty, "--mode", "mok"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_cmd_enroll_insecure_key_errors(tmp_path, capsys):
    keys_dir = _write_key_set(str(tmp_path / "keys"))
    os.chmod(os.path.join(keys_dir, "DB.key"), 0o644)
    rc = cli.main(["enroll", "--keys", str(keys_dir), "--mode", "mok"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_cmd_enroll_unknown_mode_rejected_by_argparse():
    with pytest.raises(SystemExit):
        cli.main(["enroll", "--keys", "keys", "--mode", "bogus"])


def test_cmd_enroll_default_out_is_cwd(tmp_path, capsys, monkeypatch):
    _patch_sign_metadata(monkeypatch)
    keys_dir = _write_key_set(str(tmp_path / "keys"))
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["enroll", "--keys", str(keys_dir), "--mode", "mok"])
    assert rc == 0
    assert (tmp_path / enroll.CERT_DER).is_file()


# --- opt-in integration: real openssl round-trip ---------------------------


@pytest.mark.skipif(not REAL_OPENSSL, reason="needs openssl")
def test_enroll_with_real_openssl(tmp_path):
    """Build enrolment material against *real* openssl key material (opt-in)."""
    if os.environ.get("PENGYOS_IT") != "1":
        pytest.skip("real openssl enrolment is opt-in (PENGYOS_IT=1)")

    keys_dir = str(tmp_path)
    # Real EC P-256 keys + self-signed certs replace the fakes.
    keys.generate_all(keys_dir)

    for role in ("PK", "KEK", "DB"):
        mode = stat.S_IMODE((tmp_path / f"{role}.key").stat().st_mode)
        assert mode & 0o077 == 0

    material = enroll.build_enrolment(keys_dir, mode="custom")
    assert material.db_key_id == keys.load_key_set(keys_dir).db.key_id

    # The DER we extracted equals openssl's own DER of the db cert.
    der = subprocess.run(
        ["openssl", "x509", "-in", str(tmp_path / "DB.cert"), "-outform", "DER"],
        capture_output=True,
    )
    assert der.returncode == 0, der.stderr.decode()
    assert material.db_cert_der == der.stdout

    # Write the bundle and confirm the DER on disk is the enrolment artefact.
    out = enroll.write_enrolment(material, tmp_path / "out")
    assert (out / enroll.CERT_DER).read_bytes() == der.stdout
