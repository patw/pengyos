"""The rollout data model — plan + journal, as committed, schema-validated data.

This module owns *what a rollout is*, before any of it is produced or consumed.
The later subtasks implement ``plan`` (which builds a :class:`RolloutPlan`),
``apply`` (which appends :class:`JournalEntry` records to a :class:`RolloutJournal`)
and ``rollback`` (which reads that journal) — but every one of them moves data
in and out of exactly the two shapes defined here. Keeping those shapes in one
place means the plan, the journal and the CLI all agree on the format.

Two documents are modelled:

* :class:`RolloutPlan` — the output of ``plan``: a built image id, the ordered,
  canary-first batches it will be rolled out in, each with an explicit health
  gate, and a rollback record naming every host that can be rolled back.
* :class:`RolloutJournal` — the output of ``apply``: one :class:`JournalEntry`
  per host, recording the prior artifact it had, the artifact it was rolled
  forward to, and its state. ``rollback`` reads this and restores each host's
  prior artifact.

Both are plain ``dict`` documents (``as_dict``) that validate against a small
hand-rolled JSON-schema-style validator (``validate``) and round-trip through
JSON (``from_dict`` / ``to_json`` / ``load`` / ``dump``). The schema is
declarative data too, so a plan or journal can be committed to the repo as a
fixture and inspected by a human. Standard library only.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# Constants shared by the plan and the journal.
# ---------------------------------------------------------------------------

#: The image id a :func:`pengyos_fleet_rollout.plan.plan` cannot resolve.
UNKNOWN_ARTIFACT = "UNKNOWN"

#: A host has never been touched by an apply (the plan's rollback template).
STATE_PENDING = "pending"
#: The transport applied the new artifact to the host successfully.
STATE_APPLIED = "applied"
#: The transport failed to apply the new artifact to the host (gate failed).
STATE_FAILED = "failed"
#: The transport restored the host's prior artifact (rollback step).
STATE_ROLLED_BACK = "rolled_back"

#: The only health-gate type so far: every host must reach ``require`` state.
GATE_ALL_HEALTHY = "all_healthy"

#: Schema-version markers, so a plan/journal can be recognised on load.
PLAN_SCHEMA = "pengyos-fleet-rollout/plan/v1"
JOURNAL_SCHEMA = "pengyos-fleet-rollout/journal/v1"


# ---------------------------------------------------------------------------
# A tiny JSON-schema-style validator (stdlib only).
#
# ``jsonschema`` is a third-party dependency; the project is stdlib-only at
# runtime and the scaffold locks an empty dependency list, so the rollout model
# validates itself against a minimal schema description. It covers exactly the
# subset the two documents need: ``object`` with required keys and typed/enum/
# nested properties, plus ``array`` of a single item schema. It is deliberately
# small — the goal is "fail loud on a malformed document", not a full schema
# engine.
# ---------------------------------------------------------------------------


class ModelError(ValueError):
    """A document did not validate against its schema.

    Carries a single, human-readable message naming the offending key and
    value, so a malformed plan/journal is easy to fix rather than a traceback.
    """


def _check_type(value: Any, expected: str, where: str) -> None:
    """Raise :class:`ModelError` unless ``value`` matches the ``expected`` type."""

    if expected == "string":
        ok = isinstance(value, str)
    elif expected == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif expected == "number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected == "boolean":
        ok = isinstance(value, bool)
    elif expected == "array":
        ok = isinstance(value, list)
    elif expected == "object":
        ok = isinstance(value, dict)
    else:
        raise ModelError(f"{where}: unknown type {expected!r}")
    if not ok:
        raise ModelError(f"{where}: expected {expected}, got {type(value).__name__}")


def _validate_property(value: Any, spec: Any, where: str) -> None:
    """Validate ``value`` against a single property ``spec``.

    A ``None`` value is treated as "optional / not set": optional fields that
    are absent round-trip through ``as_dict`` as ``None`` and must validate
    cleanly, so ``None`` short-circuits every check below.
    """

    if value is None:
        return

    if not isinstance(spec, dict):
        # A bare type string, e.g. {"host": "string"}.
        _check_type(value, spec, where)
        return

    if "type" in spec:
        _check_type(value, spec["type"], where)

    if "enum" in spec and value not in spec["enum"]:
        raise ModelError(
            f"{where}: {value!r} is not one of {spec['enum']}"
        )

    if isinstance(value, dict) and "properties" in spec:
        for key, prop_spec in spec["properties"].items():
            if key in value:
                _validate_property(value[key], prop_spec, f"{where}.{key}")
        for req in spec.get("required", ()):
            if req not in value or value[req] is None:
                raise ModelError(f"{where}: missing required key {req!r}")

    if isinstance(value, list) and "items" in spec:
        for index, item in enumerate(value):
            _validate_property(item, spec["items"], f"{where}[{index}]")


def validate(document: Mapping[str, Any], schema: Mapping[str, Any]) -> None:  # type: ignore[name-defined]
    """Validate ``document`` against ``schema`` in place, raising on mismatch.

    ``schema`` is a minimal JSON-schema subset: an object with a ``type`` of
    ``"object"``, a list of ``required`` keys, and ``properties`` (each itself a
    schema). Arrays validate each element against ``items``. Enumerations are
    enforced via an ``enum`` key. Anything else raises :class:`ModelError` with
    a message pointing at the offending key.
    """

    if not isinstance(document, dict):
        raise ModelError("document must be an object")
    _validate_property(document, schema, "$")


# ---------------------------------------------------------------------------
# The schema documents themselves.
# ---------------------------------------------------------------------------

#: The schema a :class:`RolloutPlan` validates against.
PLAN_SCHEMA_DOCUMENT: dict[str, Any] = {
    "type": "object",
    "required": ["schema", "image", "batches", "rollback"],
    "properties": {
        "schema": {"type": "string"},
        "image": {"type": "string"},
        "inventory": {"type": "string"},
        "batches": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["batch", "hosts", "gate"],
                "properties": {
                    "batch": {"type": "integer"},
                    "hosts": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "gate": {
                        "type": "object",
                        "required": ["type", "require"],
                        "properties": {
                            "type": {"enum": [GATE_ALL_HEALTHY]},
                            "require": {
                                "type": "array",
                                "items": {"enum": [STATE_APPLIED]},
                            },
                        },
                    },
                },
            },
        },
        "rollback": {
            "type": "object",
            "required": ["hosts"],
            "properties": {
                "hosts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["host", "prior", "new"],
                        "properties": {
                            "host": {"type": "string"},
                            "prior": {"type": "string"},
                            "new": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
}

#: The schema a :class:`RolloutJournal` validates against.
JOURNAL_SCHEMA_DOCUMENT: dict[str, Any] = {
    "type": "object",
    "required": ["schema", "entries"],
    "properties": {
        "schema": {"type": "string"},
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["host", "prior", "new"],
                "properties": {
                    "host": {"type": "string"},
                    "prior": {"type": "string"},
                    "new": {"type": "string"},
                    "state": {"enum": [
                        STATE_PENDING,
                        STATE_APPLIED,
                        STATE_FAILED,
                        STATE_ROLLED_BACK,
                    ]},
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# The health gate.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HealthGate:
    """The condition a batch must satisfy before the rollout proceeds.

    Attributes:
        kind: The gate type. Currently only :data:`GATE_ALL_HEALTHY`.
        require: The per-host state(s) that count as "healthy". A batch is
            healthy when every one of its hosts is in one of these states.
    """

    kind: str = GATE_ALL_HEALTHY
    require: tuple[str, ...] = field(default_factory=lambda: (STATE_APPLIED,))

    def as_dict(self) -> dict[str, Any]:
        """Return the gate as a plain dict for serialisation."""

        return {"type": self.kind, "require": list(self.require)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HealthGate":
        """Build a gate from a validated ``{"type", "require"}`` dict."""

        if not isinstance(data, dict):
            raise ModelError("gate must be an object")
        kind = data.get("type", GATE_ALL_HEALTHY)
        if kind != GATE_ALL_HEALTHY:
            raise ModelError(f"unknown gate type {kind!r}")
        require = tuple(data.get("require", (STATE_APPLIED,)))
        for state in require:
            if state not in (STATE_APPLIED,):
                raise ModelError(
                    f"gate require {state!r} is not one of "
                    f"{[STATE_APPLIED]}"
                )
        return cls(kind=kind, require=require)


# ---------------------------------------------------------------------------
# The per-batch record.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Batch:
    """One rollout batch: an ordered group of hosts plus its health gate.

    Attributes:
        index: The batch's position in the rollout (0 is the canary).
        hosts: The host names in this batch, in rollout order.
        gate: The health gate that must pass before the next batch starts.
    """

    index: int
    hosts: tuple[str, ...]
    gate: HealthGate = field(default_factory=HealthGate)

    def as_dict(self) -> dict[str, Any]:
        """Return the batch as a plain dict for serialisation."""

        return {
            "batch": self.index,
            "hosts": list(self.hosts),
            "gate": self.gate.as_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Batch":
        """Build a batch from a validated ``{"batch", "hosts", "gate"}`` dict."""

        if not isinstance(data, dict):
            raise ModelError("batch must be an object")
        index = data.get("batch")
        _check_type(index, "integer", "batch.batch")
        hosts = tuple(data.get("hosts", ()))
        for h in hosts:
            _check_type(h, "string", "batch.hosts")
        gate_data = data.get("gate", {})
        gate = HealthGate.from_dict(gate_data)
        return cls(index=index, hosts=hosts, gate=gate)


# ---------------------------------------------------------------------------
# The rollback record (live, per-host).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RollbackEntry:
    """One host's rollback record: what it had, what it was rolled forward to.

    Attributes:
        host: The host name (also the rollback target id).
        prior: The artifact the host currently has (or had). Filled in by
            ``apply``; the plan's template leaves it as :data:`UNKNOWN_ARTIFACT`.
        new: The artifact it was rolled forward to (the plan's image id).
    """

    host: str
    prior: str = UNKNOWN_ARTIFACT
    new: str = UNKNOWN_ARTIFACT

    def as_dict(self) -> dict[str, Any]:
        """Return the entry as a plain dict for serialisation."""

        return {"host": self.host, "prior": self.prior, "new": self.new}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RollbackEntry":
        """Build an entry from a validated ``{"host", "prior", "new"}`` dict."""

        if not isinstance(data, dict):
            raise ModelError("rollback entry must be an object")
        host = data.get("host")
        _check_type(host, "string", "rollback.host")
        prior = data.get("prior", UNKNOWN_ARTIFACT)
        new = data.get("new", UNKNOWN_ARTIFACT)
        _check_type(prior, "string", "rollback.prior")
        _check_type(new, "string", "rollback.new")
        return cls(host=host, prior=prior, new=new)


# ---------------------------------------------------------------------------
# The rollout plan.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RolloutPlan:
    """A staged rollout plan: an image, ordered batches, and a rollback record.

    Attributes:
        image: The built PengyOS image id (or pending update set) being rolled
            out.
        inventory: The path to the source inventory, for provenance (optional).
        batches: The ordered, canary-first batches. ``batches[0]`` is the
            canary.
        rollback: The rollback record — one :class:`RollbackEntry` per host in
            the plan (``prior`` is a placeholder until ``apply`` fills it in).
    """

    image: str
    batches: tuple[Batch, ...] = field(default_factory=tuple)
    rollback: tuple[RollbackEntry, ...] = field(default_factory=tuple)
    inventory: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the plan as a plain dict, ready for JSON serialisation."""

        return {
            "schema": PLAN_SCHEMA,
            "image": self.image,
            "inventory": self.inventory,
            "batches": [b.as_dict() for b in self.batches],
            "rollback": {"hosts": [r.as_dict() for r in self.rollback]},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RolloutPlan":
        """Build a plan from a dict, validating it against the plan schema."""

        validate(dict(data), PLAN_SCHEMA_DOCUMENT)
        batches = tuple(Batch.from_dict(b) for b in data.get("batches", ()))
        rollback = tuple(
            RollbackEntry.from_dict(r)
            for r in data.get("rollback", {}).get("hosts", ())
        )
        return cls(
            image=data["image"],
            inventory=data.get("inventory"),
            batches=batches,
            rollback=rollback,
        )

    def hosts(self) -> tuple[str, ...]:
        """Every host in the plan, in rollout order (flattened across batches)."""

        return tuple(h for batch in self.batches for h in batch.hosts)

    def rollback_by_host(self) -> dict[str, RollbackEntry]:
        """The rollback record keyed by host, for O(1) lookup during apply."""

        return {entry.host: entry for entry in self.rollback}

    def canary(self) -> tuple[str, ...]:
        """The first (canary) batch's hosts — the smallest first step."""

        return self.batches[0].hosts if self.batches else ()

    def render_text(self) -> str:
        """Render the plan as a human-readable, line-oriented summary.

        One line per batch (its index, hosts and gate), then the per-host
        rollback record — enough to eyeball the rollout order and the rollback
        targets without parsing JSON.
        """

        lines: list[str] = [
            f"PengyOS fleet rollout plan",
            f"image:  {self.image}",
        ]
        if self.inventory:
            lines.append(f"inventory: {self.inventory}")
        if not self.batches:
            lines.append("batches: (none — the fleet is empty)")
        for batch in self.batches:
            lines.append(
                f"batch {batch.index}: [{', '.join(batch.hosts)}] "
                f"gate={batch.gate.kind}(require={','.join(batch.gate.require)})"
            )
        if not self.rollback:
            lines.append("rollback: (none)")
        else:
            lines.append("rollback:")
            for entry in self.rollback:
                lines.append(
                    f"  {entry.host}: prior={entry.prior} new={entry.new}"
                )
        return "\n".join(lines)

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialise the plan to JSON text."""

        return json.dumps(self.as_dict(), indent=indent, sort_keys=True)

    @classmethod
    def load(cls, path: str | Path) -> "RolloutPlan":
        """Load and validate a plan from a JSON file on disk."""

        file_path = Path(path)
        if not file_path.is_file():
            raise ModelError(f"plan not found: {file_path}")
        try:
            with file_path.open("rb") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ModelError(f"invalid JSON in {file_path}: {exc}") from exc
        return cls.from_dict(data)

    def dump(self, path: str | Path) -> Path:
        """Serialise the plan and write it to ``path``. Returns the path."""

        file_path = Path(path)
        file_path.write_text(self.to_json(), encoding="utf-8")
        return file_path


# ---------------------------------------------------------------------------
# The per-host journal entry.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JournalEntry:
    """One host's rollout record: prior artifact, new artifact, and state.

    Attributes:
        host: The host name (also the rollback target id).
        prior: The artifact the host had before this rollout step.
        new: The artifact the host was rolled forward to.
        state: The host's current state (:data:`STATE_APPLIED`,
            :data:`STATE_FAILED`, :data:`STATE_ROLLED_BACK`, or
            :data:`STATE_PENDING`).
        at: The UTC timestamp of the record (defaults to now on construction).
    """

    host: str
    prior: str
    new: str
    state: str = STATE_APPLIED
    at: str = ""

    def __post_init__(self) -> None:
        if not self.at:
            object.__setattr__(
                self,
                "at",
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )

    def as_dict(self) -> dict[str, Any]:
        """Return the entry as a plain dict for serialisation."""

        return {
            "host": self.host,
            "prior": self.prior,
            "new": self.new,
            "state": self.state,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "JournalEntry":
        """Build an entry from a validated ``{"host", "prior", "new", "state"}`` dict."""

        if not isinstance(data, dict):
            raise ModelError("journal entry must be an object")
        host = data.get("host")
        prior = data.get("prior")
        new = data.get("new")
        state = data.get("state", STATE_APPLIED)
        for name, value in (("host", host), ("prior", prior), ("new", new)):
            _check_type(value, "string", f"entry.{name}")
        if state not in (STATE_PENDING, STATE_APPLIED, STATE_FAILED, STATE_ROLLED_BACK):
            raise ModelError(
                f"entry.state {state!r} is not one of "
                f"{[STATE_PENDING, STATE_APPLIED, STATE_FAILED, STATE_ROLLED_BACK]}"
            )
        at = data.get("at", "")
        return cls(host=host, prior=prior, new=new, state=state, at=at)


# ---------------------------------------------------------------------------
# The rollout journal.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RolloutJournal:
    """The rollout journal: one :class:`JournalEntry` per touched host.

    ``apply`` appends an entry before each host; ``rollback`` rewrites the
    entries of the hosts it restores. The journal is declarative data — it can
    be committed to the repo and inspected by a human.

    Attributes:
        entries: The recorded entries, in the order they were written.
    """

    entries: tuple[JournalEntry, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        """Return the journal as a plain dict, ready for JSON serialisation."""

        return {"schema": JOURNAL_SCHEMA, "entries": [e.as_dict() for e in self.entries]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RolloutJournal":
        """Build a journal from a dict, validating it against the journal schema."""

        validate(dict(data), JOURNAL_SCHEMA_DOCUMENT)
        entries = tuple(JournalEntry.from_dict(e) for e in data.get("entries", ()))
        return cls(entries=entries)

    def hosts(self) -> tuple[str, ...]:
        """Every host that has a journal entry, in first-occurrence order.

        A host may appear in more than one entry (e.g. ``applied`` then
        ``rolled_back`` during a rollback), so this is de-duplicated on first
        sight — the set of hosts, in the order they were first touched.
        """

        seen: set[str] = set()
        ordered: list[str] = []
        for entry in self.entries:
            if entry.host not in seen:
                seen.add(entry.host)
                ordered.append(entry.host)
        return tuple(ordered)

    def entries_for(self, host: str) -> tuple[JournalEntry, ...]:
        """All journal entries for ``host``, in write order (may be > 1)."""

        return tuple(e for e in self.entries if e.host == host)

    def latest_for(self, host: str) -> JournalEntry | None:
        """The most recent entry for ``host``, or ``None`` if the host is absent."""

        recent = self.entries_for(host)
        return recent[-1] if recent else None

    def applied_hosts(self) -> tuple[str, ...]:
        """Hosts whose latest entry is :data:`STATE_APPLIED` (roll-forward done)."""

        return tuple(
            host for host in self.hosts() if self.latest_for(host).state == STATE_APPLIED
        )

    def rolled_back_hosts(self) -> tuple[str, ...]:
        """Hosts whose latest entry is :data:`STATE_ROLLED_BACK` (fully reversed)."""

        return tuple(
            host for host in self.hosts() if self.latest_for(host).state == STATE_ROLLED_BACK
        )

    def append(self, entry: JournalEntry) -> "RolloutJournal":
        """Return a new journal with ``entry`` added (the journal is immutable)."""

        return RolloutJournal(entries=self.entries + (entry,))

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialise the journal to JSON text."""

        return json.dumps(self.as_dict(), indent=indent, sort_keys=True)

    @classmethod
    def load(cls, path: str | Path) -> "RolloutJournal":
        """Load and validate a journal from a JSON file on disk."""

        file_path = Path(path)
        if not file_path.is_file():
            raise ModelError(f"journal not found: {file_path}")
        try:
            with file_path.open("rb") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ModelError(f"invalid JSON in {file_path}: {exc}") from exc
        return cls.from_dict(data)

    def dump(self, path: str | Path) -> Path:
        """Serialise the journal and write it to ``path``. Returns the path."""

        file_path = Path(path)
        file_path.write_text(self.to_json(), encoding="utf-8")
        return file_path


# ---------------------------------------------------------------------------
# The inventory loader (stdlib ``tomllib``).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InventoryHost:
    """One host in the source inventory.

    Attributes:
        name: Logical machine name; also the rollout target id.
        role: The host's role (e.g. ``canary`` / ``edge`` / ``core``), or
            ``None`` when unset.
        priority: An integer ordering hint, or ``None`` when unset.
        address: A reachability target forwarded to the transport, or ``None``.
    """

    name: str
    role: str | None = None
    priority: int | None = None
    address: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the host as a plain dict (only set fields are included)."""

        data: dict[str, Any] = {"name": self.name}
        if self.role is not None:
            data["role"] = self.role
        if self.priority is not None:
            data["priority"] = self.priority
        if self.address is not None:
            data["address"] = self.address
        return data


def load_inventory(path: str | Path) -> list[InventoryHost]:
    """Load and validate a ``pengyos-fleet-inventory`` fleet description.

    Reads the TOML document, expects a top-level ``[[hosts]]`` array of tables,
    and returns one :class:`InventoryHost` per entry. Raises :class:`ModelError`
    (a ``ValueError`` subclass) for a missing file, invalid TOML, a missing
    ``hosts`` array, or a malformed / field-less entry.

    This is a strict loader: a bad inventory fails loudly rather than producing
    a silently-wrong plan. The schema-conformance detail (which fields the
    inventory tool actually uses) lives in the ``plan`` subtask; here we only
    require the ``name`` that every rollout targets.
    """

    file_path = Path(path)
    if not file_path.is_file():
        raise ModelError(f"inventory not found: {file_path}")

    try:
        with file_path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ModelError(f"invalid TOML in {file_path}: {exc}") from exc
    except OSError as exc:
        raise ModelError(f"cannot read inventory {file_path}: {exc}") from exc

    entries = data.get("hosts", [])
    if not isinstance(entries, list):
        raise ModelError(
            f"{file_path}: 'hosts' must be an array of tables ([[hosts]]), "
            f"got {type(entries).__name__}"
        )

    hosts: list[InventoryHost] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ModelError(f"{file_path}[{index}]: expected a table")

        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ModelError(
                f"{file_path}[{index}]: missing required string field 'name'"
            )

        role = entry.get("role")
        if role is not None and (not isinstance(role, str) or not role.strip()):
            raise ModelError(
                f"{file_path}[{index}] ({name!r}): 'role' must be a non-empty string"
            )

        priority = entry.get("priority")
        if priority is not None:
            if isinstance(priority, bool) or not isinstance(priority, int):
                raise ModelError(
                    f"{file_path}[{index}] ({name!r}): 'priority' must be an integer"
                )

        address = entry.get("address")
        if address is not None and (not isinstance(address, str) or not address.strip()):
            raise ModelError(
                f"{file_path}[{index}] ({name!r}): 'address' must be a non-empty string"
            )

        hosts.append(
            InventoryHost(name=name, role=role, priority=priority, address=address)
        )

    if not hosts:
        raise ModelError(f"{file_path}: defines no '[[hosts]]' entries")

    return hosts
