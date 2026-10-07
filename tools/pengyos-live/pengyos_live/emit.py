"""Emit the live overlay to disk.

This module *writes* the live overlay produced by :mod:`pengyos_live.model`.
``emit`` writes :class:`LiveOverlay.files`; ``lint`` reads them back. Both
reason over the *same* structured representation, so what ``emit`` writes is
exactly what ``lint`` checks.

The overlay files carry absolute paths inside the target rootfs (e.g.
``/etc/pengyos/live``, ``/etc/systemd/system/getty@.service.d/autologin.conf``).
Emitting flattens them relative to the output directory by stripping the
leading ``/`` and joining onto the output root, so a rendered overlay is a
directory tree mirroring the target's ``/etc`` / ``/usr`` layout:

    <out>/etc/pengyos/live
    <out>/etc/systemd/system/getty@.service.d/autologin.conf
    <out>/usr/share/autostart/pengyos-installer.desktop   (cosmic)
    <out>/etc/systemd/system/pengyos-installer.service    (console)

The write is deterministic: files are written in the model's fixed
(autologin, marker, installer) order, so two runs for the same edition +
session produce byte-for-byte identical trees.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .model import LiveOverlay, OverlayFile, build_overlay, build_overlay_from_strings, overlay_to_dict

__all__ = [
    "emit_path",
    "write_overlay",
    "emit_from_strings",
    "summary_from_written",
]


def emit_path(target: str, out_dir: os.PathLike[str] | str) -> Path:
    """The on-disk path for an overlay file emitted under ``out_dir``.

    Overlay paths are absolute inside the target rootfs (e.g.
    ``/etc/pengyos/live``); emitting flattens them relative to ``out_dir`` by
    stripping the leading ``/``. ``emit_path("/etc/a/b", "/out")`` is
    ``/out/etc/a/b``.
    """
    rel = target.lstrip("/")
    return Path(out_dir) / rel


def write_overlay(
    overlay: LiveOverlay, out_dir: os.PathLike[str] | str
) -> list[Path]:
    """Write every file in ``overlay`` under ``out_dir``.

    Returns the list of paths written, in the model's deterministic order
    (autologin, live marker, installer). Parent directories are created as
    needed; an existing file at a destination is overwritten.
    """
    out = Path(out_dir)
    written: list[Path] = []
    for f in overlay.files:
        dest = emit_path(f.path, out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f.content, encoding="utf-8")
        written.append(dest)
    return written


def emit_from_strings(
    edition: str, session: str, out_dir: os.PathLike[str] | str
) -> LiveOverlay:
    """Build the overlay from canonical edition/session strings and write it.

    Raises :class:`ValueError` for an unknown edition or session (mirroring
    :func:`pengyos_live.model.build_overlay_from_strings`). Returns the written
    :class:`LiveOverlay`.
    """
    overlay = build_overlay_from_strings(edition, session)
    write_overlay(overlay, out_dir)
    return overlay


def summary_from_written(
    overlay: LiveOverlay, written: list[Path]
) -> dict[str, Any]:
    """A JSON-ready summary of an emitted overlay.

    Combines the structured overlay (:func:`overlay_to_dict`) with the concrete
    on-disk paths written, so a ``--json`` emit is both machine-checkable and
    tells the caller exactly what was produced and where.
    """
    summary = overlay_to_dict(overlay)
    summary["written"] = [str(p) for p in written]
    return summary


def summary_to_json(summary: dict[str, Any], *, indent: int | None = 2) -> str:
    """Serialise an emit summary to a JSON string (stable, sorted keys)."""
    return json.dumps(summary, indent=indent, sort_keys=True)
