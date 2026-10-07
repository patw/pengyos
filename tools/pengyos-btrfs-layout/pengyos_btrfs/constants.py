"""Process-wide constants for pengyos-btrfs-layout.

Centralizing these keeps every subcommand and its tests aligned on a single
source of truth for exit codes, the ``btrfs`` binary name, and the naming
conventions that the layout planner (a later task) will use.
"""

from __future__ import annotations

# --- Process exit codes ---------------------------------------------------
EXIT_OK = 0
EXIT_ERROR = 1  # generic failure (bad input, validation failed, I/O error)
EXIT_USAGE = 2  # command-line usage error (matches argparse)
EXIT_NOT_IMPLEMENTED = 3  # a scaffolded subcommand that is not built yet

# --- btrfs ----------------------------------------------------------------
# The tool shells out to the ``btrfs`` command from btrfs-progs. In tests this
# is replaced by a mockable runner seam (see a later task), so the argv it
# would run is golden-testable without root or a real btrfs volume.
BTRFS_BIN = "btrfs"
# The generated ``btrfs`` commands address subvolumes by absolute path relative
# to a staging mount point: the filesystem is ``mkfs``'d and mounted there, the
# subvolumes are created under it, and the live mount points (``/``, ``/home``,
# ...) are wired up afterwards. The default is overridable per call.
DEFAULT_MOUNT_POINT = "/mnt"

# --- Snapshot naming (conventions) ----------------------------------------
# A snapshot is a read-only subvolume under a well-known snapshot directory so
# it never shadows a live mount point. The exact layout tree is defined in the
# "Define the layout" task; these names pin the convention the planner will
# validate against so tests and the planner cannot drift.
SNAPSHOT_DIR = "@snapshots"
# Rollback is destructive (it can replace a live subvolume), so the command
# line refuses to run it without an explicit confirmation flag.
ROLLBACK_CONFIRM_FLAG = "--yes"
