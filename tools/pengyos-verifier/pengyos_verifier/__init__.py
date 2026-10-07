"""PengyOS verifier — external witness for builds.

Independently checks a build's attest chain, SBOM presence, signatures,
and repro score, and reports findings. Verification, not authority: the
witness exists so the seed can be *untrusted*.

See ``spec.md`` for the full interface.
"""

__version__ = "0.7.0"

__all__ = ["__version__"]
