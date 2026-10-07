"""pengyos-repro — reproducible-build differ for PengyOS.

Compare two builds' outputs and classify differences; report a reproducibility
score and the offending inputs.

Reproducibility is how we verify rather than trust (SPEC §7 bootstrap chain): two builds of the
same pinned inputs must produce byte-identical outputs, and any difference points
at a nondeterministic input.

Given two image/build directories this tool walks both trees, hashes each artifact
(streaming, large-file safe), classifies every path (identical / differs /
only-in-A / only-in-B), tolerates known-nondeterministic inputs via configurable
ignore patterns, and emits a human report plus a machine-readable score in
``[0.0, 1.0]``.

See ``spec.md`` for the interface and behaviour contract and ``README.md`` for
usage.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
