"""Emit the Flatpak ref list and the mirror manifest.

``emit`` writes two artifacts for
``pengyos-flatpak-mirror`` to consume, into ``--out DIR``:

* **``flatpak-refs.txt``** — the Flatpak ref list: one Flathub ref per line,
  the ``ref`` of every ``flatpak``-lane curated entry, in source order. This is
  exactly :meth:`pengyos_desktop_curation.data.CuratedSet.flatpak_refs`.
* **``mirror.toml``** — the declarative mirror manifest the mirror reads. Each
  ``flatpak``-lane entry becomes an ``apps[]`` entry (``id = <ref>``), with a
  ``collection-id`` derived from the ref.

The manifest pins each app to a commit. The curated set carries Flathub refs
but **no pinned commits** (commit resolution is the mirror tool's job, not this
tool's — the curated set is human-ratified *which apps*, not *which commit of
each*). So the emitted manifest is a **seed**: every ``commit`` is the literal
placeholder :data:`UNPINNED_COMMIT`, which the mirror tool resolves to the real
commit at sync time. See the "Commit placeholder" note in
``pengyos_desktop_curation.cli.emit``.

Output is **deterministic**: two runs against the same committed data produce
byte-identical files. The manifest is hand-written (not ``tomllib``-dumped) so
its formatting is stable and readable across runs and edits.

Standard library only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .data import CuratedEntry, CuratedSet

# The literal placeholder written into the mirror manifest's per-app ``commit``.
# The curated set has Flathub refs but no pinned commits, so the mirror tool
# resolves this to the real commit at sync time. It is a *marker*, not a commit:
# it is intentionally not valid hex (``pengyos_flatpak_mirror`` rejects
# non-hex commits on purpose) so a seed that was never resolved fails loudly
# rather than silently pinning to a bogus commit.
UNPINNED_COMMIT = "UNPINNED"

# Defaults baked into the emitted mirror manifest. These are the same constants
# the sibling ``pengyos-flatpak-mirror`` example uses (``remote = "flathub"``,
# ``remote-name = "pengyos"``); the mirror tool documents them as the canonical
# PengyOS self-hosted-mirror remote.
MIRROR_REMOTE = "flathub"
MIRROR_REMOTE_NAME = "pengyos"

# The emitted artifact file names (stable, documented in the README).
FLATPAK_REFS_FILENAME = "flatpak-refs.txt"
MIRROR_MANIFEST_FILENAME = "mirror.toml"

# The optional .desktop/mime integration artifacts (written by `emit --mime`). `mimeapps.list` is the freedesktop default-application
# file `xdg-mime` reads; `integration-plan.json` is the machine-readable plan.
MIMEAPPS_LIST_FILENAME = "mimeapps.list"
INTEGRATION_PLAN_JSON_FILENAME = "integration-plan.json"

# A Flathub id is ``org.<group>.<name>`` (or ``io.<group>.<name>``); the
# collection id is the ``org.<group>`` / ``io.<group>`` prefix.
_COLLECTION_RE = re.compile(r"^(org|io)\.[^.]+")


@dataclass(frozen=True)
class EmitResult:
    """What :func:`emit` produced.

    Attributes:
        flatpak_refs: The Flatpak ref list payload (one ref per line).
        manifest_toml: The mirror-manifest payload (TOML text).
        artifacts: The emitted (flatpak ref list + mirror manifest) file names,
            in a stable order.
        mimeapps_list: The emitted ``mimeapps.list`` payload (empty when the
            integration plan was not emitted — see ``emit.py::emit``).
        integration_plan_json: The emitted machine-readable integration plan
            (empty when the integration plan was not emitted).
        mime_artifacts: The emitted integration-plan file names, in a stable
            order (empty when the integration plan was not emitted).
    """

    flatpak_refs: str = ""
    manifest_toml: str = ""
    artifacts: tuple[str, ...] = (FLATPAK_REFS_FILENAME, MIRROR_MANIFEST_FILENAME)
    mimeapps_list: str = ""
    integration_plan_json: str = ""
    mime_artifacts: tuple[str, ...] = ()

    def write(self, out_dir: "str | Path") -> list[Path]:
        """Write the artifacts into ``out_dir``; return the written paths.

        Always writes the flatpak ref list + mirror manifest. When the mime
        payloads are non-empty (``emit --mime``), also writes ``mimeapps.list``
        and ``integration-plan.json``.
        """

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        (out_dir / FLATPAK_REFS_FILENAME).write_text(
            self.flatpak_refs, encoding="utf-8"
        )
        (out_dir / MIRROR_MANIFEST_FILENAME).write_text(
            self.manifest_toml, encoding="utf-8"
        )
        written.extend(out_dir / name for name in self.artifacts)
        if self.mimeapps_list:
            (out_dir / MIMEAPPS_LIST_FILENAME).write_text(
                self.mimeapps_list, encoding="utf-8"
            )
            written.append(out_dir / MIMEAPPS_LIST_FILENAME)
        if self.integration_plan_json:
            (out_dir / INTEGRATION_PLAN_JSON_FILENAME).write_text(
                self.integration_plan_json, encoding="utf-8"
            )
            written.append(out_dir / INTEGRATION_PLAN_JSON_FILENAME)
        return written


def _collection_id(ref: str) -> str | None:
    """Derive the Flatpak collection id from a Flathub id (``org.<group>``).

    Returns ``None`` when the ref does not look like a Flathub id (so a
    non-``org.``/``io.`` id contributes no ``collection-id`` — the mirror tool
    simply mirrors the ref without a collection pull).
    """

    match = _COLLECTION_RE.match(ref)
    if not match:
        return None
    return match.group(0)


def flatpak_refs(curated: CuratedSet) -> str:
    """The Flatpak ref list payload: one ref per line, trailing newline.

    This is exactly :meth:`CuratedSet.flatpak_refs` joined with newlines. An
    empty set yields an empty string (no spurious trailing newline).
    """

    refs = curated.flatpak_refs()
    return "".join(f"{ref}\n" for ref in refs)


def _apps_block(curated: CuratedSet) -> str:
    """Render the ``apps = [ ... ]`` table for the mirror manifest.

    One entry per ``flatpak``-lane curated entry, in source order. Each entry is
    ``{ id = "<ref>", commit = "UNPINNED", collection-id = "<derived>" }``; the
    ``collection-id`` key is omitted when no collection id is derivable.
    """

    lines: list[str] = []
    for entry in curated.entries:
        if entry.lane != "flatpak" or not entry.ref:
            continue
        app = f'  {{ id = "{entry.ref}", commit = "{UNPINNED_COMMIT}"'
        collection_id = _collection_id(entry.ref)
        if collection_id is not None:
            app += f', collection-id = "{collection_id}"'
        app += " }"
        lines.append(app)
    return "apps = [\n" + ",\n".join(lines) + "\n]" if lines else "apps = []"


def _collections_block(curated: CuratedSet) -> str:
    """Render the ``collections = [ ... ]`` block.

    The derived collection ids, in first-seen order (de-duplicated): first
    appearance wins, later duplicates dropped, ids with no derivable collection
    skipped. This mirrors the ``pengyos-flatpak-mirror`` "derive from apps"
    rule, but is written explicitly so the manifest is self-describing.
    """

    seen: set[str] = set()
    derived: list[str] = []
    for entry in curated.entries:
        if entry.lane != "flatpak" or not entry.ref:
            continue
        collection_id = _collection_id(entry.ref)
        if collection_id is None or collection_id in seen:
            continue
        seen.add(collection_id)
        derived.append(collection_id)
    if not derived:
        return "collections = []"
    rendered = ",\n".join(f'    "{cid}"' for cid in derived)
    return f"collections = [\n{rendered}\n]"


def mirror_manifest(curated: CuratedSet) -> str:
    """Render the mirror manifest (``mirror.toml``) as TOML text.

    Deterministic and hand-formatted (not ``tomllib``-dumped) so the output is
    stable and readable across runs and edits.
    """

    header = (
        "# Mirror manifest emitted by pengyos-desktop-curation.\n"
        "# Seed for pengyos-flatpak-mirror: each app is pinned to the placeholder\n"
        f"# commit {UNPINNED_COMMIT!r} (the curated set has Flathub refs but no\n"
        "# pinned commits); the mirror tool resolves it to the real commit at sync\n"
        "# time. Every flatpak-lane curated entry becomes one apps[] entry.\n"
    )
    remote = f'remote = "{MIRROR_REMOTE}"\n'
    remote_name = f'remote-name = "{MIRROR_REMOTE_NAME}"\n'
    apps = _apps_block(curated)
    collections = _collections_block(curated)
    return "\n".join(
        [header.rstrip(), remote, remote_name, apps, collections, ""]
    )


def emit(curated: CuratedSet) -> EmitResult:
    """Build the emit payload (ref list + manifest) for ``curated``.

    Pure: no I/O. :meth:`EmitResult.write` persists the payload to disk.
    """

    return EmitResult(
        flatpak_refs=flatpak_refs(curated),
        manifest_toml=mirror_manifest(curated),
    )


def emit_mime(plan: "IntegrationPlan | None") -> EmitResult:
    """Build the .desktop/mime integration artifacts for ``plan``.

    Pure: no I/O. :meth:`EmitResult.write` persists the payload to disk. When
    ``plan`` is ``None`` (``emit`` without ``--mime``) the payloads are empty and
    :meth:`EmitResult.write` writes no integration-plan files.
    """

    if plan is None:
        return EmitResult()
    from . import mime as _mime

    return EmitResult(
        mimeapps_list=_mime.mimeapps_list(plan),
        integration_plan_json=_mime.plan_json(plan),
        mime_artifacts=(
            MIMEAPPS_LIST_FILENAME,
            INTEGRATION_PLAN_JSON_FILENAME,
        ),
    )
