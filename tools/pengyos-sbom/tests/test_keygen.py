"""Tests for the ``keygen`` subcommand (``pengyos_sbom.keys`` + CLI).

Covers the keypair-generation contract:

* ``generate_keypair`` / ``write_keypair`` produce matching 32-byte seed
  and public-key files (the public key is *derived* from the seed, so the
  pair can never be mismatched) — pinned against the RFC 8032 TEST 1 key;
* fresh seeds come from ``os.urandom`` (two calls differ);
* existing files are **never** overwritten (and nothing is half-written:
  if either target exists, neither is touched);
* the seed file is ``0o600`` and the public key ``0o644`` (POSIX);
* the generated files are immediately loadable by
  :mod:`pengyos_sbom.signing` (the whole point: no external tooling);
* CLI-level: ``--help``, default paths (``seed.key`` / ``seed.key.pub``),
  explicit ``--out`` / ``--pub``, overwrite refusal (exit 1), and a full
  ``keygen`` → ``sign`` → ``verify`` round-trip against the committed
  fixtures.

No network, no root, no external binaries — stdlib only.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
LEGAL = FIX / "legal-info"
SHOWINFO = FIX / "show-info.json"

from pengyos_sbom import cli, constants, ed25519, keys, signing  # noqa: E402

# RFC 8032 §7.1 TEST 1: secret key / public key pair (pins derivation;
# cross-checked against the audited `cryptography` implementation).
RFC_SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)
RFC_PUB = bytes.fromhex(
    "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
)


def run_cli(*args, cwd=REPO):
    """Run the CLI as a subprocess; PYTHONPATH pins the in-tree package so
    the test can run from any cwd (the tool is not pip-installed)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO)
    return subprocess.run(
        [sys.executable, "-m", "pengyos_sbom", *map(str, args)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


# --- keys.write_keypair (pinned seed) ------------------------------------
def test_write_keypair_writes_matching_files(tmp_path):
    seed_path = tmp_path / "s.key"
    pub_path = tmp_path / "p.key"
    sp, pp = keys.write_keypair(RFC_SEED, seed_path, pub_path)
    assert sp == seed_path and pp == pub_path
    assert seed_path.read_bytes() == RFC_SEED
    assert pub_path.read_bytes() == RFC_PUB  # RFC 8032 TEST 1 public key


def test_write_keypair_rejects_wrong_seed_length(tmp_path):
    with pytest.raises(ValueError):
        keys.write_keypair(b"short", tmp_path / "a", tmp_path / "b")
    with pytest.raises(ValueError):
        keys.write_keypair(os.urandom(33), tmp_path / "a", tmp_path / "b")
    # Nothing may have been written by the failed calls.
    assert not (tmp_path / "a").exists()
    assert not (tmp_path / "b").exists()


def test_write_keypair_refuses_existing_seed_and_writes_nothing(tmp_path):
    seed_path = tmp_path / "s.key"
    pub_path = tmp_path / "p.key"
    seed_path.write_bytes(b"\x00" * 32)  # pre-existing file
    with pytest.raises(keys.KeygenError):
        keys.write_keypair(RFC_SEED, seed_path, pub_path)
    assert seed_path.read_bytes() == b"\x00" * 32  # untouched
    assert not pub_path.exists()  # nothing half-written


def test_write_keypair_refuses_existing_pub_and_writes_nothing(tmp_path):
    seed_path = tmp_path / "s.key"
    pub_path = tmp_path / "p.key"
    pub_path.write_bytes(b"\x01" * 32)
    with pytest.raises(keys.KeygenError):
        keys.write_keypair(RFC_SEED, seed_path, pub_path)
    assert pub_path.read_bytes() == b"\x01" * 32  # untouched
    assert not seed_path.exists()


def test_write_keypair_creates_parent_directories(tmp_path):
    sp, pp = keys.write_keypair(
        RFC_SEED, tmp_path / "deep" / "nested" / "s.key", tmp_path / "a" / "p.key"
    )
    assert sp.read_bytes() == RFC_SEED
    assert pp.read_bytes() == RFC_PUB


@pytest.mark.skipif(os.name == "nt", reason="file modes are POSIX-only")
def test_write_keypair_file_modes(tmp_path):
    sp, pp = keys.write_keypair(RFC_SEED, tmp_path / "s.key", tmp_path / "p.key")
    assert sp.stat().st_mode & 0o777 == keys.SEED_MODE == 0o600
    assert pp.stat().st_mode & 0o777 == keys.PUB_MODE == 0o644


def test_keygen_files_load_via_signing(tmp_path):
    """The generated files must be usable as-is by sign/verify."""
    sp, pp = keys.write_keypair(RFC_SEED, tmp_path / "s.key", tmp_path / "p.key")
    assert signing.load_signing_key(sp) == RFC_SEED
    assert signing.load_verification_key(pp) == RFC_PUB
    # A seed file doubles as a verification key (documented behavior).
    assert signing.load_verification_key(sp) == RFC_PUB


# --- keys.generate_keypair (fresh randomness) ----------------------------
def test_generate_keypair_produces_matching_pair(tmp_path):
    seed, pub, sp, pp = keys.generate_keypair(tmp_path / "s.key")
    assert sp == tmp_path / "s.key"
    assert pp == tmp_path / "s.key.pub"  # conventional default pub path
    assert seed == sp.read_bytes()
    assert pub == pp.read_bytes()
    assert pub == ed25519.publickey(seed)
    assert ed25519.is_on_curve(pub)


def test_generate_keypair_explicit_pub_path(tmp_path):
    seed, pub, sp, pp = keys.generate_keypair(
        tmp_path / "s.key", tmp_path / "elsewhere" / "p.key"
    )
    assert pp == tmp_path / "elsewhere" / "p.key"
    assert pub == pp.read_bytes() == ed25519.publickey(seed)


def test_generate_keypair_seeds_are_fresh_each_call(tmp_path):
    s1, _, sp1, _ = keys.generate_keypair(tmp_path / "a.key")
    s2, _, sp2, _ = keys.generate_keypair(tmp_path / "b.key")
    assert s1 != s2
    assert sp1.read_bytes() != sp2.read_bytes()


def test_generate_keypair_refuses_existing(tmp_path):
    keys.generate_keypair(tmp_path / "s.key")
    first_seed = (tmp_path / "s.key").read_bytes()
    with pytest.raises(keys.KeygenError):
        keys.generate_keypair(tmp_path / "s.key")
    # The first pair must survive a refused second generation untouched.
    assert (tmp_path / "s.key").read_bytes() == first_seed
    assert (tmp_path / "s.key.pub").read_bytes() == ed25519.publickey(first_seed)


def test_default_pub_path():
    assert keys.default_pub_path("seed.key") == Path("seed.key.pub")
    assert keys.default_pub_path("pub.pub") == Path("pub")
    assert (
        keys.default_pub_path(Path("dir/sub/seed.key"))
        == Path("dir/sub/seed.key.pub")
    )
    assert keys.default_pub_path("key") == Path("key.pub")


# --- CLI: help / usage ----------------------------------------------------
def test_help_lists_keygen():
    res = run_cli("--help")
    assert res.returncode == 0
    assert "keygen" in res.stdout


def test_keygen_help():
    res = run_cli("keygen", "--help")
    assert res.returncode == 0
    assert "--out" in res.stdout
    assert "--pub" in res.stdout
    assert "seed.key" in res.stdout  # documented default


# --- CLI: generation ------------------------------------------------------
def test_keygen_cli_default_paths(tmp_path):
    res = run_cli("keygen", cwd=tmp_path)
    assert res.returncode == constants.EXIT_OK, res.stderr
    seed_path = tmp_path / "seed.key"
    pub_path = tmp_path / "seed.key.pub"
    assert seed_path.is_file() and pub_path.is_file()
    seed = seed_path.read_bytes()
    assert len(seed) == 32
    assert pub_path.read_bytes() == ed25519.publickey(seed)
    assert "generated ed25519 keypair" in res.stdout


def test_keygen_cli_explicit_paths(tmp_path):
    res = run_cli(
        "keygen", "--out", "keys/myseed.key", "--pub", "keys/other.pub",
        cwd=tmp_path,
    )
    assert res.returncode == constants.EXIT_OK, res.stderr
    seed = (tmp_path / "keys" / "myseed.key").read_bytes()
    assert (tmp_path / "keys" / "other.pub").read_bytes() == ed25519.publickey(seed)


@pytest.mark.skipif(os.name == "nt", reason="file modes are POSIX-only")
def test_keygen_cli_file_modes(tmp_path):
    res = run_cli("keygen", "--out", "s.key", cwd=tmp_path)
    assert res.returncode == constants.EXIT_OK, res.stderr
    assert (tmp_path / "s.key").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "s.key.pub").stat().st_mode & 0o777 == 0o644


def test_keygen_cli_refuses_overwrite(tmp_path):
    res1 = run_cli("keygen", cwd=tmp_path)
    assert res1.returncode == constants.EXIT_OK, res1.stderr
    first = (tmp_path / "seed.key").read_bytes()

    res2 = run_cli("keygen", cwd=tmp_path)
    assert res2.returncode == constants.EXIT_ERROR
    assert "refusing to overwrite" in res2.stderr
    assert (tmp_path / "seed.key").read_bytes() == first  # first seed untouched

    # Also refuses when only the *public* key already exists.
    (tmp_path / "seed.key").unlink()
    res3 = run_cli("keygen", cwd=tmp_path)
    assert res3.returncode == constants.EXIT_ERROR
    assert "refusing to overwrite" in res3.stderr


def test_main_keygen_error_codes():
    # Refusal path via main() directly (no subprocess).
    with tempfile.TemporaryDirectory() as tmp:
        seed_file = Path(tmp, "seed.key")
        seed_file.write_bytes(b"\x02" * 32)
        assert cli.main(["keygen", "--out", str(seed_file)]) == (
            constants.EXIT_ERROR
        )


# --- CLI: end-to-end with sign/verify ------------------------------------
def test_keygen_sign_verify_roundtrip(tmp_path):
    """The full no-external-tooling flow: keygen → generate → sign → verify."""
    spdx = tmp_path / "sbom.spdx.json"
    res = run_cli(
        "generate",
        "--legal-info", str(LEGAL),
        "--show-info", str(SHOWINFO),
        "--out", str(spdx),
        cwd=REPO,
    )
    assert res.returncode == constants.EXIT_OK, res.stderr

    res = run_cli("keygen", "--out", str(tmp_path / "seed.key"), cwd=tmp_path)
    assert res.returncode == constants.EXIT_OK, res.stderr
    seed_path = tmp_path / "seed.key"
    pub_path = tmp_path / "seed.key.pub"

    res = run_cli(
        "sign", str(spdx), "--key", str(seed_path), "--out", str(tmp_path / "s.sig")
    )
    assert res.returncode == constants.EXIT_OK, res.stderr

    # Verify with the generated *public* key.
    res = run_cli(
        "verify", str(spdx), "--key", str(pub_path), "--sig", str(tmp_path / "s.sig")
    )
    assert res.returncode == constants.EXIT_OK, res.stderr
    assert "OK" in res.stdout

    # The same seed file can also serve verify (documented behavior).
    res = run_cli(
        "verify", str(spdx), "--key", str(seed_path), "--sig", str(tmp_path / "s.sig")
    )
    assert res.returncode == constants.EXIT_OK, res.stderr
