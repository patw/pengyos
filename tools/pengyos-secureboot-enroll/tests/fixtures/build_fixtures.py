#!/usr/bin/env python3
"""Regenerate the committed Secure Boot fixtures under ``tests/fixtures/``.

This is the *generator* for the checked-in fixture tree used by
``tests/test_fixtures.py``. It drives the real library code (no fabrication):
it writes a deterministic PEM key set, loads the trust root, signs the pinned
boot-chain ESP and records the signature bindings -- exactly what
``keys`` + ``sign-chain`` do.

Because the stdlib Ed25519 signer is deterministic (RFC 8032) and the key
material is fixed, the output is byte-for-byte reproducible, so the committed
fixtures stay "grounded" in the code. Run it to rebuild after a change::

    python tests/fixtures/build_fixtures.py

It writes into ``<repo>/tests/fixtures`` by default; pass a different root to
generate elsewhere (e.g. a temp dir for a drift check).
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

# build_fixtures.py lives at <repo>/tests/fixtures/, so the repo root is three
# parents up.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pengyos_secureboot_enroll import keys as keys_module  # noqa: E402
from pengyos_secureboot_enroll import sign_chain as sc  # noqa: E402
from pengyos_secureboot_enroll.sign_chain import SIGNATURES_JSON  # noqa: E402

#: The three pinned boot-chain binaries the ESP must ship (see bootchain.py).
ESP_BINARIES = {
    "/EFI/systemd/systemd-bootx64.efi": b"systemd-boot-bytes",
    "/EFI/BOOT/BOOTX64.EFI": b"boot-fallback-bytes",
    "/EFI/Linux/pengyos-server+1.0.0-a.efi": b"uki-bytes",
}


def _make_pem(body: bytes) -> bytes:
    """Wrap ``body`` in a single-cert PEM with correct base64 padding."""
    b64 = base64.b64encode(body).decode("ascii")
    wrapped = "\n".join(b64[i : i + 64] for i in range(0, len(b64), 64))
    return f"-----BEGIN CERTIFICATE-----\n{wrapped}\n-----END CERTIFICATE-----\n".encode("utf-8")


def _write_key_set(keys_dir: Path) -> None:
    """Write a deterministic PK/KEK/db PEM key set (0600 keys, 0644 certs)."""
    keys_dir.mkdir(parents=True, exist_ok=True)
    for role in ("PK", "KEK", "DB"):
        cert = keys_dir / f"{role}.cert"
        cert.write_bytes(_make_pem(f"CERT-{role}-DER-BYTES-0123456789".encode()))
        cert.chmod(0o644)
        key = keys_dir / f"{role}.key"
        key.write_bytes(f"PRIVATE-KEY-{role}-BYTES-0123456789abcdef".encode())
        key.chmod(0o600)
    keys_dir.chmod(0o700)


def _write_esp(esp_dir: Path) -> None:
    """Write the pinned boot-chain binaries into ``esp_dir``."""
    for rel, data in ESP_BINARIES.items():
        target = esp_dir / rel.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def build(root: Path) -> None:
    """Build the committed fixture tree under ``root`` (``keys`` + ``signed_esp``)."""
    root = Path(root)
    keys_dir = root / "keys"
    esp_dir = root / "signed_esp"

    _write_key_set(keys_dir)
    _write_esp(esp_dir)

    trust_root = keys_module.load_key_set(keys_dir)
    signed = sc.sign_binaries(esp_dir, sc.build_manifest(), trust_root)
    sc.save_bindings(signed.bindings, keys_dir / SIGNATURES_JSON)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "tests" / "fixtures"
    build(out)
    print(f"built fixtures under {out}")
