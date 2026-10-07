"""PengyOS btrfs-layout tooling.

Plan a **btrfs subvolume layout** (root, home, var, snapshots, …) for PengyOS and
generate the ``btrfs subvolume`` / snapshot commands needed to create it, take a
named snapshot, and roll back to a prior one. Snapshots are the safety net for
free agent mutation (project ``SPEC.md``, §1.5).

The tool *plans and generates commands*; it never touches a live filesystem in
the test suite. All ``btrfs`` invocations go through a single mockable runner
seam, so the argv is golden-testable without root or a real btrfs volume.

This package is pure standard library: no third-party runtime dependencies.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
