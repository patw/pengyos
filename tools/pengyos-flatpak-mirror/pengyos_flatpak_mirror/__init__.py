"""PengyOS flatpak-mirror — self-hosted Flatpak (OSTree) repo manager.

A small, dependency-free CLI that wraps the ``flatpak build-update-repo`` and
``flatpak`` remote flows behind a declarative ``mirror.toml`` manifest: mirror
pinned apps, list refs + commits, verify GPG signatures, and prune old commits.

This is the package root. The CLI, manifest loader, runner, and engine (the
sync/list orchestration) live in sibling modules; see
``pengyos_flatpak_mirror.cli`` for the entry point.
"""

__version__ = "0.8.0"

__all__ = ["__version__"]
