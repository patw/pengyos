"""Delete superseded releases from the public site: ``update/`` and ``download/``.

Per edition (``update/<image>/``) the site keeps

* every release on **stable** (the download page lists older stable ones), or
  the newest ``keep_stable`` of them when that is set, and
* the edition's **newest** release, whatever its channel: machines on testing
  update to it, and promoting it later needs its download images.

Every other release is a testing build that a newer one superseded, and goes.
The updater never downgrades, so no machine is left without a way forward.

A ``download/<version>/`` holds one image per edition of that build. It stays
while any of its editions is kept (Core rides along with any edition of its
version, as on the download page) or while it is the newest version, and goes
as a whole otherwise. A half-kept directory is left as it is: removing single
images would mean re-signing its ``SHA256SUMS``, and prune never needs the key.

Dry run by default. ``apply`` deletes, then rewrites ``update/feed.xml``,
``update/index.html`` and ``download/index.html``. The site push must then
mirror deletions (``rsync --delete``), or the server keeps its copies.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .errors import PengyOsError
from .manifest import parse_version


class PruneError(PengyOsError):
    """The site could not be pruned."""


@dataclass
class PrunePlan:
    kept: dict[str, list[str]] = field(default_factory=dict)       # image -> versions
    pruned: dict[str, list[str]] = field(default_factory=dict)     # image -> versions
    downloads_kept: list[str] = field(default_factory=list)
    downloads_pruned: list[str] = field(default_factory=list)

    def paths(self, site: Path) -> list[Path]:
        return ([site / "update" / i / v for i, vs in sorted(self.pruned.items()) for v in vs]
                + [site / "download" / v for v in self.downloads_pruned])


def _versions(d: Path) -> list[str]:
    """Version subdirectories of ``d``, oldest first (anything else is ignored)."""
    out = []
    for p in d.iterdir() if d.is_dir() else ():
        if not p.is_dir():
            continue
        try:
            parse_version(p.name)
        except PengyOsError:
            continue
        out.append(p.name)
    return sorted(out, key=parse_version)


def plan(site: Path, keep_stable: int | None = None) -> PrunePlan:
    if keep_stable is not None and keep_stable < 1:
        raise PruneError("--keep-stable must be at least 1")
    upd, dl = site / "update", site / "download"
    if not upd.is_dir() and not dl.is_dir():
        raise PruneError(f"{site} has neither update/ nor download/")
    result = PrunePlan()
    keep_pairs: set[tuple[str, str]] = set()
    for image_dir in sorted(p for p in upd.iterdir() if p.is_dir()) if upd.is_dir() else ():
        versions = _versions(image_dir)
        if not versions:
            continue
        stable = []
        for v in versions:
            rj = image_dir / v / "release.json"
            if not rj.is_file():
                raise PruneError(f"{rj} is missing: is {image_dir.name} {v} half published?")
            if "stable" in json.loads(rj.read_text()).get("channels", []):
                stable.append(v)
        if keep_stable is not None:
            stable = stable[-keep_stable:]
        keep = set(stable) | {versions[-1]}
        result.kept[image_dir.name] = [v for v in versions if v in keep]
        pruned = [v for v in versions if v not in keep]
        if pruned:
            result.pruned[image_dir.name] = pruned
        keep_pairs |= {(image_dir.name, v) for v in keep}
    kept_versions = {v for _, v in keep_pairs}
    keep_pairs |= {("pengyos-core", v) for v in kept_versions}

    dl_versions = _versions(dl)
    for v in dl_versions:
        dj = dl / v / "downloads.json"
        images = [e["image"] for e in json.loads(dj.read_text())["images"]] if dj.is_file() else []
        if v == dl_versions[-1] or any((i, v) in keep_pairs for i in images):
            result.downloads_kept.append(v)
        else:
            result.downloads_pruned.append(v)
    return result


def apply(site: Path, p: PrunePlan, base_url: str | None = None) -> list[Path]:
    """Delete what ``p`` prunes and regenerate the site's listings."""
    from .downloads import refresh_index
    from .osupdate import DEFAULT_BASE_URL, write_feed

    removed = []
    for path in p.paths(site):
        shutil.rmtree(path)
        removed.append(path)
    if (site / "update").is_dir():
        write_feed(site, base_url or DEFAULT_BASE_URL)
    refresh_index(site)
    return removed
