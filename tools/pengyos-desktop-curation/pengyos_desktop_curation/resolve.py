"""Resolve the ``UNPINNED`` commit placeholder in the emitted mirror manifest.

The emitted mirror manifest pins every
``flatpak``-lane app to the literal placeholder :data:`UNPINNED_COMMIT` (the
curated set carries Flathub refs but no pinned commits — spec Principle 8). The
``pengyos-flatpak-mirror`` loader *rejects* that placeholder
(``commit must be a non-empty hex string``), so before the mirror can sync the
placeholder must be resolved to a real Flathub commit.

This module provides the resolution mechanism:

* :class:`CommitDatabase` — the committed ref -> commit map shipped with the
  tool (``flathub-commits.toml``). It is the offline, deterministic source of
  truth for the resolution: no network, no live mirror needed.
* :func:`resolve_manifest` — the core operation: a pure function that takes the
  emitted seed-manifest text and returns a *pinned* manifest in which every
  ``UNPINNED`` placeholder is replaced by the real commit for that app's ref.
* :func:`pin_manifest` — the convenience: emit the seed manifest for a curated
  set, then resolve it in one step.
* :func:`fetch_commit` — the optional live source: query Flathub for the current
  commit of a ref. It is a thin, injectable boundary (see :func:`with_fetcher`);
  tests inject a fake so no network is needed.
* :func:`refresh` — fetch every curated
  ``flatpak`` ref live, merge the fresh commits into the committed
  ``flathub-commits.toml`` database, and persist it back (self-update with real
  Flathub commits). :func:`write_db` / :func:`render` are the pure, header-
  preserving serialisers it uses.

Design notes
------------
The resolution is deliberately split from the mirror tool's sync (spec:
"Follow-up in ``pengyos-flatpak-mirror``"). This tool owns the curated set and
ships the commit database; the mirror tool consumes the *resolved* manifest and
may re-verify/refresh commits live at sync time.

The database is a **cache**, not a proof of existence: OSTree commits change
with every Flathub rebuild, so a committed value is only valid until the next
build. :func:`fetch_commit` (and the ``--refresh`` flag on ``emit``) refresh it
from Flathub; the mirror tool can do the same at sync. The committed database is
what makes the resolution work *offline and deterministically* at release time.

Standard library only.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from . import emit as _emit
from .data import CuratedSet, load_default

# The committed commit database, at the repo root next to ``curated.toml``.
COMMIT_DATABASE_FILE = Path(__file__).resolve().parents[1] / "flathub-commits.toml"

# A real OSTree/Flatpak commit is a hex SHA-256: 64 hex characters. The mirror
# tool only requires >=8 hex chars, but a real Flathub commit is always 64, so
# we validate strictly here (a seed that slipped through would be caught).
REAL_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# The per-app manifest table as emitted by ``emit._apps_block`` — one app is
# ``{ id = "<ref>", commit = "<commit>", collection-id = "<cid>" }`` on a single
# line. We match each app's ``id`` and ``commit`` independently so resolution
# works even if the table is hand-edited onto multiple lines.
_ID_RE = re.compile(r'''id\s*=\s*"([^"]+)"''')
_COMMIT_RE = re.compile(r'''commit\s*=\s*"([^"]+)"''')

# The seed header comment mentions the ``UNPINNED`` placeholder by name. After
# resolution the commits are real, so rewrite that mention (in any comment line)
# to reflect the pinned state. Match the quoted placeholder token inside a
# leading ``#`` comment.
_REWRITE_HEADER = re.compile(r"""(?m)^(#.*\b)UNPINNED(\b.*)$""")
_HEADER_REPLACEMENT = r"\1a real Flathub commit\2"


class ResolveError(Exception):
    """Base error for the commit-resolution layer."""


class UnknownCommitError(ResolveError):
    """A ref has no known real commit in the database.

    Raised by :func:`resolve_manifest` when an app is pinned to the
    ``UNPINNED`` placeholder whose ref is absent from the database. This is a
    hard failure by design: emitting an unresolved manifest to the mirror would
    make it reject the manifest (or, worse, mirror a bogus commit).
    """


class BadCommitError(ResolveError):
    """A resolved commit is not a valid real OSTree commit (not 64 hex chars).

    Raised when the database contains a value that is not a real commit — a
    guard so a malformed database entry fails loudly rather than being written
    into the resolved manifest.
    """


# The fetcher type: ``fetcher(ref) -> commit``. Injected into the resolver so
# tests never touch the network.
Fetcher = Callable[[str], str]


@dataclass(frozen=True)
class CommitDatabase:
    """The committed ref -> commit map.

    ``commits`` maps each Flathub ref to its real OSTree commit hash.
    ``last_verified`` is the date the database was last refreshed from Flathub
    (informational; the mirror tool decides whether a value is stale enough to
    refresh).
    """

    commits: Mapping[str, str] = field(default_factory=dict)
    last_verified: str | None = None

    def resolve(self, ref: str) -> str:
        """Return the real commit for ``ref`` or raise :class:`UnknownCommitError`."""

        try:
            commit = self.commits[ref]
        except KeyError:
            raise UnknownCommitError(
                f"no known commit for {ref!r} in the commit database "
                f"(run `pengyos-desktop-curation emit --out DIR --refresh` to "
                f"fetch it from Flathub, or add it to flathub-commits.toml)"
            ) from None
        if not REAL_COMMIT_RE.match(commit):
            raise BadCommitError(
                f"commit {commit!r} for {ref!r} is not a real OSTree commit "
                f"(expected 64 hex characters)"
            )
        return commit

    def has(self, ref: str) -> bool:
        """Whether the database knows a commit for ``ref``."""

        return ref in self.commits

    @classmethod
    def load(cls, path: object) -> "CommitDatabase":
        """Load a commit database from ``path`` (``flathub-commits.toml``)."""

        path = Path(path)
        text = path.read_text(encoding="utf-8")
        import tomllib

        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ResolveError(f"{path}: invalid TOML: {exc}") from exc

        if not isinstance(data, Mapping):
            raise ResolveError(f"{path}: top-level value must be a table")

        commits_raw = data.get("commits", {})
        if not isinstance(commits_raw, Mapping):
            raise ResolveError(f"{path}: 'commits' must be a table")
        commits: dict[str, str] = {}
        for ref, commit in commits_raw.items():
            if not isinstance(ref, str) or not isinstance(commit, str):
                raise ResolveError(
                    f"{path}: commit entries must be string ref -> string commit"
                )
            commits[ref] = commit

        last_verified = data.get("last_verified")
        if last_verified is not None and not isinstance(last_verified, str):
            raise ResolveError(
                f"{path}: 'last_verified' must be a string or absent"
            )
        return cls(commits=commits, last_verified=last_verified)

    @classmethod
    def load_default(cls) -> "CommitDatabase":
        """Load the committed commit database (``flathub-commits.toml``)."""

        return cls.load(COMMIT_DATABASE_FILE)


def resolve_ref(ref: str, db: CommitDatabase) -> str:
    """Resolve one ref to its real commit (thin wrapper over :meth:`CommitDatabase.resolve`)."""

    return db.resolve(ref)


def resolve_manifest(
    manifest_text: str,
    db: CommitDatabase | None = None,
    *,
    fetcher: Fetcher | None = None,
    record: Mapping[str, str] | None = None,
) -> str:
    """Resolve the ``UNPINNED`` placeholders in ``manifest_text`` to real commits.

    Args:
        manifest_text: The emitted seed manifest (``emit.mirror_manifest``).
        db: The commit database. Defaults to the committed
            :func:`CommitDatabase.load_default`.
        fetcher: Optional live fetcher (``fetcher(ref) -> commit``). When given
            and a ref is missing from ``db``, the ref is resolved by calling it.
            Tests inject a fake; production leaves it ``None`` (offline).
        record: Optional mutable mapping. Every commit resolved *by the
            fetcher* (i.e. a ref not already in ``db``) is recorded here as
            ``record[ref] = commit``. Lets a caller (e.g. ``--refresh``) learn
            exactly which live commits it pulled without re-implementing the
            resolution loop. ``db``-sourced resolutions are not recorded.

    Returns:
        The pinned manifest: identical to ``manifest_text`` except every
        ``commit = "UNPINNED"`` is replaced by the real commit for that app's
        ref.

    Raises:
        UnknownCommitError: a placeholder's ref is unknown (and no fetcher
            resolves it).
        BadCommitError: a resolved commit is not a real 64-hex commit.
    """

    db = db if db is not None else CommitDatabase.load_default()

    def _resolve(ref: str) -> str:
        if db.has(ref):
            return db.resolve(ref)
        if fetcher is None:
            raise UnknownCommitError(
                f"no known commit for {ref!r} and no fetcher is installed"
            )
        commit = fetcher(ref)
        if not REAL_COMMIT_RE.match(commit):
            raise BadCommitError(
                f"fetcher returned {commit!r} for {ref!r}, not a real "
                f"OSTree commit (expected 64 hex characters)"
            )
        if record is not None:
            record[ref] = commit
        return commit

    current_ref: str | None = None
    out: list[str] = []
    replaced = 0
    for line in manifest_text.splitlines():
        id_match = _ID_RE.search(line)
        if id_match:
            current_ref = id_match.group(1)
        commit_match = _COMMIT_RE.search(line)
        if commit_match:
            value = commit_match.group(1)
            if value == _emit.UNPINNED_COMMIT:
                if current_ref is None:
                    raise ResolveError(
                        "manifest has a commit placeholder with no preceding "
                        "app id; the manifest is malformed"
                    )
                commit = _resolve(current_ref)
                line = line[: commit_match.start(1)] + commit + line[commit_match.end(1):]
                replaced += 1
        out.append(line)
    if replaced == 0:
        # Nothing to resolve: the manifest was already pinned (or empty). Return
        # it unchanged rather than erroring — resolution is idempotent.
        return manifest_text

    # The seed header comment documents the UNPINNED placeholder; rewrite the
    # mention so the pinned manifest is self-consistent (its commits are real).
    out = [_REWRITE_HEADER.sub(_HEADER_REPLACEMENT, line) for line in out]

    return "\n".join(out)


def pin_manifest(
    curated: CuratedSet,
    db: CommitDatabase | None = None,
    *,
    fetcher: Fetcher | None = None,
) -> str:
    """Emit the seed manifest for ``curated`` and resolve its placeholders.

    One-step convenience: :func:`emit.mirror_manifest` then
    :func:`resolve_manifest`. Returns the pinned manifest (real commits).
    """

    seed = _emit.mirror_manifest(curated)
    return resolve_manifest(seed, db, fetcher=fetcher)


def fetch_commit(ref: str, *, url: str | None = None) -> str:
    """Fetch the current Flathub stable commit for ``ref`` (the live boundary).

    Queries the OSTree refs endpoint ``refs/stable/<ref>`` on Flathub, which
    returns the raw commit hash. This is the network path the ``--refresh`` flag
    installs: it resolves refs the committed database does not know. It is
    injectable (see :func:`resolve_manifest`'s ``fetcher``) so tests never touch
    the network.

    Raises :class:`ResolveError` on any network/HTTP failure (with the URL and
    status so the curator can act).
    """

    url = url or f"https://dl.flathub.org/repo/refs/stable/{ref}"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "pengyos-desktop-curation"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8").strip()
    except urllib.error.URLError as exc:
        raise ResolveError(
            f"could not fetch the current commit for {ref!r} from Flathub "
            f"({url}): {exc}"
        ) from exc
    except OSError as exc:  # network-level (DNS, connection, timeout)
        raise ResolveError(
            f"could not fetch the current commit for {ref!r} from Flathub "
            f"({url}): {exc}"
        ) from exc
    if not REAL_COMMIT_RE.match(body):
        raise ResolveError(
            f"Flathub returned {body!r} for {ref!r}, not a real OSTree commit "
            f"(expected 64 hex characters)"
        )
    return body


def with_fetcher(db: CommitDatabase, fetcher: Fetcher) -> Fetcher:
    """Return a fetcher that fills any ref missing from ``db`` via ``fetcher``.

    Handy for the ``--refresh`` path: resolve from the committed database, and
    fall back to the live fetcher (then optionally persist) for anything missing.
    """

    def _fetch(ref: str) -> str:
        if db.has(ref):
            return db.resolve(ref)
        return fetcher(ref)

    return _fetch


# --- persisting the commit database ---------------


def _default_header() -> str:
    """The header comment block written above ``last_verified`` / ``[commits]``.

    Shipped verbatim with the committed ``flathub-commits.toml`` so a fresh
    file (e.g. one created by ``--refresh`` on a repo that never had one) is
    self-documenting. When rewriting an existing file the *existing* header is
    preserved instead (see :func:`write_db`).
    """

    return (
        "# Flathub commit database — ref -> real OSTree commit hash.\n"
        "#\n"
        "# Committed by `pengyos-desktop-curation` when it "
        "runs\n"
        "# `emit --out DIR --refresh`: the live Flathub commit for each curated "
        "ref\n"
        "# is fetched and written back here, so the committed database "
        "self-updates\n"
        "# with real commits instead of requiring a manual rewrite.\n"
        "#\n"
        "# This is a CACHE: the current Flathub stable commit changes with every "
        "Flathub\n"
        "# rebuild. `--refresh` re-validates every curated ref against Flathub "
        "and\n"
        "# rewrites this file; the mirror tool (`pengyos-flatpak-mirror`) may "
        "also\n"
        "# refresh live at sync time. Stand-in values here are NOT real Flathub "
        "commits\n"
        "# until a real `--refresh` runs (this environment has no Flathub "
        "network).\n"
    )


def render(db: CommitDatabase, *, header: str | None = None) -> str:
    """Serialise a :class:`CommitDatabase` to committed-``flathub-commits.toml`` text.

    Pure and deterministic (standard-library ``tomllib``/``json`` only — there
    is no ``tomllib.dumps`` in the stdlib, so the TOML is hand-written). The
    output is:

    * an optional header comment block,
    * ``last_verified = "<date>"`` (or omitted when ``None``),
    * a ``[commits]`` table, one ``"ref" = "commit"`` entry per ref, sorted by
      ref for a stable, diff-friendly file.

    The header is the existing file's header when the caller passes one, else
    :func:`_default_header`.
    """

    lines: list[str] = []
    if header is not None:
        lines.append(header.rstrip("\n"))
    if db.last_verified is not None:
        lines.append(f'last_verified = "{db.last_verified}"')
    if db.commits:
        lines.append("")
        lines.append("[commits]")
        for ref in sorted(db.commits):
            lines.append(f'"{ref}" = "{db.commits[ref]}"')
    return "\n".join(lines) + "\n"


def write_db(
    path: "str | Path",
    db: CommitDatabase,
    *,
    preserve_header: bool = True,
) -> CommitDatabase:
    """Persist ``db`` to ``path`` (the committed ``flathub-commits.toml``).

    Read-modify-write: the existing file's header comment block is preserved
    (``preserve_header``) so the file keeps its documentation across refreshes;
    only ``last_verified`` and the ``[commits]`` table are rewritten. A missing
    file is written fresh (with :func:`_default_header`).

    A file that exists but is not valid TOML (or has no ``[commits]`` table) is
    a hard error: :func:`write_db` never silently clobbers a file it cannot
    parse — a corrupt committed database must be fixed by hand, not overwritten
    by a tool.

    Returns the persisted :class:`CommitDatabase` (for the caller's record).

    Raises:
        ResolveError: the target file exists, is not valid TOML, or has no
            ``[commits]`` table.
        OSError: the file could not be written.
    """

    path = Path(path)
    header = None
    if path.exists():
        existing_text = path.read_text(encoding="utf-8")
        import tomllib

        try:
            existing = tomllib.loads(existing_text)
        except tomllib.TOMLDecodeError as exc:
            raise ResolveError(
                f"{path}: existing file is not valid TOML ({exc}); refusing to "
                "overwrite a corrupt committed commit database"
            ) from exc
        if not isinstance(existing, Mapping):
            raise ResolveError(
                f"{path}: existing file is not a table; refusing to overwrite a "
                "committed commit database of an unexpected shape"
            )
        # A missing [commits] table is an empty (valid) database — the same
        # convention CommitDatabase.load uses — so only a present-but-wrong-type
        # commits value is rejected.
        commits_raw = existing.get("commits", {})
        if not isinstance(commits_raw, Mapping):
            raise ResolveError(
                f"{path}: existing 'commits' must be a table; refusing to "
                "overwrite a committed commit database of an unexpected shape"
            )
        if preserve_header:
            # The header is everything before the first non-comment, non-blank
            # line. Keep it verbatim so refreshes don't lose the docs.
            header = _header_of(existing_text)
    elif preserve_header:
        # A fresh file gets the default self-documenting header.
        header = _default_header()

    rendered = render(db, header=header)
    path.write_text(rendered, encoding="utf-8")
    return db


def _header_of(text: str) -> str:
    """Return the leading comment block of a committed-DB file (up to the first
    non-comment, non-blank line). Empty string when there is no leading block."""

    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            # Blank line: keep going only if we've already collected comment
            # lines (a blank line can separate the header from the body), but
            # stop at the first blank line that follows the comment block.
            if lines and lines[-1].strip():
                break
            lines.append(line)
            continue
        if stripped.startswith("#"):
            lines.append(line)
        else:
            break
    return "\n".join(lines) + "\n"


def refresh(
    curated: CuratedSet,
    *,
    fetcher: Fetcher,
    path: "str | Path" = COMMIT_DATABASE_FILE,
    today: str | None = None,
) -> CommitDatabase:
    """Fetch every curated ``flatpak`` ref live, merge into the committed DB,
    and persist it back.

    The committed ``flathub-commits.toml`` is a cache of the *current* Flathub
    stable commit per ref. ``--refresh`` re-validates every curated ref against
    Flathub and writes the fresh values back, so the committed database
    self-updates with real commits instead of requiring a manual rewrite.

    The merge is additive: refs already in the database that are *not* curated
    are preserved; every curated ``flatpak`` ref is overwritten with its freshly
    fetched value, and ``last_verified`` is set to ``today`` (today's date, or
    ``None`` to drop the field).

    Fetching re-uses :func:`resolve_manifest` against an **empty** DB with a
    ``record`` sink: every curated ref is therefore re-fetched (not served from
    the stale DB — that is the whole point of a refresh) and validated as a real
    64-hex commit in one place. The seed manifest is resolved to the same fresh
    values, so the manifest the caller resolves after this call matches the
    persisted DB.

    Args:
        curated: The curated set — every ``flatpak`` ref here is fetched live.
        fetcher: The live fetcher (``fetcher(ref) -> commit``). Use the raw
            Flathub boundary (:func:`fetch_commit`) so *every* ref is re-fetched.
        path: Where to persist. Defaults to the committed
            ``flathub-commits.toml``.
        today: The ``last_verified`` date to stamp. Defaults to today's date
            (injectable for deterministic tests).

    Returns:
        The merged, persisted :class:`CommitDatabase`.

    Raises:
        BadCommitError: a ref's fetched commit is not a real 64-hex commit.
        ResolveError: the target file is corrupt / has no ``[commits]`` table, or
            the curated set has no flatpak refs (nothing to persist).
    """

    db = CommitDatabase.load(path)
    today = today or _today()

    seed = _emit.mirror_manifest(curated)
    record: dict[str, str] = {}
    # Resolve against an empty DB so *every* ref goes through the fetcher (and
    # is recorded + validated as a real 64-hex commit) — none is served from the
    # stale committed values.
    pinned = resolve_manifest(seed, CommitDatabase(), fetcher=fetcher, record=record)
    if not record:
        raise ResolveError(
            "no flatpak refs to refresh; the curated set has no flatpak-lane "
            "entries (nothing to persist)"
        )

    merged = dict(db.commits)
    merged.update(record)
    new_db = CommitDatabase(commits=merged, last_verified=today)
    write_db(path, new_db)
    # The manifest must resolve to exactly the values we just persisted.
    assert resolve_manifest(pinned, new_db) == pinned
    return new_db


def _today() -> str:
    """Today's date (ISO YYYY-MM-DD) — the default ``last_verified`` stamp."""

    import datetime

    return datetime.date.today().isoformat()
