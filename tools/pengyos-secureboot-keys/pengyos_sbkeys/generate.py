"""Generate real Secure Boot key material with ``openssl``.

This module produces *real* PK/KEK/db key material using the ``openssl``
command-line tool instead of the random-bytes placeholder that ``init`` used to
write (the previous stand-in, now replaced):

* private keys -> EC (ECDSA) P-256 keys in PKCS#8 PEM form, produced with
  ``openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256``;
* certificates -> self-signed X.509 certificates in PEM form, produced with
  ``openssl req -x509 -new -key <key> -days 3650 -subj "/CN=..."``.

The ``init`` command delegates here (``generate_all``). The ``openssl`` calls
are the *only* external dependency in the package; they are isolated in
:func:`_run` so they can be mocked in unit tests (no real ``openssl`` required)
and exercised for real only under the opt-in integration guard
(``PENGYOS_IT=1``).

Only the key *file* is handed to ``openssl`` when building a certificate -- the
contents are never read by Python, so key material never appears in this module's
output or in the ``init`` summary printed by the CLI.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List, Sequence

from .keys import KEY_FILES, is_private

__all__ = [
    "GenerateError",
    "PRIVATE_KEY_CURVE",
    "CERTIFICATE_DAYS",
    "openssl_private_key_cmd",
    "openssl_certificate_cmd",
    "generate_private_key",
    "generate_certificate",
    "generate_all",
]


class GenerateError(RuntimeError):
    """Raised when ``openssl`` fails to produce key material.

    The CLI maps this to exit 1 with a stderr message.
    """


#: Elliptic curve used for every Secure Boot key (ECDSA, NIST P-256).
PRIVATE_KEY_CURVE = "P-256"

#: Lifetime of the generated self-signed certificates, in days (10 years).
CERTIFICATE_DAYS = 3650


# --- openssl command construction -------------------------------------------


def openssl_private_key_cmd(role: str) -> List[str]:
    """Return the ``openssl genpkey`` argv that produces ``role``'s key.

    The EC P-256 PKCS#8 PEM key is written to ``openssl``'s stdout; the caller
    owns writing it to disk (see :func:`generate_private_key`). The argv is
    returned as a list (no shell) so it is safe to pass to
    :func:`subprocess.run` directly and easy to assert on in tests.
    """
    return [
        "openssl",
        "genpkey",
        "-algorithm",
        "EC",
        "-pkeyopt",
        f"ec_paramgen_curve:{PRIVATE_KEY_CURVE}",
    ]


def openssl_certificate_cmd(role: str, key_path: os.PathLike | str) -> List[str]:
    """Return the ``openssl req -x509`` argv that produces ``role``'s cert.

    Builds a self-signed X.509 certificate from the already-generated private
    key at ``key_path``. The PEM certificate is written to ``openssl``'s
    stdout; the caller owns writing it to disk (see
    :func:`generate_certificate`). The subject names the role so the
    certificates are individually identifiable.
    """
    return [
        "openssl",
        "req",
        "-x509",
        "-new",
        "-key",
        str(key_path),
        "-days",
        str(CERTIFICATE_DAYS),
        "-subj",
        f"/CN=PengyOS Secure Boot {role}",
    ]


# --- low-level runner -------------------------------------------------------


def _run(cmd: Sequence[str]) -> bytes:
    """Run an ``openssl`` command, capturing its stdout as bytes.

    The key/certificate blob is written to ``openssl``'s stdout and returned, so
    the caller owns writing it to disk. Raises :class:`GenerateError` (with the
    tool's stderr) if ``openssl`` exits non-zero.
    """
    completed = subprocess.run(list(cmd), capture_output=True)
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", "replace").strip()
        raise GenerateError(
            f"{cmd[0]} failed (exit {completed.returncode}): {message}"
        )
    return completed.stdout


# --- per-file generation ----------------------------------------------------


def generate_private_key(role: str, key_path: os.PathLike | str) -> bytes:
    """Generate ``role``'s private key at ``key_path`` and return its bytes.

    Runs ``openssl genpkey`` (EC P-256), capturing the PEM key from stdout and
    writing it to ``key_path``. The key bytes are returned so callers can assert
    on / protect them.
    """
    data = _run(openssl_private_key_cmd(role))
    Path(key_path).write_bytes(data)
    return data


def generate_certificate(
    role: str, key_path: os.PathLike | str, cert_path: os.PathLike | str
) -> bytes:
    """Generate ``role``'s self-signed cert at ``cert_path`` from ``key_path``.

    Runs ``openssl req -x509 -new -key <key>`` (the key at ``key_path`` was
    produced by :func:`generate_private_key`), capturing the PEM certificate
    from stdout and writing it to ``cert_path``. The private key is only ever
    handed to ``openssl`` by path -- its contents are never read by Python.
    """
    data = _run(openssl_certificate_cmd(role, key_path))
    Path(cert_path).write_bytes(data)
    return data


# --- orchestration ----------------------------------------------------------


def generate_all(keys_dir: os.PathLike | str) -> None:
    """Generate the full PK/KEK/db key set under ``keys_dir``.

    Writes every private key first (so the self-signed certificates can be built
    from them) and then every certificate, in Secure Boot role order (PK, KEK,
    db). Raises :class:`GenerateError` if any ``openssl`` call fails.
    """
    keys_dir = Path(keys_dir)
    keys_dir.mkdir(parents=True, exist_ok=True)

    # Private keys first: the certificates are signed from these keys.
    for key_file in KEY_FILES:
        if not is_private(key_file):
            continue
        generate_private_key(key_file.role, keys_dir / key_file.name)

    # Then the self-signed certificates, each from its freshly written key.
    for key_file in KEY_FILES:
        if is_private(key_file):
            continue
        role = key_file.role
        key_path = keys_dir / f"{role}.key"
        generate_certificate(role, key_path, keys_dir / key_file.name)
