"""Emit MOK / custom-key enrolment material + operator instructions.

This module emits the MOK/custom-key enrolment material + the operator
instructions (no NVRAM writes).

It does **not** write to firmware NVRAM -- that is an operator step on a real
machine (a non-goal of the spec). Instead it produces the *enrolment material*
that an operator then enrols, in one of two modes:

* **``mok``** -- the Debian/Ubuntu/Redhat *Machine Owner Key* workflow. The
  artefact is the **DER-encoded ``db`` certificate**, which is exactly what
  ``mokutil --import`` consumes; the instructions tell the operator to run
  ``mokutil --import db.cert.der`` and confirm the key in the rebooting MOK
  manager.
* **``custom``** -- a self-contained enrolment **bundle** for a custom signing
  pipeline (e.g. ``sbsign`` / ``sbvarsign``, or a manual firmware enrolment). The
  artefact is a JSON document that carries the ``db`` key id, the DER ``db``
  certificate (base64), the **signed key metadata** and the metadata signature --
  so the bundle is *self-authenticating*: the PK signature binds the certificate
  to the trust root that produced it.

In both modes the material is public (the certificate and its id are, by
definition, not secret), so it is written world-readable (``0644``); the private
keys are never read out of the key directory.

Everything is derived from the on-disk key set with the standard library
(:func:`pengyos_secureboot_enroll.keys.pem_to_der` for the DER,
:func:`pengyos_secureboot_enroll.keys.build_metadata` +
:func:`pengyos_secureboot_enroll.keys.sign_metadata` for the signed metadata), so
``enroll`` needs no external tool, no root and no real NVRAM.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .keys import (
    MissingKeyError,
    PrivateKeyError,
    build_metadata,
    check_key_set,
    load_key_set,
    pem_to_der,
    sign_metadata,
)

__all__ = [
    "EnrollError",
    "MODE_MOK",
    "MODE_CUSTOM",
    "CERT_DER",
    "ENROLMENT_JSON",
    "INSTRUCTIONS",
    "MOK_IMPORT_CMD",
    "EnrolmentMaterial",
    "db_cert_der",
    "build_enrolment",
    "write_enrolment",
    "instructions_for",
]

#: The MOK (Machine Owner Key) enrolment target.
MODE_MOK = "mok"

#: The custom-key enrolment bundle target.
MODE_CUSTOM = "custom"

#: Every enrolment mode (kept in one place so the CLI choices stay in sync).
ENROLMENT_MODES = (MODE_MOK, MODE_CUSTOM)

#: Filename of the DER-encoded db certificate in the enrolment bundle.
CERT_DER = "db.cert.der"

#: Filename of the JSON enrolment bundle (custom mode).
ENROLMENT_JSON = "enrolment.json"

#: Filename of the operator instructions (both modes).
INSTRUCTIONS = "instructions.txt"

#: The ``mokutil`` command that imports the DER cert into the MOK database.
MOK_IMPORT_CMD = "mokutil --import"


class EnrollError(RuntimeError):
    """An enrolment operation failed (fail closed)."""


@dataclass
class EnrolmentMaterial:
    """The produced enrolment material for one key set + mode.

    ``mode`` is ``"mok"`` or ``"custom"``; ``db_key_id`` is the SHA-256
    fingerprint of the ``db`` certificate (the value an operator looks up in the
    firmware's ``db`` list); ``db_cert_der`` is the DER encoding of that
    certificate -- the enrolment artefact; ``metadata`` / ``signature`` bind the
    material to the trust root (the PK signature over the key metadata proves the
    certificate belongs to the key set that produced it). ``files`` records the
    on-disk paths :func:`write_enrolment` produced.
    """

    mode: str
    db_key_id: str
    db_cert_der: bytes
    metadata_digest: str
    metadata_signature: str
    files: List[str] = field(default_factory=list)

    @property
    def is_mok(self) -> bool:
        """``True`` when this material is MOK-mode enrolment."""
        return self.mode == MODE_MOK

    def to_json(self) -> dict:
        """Return a JSON-serialisable representation of the material.

        The ``db_cert_der`` is base64-encoded so the bundle is a single,
        portable text document.
        """
        return {
            "mode": self.mode,
            "db_key_id": self.db_key_id,
            "db_cert_der_b64": base64.b64encode(self.db_cert_der).decode("ascii"),
            "metadata_digest": self.metadata_digest,
            "metadata_signature_b64": base64.b64encode(
                bytes.fromhex(self.metadata_signature)
            ).decode("ascii"),
            "files": list(self.files),
        }


def db_cert_der(keys_dir: "os.PathLike | str") -> bytes:
    """Return the DER encoding of the ``db`` certificate in ``keys_dir``.

    The DER is recovered from ``<keys_dir>/DB.cert`` (PEM) with the standard
    library (:func:`pengyos_secureboot_enroll.keys.pem_to_der`) -- the same DER
    ``openssl x509 -outform DER`` would emit, and the same bytes the ``db`` key
    id is computed from. This is the enrolment artefact MOK's ``mokutil --import``
    consumes.
    """
    keys_dir = Path(keys_dir)
    cert_path = keys_dir / "DB.cert"
    if not cert_path.is_file():
        raise EnrollError(
            f"missing db certificate: {cert_path} (run `keys --out <dir>` first)"
        )
    return pem_to_der(cert_path.read_bytes())


def build_enrolment(
    keys_dir: "os.PathLike | str", mode: str = MODE_MOK
) -> EnrolmentMaterial:
    """Build the enrolment material for a key set in ``mode``.

    Loads the trust root (raising :class:`MissingKeyError` if the key set is
    absent, :class:`PrivateKeyError` if a private key has insecure permissions),
    extracts the DER ``db`` certificate, and produces the PK-signed key metadata
    that binds the material to the trust root. ``mode`` must be one of
    :data:`ENROLMENT_MODES`.
    """
    if mode not in ENROLMENT_MODES:
        raise EnrollError(f"unknown enrolment mode: {mode!r}")

    keys_dir = Path(keys_dir)
    # Fail closed up front: refuse a key set with a missing cert or any private
    # key with group/world bits (treated as compromised). Mirrors sign-chain.
    check_key_set(keys_dir)
    trust_root = load_key_set(keys_dir)
    der = db_cert_der(keys_dir)
    metadata = build_metadata(trust_root)
    signature = sign_metadata(metadata, keys_dir)

    return EnrolmentMaterial(
        mode=mode,
        db_key_id=trust_root.db.key_id,
        db_cert_der=der,
        metadata_digest=metadata.digest,
        metadata_signature=signature.hex(),
    )


def instructions_for(material: EnrolmentMaterial) -> str:
    """Return the operator instructions for ``material`` (a human-readable doc).

    The instructions never contain key material -- only the public cert id and
    the enrolment command. They differ per mode:

    * ``mok`` -- import the DER cert with ``mokutil --import`` and confirm it in
      the rebooting MOK manager.
    * ``custom`` -- enrol the ``db`` key (id below) into the firmware ``db``
      variable with the operator's signing tool, using the DER in the bundle.
    """
    header = (
        "PengyOS Secure Boot enrolment instructions\n"
        "==========================================\n\n"
        f"Mode: {material.mode}\n"
        f"db key id (fingerprint): {material.db_key_id}\n\n"
        "This material enrols the PengyOS Secure Boot 'db' signing key into the\n"
        "firmware's trusted database. It writes NO firmware NVRAM itself -- the\n"
        "steps below run on the target machine.\n"
    )

    if material.is_mok:
        return (
            header
            + "MOK (Machine Owner Key) enrolment\n"
            + "---------------------------------\n"
            + "1. Copy the enrolment bundle to the target machine.\n"
            + "2. Import the db certificate into the MOK database:\n"
            + f"       {MOK_IMPORT_CMD} {CERT_DER}\n"
            + "   (you will be prompted for the root password; the key is queued,\n"
            + "    not written yet).\n"
            + "3. Reboot. The UEFI MOK manager will prompt you to ENROL the key --\n"
            + "   confirm it. The db key id above is what you are enrolling.\n"
            + "4. Verify it is enrolled:\n"
            + "       mokutil --list-keys\n"
        )

    return (
        header
        + "Custom-key enrolment\n"
        + "--------------------\n"
        + "1. Copy the enrolment bundle (db.cert.der + enrolment.json) to the\n"
        + "   target machine.\n"
        + "2. Enrol the db certificate into the firmware 'db' variable with your\n"
        + "   signing tool, e.g.:\n"
        + "       sbsign --key db.key --cert db.cert --input boot.efi --output signed.efi\n"
        + "       sbvarsign --key db.key --cert db.cert --name db --input signed.efi\n"
        + "   (or enrol the key id "
        f"{material.db_key_id} through the firmware enrolment UI).\n"
        + "3. The enrolment.json bundle is self-authenticating: its metadata\n"
        + "   signature (signed by the PK key) proves db.cert.der belongs to the\n"
        + "   key set that produced it -- check it before enrolling.\n"
    )


def write_enrolment(
    material: EnrolmentMaterial, out_dir: "os.PathLike | str"
) -> Path:
    """Write ``material`` to ``out_dir`` and return the directory.

    Produces the mode-appropriate artefacts, all world-readable (``0644``)
    because they are public enrolment material:

    * both modes -- ``db.cert.der`` (the DER db certificate) and
      ``instructions.txt`` (operator instructions);
    * ``custom`` -- an additional ``enrolment.json`` self-authenticating bundle.

    The private keys are never written out.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cert_der_path = out_dir / CERT_DER
    cert_der_path.write_bytes(material.db_cert_der)
    cert_der_path.chmod(0o644)

    files = [str(cert_der_path)]

    if material.is_mok:
        pass  # MOK needs only the DER cert.
    else:
        bundle_path = out_dir / ENROLMENT_JSON
        bundle_path.write_text(
            json.dumps(material.to_json(), indent=2) + "\n", encoding="utf-8"
        )
        bundle_path.chmod(0o644)
        files.append(str(bundle_path))

    instructions_path = out_dir / INSTRUCTIONS
    instructions_path.write_text(instructions_for(material), encoding="utf-8")
    instructions_path.chmod(0o644)
    files.append(str(instructions_path))

    material.files = files
    return out_dir
