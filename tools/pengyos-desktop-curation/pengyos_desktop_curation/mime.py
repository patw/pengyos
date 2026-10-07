"""Emit the .desktop/mime integration plan.

The **.desktop/mime integration plan**
— *which everyday category integrates how* — as committed, declarative data.

The curated set (``pengyos_desktop_curation.data``) says *which* apps are the
defaults. This module says *how* each one is wired into the freedesktop
default-application machinery system-wide:

* the ``.desktop`` id that becomes the default (e.g. ``org.gnome.Files.desktop``),
* the MIME types it is the default for (e.g. ``x-scheme-handler/https``), and
* the **mechanism** the association is applied through (one of
  :data:`MECHANISMS`: ``mimeapps_list`` / ``xdg_desktop_dirs`` / ``systemd_mime``).

The plan is emitted as a real ``mimeapps.list`` (the freedesktop
``XDG_CONFIG_HOME/mimeapps.list`` / ``/etc/xdg/mimeapps.list`` INI file that
``xdg-mime`` reads) plus a machine-readable JSON plan.

Design
------
* :data:`MECHANISMS` — the integration mechanisms a curated entry may use.
* :class:`IntegrationEntry` — one everyday category's integration, an immutable
  record.
* :class:`IntegrationPlan` — the ordered collection of entries, with the queries
  the rest of the tool needs (by category, by mechanism, the ``mimeapps.list``
  payload).
* :func:`load` / :func:`load_default` — read the committed
  ``mime-integration.toml``.
* :func:`mimeapps_list` / :func:`plan_json` — the pure emitters (no I/O).

The plan is human-authored data (spec Principle 8): every entry carries a
human-authored ``rationale`` and ``human_signed = true``. The tool never chooses
the integration unaided — it only stores and emits what a human has ratified.

Standard library only.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

# The integration mechanisms: how a category's default app is applied
# system-wide on a freedesktop/Linux desktop.
#
#   mimeapps_list  — the XDG mimeapps.list INI file (xdg-mime's source of truth
#                    for default associations; the emitted artifact).
#   xdg_desktop_dirs — advertise a directory of .desktop files via
#                    XDG_DESKTOP_DIRS so the apps are discoverable.
#   systemd_mime   — the systemd `systemd-XDG-MIME-apps-dirs` drop-in form.
MECHANISMS: tuple[str, ...] = ("mimeapps_list", "xdg_desktop_dirs", "systemd_mime")

# Fields every entry must carry. ``human_signed`` and ``ref`` have defaults (see
# :func:`parse_entry`) so a later lint subtask can lint a *missing* signature
# rather than having the loader reject it outright.
REQUIRED_FIELDS: tuple[str, ...] = ("category", "app", "desktop_id", "rationale")

# The committed integration plan. See the file for the human-authored entries.
PLAN_FILE = Path(__file__).resolve().parents[1] / "mime-integration.toml"


class IntegrationError(Exception):
    """Base error for the integration-plan layer."""


class MalformedPlanError(IntegrationError):
    """An integration entry is missing a required field or has an invalid value."""


class PlanLoadError(IntegrationError):
    """The committed integration-plan file could not be read or parsed."""


@dataclass(frozen=True)
class IntegrationEntry:
    """One everyday category's .desktop/mime integration.

    Fields follow the natural order: ``category -> app -> desktop_id -> ref ->
    mechanism -> mime_types -> rationale -> human_signed``.

    * ``desktop_id`` is the ``.desktop`` id that becomes the default (e.g.
      ``org.gnome.Files.desktop``).
    * ``ref`` is the Flathub ref (for the ``flatpak`` lane; matches the curated
      set's ref). Optional — empty for non-flatpak lanes.
    * ``mechanism`` is how the association is applied system-wide (one of
      :data:`MECHANISMS`; defaults to ``mimeapps_list``).
    * ``mime_types`` is every MIME type this app is the default for.
    * ``human_signed`` records that a human ratified the entry (spec Principle 8).
    """

    category: str
    app: str
    desktop_id: str
    mime_types: tuple[str, ...] = field(default_factory=tuple)
    ref: str = ""
    mechanism: str = "mimeapps_list"
    rationale: str = ""
    human_signed: bool = False

    def as_dict(self) -> dict[str, object]:
        """Return the entry as a plain dict, in field order."""

        return {
            "category": self.category,
            "app": self.app,
            "desktop_id": self.desktop_id,
            "ref": self.ref,
            "mechanism": self.mechanism,
            "mime_types": list(self.mime_types),
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

    def validate(self) -> "IntegrationEntry":
        """Assert this entry is well-formed; return it, or raise.

        Raises :class:`MalformedPlanError` if a required field is missing or
        empty, if ``mechanism`` is not one of :data:`MECHANISMS`, or if a
        ``mime_types`` value is empty/whitespace.
        """

        for name in REQUIRED_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise MalformedPlanError(
                    f"entry {self.app!r} is missing required field {name!r}"
                )
        if self.mechanism not in MECHANISMS:
            raise MalformedPlanError(
                f"entry {self.app!r}: mechanism {self.mechanism!r} is not one of "
                f"{MECHANISMS}"
            )
        for mt in self.mime_types:
            if not isinstance(mt, str) or not mt.strip():
                raise MalformedPlanError(
                    f"entry {self.app!r}: a mime_type must be a non-empty string"
                )
        return self


@dataclass
class IntegrationPlan:
    """The ordered collection of integration entries.

    Iteration order is the order the entries appear in the source data, so the
    plan is deterministic and stable across runs.
    """

    entries: tuple[IntegrationEntry, ...] = field(default_factory=tuple)

    def __iter__(self) -> Iterator[IntegrationEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, index: int) -> IntegrationEntry:
        return self.entries[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, IntegrationPlan):
            return self.entries == other.entries
        return NotImplemented

    def categories(self) -> list[str]:
        """The categories present, in first-seen order (de-duplicated)."""

        seen: list[str] = []
        for entry in self.entries:
            if entry.category not in seen:
                seen.append(entry.category)
        return seen

    def by_category(self, category: str) -> list[IntegrationEntry]:
        """Every entry in ``category`` (source order)."""

        return [entry for entry in self.entries if entry.category == category]

    def mechanisms(self) -> list[str]:
        """The mechanisms present, in first-seen order (de-duplicated)."""

        seen: list[str] = []
        for entry in self.entries:
            if entry.mechanism not in seen:
                seen.append(entry.mechanism)
        return seen

    def desktop_ids(self) -> list[str]:
        """The .desktop ids, in source order (de-duplicated)."""

        seen: list[str] = []
        for entry in self.entries:
            if entry.desktop_id not in seen:
                seen.append(entry.desktop_id)
        return seen

    def validate(self) -> "IntegrationPlan":
        """Validate every entry; return ``self`` or raise.

        Raises :class:`MalformedPlanError` on the first malformed entry.
        """

        for index, entry in enumerate(self.entries):
            try:
                entry.validate()
            except MalformedPlanError as exc:
                raise MalformedPlanError(
                    f"entry #{index} ({entry.app!r}): {exc}"
                ) from exc
        return self


def _coerce_bool(value: object, where: str) -> bool:
    """Coerce a data value to a strict ``bool`` (rejects ints/strings)."""

    if isinstance(value, bool):
        return value
    raise MalformedPlanError(f"{where}: must be a boolean, got {value!r}")


def _coerce_str_list(value: object, where: str) -> tuple[str, ...]:
    """Coerce a data value to a tuple of non-empty strings (rejects non-lists
    and non-string items; empty/whitespace items are trimmed and later rejected
    by :meth:`IntegrationEntry.validate`)."""

    if not isinstance(value, list):
        raise MalformedPlanError(f"{where}: must be a list of strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise MalformedPlanError(
                f"{where}: must be a list of strings, got {item!r}"
            )
        items.append(item.strip())
    return tuple(items)


def parse_entry(raw: object, index: int) -> IntegrationEntry:
    """Build and validate an :class:`IntegrationEntry` from one source mapping.

    ``human_signed`` defaults to ``False``, ``ref`` to ``""``, ``mechanism`` to
    ``mimeapps_list``, and ``mime_types`` to ``()`` — so a later lint subtask
    can flag a missing signature rather than having the loader reject it. Raises
    :class:`MalformedPlanError` (naming the entry index) on any invalid entry.
    """

    if not isinstance(raw, dict):
        raise MalformedPlanError(
            f"entry #{index} must be a table, got {type(raw).__name__}"
        )

    missing = [name for name in REQUIRED_FIELDS if name not in raw]
    if missing:
        raise MalformedPlanError(
            f"entry #{index} is missing required field(s): {', '.join(missing)}"
        )

    human_signed = raw.get("human_signed", False)
    if human_signed is not True and human_signed is not False:
        human_signed = _coerce_bool(
            human_signed, f"entry #{index} human_signed"
        )

    ref = raw.get("ref", "")
    if ref is not None and not isinstance(ref, str):
        ref = str(ref)

    mechanism = raw.get("mechanism", "mimeapps_list")
    if mechanism is not None and not isinstance(mechanism, str):
        mechanism = str(mechanism)

    mime_types = _coerce_str_list(
        raw.get("mime_types", []), f"entry #{index} mime_types"
    )

    entry = IntegrationEntry(
        category=str(raw["category"]).strip(),
        app=str(raw["app"]).strip(),
        desktop_id=str(raw["desktop_id"]).strip(),
        mime_types=mime_types,
        ref=str(ref).strip() if ref else "",
        mechanism=str(mechanism).strip() or "mimeapps_list",
        rationale=str(raw["rationale"]).strip(),
        human_signed=bool(human_signed),
    )
    return entry.validate()


def parse_plan(data: object) -> IntegrationPlan:
    """Build an :class:`IntegrationPlan` from an already-parsed data document.

    Accepts either a top-level array of entry tables, or a mapping with an
    ``entry`` array. Raises :class:`MalformedPlanError` on the first bad entry.
    """

    entries_raw: Iterable[object]
    if isinstance(data, dict):
        if "entry" not in data:
            raise MalformedPlanError("data document has no 'entry' list")
        entries_raw = data["entry"]
    elif isinstance(data, (list, tuple)):
        entries_raw = data
    else:
        raise MalformedPlanError(
            "data document must be a mapping with an 'entry' list or an array"
        )

    entries: list[IntegrationEntry] = []
    for index, raw in enumerate(entries_raw):
        entries.append(parse_entry(raw, index))
    return IntegrationPlan(tuple(entries))


def _read_source(path: Path) -> dict:
    """Read a plan file and parse it as TOML (falling back to JSON)."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanLoadError(f"integration plan file not found: {path}") from exc

    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        pass
    import json

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanLoadError(
            f"integration plan file is not valid TOML or JSON: {exc}"
        ) from exc


def load(path: object) -> IntegrationPlan:
    """Read and parse the integration plan from ``path`` (TOML or JSON)."""

    parsed = _read_source(Path(path))
    try:
        return parse_plan(parsed)
    except MalformedPlanError as exc:
        raise PlanLoadError(str(exc)) from exc


def load_default() -> IntegrationPlan:
    """Load the committed integration plan (``mime-integration.toml``)."""

    return load(PLAN_FILE)


def _default_header() -> str:
    """The header comment block written above the ``[Default Applications]``
    section of the emitted ``mimeapps.list``."""

    return (
        "# mimeapps.list emitted by pengyos-desktop-curation.\n"
        "#\n"
        "# The freedesktop default-application associations for the curated\n"
        "# PengyOS Desktop set. xdg-mime reads this file (from\n"
        "# $XDG_CONFIG_HOME/mimeapps.list or /etc/xdg/mimeapps.list) to decide the\n"
        "# default handler for each MIME type. Two sections are emitted:\n"
        "#\n"
        "#   [Default Applications]  mime-type = app.desktop\n"
        "#   [Added Applications]    app.desktop = type1;type2;\n"
        "#\n"
        "# Each row is human-authored (spec Principle 8): the curated app that is\n"
        "# the default for a category, the .desktop id it maps to, and the MIME\n"
        "# types it covers. See mime-integration.toml for the source plan.\n"
    )


def _default_app_rows(plan: IntegrationPlan) -> list[str]:
    """The ``[Default Applications]`` rows: ``mime_type = desktop_id``.

    One row per (MIME type, desktop id) pair, in source order, sorted by MIME
    type within each entry for a stable, diff-friendly file.
    """

    rows: list[str] = []
    for entry in plan.entries:
        for mime_type in sorted(entry.mime_types):
            rows.append(f"{mime_type}={entry.desktop_id}")
    return rows


def _added_app_rows(plan: IntegrationPlan) -> list[str]:
    """The ``[Added Applications]`` rows: ``desktop_id = type1;type2;``.

    One row per entry, listing every MIME type it is the default for, sorted.
    Trailing ``;`` per the freedesktop spec.
    """

    rows: list[str] = []
    for entry in plan.entries:
        types = sorted(entry.mime_types)
        if not types:
            continue
        rows.append(f"{entry.desktop_id}=" + ";".join(types) + ";")
    return rows


def mimeapps_list(plan: IntegrationPlan) -> str:
    """Render the integration plan as a ``mimeapps.list`` (INI) document.

    Deterministic and hand-formatted (not ``tomllib``/``json``-dumped) so the
    output is stable and readable across runs and edits. Emits the two sections
    ``xdg-mime`` reads:

    * ``[Default Applications]`` — ``mime_type = desktop_id`` (the default for
      each MIME type), and
    * ``[Added Applications]`` — ``desktop_id = type1;type2;`` (the reverse
      mapping, per the freedesktop spec).

    An empty plan yields just the header comment (no empty sections).
    """

    lines: list[str] = [_default_header().rstrip("\n")]
    default_rows = _default_app_rows(plan)
    added_rows = _added_app_rows(plan)
    if default_rows:
        lines.append("")
        lines.append("[Default Applications]")
        lines.extend(default_rows)
    if added_rows:
        lines.append("")
        lines.append("[Added Applications]")
        lines.extend(added_rows)
    return "\n".join(lines) + "\n"


def plan_json(plan: IntegrationPlan) -> str:
    """Serialise the integration plan to deterministic JSON (sorted keys)."""

    return json.dumps(
        [entry.as_dict() for entry in plan],
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )


def build_plan(entries: Iterable[object]) -> IntegrationPlan:
    """Build an :class:`IntegrationPlan` from raw entry mappings."""

    return parse_plan(list(entries))
