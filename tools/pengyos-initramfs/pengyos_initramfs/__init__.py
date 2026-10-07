"""PengyOS initramfs — deterministic minimal initramfs builder.

Builds a small, reproducible cpio initramfs from a spec + a root dir. The
archive is deterministic: members are sorted and metadata (uid/gid/mtime) is
normalized, so two builds of the same inputs are byte-identical.
"""

__all__ = ["__version__"]

# Single source of truth for the version (also declared in pyproject.toml).
__version__ = "0.1.0"
