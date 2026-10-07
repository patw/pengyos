"""Lint the ESP: every expected EFI binary must be signed by a key in ``db``.

This module implements the ``lint`` subtask.

``lint`` is the *assertion* that ties the whole chain together. ``keys`` produces
the trust root, ``sign-chain`` signs the binaries and records a signature
binding per binary, and ``lint`` verifies that what is actually on the ESP matches
what was signed by a key the firmware trusts.

The check runs one rule per expected binary (the manifest, built by
:func:`pengyos_secureboot_enroll.sign_chain.build_manifest` /
:func:`pengyos_secureboot_enroll.bootchain.expected_manifest`):

==================  ===================  ===============================================
``LintViolation``    When it fires
==================  ===================  ===============================================
``unsigned``         no signature binding   A binary on the ESP with no recorded signature
                                                -- it would be rejected by the firmware.
``untrusted_signer`` a binding whose ``key``  signed by a key that is **not** in ``db`` --
                    ``id`` is not in ``db``  the firmware does not trust that signer.
``signature_invalid`` the signature does not   the recorded signature does not verify over the
                    verify against ``db``    current ESP bytes (tampered binary) or the db
                    certificate           cert (a forged / wrong-key signature).
==================  ===================  ===============================================

A binary that passes all three rules is *verified*. The whole ESP lints clean
when there are no violations.

The signature a binary is checked against comes from the recorded bindings
(``signatures.json``) produced by :mod:`pengyos_secureboot_enroll.sign_chain` --
the on-disk artefact that stands in for the Authenticode signature the firmware
would read from the binary itself. No root, no real NVRAM, no real openssl
(signing/verifying runs through the injectable signer).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from .truststate import EspManifest
from .keys import TrustRoot
from .sign_chain import SignatureBinding, verify_binding
from .signer import Signer, StdlibSigner

__all__ = [
    "LintViolation",
    "LintResult",
    "LintError",
    "lint_esp",
]


class LintError(RuntimeError):
    """An internal lint failure (fail closed)."""


@dataclass(frozen=True)
class LintViolation:
    """One lint failure: an ESP binary and why it was rejected.

    ``path`` is the ESP-relative path of the offending binary. ``reason`` is one
    of :data:`unsigned`, :data:`untrusted_signer`, :data:`signature_invalid` --
    the three rules :func:`lint_esp` enforces.
    """

    path: str
    reason: str


@dataclass
class LintResult:
    """The outcome of linting an ESP: the violations found (if any).

    :attr:`violations` lists every failing binary (empty when the ESP lints
    clean). :attr:`binaries_checked` is the number of binaries that passed all
    lint rules. :attr:`verified` is ``True`` only when the ESP lints clean *and*
    at least one binary was checked -- an empty ESP is not a passing lint.
    """

    violations: List[LintViolation] = field(default_factory=list)
    binaries_checked: int = 0

    @property
    def verified(self) -> bool:
        """``True`` when the ESP lints clean (no violations, at least one checked)."""
        return not self.violations and self.binaries_checked > 0


def _normalise(path: str) -> str:
    """Return ``path`` in canonical POSIX form (leading ``/``)."""
    return "/" + "/".join(part for part in path.split("/") if part)


def lint_esp(
    esp_dir: "os.PathLike | str",
    manifest: EspManifest,
    trust_root: TrustRoot,
    bindings: Optional[Sequence[SignatureBinding]] = None,
    signer: Optional[Signer] = None,
) -> LintResult:
    """Lint the ESP against ``manifest`` and the ``db`` trust root.

    For each expected binary in ``manifest`` this applies the three lint rules
    in order:

    1. **unsigned** -- the binary has no recorded signature binding (it would be
       rejected by the firmware).
    2. **untrusted_signer** -- the binding's ``key_id`` is not one of the trust
       root's ``db`` key ids (the firmware does not trust that signer).
    3. **signature_invalid** -- the recorded signature does not verify over the
       current ESP bytes against the ``db`` certificate (a tampered / forged
       binary).

    A binary that passes all three rules counts as verified. Returns a
    :class:`LintResult`; :attr:`LintResult.verified` is ``True`` only when there
    are no violations and at least one binary was checked. Raises
    :class:`LintError` only on an internal failure (e.g. an unreadable binary).
    """
    signer = signer or StdlibSigner()
    esp_dir = Path(esp_dir)

    # The set of signing keys the firmware trusts: the trust root's db keys.
    # (A single db key today; modelled as a set so a multi-key db could extend
    # it without changing the rules.)
    db_key_ids = {trust_root.db.key_id}

    # Index the recorded bindings by normalised path for O(1) lookup.
    binding_by_path = {}
    if bindings:
        for binding in bindings:
            binding_by_path[_normalise(binding.path)] = binding

    violations: List[LintViolation] = []
    binaries_checked = 0

    for binary in manifest:
        path = binary.path
        on_disk = esp_dir / path.lstrip("/")

        # Rule 0 (implicit): the binary must exist on the ESP. A binary that is
        # expected but absent cannot be verified, so it is a violation.
        if not on_disk.is_file():
            violations.append(LintViolation(path=path, reason="missing"))
            continue

        binding = binding_by_path.get(_normalise(path))
        if binding is None:
            # Rule 1: unsigned.
            violations.append(LintViolation(path=path, reason="unsigned"))
            continue

        # Rule 2: signed by a key NOT in db.
        if binding.key_id not in db_key_ids:
            violations.append(
                LintViolation(path=path, reason="untrusted_signer")
            )
            continue

        # Rule 3: the signature must verify over the current ESP bytes against
        # the db cert. A tampered binary (or a forged signature) fails here.
        try:
            data = on_disk.read_bytes()
        except OSError as exc:
            raise LintError(f"cannot read {path} from ESP: {exc}") from exc
        if not verify_binding(binding, data, Path(trust_root.db.cert_path), signer):
            violations.append(
                LintViolation(path=path, reason="signature_invalid")
            )
            continue

        binaries_checked += 1

    return LintResult(violations=violations, binaries_checked=binaries_checked)
