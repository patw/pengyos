"""Process-wide constants for pengyos-sbom.

Centralizing these keeps every subcommand and its tests aligned on a single
source of truth for exit codes, file extensions and the pinned spec versions.
"""

# --- Process exit codes ---------------------------------------------------
EXIT_OK = 0
EXIT_ERROR = 1  # generic failure (bad input, I/O error, validation failed)
EXIT_USAGE = 2  # command-line usage error (matches argparse)
EXIT_NOT_IMPLEMENTED = 3  # a scaffolded subcommand that is not built yet

# --- File extensions ------------------------------------------------------
SIG_EXT = ".sig"  # detached ed25519 signature sidecar

# --- Pinned spec versions -------------------------------------------------
SPDX_VERSION = "2.3"
SPDX_NAMESPACE_PREFIX = "https://pengyos.catbee.ca/sbom"
CYCLONEDX_BOM_FORMAT = "CycloneDX"
CYCLONEDX_SPEC_VERSION = "1.5"
