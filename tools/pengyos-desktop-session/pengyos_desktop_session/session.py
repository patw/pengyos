"""Write the emitted COSMIC session to disk.

The pure string emitter lives in :mod:`pengyos_desktop_session.emit`; this
module turns those strings into files under an output directory. It is the
``emit`` CLI's I/O layer.

:func:`write_session` renders the artifacts in canonical order and writes each
one under ``out_dir`` (creating it if necessary), returning the ordered list of
written filenames. Re-emitting is idempotent: identical content is written back
byte-for-byte, and files that are *not* part of the artifact set are left
untouched — emit writes its files, it does not wipe the overlay.

Nothing here touches the network, a display, or real hardware, so it is safe to
call from the unit suite against a temporary directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from .emit import ARTIFACT_ORDER, render_artifacts
from .model import CosmicSession

__all__ = ["write_session", "written_paths"]


def write_session(
    out_dir: "str | Path",
    session: "CosmicSession | None" = None,
    *,
    overwrite: bool = True,
) -> "list[str]":
    """Write the COSMIC session definition under ``out_dir``.

    Renders the five session-definition artifacts in canonical
    (:data:`~pengyos_desktop_session.emit.ARTIFACT_ORDER`) order and writes each
    one under ``out_dir`` (creating the directory if necessary). Returns the
    list of written filenames **in the order they were written** — the stable
    ordering that makes the output reproducible.

    Parameters
    ----------
    out_dir:
        The target overlay directory. Created if it does not exist.
    session:
        The session to emit. Defaults to the canonical PengyOS Desktop
        :class:`~pengyos_desktop_session.model.CosmicSession`.
    overwrite:
        When ``True`` (the default) existing files are overwritten with the
        freshly rendered content (emit is idempotent). When ``False`` an
        existing file is left as-is and skipped — useful for detecting a dirty
        overlay.

    Returns
    -------
    list[str]
        The written filenames, in canonical order.
    """
    artifacts = render_artifacts(session)
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for name in ARTIFACT_ORDER:  # canonical, stable ordering
        content = artifacts[name]
        path = target / name
        if path.exists() and not overwrite:
            continue
        # ``newline=""`` + explicit LF content keeps the files LF-only even on
        # Windows; the content already ends in exactly one trailing newline.
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(name)
    return written


def written_paths(
    out_dir: "str | Path",
    session: "CosmicSession | None" = None,
) -> "dict[str, Path]":
    """Map each written artifact filename to its resolved path under ``out_dir``.

    Convenience for callers (and tests) that want the destination path for a
    given artifact without re-deriving it.
    """
    target = Path(out_dir)
    return {name: target / name for name in ARTIFACT_ORDER}
