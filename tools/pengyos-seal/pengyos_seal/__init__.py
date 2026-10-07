"""pengyos-seal — wire provenance into a pengyos-build output.

The closing glue for US-CORE-6: given a pengyos-build output directory and a
signing key, :mod:`.cli` runs the provenance closure in order — sign the expected
artifacts (``images/rootfs.ext4`` + ``images/bzImage``) with ``pengyos-sign``,
normalise the SBOM to the verifier's conventional root name, surface the repro
report, build the witness layout with ``pengyos-witness-bundle``, and run
``pengyos-verifier`` — surfacing its verdict and exit code.

Every external tool call (``pengyos-sign``, ``pengyos-witness-bundle``,
``pengyos-verifier``, ``pengyos-repro-report``) goes through the injected runner
seam in :mod:`.runner`, so the suite stays offline, root-free, and free of real
artifacts: tests inject a recording in-memory runner instead of touching the
host or the network.

This package never builds and never writes outside the build directory it is
given. It runs the seal stages (sign, name, repro, witness, verify) through an
injected runner seam.

Runtime depends only on the Python standard library.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
