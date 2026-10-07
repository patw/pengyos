"""PengyOS Secure Boot enroll.

Sign and enrol the PengyOS Secure Boot chain: generate/rotate the PK/KEK/db
trust root, sign the Unified Kernel Image (UKI) + systemd-boot, emit the MOK /
custom-key enrolment material, and lint that every EFI binary on the ESP is
signed by a key present in ``db``.

See ``spec.md`` for the full contract.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
