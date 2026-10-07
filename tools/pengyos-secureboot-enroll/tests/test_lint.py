"""Tests for the ``lint`` subtask (``lint`` + ``cmd_lint``).

These pin down what ``lint`` must do — fail when an ESP binary is unsigned or
signed by a key NOT in db:

* **unsigned** -- an ESP binary with no recorded signature binding is rejected;
* **untrusted_signer** -- a binary signed by a key that is *not* in ``db`` is
  rejected; and
* **signature_invalid** -- a binary whose recorded signature does not verify over
  the current bytes (a tampered / forged binary) is rejected.

A signed tree lints clean; each mutation fires exactly one rule. ``lint`` reuses
:func:`pengyos_secureboot_enroll.sign_chain.sign_binaries` (to produce real
bindings) and the injectable signer (no real openssl, no root, no network).

A single opt-in integration test (``PENGYOS_IT=1``) runs the whole
sign -> verify -> lint round-trip against *real* openssl.
"""

import base64
import hashlib
import json
import os
import shutil

import pytest

from pengyos_secureboot_enroll import cli
from pengyos_secureboot_enroll import keys
from pengyos_secureboot_enroll import sign_chain as sc
from pengyos_secureboot_enroll import lint as lint_module
from pengyos_secureboot_enroll.sign_chain import SIGNATURES_JSON
from pengyos_secureboot_enroll.truststate import KeyPerms

REAL_OPENSSL = shutil.which("openssl") is not None


# --- fixtures --------------------------------------------------------------


def _make_pem(body: bytes) -> bytes:
    """Wrap ``body`` in a single-cert PEM with correct base64 padding."""
    b64 = base64.b64encode(body).decode("ascii")
    wrapped = "\n".join(b64[i : i + 64] for i in range(0, len(b64), 64))
    return f"-----BEGIN CERTIFICATE-----\n{wrapped}\n-----END CERTIFICATE-----\n".encode("utf-8")


def _write_key_set(dirpath: "os.PathLike | str", role_suffix: str = "0123456789") -> str:
    """Write a deterministic, valid PEM key set (PK/KEK/db) under ``dirpath``.

    ``role_suffix`` distinguishes key sets so a *second* set has a different
    ``db`` key id (the "removed db entry" / "wrong key" tests need a foreign db
    key id that is not the primary one). Returns the absolute path.
    """
    dirpath = os.path.abspath(dirpath)
    os.makedirs(dirpath, exist_ok=True)
    for role in ("PK", "KEK", "DB"):
        cert = os.path.join(dirpath, f"{role}.cert")
        with open(cert, "wb") as handle:
            handle.write(_make_pem(f"CERT-{role}-{role_suffix}".encode()))
        key = os.path.join(dirpath, f"{role}.key")
        with open(key, "wb") as handle:
            handle.write(f"PRIVATE-KEY-{role}-{role_suffix}".encode())
        os.chmod(key, KeyPerms.PRIVATE)
        os.chmod(cert, KeyPerms.PUBLIC)
    return dirpath


def _write_esp(esp_dir: "os.PathLike | str", paths: dict) -> str:
    """Write the given ``{esp_relative_path: bytes}`` into ``esp_dir``."""
    esp_dir = os.path.abspath(esp_dir)
    os.makedirs(esp_dir, exist_ok=True)
    for rel, data in paths.items():
        target = os.path.join(esp_dir, rel.lstrip("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
    return esp_dir


def _signed_esp(tmp_path):
    """Return ``(keys_dir, esp, trust_root, bindings)`` for a signed tree.

    The ESP carries the three pinned boot-chain binaries, each signed by the db
    key; ``bindings`` is the list recorded to ``signatures.json``.
    """
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
    signed = sc.sign_binaries(esp, sc.build_manifest(), trust_root)
    sc.save_bindings(signed.bindings, os.path.join(keys_dir, SIGNATURES_JSON))
    bindings = sc.load_bindings(os.path.join(keys_dir, SIGNATURES_JSON))
    return keys_dir, esp, trust_root, bindings


# --- LintResult ------------------------------------------------------------


def test_lint_result_verified_clean():
    result = lint_module.LintResult(violations=[], binaries_checked=3)
    assert result.verified is True
    assert result.binaries_checked == 3


def test_lint_result_verified_with_violation():
    result = lint_module.LintResult(
        violations=[lint_module.LintViolation("/x", "unsigned")], binaries_checked=2
    )
    assert result.verified is False


def test_lint_result_empty_is_not_verified():
    # An empty ESP (no binaries checked) is not a passing lint.
    result = lint_module.LintResult(violations=[], binaries_checked=0)
    assert result.verified is False


def test_lint_violation_is_frozen():
    violation = lint_module.LintViolation("/EFI/BOOT/BOOTX64.EFI", "unsigned")
    assert violation.path == "/EFI/BOOT/BOOTX64.EFI"
    assert violation.reason == "unsigned"
    with pytest.raises(Exception):
        violation.reason = "x"  # frozen dataclass


# --- lint_esp: clean -------------------------------------------------------


def test_lint_esp_clean_lints_ok(tmp_path):
    _, esp, trust_root, bindings = _signed_esp(tmp_path)
    result = lint_module.lint_esp(esp, sc.build_manifest(), trust_root, bindings=bindings)
    assert result.verified is True
    assert result.binaries_checked == 3
    assert result.violations == []


def test_lint_esp_counts_checked_binaries(tmp_path):
    _, esp, trust_root, bindings = _signed_esp(tmp_path)
    result = lint_module.lint_esp(esp, sc.build_manifest(), trust_root, bindings=bindings)
    assert result.binaries_checked == 3


def test_lint_esp_explicit_paths(tmp_path):
    keys_dir = _write_key_set(os.path.join(str(tmp_path), "keys"))
    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {"/EFI/BOOT/BOOTX64.EFI": b"boot-fallback-bytes"},
    )
    trust_root = keys.load_key_set(keys_dir)
    signed = sc.sign_binaries(esp, sc.build_manifest(paths=["/EFI/BOOT/BOOTX64.EFI"]), trust_root)
    bindings = signed.bindings
    result = lint_module.lint_esp(esp, sc.build_manifest(paths=["/EFI/BOOT/BOOTX64.EFI"]), trust_root, bindings=bindings)
    assert result.verified is True
    assert result.binaries_checked == 1


# --- lint_esp: unsigned ----------------------------------------------------


def test_lint_esp_unsigned_binary_fails(tmp_path):
    """A binary on the ESP with no recorded signature is rejected (unsigned)."""
    keys_dir = _write_key_set(os.path.join(str(tmp_path), "keys"))
    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {
            "/EFI/systemd/systemd-bootx64.efi": b"systemd-boot-bytes",
            "/EFI/BOOT/BOOTX64.EFI": b"boot-fallback-bytes",
            "/EFI/Linux/pengyos-server+1.0.0-a.efi": b"uki-bytes",
        },
    )
    # No bindings at all: every expected binary is unsigned.
    trust_root = keys.load_key_set(keys_dir)
    result = lint_module.lint_esp(esp, sc.build_manifest(), trust_root, bindings=[])
    assert result.verified is False
    reasons = {v.path: v.reason for v in result.violations}
    assert reasons == {
        "/EFI/systemd/systemd-bootx64.efi": "unsigned",
        "/EFI/BOOT/BOOTX64.EFI": "unsigned",
        "/EFI/Linux/pengyos-server+1.0.0-a.efi": "unsigned",
    }


def test_lint_esp_one_unsigned_fails(tmp_path):
    """A single unsigned binary among signed ones fires the unsigned rule once."""
    keys_dir, esp, trust_root, bindings = _signed_esp(tmp_path)
    # Drop the binding for the fallback bootloader -> it is now unsigned.
    unsigned_path = "/EFI/BOOT/BOOTX64.EFI"
    filtered = [b for b in bindings if b.path != unsigned_path]
    result = lint_module.lint_esp(esp, sc.build_manifest(), trust_root, bindings=filtered)
    assert result.verified is False
    assert result.binaries_checked == 2
    assert [v.path for v in result.violations] == [unsigned_path]
    assert result.violations[0].reason == "unsigned"


# --- lint_esp: untrusted signer (wrong key) --------------------------------


def test_lint_esp_untrusted_signer_fails(tmp_path):
    """A binary signed by a key NOT in db is rejected (untrusted_signer)."""
    keys_dir = _write_key_set(os.path.join(str(tmp_path), "keys"))
    # A *foreign* key set: a different db cert -> a different db key id that is
    # not in the primary trust root's db.
    foreign_dir = _write_key_set(os.path.join(str(tmp_path), "foreign"), role_suffix="FOREIGN")
    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {"/EFI/BOOT/BOOTX64.EFI": b"boot-fallback-bytes"},
    )

    foreign_root = keys.load_key_set(foreign_dir)
    foreign_sig = sc.sign_bytes(b"boot-fallback-bytes", os.path.join(foreign_dir, "DB.key"))
    foreign_binding = sc.SignatureBinding(
        path="/EFI/BOOT/BOOTX64.EFI",
        sha256=hashlib.sha256(b"boot-fallback-bytes").hexdigest(),
        key_id=foreign_root.db.key_id,  # a key id not in the primary db
        signature=foreign_sig.hex(),
        signer="stdlib-ed25519",
    )

    primary_root = keys.load_key_set(keys_dir)
    result = lint_module.lint_esp(
        esp, sc.build_manifest(paths=["/EFI/BOOT/BOOTX64.EFI"]), primary_root, bindings=[foreign_binding]
    )
    assert result.verified is False
    assert result.binaries_checked == 0
    assert result.violations[0].reason == "untrusted_signer"


def test_lint_esp_removed_db_entry_fails(tmp_path):
    """If the signing key is no longer in db, the binary is rejected.

    Models a ``db`` that no longer contains the key that signed the binary: the
    binary was signed by ``foreign_dir``'s db key, and we lint against that same
    foreign key set after *removing* its db entry (an empty db trust root cannot
    contain the signer), so the signer is no longer trusted.
    """
    foreign_dir = _write_key_set(os.path.join(str(tmp_path), "foreign"), role_suffix="REMOVED")
    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {"/EFI/BOOT/BOOTX64.EFI": b"boot-fallback-bytes"},
    )
    foreign_root = keys.load_key_set(foreign_dir)
    foreign_sig = sc.sign_bytes(b"boot-fallback-bytes", os.path.join(foreign_dir, "DB.key"))
    binding = sc.SignatureBinding(
        path="/EFI/BOOT/BOOTX64.EFI",
        sha256=hashlib.sha256(b"boot-fallback-bytes").hexdigest(),
        key_id=foreign_root.db.key_id,
        signature=foreign_sig.hex(),
        signer="stdlib-ed25519",
    )

    # A trust root whose db key id is a *different* (foreign, absent) key: the
    # real signer is not in db.
    absent_dir = _write_key_set(os.path.join(str(tmp_path), "absent"), role_suffix="ABSENT")
    absent_root = keys.load_key_set(absent_dir)

    result = lint_module.lint_esp(
        esp, sc.build_manifest(paths=["/EFI/BOOT/BOOTX64.EFI"]), absent_root, bindings=[binding]
    )
    assert result.verified is False
    assert result.violations[0].reason == "untrusted_signer"


# --- lint_esp: signature invalid (tampered) --------------------------------


def test_lint_esp_tampered_binary_fails(tmp_path):
    """A binary whose recorded signature does not match its bytes is rejected."""
    keys_dir, esp, trust_root, bindings = _signed_esp(tmp_path)
    # Tamper the fallback bootloader on disk after it was signed.
    with open(os.path.join(esp, "EFI/BOOT/BOOTX64.EFI"), "wb") as handle:
        handle.write(b"TAMPERED")
    result = lint_module.lint_esp(esp, sc.build_manifest(), trust_root, bindings=bindings)
    assert result.verified is False
    assert result.binaries_checked == 2
    tampered = [v for v in result.violations if v.path == "/EFI/BOOT/BOOTX64.EFI"]
    assert len(tampered) == 1
    assert tampered[0].reason == "signature_invalid"


# --- lint_esp: missing binary ----------------------------------------------


def test_lint_esp_missing_binary_fails(tmp_path):
    """An expected binary absent from the ESP is rejected (missing)."""
    keys_dir, esp, trust_root, bindings = _signed_esp(tmp_path)
    os.remove(os.path.join(esp, "EFI/BOOT/BOOTX64.EFI"))
    result = lint_module.lint_esp(esp, sc.build_manifest(), trust_root, bindings=bindings)
    assert result.verified is False
    missing = [v for v in result.violations if v.path == "/EFI/BOOT/BOOTX64.EFI"]
    assert len(missing) == 1
    assert missing[0].reason == "missing"


def test_lint_esp_no_sidecar_fails_closed(tmp_path):
    """Without the verification-key sidecar the stdlib verify returns False."""
    keys_dir, esp, trust_root, bindings = _signed_esp(tmp_path)
    os.remove(os.path.join(keys_dir, "DB.cert.ed25519.pub"))
    result = lint_module.lint_esp(esp, sc.build_manifest(), trust_root, bindings=bindings)
    assert result.verified is False
    assert all(v.reason == "signature_invalid" for v in result.violations)


# --- CLI: cmd_lint ---------------------------------------------------------


def test_cmd_lint_clean(tmp_path, capsys):
    keys_dir, esp, _, _ = _signed_esp(tmp_path)
    rc = cli.main(["lint", "--esp", str(esp), "--keys", str(keys_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "lint OK" in out
    # Never prints key material.
    assert "PRIVATE KEY" not in out
    assert "0123456789" not in out


def test_cmd_lint_unsigned_fails(tmp_path, capsys):
    keys_dir = _write_key_set(os.path.join(str(tmp_path), "keys"))
    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {
            "/EFI/systemd/systemd-bootx64.efi": b"a",
            "/EFI/BOOT/BOOTX64.EFI": b"b",
            "/EFI/Linux/pengyos-server+1.0.0-a.efi": b"c",
        },
    )
    rc = cli.main(["lint", "--esp", str(esp), "--keys", str(keys_dir)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAILED" in err
    assert "unsigned" in err


def test_cmd_lint_untrusted_signer_fails(tmp_path, capsys):
    keys_dir = _write_key_set(os.path.join(str(tmp_path), "keys"))
    foreign_dir = _write_key_set(os.path.join(str(tmp_path), "foreign"), role_suffix="FOREIGN")
    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {"/EFI/BOOT/BOOTX64.EFI": b"boot-fallback-bytes"},
    )
    foreign_root = keys.load_key_set(foreign_dir)
    foreign_sig = sc.sign_bytes(b"boot-fallback-bytes", os.path.join(foreign_dir, "DB.key"))
    binding = sc.SignatureBinding(
        path="/EFI/BOOT/BOOTX64.EFI",
        sha256=hashlib.sha256(b"boot-fallback-bytes").hexdigest(),
        key_id=foreign_root.db.key_id,
        signature=foreign_sig.hex(),
        signer="stdlib-ed25519",
    )
    sc.save_bindings([binding], os.path.join(keys_dir, SIGNATURES_JSON))
    rc = cli.main(["lint", "--esp", str(esp), "--keys", str(keys_dir)])
    assert rc == 1
    assert "untrusted_signer" in capsys.readouterr().err


def test_cmd_lint_tampered_fails(tmp_path, capsys):
    keys_dir, esp, _, _ = _signed_esp(tmp_path)
    with open(os.path.join(esp, "EFI/BOOT/BOOTX64.EFI"), "wb") as handle:
        handle.write(b"TAMPERED")
    rc = cli.main(["lint", "--esp", str(esp), "--keys", str(keys_dir)])
    assert rc == 1
    assert "signature_invalid" in capsys.readouterr().err


def test_cmd_lint_json_clean(tmp_path, capsys):
    keys_dir, esp, _, _ = _signed_esp(tmp_path)
    rc = cli.main(["lint", "--esp", str(esp), "--keys", str(keys_dir), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verified"] is True
    assert data["binaries_checked"] == 3
    assert data["violations"] == []


def test_cmd_lint_json_violation_reports_reason(tmp_path, capsys):
    keys_dir, esp, _, _ = _signed_esp(tmp_path)
    os.remove(os.path.join(esp, "EFI/BOOT/BOOTX64.EFI"))
    rc = cli.main(["lint", "--esp", str(esp), "--keys", str(keys_dir), "--json"])
    assert rc == 1
    data = json.loads(capsys.readouterr().out)
    assert data["verified"] is False
    assert data["violations"] == [{"path": "/EFI/BOOT/BOOTX64.EFI", "reason": "missing"}]


def test_cmd_lint_missing_esp_errors(tmp_path):
    keys_dir = _write_key_set(os.path.join(str(tmp_path), "keys"))
    rc = cli.main(["lint", "--esp", os.path.join(str(tmp_path), "nope"), "--keys", str(keys_dir)])
    assert rc == 1


def test_cmd_lint_missing_key_set_errors(tmp_path, capsys):
    empty = os.path.join(str(tmp_path), "empty-keys")
    os.makedirs(empty, exist_ok=True)
    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {"/EFI/BOOT/BOOTX64.EFI": b"x"},
    )
    rc = cli.main(["lint", "--esp", str(esp), "--keys", empty])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_cmd_lint_insecure_key_perms_errors(tmp_path, capsys):
    keys_dir = _write_key_set(os.path.join(str(tmp_path), "keys"))
    os.chmod(os.path.join(keys_dir, "DB.key"), 0o644)  # loosened -> compromised
    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {"/EFI/BOOT/BOOTX64.EFI": b"x"},
    )
    rc = cli.main(["lint", "--esp", str(esp), "--keys", str(keys_dir)])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_cmd_lint_reports_no_key_material(tmp_path, capsys):
    keys_dir, esp, _, _ = _signed_esp(tmp_path)
    rc = cli.main(["lint", "--esp", str(esp), "--keys", str(keys_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PRIVATE KEY" not in out
    assert "0123456789" not in out


def test_cmd_lint_signatures_json_shape(tmp_path):
    keys_dir, esp, _, _ = _signed_esp(tmp_path)
    cli.main(["lint", "--esp", str(esp), "--keys", str(keys_dir)])
    data = json.loads(open(os.path.join(keys_dir, SIGNATURES_JSON)).read())  # noqa: SIM115
    assert data["version"] == 1
    assert len(data["bindings"]) == 3


# --- opt-in integration: real openssl round-trip ---------------------------


@pytest.mark.skipif(not REAL_OPENSSL, reason="needs openssl")
def test_lint_with_real_openssl(tmp_path):
    """Sign -> verify -> lint against *real* openssl (opt-in)."""
    if os.environ.get("PENGYOS_IT") != "1":
        pytest.skip("real openssl round-trip is opt-in (PENGYOS_IT=1)")

    keys_dir = _write_key_set(str(tmp_path))
    # Real EC P-256 keys + self-signed certs replace the fakes.
    for role in ("PK", "KEK", "DB"):
        key_path = os.path.join(str(tmp_path), f"{role}.key")
        cert_path = os.path.join(str(tmp_path), f"{role}.cert")
        subprocess_run(
            "openssl", "genpkey", "-algorithm", "EC",
            "-pkeyopt", "ec_paramgen_curve:P-256", "-out", key_path,
        )
        subprocess_run(
            "openssl", "req", "-x509", "-new", "-key", key_path, "-days", "2",
            "-subj", f"/CN=PengyOS Secure Boot {role}", "-out", cert_path,
        )
        os.chmod(key_path, KeyPerms.PRIVATE)
        os.chmod(cert_path, KeyPerms.PUBLIC)

    esp = _write_esp(
        os.path.join(str(tmp_path), "esp"),
        {
            "/EFI/systemd/systemd-bootx64.efi": b"systemd-boot-bytes",
            "/EFI/BOOT/BOOTX64.EFI": b"boot-fallback-bytes",
            "/EFI/Linux/pengyos-server+1.0.0-a.efi": b"uki-bytes",
        },
    )
    trust_root = keys.load_key_set(str(tmp_path))
    signed = sc.sign_binaries(esp, sc.build_manifest(), trust_root)
    assert signed.verified
    # sign_binaries verifies in memory but does not persist; record the bindings
    # so lint can read them back (the gotcha from the non-real round-trip tests).
    sc.save_bindings(signed.bindings, os.path.join(str(tmp_path), SIGNATURES_JSON))
    bindings = sc.load_bindings(os.path.join(str(tmp_path), SIGNATURES_JSON))
    result = lint_module.lint_esp(esp, sc.build_manifest(), trust_root, bindings=bindings)
    assert result.verified is True
    assert result.binaries_checked == 3


def subprocess_run(*args):
    """Run a subprocess, raising on failure (local helper for the opt-in test)."""
    import subprocess

    completed = subprocess.run(list(args), capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode("utf-8", "replace").strip())
    return completed
