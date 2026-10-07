"""PengyOS desktop-curation — the curated default-app set as declarative data.

The curated Desktop default-application set is stored as *data* (category ->
app -> lane -> ref -> rationale -> human_signed) and emitted as a Flatpak ref
list, a self-hosted-mirror manifest (for :mod:`pengyos_flatpak_mirror`), and a
coverage report against the US-DSK-2 "everyday apps covered" list.

This is the package root. The CLI lives in the sibling
``pengyos_desktop_curation.cli`` module; the committed curated set lives in
``pengyos_desktop_curation.data``.
"""

__version__ = "0.7.0"

__all__ = ["__version__"]
