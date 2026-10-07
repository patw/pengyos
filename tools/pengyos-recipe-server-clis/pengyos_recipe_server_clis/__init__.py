"""PengyOS recipe-server-clis — Buildroot recipes for the Server comfort CLIs.

Emits and lints our ``BR2_EXTERNAL`` Buildroot recipes for the Server comfort
CLIs that Buildroot 2026.08 does not ship (``zoxide``, ``sd``, ``node_exporter``,
``btop``, ``helix``, ``neovim``). Recipes are our data: the tool catalogue is
committed, and the ``Config.in`` / ``<pkg>.mk`` / ``<pkg>.hash`` files are emitted
deterministically from it, mirroring the fzf/starship recipe shape already in
``pengyos-server-toolset``.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
