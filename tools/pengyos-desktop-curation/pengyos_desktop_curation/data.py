"""Committed declarative data for the curated PengyOS Desktop default-app set.

The curated default-app set is
stored as *data* — ``category -> app -> lane -> ref -> rationale ->
human_signed`` — loaded from the committed ``curated.toml`` and modelled as
immutable records.

The app set is a curated, reproducible, human-ratified decision (spec Principle
8): every entry carries a human-authored ``rationale`` and ``human_signed =
true``. The tool never chooses the app set unaided — it only stores and emits
what a human has ratified.

Layout
------
* :data:`LANES` — the delivery lanes a curated entry may live in.
* :data:`EVERYDAY_CATEGORIES` — the US-DSK-2 "everyday apps covered" list, the
  acceptance horizon :mod:`pengyos_desktop_curation.data` coverage reports against.
* :class:`CuratedEntry` — one curated app, an immutable record.
* :class:`CuratedSet` — the ordered collection of entries, with the queries the
  rest of the tool needs (by category, flatpak refs, …).
* :func:`load` / :func:`load_default` — read the committed data.

Standard library only.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

# The delivery lanes a curated entry may live in. ``flatpak`` entries carry a
# Flathub ``ref``; ``appimage`` and ``recipe`` entries do not.
LANES: tuple[str, ...] = ("flatpak", "appimage", "recipe")

# The US-DSK-2 "everyday apps covered" list: the acceptance horizon the coverage
# report (later subtask) checks against. Kept in code so the tool owns the
# contract; the committed data only supplies entries.
EVERYDAY_CATEGORIES: tuple[str, ...] = (
    "browser",
    "terminal",
    "files",
    "editor",
    "media",
    "office",
    "PDF",
    "archive",
    "calculator",
    "screenshots",
)

# Fields every entry must carry. ``human_signed`` and ``ref`` have defaults (see
# :func:`parse_entry`) so a later subtask can lint a *missing* signature rather
# than having the loader reject it outright.
REQUIRED_FIELDS: tuple[str, ...] = ("category", "app", "lane", "rationale")

# The committed curated set. See the file for the human-authored entries.
DATA_FILE = Path(__file__).resolve().parents[1] / "curated.toml"


class CuratedError(Exception):
    """Base error for the curated-data layer."""


class MalformedEntryError(CuratedError):
    """A curated entry is missing a required field or has an invalid value."""


class LoadError(CuratedError):
    """The committed data file could not be read or parsed."""


@dataclass(frozen=True)
class CuratedEntry:
    """One curated default application.

    Fields follow the spec's order: ``category -> app -> lane -> ref ->
    rationale -> human_signed``. ``ref`` is only meaningful for the ``flatpak``
    lane (the Flathub application id); ``human_signed`` records that a human
    ratified the entry (spec Principle 8).
    """

    category: str
    app: str
    lane: str
    ref: str = ""
    rationale: str = ""
    human_signed: bool = False

    def as_dict(self) -> dict[str, str | bool]:
        """Return the entry as a plain dict, in field order."""

        return {
            "category": self.category,
            "app": self.app,
            "lane": self.lane,
            "ref": self.ref,
            "rationale": self.rationale,
            "human_signed": self.human_signed,
        }

    @property
    def is_signed(self) -> bool:
        """Whether a human ratified this entry (``human_signed``)."""

        return self.human_signed

    @property
    def has_ref(self) -> bool:
        """Whether this entry carries a Flatpak ``ref``."""

        return bool(self.ref)

    def validate(self) -> CuratedEntry:
        """Assert this entry is well-formed; return it, or raise.

        Raises :class:`MalformedEntryError` if a required field is missing or
        empty, if ``lane`` is not one of :data:`LANES`, or if a ``flatpak``
        entry has no ``ref`` (a ref list entry needs its ref).
        """

        for name in REQUIRED_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise MalformedEntryError(
                    f"entry {self.app!r} is missing required field {name!r}"
                )
        if self.lane not in LANES:
            raise MalformedEntryError(
                f"entry {self.app!r}: lane {self.lane!r} is not one of {LANES}"
            )
        if self.lane == "flatpak" and not self.ref:
            raise MalformedEntryError(
                f"entry {self.app!r}: flatpak lane requires a ref"
            )
        return self


@dataclass
class CuratedSet:
    """The ordered collection of curated entries.

    Iteration order is the order the entries appear in the source data, so the
    set is deterministic and stable across runs.
    """

    entries: tuple[CuratedEntry, ...] = field(default_factory=tuple)

    def __iter__(self) -> Iterator[CuratedEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, index: int) -> CuratedEntry:
        return self.entries[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, CuratedSet):
            return self.entries == other.entries
        return NotImplemented

    def categories(self) -> list[str]:
        """The categories present, in first-seen order (de-duplicated)."""

        seen: list[str] = []
        for entry in self.entries:
            if entry.category not in seen:
                seen.append(entry.category)
        return seen

    def by_category(self, category: str) -> list[CuratedEntry]:
        """Every entry in ``category`` (source order)."""

        return [entry for entry in self.entries if entry.category == category]

    def lanes(self) -> list[str]:
        """The lanes present, in first-seen order (de-duplicated)."""

        seen: list[str] = []
        for entry in self.entries:
            if entry.lane not in seen:
                seen.append(entry.lane)
        return seen

    def flatpak_refs(self) -> list[str]:
        """The Flatpak refs of the ``flatpak``-lane entries (source order).

        This is exactly the payload of the emitted Flatpak ref list.
        """

        return [
            entry.ref
            for entry in self.entries
            if entry.lane == "flatpak" and entry.ref
        ]

    def validate(self) -> "CuratedSet":
        """Validate every entry; return ``self`` or raise.

        Raises :class:`MalformedEntryError` on the first malformed entry.
        """

        for index, entry in enumerate(self.entries):
            try:
                entry.validate()
            except MalformedEntryError as exc:
                raise MalformedEntryError(
                    f"entry #{index} ({entry.app!r}): {exc}"
                ) from exc
        return self


def _coerce_bool(value: object, where: str) -> bool:
    """Coerce a data value to a strict ``bool`` (rejects ints/strings)."""

    if isinstance(value, bool):
        return value
    raise MalformedEntryError(f"{where}: must be a boolean, got {value!r}")


def parse_entry(raw: object, index: int) -> CuratedEntry:
    """Build and validate a :class:`CuratedEntry` from one source mapping.

    ``human_signed`` defaults to ``False`` and ``ref`` to ``""`` when absent, so
    a later lint subtask can flag a missing signature. Raises
    :class:`MalformedEntryError` (naming the entry index) on any invalid entry.
    """

    if not isinstance(raw, Mapping):
        raise MalformedEntryError(
            f"entry #{index} must be a table, got {type(raw).__name__}"
        )

    missing = [name for name in REQUIRED_FIELDS if name not in raw]
    if missing:
        raise MalformedEntryError(
            f"entry #{index} is missing required field(s): {', '.join(missing)}"
        )

    human_signed = raw.get("human_signed", False)
    if human_signed is not True and human_signed is not False:
        human_signed = _coerce_bool(human_signed, f"entry #{index} human_signed")

    ref = raw.get("ref", "")
    if ref is not None and not isinstance(ref, str):
        ref = str(ref)

    entry = CuratedEntry(
        category=str(raw["category"]).strip(),
        app=str(raw["app"]).strip(),
        lane=str(raw["lane"]).strip(),
        ref=str(ref).strip() if ref else "",
        rationale=str(raw["rationale"]).strip(),
        human_signed=bool(human_signed),
    )
    return entry.validate()


def parse_entries(data: object) -> CuratedSet:
    """Build a :class:`CuratedSet` from an already-parsed data document.

    Accepts either a top-level array of entry tables, or a mapping with an
    ``entry`` array. Raises :class:`MalformedEntryError` on the first bad entry.
    """

    entries_raw: Sequence[object]
    if isinstance(data, Mapping):
        if "entry" not in data:
            raise MalformedEntryError("data document has no 'entry' list")
        entries_raw = data["entry"]
    elif isinstance(data, (list, tuple)):
        entries_raw = data
    else:
        raise MalformedEntryError(
            "data document must be a mapping with an 'entry' list or an array"
        )

    if not isinstance(entries_raw, (list, tuple)):
        raise MalformedEntryError("'entry' must be a list of entry tables")

    entries: list[CuratedEntry] = []
    for index, raw in enumerate(entries_raw):
        entries.append(parse_entry(raw, index))
    return CuratedSet(tuple(entries))


def _read_source(path: Path) -> dict:
    """Read a data file and parse it as TOML (falling back to JSON)."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LoadError(f"curated data file not found: {path}") from exc

    stripped = text.lstrip()
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        pass
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LoadError(f"curated data file is not valid TOML or JSON: {exc}") from exc


def load(path: object) -> CuratedSet:
    """Read and parse the curated set from ``path`` (TOML or JSON)."""

    parsed = _read_source(Path(path))
    try:
        return parse_entries(parsed)
    except MalformedEntryError as exc:
        raise LoadError(str(exc)) from exc


def load_default() -> CuratedSet:
    """Load the committed curated set (``curated.toml``)."""

    return load(DATA_FILE)


def to_json(data: CuratedSet) -> str:
    """Serialise a curated set to deterministic JSON (sorted keys)."""

    return json.dumps(
        [entry.as_dict() for entry in data],
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
