"""pengyos-update-triage — advisories + changelogs → update proposals.

The PengyOS "flagship" (SPEC §3): let the agent watch upstream and propose what
matters, while a human ratifies the boundary.

Given a pins manifest (the pinned ``(name, version)`` pairs that make a build
reproducible) plus a directory of OSV/NVD advisory fixtures and a directory of
upstream changelog fixtures, this tool:

* ingests the advisories and matches them against the pinned components,
* scans the changelogs for security-relevant signals (keyword / version-bump
  detection),
* classifies each finding as ``security`` / ``relevant`` / ``ignore`` with a
  human-readable rationale and the evidence (source ids) that drove it,
* ranks the surviving findings (severity, exploitability signal, reachability)
  with a documented, no-ML heuristic, and
* emits ``proposals.json`` plus a human summary; ``explain <id>`` prints the
  full rationale chain for one proposal.

See ``spec.md`` and ``README.md`` for the full interface and behaviour
contract.
"""

__version__ = "0.11.0"

__all__ = ["__version__"]
