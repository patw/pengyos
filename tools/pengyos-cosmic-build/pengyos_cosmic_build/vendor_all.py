"""``vendor-all`` — drive the pkg vendor engine per component.

For every component in the manifest (optionally filtered to one ``--group``):

1. locate the checked-out sources + its ``Cargo.lock`` under
   ``--sources/<name>/`` (the git crates live in the sibling
   ``--sources/<name>/git-sources`` mirror, the engine's own convention);
2. run the pkg ``vendor`` engine (:func:`pengyos_cosmic_pkg.vendor.vend`) on
   that component, writing the deterministic vendored tarball into ``--dl``;
3. capture the tarball's name + sha256 and the real ``file://`` SITE;
4. record a :class:`~pengyos_cosmic_build.records.VendorRecord` per component
   under ``--out``.

The vendoring engine is an **injectable runner** (see :class:`VendorRunner`):
:func:`vendor_all` takes a ``runner`` and calls it once per component. The
production runner (:class:`VendEngineRunner`) drives the real engine; tests
inject a :class:`FakeVendorRunner` so no network, real sources, or cargo is
needed. A component missing its ``Cargo.lock`` is a *reported* error (a clean
CLI exit), never a crash.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pengyos_cosmic_build.records import (
    VendorRecord,
    write_record,
)

# ``symbol_for`` lives in records: emit keys each recipe off it. Not needed by
# vendor-all (which only records name/version/tarball/sha256/site).

# The manifest vendor-all consumes is the cosmic-recipes manifest (a list of
# components). Imported lazily inside ``vendor_all`` so the import machinery
# (which sibling tools live on sys.path) stays in conftest, not here.

#: Directory (under each component's checked-out source) that holds the mirror
#: of that component's git crates — the engine's ``<cargo-lock dir>/git-sources``
#: convention.
_GIT_SOURCES_DIRNAME = "git-sources"
#: The per-component vendored tree is written into ``--dl/<name>/``.


class VendorAllError(RuntimeError):
    """A component could not be vendored (missing Cargo.lock, engine failure).

    Raised by :class:`VendEngineRunner` / :func:`vendor_all` so a bad component
    is a *reported* condition (clean CLI exit), not a crash.
    """


@dataclass(frozen=True)
class VendOutcome:
    """The vendored-source outcome for one component.

    ``name`` / ``version`` are the component identity; ``tarball`` is the
    vendored tarball filename; ``sha256`` is its digest. The SITE is derived by
    the caller from the ``--dl`` dir (see :func:`vendor_all`).
    """

    name: str
    version: str
    tarball: str
    sha256: str


#: A vendor runner: vendor one component's source and return its outcome. The
#: runner owns *how* the engine is driven (the real engine, or a fake in tests);
#: :func:`vend_all` only calls it once per component.
VendorRunner = Callable[["ComponentContext", Path, Path], VendOutcome]


@dataclass(frozen=True)
class ComponentContext:
    """One component's on-disk vendoring context.

    ``name`` / ``git_url`` / ``tag`` / ``license`` / ``features`` / ``binaries``
    are the component's identity (from the manifest); ``sources_dir`` is the
    checked-out source dir for this component (``--sources/<name>``).
    """

    name: str
    git_url: str
    tag: str
    license: str
    features: tuple[str, ...]
    binaries: tuple[str, ...]
    sources_dir: Path


class FakeVendorRunner:
    """A deterministic :class:`VendorRunner` for tests (no real engine).

    Produces a canned tarball name + a deterministic sha256 derived from the
    component name + tag, so a vendor-all run is reproducible without the real
    engine, network, or cargo. Every component is "vendored" into the ``--dl``
    dir with a real (non-placeholder) SITE.
    """

    def __call__(
        self, ctx: ComponentContext, sources_dir: Path, dl_dir: Path
    ) -> VendOutcome:
        # "Vendors" the tarball into ``<dl>/<name>/`` (mirroring the real
        # engine's layout) so the per-component SITE resolves to a real dir.
        tarball = f"{ctx.name}-{ctx.tag}.tar.gz"
        (dl_dir / ctx.name).mkdir(parents=True, exist_ok=True)
        digest = _fake_digest(ctx.name, ctx.tag)
        return VendOutcome(ctx.name, ctx.tag, tarball, digest)


def _fake_digest(name: str, tag: str) -> str:
    """A deterministic 64-hex digest from the component identity (tests only)."""
    import hashlib

    h = hashlib.sha256(f"{name}:{tag}".encode("utf-8")).hexdigest()
    return h


def _component_manifest(ctx: ComponentContext):
    """Build an engine-compatible :class:`ComponentManifest` for *ctx*."""
    from pengyos_cosmic_pkg.manifest import parse_manifest

    return parse_manifest(
        {
            "name": ctx.name,
            "git_url": ctx.git_url,
            "tag": ctx.tag,
            "license": ctx.license,
            **({"features": list(ctx.features)} if ctx.features else {}),
            **({"binaries": list(ctx.binaries)} if ctx.binaries else {}),
        },
        source=f"{ctx.name}.toml",
    )


class VendEngineRunner:
    """The production :class:`VendorRunner`: drives the real engine.

    For one component: build the engine manifest from the context, load the
    component's ``Cargo.lock`` (raising :class:`VendorAllError` when it is
    missing), and call :func:`pengyos_cosmic_pkg.vendor.vend` writing the
    vendored tree into ``--dl/<name>/``.
    """

    def __call__(
        self, ctx: ComponentContext, sources_dir: Path, dl_dir: Path
    ) -> VendOutcome:
        from pengyos_cosmic_pkg.cargo import load_lock
        from pengyos_cosmic_pkg.vendor import vend

        manifest = _component_manifest(ctx)
        cargo_lock = sources_dir / "Cargo.lock"
        if not cargo_lock.is_file():
            raise VendorAllError(
                f"{ctx.name}: Cargo.lock not found at {cargo_lock}"
            )
        lock = load_lock(cargo_lock)
        git_sources = sources_dir / _GIT_SOURCES_DIRNAME
        # The engine writes the whole vendored tree (VENDOR/ + Cargo.lock +
        # .cargo/ + tarball + .hash) into ``<dl>/<name>/``; the tarball the
        # recipe pins lives directly in that dir, so the per-component SITE is
        # ``file://<dl>/<name>/`` (see :func:`vend_all`).
        out_dir = dl_dir / ctx.name
        result = vend(manifest, lock, git_sources, out_dir)
        return VendOutcome(
            name=ctx.name,
            version=ctx.tag,
            tarball=result.hash_filename,
            sha256=result.hash_value,
        )


def _site_for(dl_dir: Path) -> str:
    """The ``file://`` SITE Buildroot resolves for tarballs under *dl_dir*.

    A real absolute URI (``file:///abs/path``) — never the placeholder.
    """
    return dl_dir.resolve().as_uri()


def vend_all(
    manifest_path: Path | str,
    sources_dir: Path | str,
    dl_dir: Path | str,
    out_dir: Path | str,
    *,
    group: str | None = None,
    runner: VendorRunner | None = None,
) -> "VendorAllReport":
    """Vendor every component in the manifest and record each outcome.

    * ``manifest_path`` — the cosmic-recipes manifest (a list of components).
    * ``sources_dir`` — the checked-out component sources (``--sources``); each
      component lives at ``<sources_dir>/<name>/`` with its ``Cargo.lock`` and a
      ``<sources_dir>/<name>/git-sources`` mirror.
    * ``dl_dir`` — the download dir the vendored tarballs are written into
      (``--dl``); tarballs land under ``<dl_dir>/vendor/<name>/``.
    * ``out_dir`` — where the per-component vendor records are written
      (``--out``); one ``<name>.json`` per component.
    * ``group`` — optionally restrict to one manifest group.
    * ``runner`` — the injectable vendor engine (defaults to
      :class:`VendEngineRunner`).

    Returns a :class:`VendorAllReport`. Raises :class:`VendorAllError` on the
    first component that fails to vendor (missing Cargo.lock, engine failure);
    a partial set of records may already be written under ``out_dir``.
    """
    from pengyos_cosmic_recipes.manifest import load_manifest as _load_manifest

    manifest = _load_manifest(manifest_path)
    sources_dir = Path(sources_dir)
    dl_dir = Path(dl_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    runner = runner or VendEngineRunner()

    entries = manifest.entries(group)
    if not entries:
        groups = ", ".join(manifest.groups) or "(none)"
        raise VendorAllError(
            f"group {group!r} has no components (known groups: {groups})"
        )

    written: list[VendorRecord] = []
    for entry in entries:
        ctx = ComponentContext(
            name=entry.name,
            git_url=entry.git_url,
            tag=entry.tag,
            license=entry.license,
            features=entry.features,
            binaries=entry.binaries,
            sources_dir=sources_dir / entry.name,
        )
        # The runner writes into ``<dl>/<name>/``; the recipe pins that dir as
        # the component's ``*_SITE`` (a real file:// URI, never the
        # placeholder).
        outcome = runner(ctx, ctx.sources_dir, dl_dir)
        site = _site_for(dl_dir / entry.name)

        record = VendorRecord(
            name=outcome.name,
            version=outcome.version,
            tarball=outcome.tarball,
            sha256=outcome.sha256,
            site=site,
        )
        record.validate()  # real sha256 + real SITE, or a reported error
        write_record(record, out_dir / f"{entry.name}.json")
        written.append(record)

    return VendorAllReport(
        out_dir=out_dir,
        site=_site_for(dl_dir),
        records=tuple(written),
    )


@dataclass(frozen=True)
class VendorAllReport:
    """The outcome of a :func:`vend_all` run."""

    out_dir: Path
    site: str
    records: tuple[VendorRecord, ...]

    @property
    def names(self) -> tuple[str, ...]:
        """The vendored component names, in file order."""
        return tuple(r.name for r in self.records)

    def as_dict(self) -> dict:
        """A deterministic, JSON-serialisable view."""
        return {
            "command": "vendor-all",
            "ok": True,
            "out": str(self.out_dir),
            "site": self.site,
            "count": len(self.records),
            "components": [r.as_dict() for r in self.records],
        }


def _emit(args, report: VendorAllReport) -> int:
    """Print the vendor-all result (human or JSON) and return the exit code."""
    if getattr(args, "json", False):
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(f"Vendored {len(report.records)} component(s) into {report.out_dir}")
        for record in report.records:
            print(f"  - {record.name} {record.version}  {record.sha256[:12]}…")
        print(f"SITE: {report.site}")
    return 0


def main(args) -> int:
    """CLI entry point for ``vendor-all``."""
    report = vend_all(
        args.manifest,
        args.sources,
        args.dl,
        args.out,
        group=getattr(args, "group", None),
    )
    return _emit(args, report)
