"""Tests for the permission-policy enforcement in ``pengyos_sbkeys.security``.

These pin down the contract ``spec.md`` "Security posture" promises for the
guard: a private key with group/world bits is refused (fail closed), while
secure ``0600`` keys and world-readable certificates pass. The guard is exercised
both directly (the pure functions) and through the ``enroll-commands`` / ``sign``
CLI paths.
"""

import os
import stat
from pathlib import Path

import pytest

from pengyos_sbkeys import cli
from pengyos_sbkeys import security
from pengyos_sbkeys.keys import (
    KEY_FILES,
    KeyKind,
    key_file_for_name,
    perms_for,
)
from pengyos_sbkeys.security import (
    PERMISSIONS_MASK,
    PermCheckError,
    check_dir,
    check_key,
    enforce_dir,
    enforce_key,
    perms_are_secure,
    resolve_key_file,
)


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fake_generate_all(monkeypatch):
    """Keep the ``init``-involving flow tests openssl-free.

    ``init`` materializes keys via ``openssl`` (``generate_all``); replace it
    with a dummy that writes PEM files so the guard / flow tests never invoke a
    real ``openssl``. See ``test_init.py`` for the same approach.
    """

    def _fake(keys_dir):
        keys_dir = Path(keys_dir)
        keys_dir.mkdir(parents=True, exist_ok=True)
        for key_file in KEY_FILES:
            (keys_dir / key_file.name).write_bytes(b"-----BEGIN PRIVATE KEY-----\n")

    monkeypatch.setattr(cli, "generate_all", _fake)


def _write_all(tmp_path: Path, perms: dict[str, int]) -> Path:
    """Create a full key set under ``tmp_path/keys`` with the given per-file
    mode map (by filename). Returns the ``keys`` path."""
    keys = tmp_path / "keys"
    keys.mkdir()
    for key_file in KEY_FILES:
        path = keys / key_file.name
        path.write_bytes(b"material")
        os.chmod(path, perms[key_file.name])
    return keys


def _secure_perms() -> dict[str, int]:
    """0600 for private keys, 0644 for certificates."""
    return {key_file.name: perms_for(key_file) for key_file in KEY_FILES}


# --- policy constants -------------------------------------------------------


def test_permissions_mask_is_group_and_world_bits():
    assert PERMISSIONS_MASK == 0o077


# --- perms_are_secure -------------------------------------------------------


def test_private_key_is_secure_at_0600(tmp_path):
    keys = _write_all(tmp_path, _secure_perms())
    key_file = next(k for k in KEY_FILES if k.name == "DB.key")
    assert perms_are_secure(key_file, keys / "DB.key") is True


def test_private_key_is_secure_at_0400(tmp_path):
    # Read-only private key is *stricter* than 0600, so still secure.
    keys = _write_all(tmp_path, {**_secure_perms(), "DB.key": 0o400})
    key_file = next(k for k in KEY_FILES if k.name == "DB.key")
    assert perms_are_secure(key_file, keys / "DB.key") is True


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o666, 0o777])
def test_private_key_insecure_with_group_world_bits(tmp_path, mode):
    keys = _write_all(tmp_path, {**_secure_perms(), "DB.key": mode})
    key_file = next(k for k in KEY_FILES if k.name == "DB.key")
    assert perms_are_secure(key_file, keys / "DB.key") is False


def test_certificate_is_always_secure(tmp_path):
    # Certificates are public: even 0666 must pass.
    keys = _write_all(tmp_path, {"DB.cert": 0o666, **_secure_perms()})
    key_file = next(k for k in KEY_FILES if k.name == "DB.cert")
    assert key_file.kind is KeyKind.PUBLIC
    assert perms_are_secure(key_file, keys / "DB.cert") is True


# --- check_dir / enforce_dir ------------------------------------------------


def test_check_dir_empty_when_all_secure(tmp_path):
    keys = _write_all(tmp_path, _secure_perms())
    assert check_dir(keys) == []


def test_check_dir_reports_loose_private_keys(tmp_path):
    keys = _write_all(
        tmp_path,
        {
            "PK.key": 0o600,
            "KEK.key": 0o644,
            "DB.key": 0o604,
            "PK.cert": 0o644,
            "KEK.cert": 0o644,
            "DB.cert": 0o644,
        },
    )
    reported = check_dir(keys)
    assert str(keys / "KEK.key") in reported
    assert str(keys / "DB.key") in reported
    assert str(keys / "PK.key") not in reported


def test_check_dir_ignores_missing_files(tmp_path):
    # No private keys present -> no violations (missing files are reported
    # elsewhere, not here).
    keys = tmp_path / "keys"
    keys.mkdir()
    assert check_dir(keys) == []


def test_enforce_dir_passes_when_secure(tmp_path):
    keys = _write_all(tmp_path, _secure_perms())
    enforce_dir(keys)  # must not raise


def test_enforce_dir_raises_on_loose(tmp_path):
    keys = _write_all(
        tmp_path, {"DB.key": 0o644, **{k.name: perms_for(k) for k in KEY_FILES if k.name != "DB.key"}}
    )
    with pytest.raises(PermCheckError) as excinfo:
        enforce_dir(keys)
    assert "insecure key permissions" in str(excinfo.value)
    assert "DB.key" in str(excinfo.value)


# --- check_key / enforce_key (single key) -----------------------------------


def test_check_key_resolves_managed_private_key(tmp_path):
    keys = _write_all(tmp_path, {**_secure_perms(), "DB.key": 0o644})
    # No KeyFile supplied -> resolved from the filename.
    assert check_key(keys / "DB.key") is False


def test_check_key_resolves_managed_private_key_secure(tmp_path):
    keys = _write_all(tmp_path, _secure_perms())
    assert check_key(keys / "DB.key") is True


def test_check_key_unmanaged_key_is_never_blocked(tmp_path):
    # An arbitrary, unmanaged key cannot be classified, so it must not block
    # legitimate signing even with loose perms.
    foreign = tmp_path / "foreign.key"
    foreign.write_bytes(b"not one of ours")
    os.chmod(foreign, 0o644)
    assert check_key(foreign) is True


def test_enforce_key_raises_on_managed_private_loose(tmp_path):
    keys = _write_all(tmp_path, {**_secure_perms(), "DB.key": 0o644})
    with pytest.raises(PermCheckError):
        enforce_key(keys / "DB.key")


def test_enforce_key_passes_on_public(tmp_path):
    keys = _write_all(tmp_path, {"DB.cert": 0o666, **_secure_perms()})
    cert_file = next(k for k in KEY_FILES if k.name == "DB.cert")
    enforce_key(keys / "DB.cert", cert_file)  # must not raise


# --- key-file resolution ----------------------------------------------------


def test_resolve_key_file_matches_managed(tmp_path):
    keys = _write_all(tmp_path, _secure_perms())
    resolved = resolve_key_file(keys / "PK.key")
    assert resolved is not None
    assert resolved.kind is KeyKind.PRIVATE
    assert resolved.role == "PK"


def test_resolve_key_file_none_for_unmanaged():
    assert resolve_key_file(Path("/tmp/not-managed.key")) is None


def test_key_file_for_name_returns_none_for_unknown():
    assert key_file_for_name("nope.key") is None


# --- CLI integration: enroll-commands ---------------------------------------


def test_cli_enroll_refuses_when_private_key_loose(tmp_path, capsys, monkeypatch):
    keys = _write_all(
        tmp_path,
        {
            "PK.key": 0o600,
            "KEK.key": 0o600,
            "DB.key": 0o644,
            "PK.cert": 0o644,
            "KEK.cert": 0o644,
            "DB.cert": 0o644,
        },
    )
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["enroll-commands", "--keys", "keys"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "insecure key permissions" in err
    assert "DB.key" in err


def test_cli_enroll_ok_when_all_private_keys_secure(tmp_path, capsys, monkeypatch):
    keys = _write_all(tmp_path, _secure_perms())
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["enroll-commands", "--keys", "keys"])
    assert rc == 0
    assert "insecure" not in capsys.readouterr().err


def test_cli_enroll_json_refuses_when_private_key_loose(tmp_path, capsys, monkeypatch):
    keys = _write_all(
        tmp_path, {"DB.key": 0o644, **{k.name: perms_for(k) for k in KEY_FILES if k.name != "DB.key"}}
    )
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["enroll-commands", "--keys", "keys", "--json"])
    assert rc == 1
    assert "insecure key permissions" in capsys.readouterr().err


# --- CLI integration: sign --------------------------------------------------


def test_cli_sign_refuses_when_private_key_loose(tmp_path, capsys, monkeypatch):
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "DB.key").write_bytes(b"-----BEGIN PRIVATE KEY-----\n")
    (keys / "DB.cert").write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    (tmp_path / "uki.efi").write_bytes(b"\x00PEI-fake\n")
    os.chmod(keys / "DB.key", 0o644)  # insecure
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["sign", "uki.efi", "--key", "keys/DB.key"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "insecure key permissions" in err
    assert "keys/DB.key" in err


def test_cli_sign_ok_when_private_key_secure(tmp_path, capsys, monkeypatch):
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "DB.key").write_bytes(b"-----BEGIN PRIVATE KEY-----\n")
    (keys / "DB.cert").write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    (tmp_path / "uki.efi").write_bytes(b"\x00PEI-fake\n")
    os.chmod(keys / "DB.key", 0o600)
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["sign", "uki.efi", "--key", "keys/DB.key"])
    assert rc == 0
    assert "insecure" not in capsys.readouterr().err


def test_cli_sign_unmanaged_loose_key_is_allowed(tmp_path, capsys, monkeypatch):
    # A foreign key with loose perms is not part of the managed set, so signing
    # with it is not blocked by the guard.
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "foreign.key").write_bytes(b"-----BEGIN PRIVATE KEY-----\n")
    (keys / "foreign.cert").write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    os.chmod(keys / "foreign.key", 0o644)
    (tmp_path / "uki.efi").write_bytes(b"\x00PEI-fake\n")
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["sign", "uki.efi", "--key", "keys/foreign.key"])
    assert rc == 0
    assert "insecure" not in capsys.readouterr().err


# --- end-to-end flow --------------------------------------------------------


def test_init_then_enroll_and_sign_flow(tmp_path, capsys, monkeypatch):
    # The realistic happy path: `init` produces protected keys, then enrollment
    # and signing both succeed against them.
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "--out", "keys"]) == 0
    (tmp_path / "uki.efi").write_bytes(b"\x00PEI-fake-uki-bytes\n")

    assert cli.main(["enroll-commands", "--keys", "keys"]) == 0
    capsys.readouterr()
    assert cli.main(["sign", "uki.efi", "--key", "keys/DB.key"]) == 0


def test_init_then_loosen_then_sign_is_refused(tmp_path, capsys, monkeypatch):
    # Simulate a key that was loosened after init: the guard refuses to sign.
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "--out", "keys"]) == 0
    os.chmod(tmp_path / "keys" / "DB.key", 0o644)
    (tmp_path / "uki.efi").write_bytes(b"\x00PEI-fake-uki-bytes\n")

    rc = cli.main(["sign", "uki.efi", "--key", "keys/DB.key"])
    assert rc == 1
    assert "insecure key permissions" in capsys.readouterr().err


# --- no-op guard ------------------------------------------------------------


def test_security_error_is_runtime():
    assert issubclass(PermCheckError, RuntimeError)
