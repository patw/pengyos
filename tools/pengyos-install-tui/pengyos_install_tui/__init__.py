"""PengyOS install-tui — the console installer for Core and Server.

* :mod:`pengyos_install_tui.session` — the pages, fields and rules. Pure, no I/O.
* :mod:`pengyos_install_tui.screen` — the full-screen (curses) front-end.
* :mod:`pengyos_install_tui.plain` — the line-by-line fallback.
* :mod:`pengyos_install_tui.keys` — fetch SSH keys from GitHub.
* :mod:`pengyos_install_tui.install` — load the medium, pick a front-end, install.

The CLI lives in :mod:`pengyos_install_tui.cli`.
"""

from __future__ import annotations

__version__ = "0.2.0"

__all__ = ["__version__"]
