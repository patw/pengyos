"""The declarative PengyOS hardening baseline — the contract, not the authority.

The baseline is committed as **data** (``data/baseline.json``): a list of rules,
each with a stable ``id``, a ``severity``, a ``domain``, and a ``matcher`` that
describes *how* the rule is evaluated. The rest of the package is the reader:
:mod:`pengyos_hardening.check` consumes :class:`Rule` objects and turns a
matcher into concrete findings, but it never decides what *should* be hardened.

This module loads that data and **structurally validates** it. A broken
contract is a hard error (:class:`BaselineError`) — a malformed rule (missing
``id``, unknown ``severity``/``domain``, or a matcher that does not conform to
the schema) is rejected rather than silently tolerated.

See ``spec.md`` for the contract and ``data/baseline.json`` for the data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .errors import BaselineError

__all__ = [
    "KNOWN_DOMAINS",
    "KNOWN_SEVERITIES",
    "KNOWN_MATCHER_KINDS",
    "SEVERITY_ORDER",
    "Matcher",
    "Rule",
    "Baseline",
    "load_baseline",
    "DEFAULT_BASELINE_PATH",
]

# --- the fixed vocabulary of the contract --------------------------------

#: Domains a rule may belong to. This is the closed set the loader accepts;
#: a rule naming any other domain is malformed.
KNOWN_DOMAINS: frozenset[str] = frozenset(
    {"ssh", "sysctl", "firewall", "defconfig", "units"}
)

#: Severity levels, ordered most-to-least severe (see :data:`SEVERITY_ORDER`).
KNOWN_SEVERITIES: frozenset[str] = frozenset(
    {"critical", "high", "medium", "low", "info"}
)

#: Matcher kinds the schema understands. The ``check`` submodule implements a
#: matcher by dispatching on ``kind``; an unknown kind is a malformed rule.
KNOWN_MATCHER_KINDS: frozenset[str] = frozenset(
    {
        "key_value",       # sshd_config style last-wins key/value
        "sysctl_value",    # sysctl.d key/value
        "mount_option",    # fstab mount option
        "nft_policy",      # nftables chain policy
        "unit_enabled",    # systemd .wants symlink
        "symbol_absent",   # defconfig: symbol must NOT appear
        "symbol_present",  # defconfig: symbol MUST appear
    }
)

#: Ranking used to order findings by severity (lower sorts first).
SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

#: Per-matcher-kind, the param keys that must be present. The ``params`` object
#: may carry additional keys (future matchers), but every listed key is
#: mandatory for the given kind.
_MATCHER_PARAM_SPECS: dict[str, tuple[str, ...]] = {
    "key_value": ("file", "key", "expected"),
    "sysctl_value": ("files", "key", "expected"),
    "mount_option": ("file", "mountpoint", "option"),
    "nft_policy": ("file", "chain", "expected"),
    "unit_enabled": ("wants_dir", "unit"),
    "symbol_absent": ("files", "symbol"),
    "symbol_present": ("files", "symbol"),
}

#: Stable rule ids match ``lowercase-kebab-case``; anything else is malformed.
_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")

#: Location of the committed baseline data, relative to this file.
_DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_BASELINE_PATH = _DATA_DIR / "baseline.json"


@dataclass(frozen=True)
class Matcher:
    """A matcher describes *how* a rule is evaluated (kind + parameters)."""

    kind: str
    params: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Serialise back to the on-disk shape (``{"kind": ..., "params": ...}``)."""
        return {"kind": self.kind, "params": dict(self.params)}

    @classmethod
    def from_dict(cls, data: Any, *, where: str) -> "Matcher":
        if not isinstance(data, dict):
            raise BaselineError(f"{where}: matcher must be an object")
        kind = data.get("kind")
        if not isinstance(kind, str) or not kind:
            raise BaselineError(f"{where}: matcher is missing a string 'kind'")
        if kind not in KNOWN_MATCHER_KINDS:
            raise BaselineError(
                f"{where}: unknown matcher kind {kind!r} "
                f"(known: {', '.join(sorted(KNOWN_MATCHER_KINDS))})"
            )
        params = data.get("params")
        if not isinstance(params, dict):
            raise BaselineError(f"{where}: matcher.params must be an object")
        missing = [k for k in _MATCHER_PARAM_SPECS[kind] if k not in params]
        if missing:
            raise BaselineError(
                f"{where}: matcher kind {kind!r} is missing param(s): "
                f"{', '.join(missing)}"
            )
        return cls(kind=kind, params=params)


@dataclass(frozen=True)
class Rule:
    """A single hardening rule: a stable id, severity, domain, and matcher."""

    id: str
    severity: str
    domain: str
    matcher: Matcher
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise the rule to a plain dict."""
        return {
            "id": self.id,
            "severity": self.severity,
            "domain": self.domain,
            "description": self.description,
            "matcher": self.matcher.to_dict(),
        }

    @property
    def severity_rank(self) -> int:
        """Numeric rank for severity ordering (lower = more severe)."""
        return SEVERITY_ORDER.get(self.severity, len(SEVERITY_ORDER))

    @classmethod
    def from_dict(cls, data: Any, *, index: int) -> "Rule":
        where = f"rule #{index}"
        if not isinstance(data, dict):
            raise BaselineError(f"{where}: rule must be an object")

        rule_id = data.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            raise BaselineError(f"{where}: rule is missing a non-empty 'id'")
        if not _ID_RE.match(rule_id):
            raise BaselineError(
                f"{where}: id {rule_id!r} is not a stable kebab-case id "
                f"(expected ^[a-z][a-z0-9-]*$)"
            )

        severity = data.get("severity")
        if not isinstance(severity, str) or severity not in KNOWN_SEVERITIES:
            raise BaselineError(
                f"{where} ({rule_id!r}): severity must be one of "
                f"{', '.join(sorted(KNOWN_SEVERITIES))}"
            )

        domain = data.get("domain")
        if not isinstance(domain, str) or domain not in KNOWN_DOMAINS:
            raise BaselineError(
                f"{where} ({rule_id!r}): unknown domain {domain!r} "
                f"(known: {', '.join(sorted(KNOWN_DOMAINS))})"
            )

        matcher = Matcher.from_dict(data.get("matcher"), where=f"rule {rule_id!r}")

        description = data.get("description", "")
        if description and not isinstance(description, str):
            raise BaselineError(
                f"{where} ({rule_id!r}): 'description' must be a string"
            )

        return cls(
            id=rule_id,
            severity=severity,
            domain=domain,
            matcher=matcher,
            description=description,
        )


@dataclass
class Baseline:
    """The validated baseline: an ordered collection of :class:`Rule` objects."""

    rules: list[Rule] = field(default_factory=list)
    baseline_id: str = ""
    schema_version: int = 0

    def __post_init__(self) -> None:
        self._by_id: dict[str, Rule] = {}
        for rule in self.rules:
            if rule.id in self._by_id:
                raise BaselineError(
                    f"duplicate rule id {rule.id!r} in baseline"
                )
            self._by_id[rule.id] = rule

    def __len__(self) -> int:
        return len(self.rules)

    def by_id(self, rule_id: str) -> Rule:
        """Look up a rule by its stable id (raises ``KeyError`` if absent)."""
        return self._by_id[rule_id]

    def by_domain(self, domain: str) -> list[Rule]:
        """Return the rules in ``domain``, most-severe first."""
        if domain not in KNOWN_DOMAINS:
            raise ValueError(f"unknown domain {domain!r}")
        return sorted(
            (r for r in self.rules if r.domain == domain),
            key=lambda r: (r.severity_rank, r.id),
        )

    def ids(self) -> list[str]:
        """The stable rule ids, in declaration order."""
        return [r.id for r in self.rules]

    def to_dict(self) -> dict[str, Any]:
        """Serialise the whole baseline to a plain dict (for ``--json``)."""
        return {
            "schema_version": self.schema_version,
            "baseline_id": self.baseline_id,
            "rule_count": len(self.rules),
            "rules": [r.to_dict() for r in self.rules],
        }


def _coerce_rules(raw: Any) -> list[Any]:
    if not isinstance(raw, dict):
        raise BaselineError("baseline must be a JSON object of rules")
    rules = raw.get("rules")
    if not isinstance(rules, list):
        raise BaselineError("baseline.data.rules must be a list of rules")
    return rules


def load_baseline(path: str | Path | None = None) -> Baseline:
    """Load and structurally validate the committed baseline.

    :param path: optional override for the baseline data file. Defaults to the
        committed ``data/baseline.json``.
    :raises BaselineError: if the data is missing, not JSON, or structurally
        malformed (see :class:`BaselineError`).
    """
    if path is None:
        raw_bytes = DEFAULT_BASELINE_PATH.read_bytes()
    else:
        raw_bytes = Path(path).read_bytes()

    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise BaselineError(f"baseline is not valid JSON: {exc}") from exc

    rules_raw = _coerce_rules(document)

    schema_version = document.get("schema_version", 0)
    baseline_id = document.get("baseline_id", "")
    if not isinstance(schema_version, int):
        raise BaselineError("baseline.schema_version must be an integer")
    if not isinstance(baseline_id, str):
        raise BaselineError("baseline.baseline_id must be a string")

    rules: list[Rule] = []
    for index, rule_raw in enumerate(rules_raw, start=1):
        rules.append(Rule.from_dict(rule_raw, index=index))

    return Baseline(
        rules=rules,
        baseline_id=str(baseline_id),
        schema_version=schema_version,
    )


def load_baseline_from_json(text: str) -> Baseline:
    """Validate a baseline from an in-memory JSON string (used by tests)."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"baseline is not valid JSON: {exc}") from exc
    rules_raw = _coerce_rules(document)
    rules = [Rule.from_dict(r, index=i) for i, r in enumerate(rules_raw, start=1)]
    return Baseline(
        rules=rules,
        baseline_id=str(document.get("baseline_id", "")),
        schema_version=int(document.get("schema_version", 0)),
    )


def validate_rules(rules: Iterable[dict[str, Any]]) -> list[Rule]:
    """Validate a list of rule dicts, raising :class:`BaselineError` on the first problem."""
    return [Rule.from_dict(r, index=i) for i, r in enumerate(rules, start=1)]


def main(args: "Any") -> int:
    """CLI entry point for ``pengyos-hardening baseline [--json]``.

    Loads and validates the committed baseline and prints either a human
    summary or the JSON representation. Exits ``3`` on a malformed baseline so
    the CLI reports the gap cleanly instead of dumping a traceback.

    Reads ``--json``/``--path`` from the incoming argparse namespace (the
    top-level parser already defined ``--json`` on this subcommand) rather than
    re-parsing, so the dispatch keys ``command``/``handler`` are ignored.
    """
    import json
    import sys

    from .errors import BaselineError

    as_json = bool(getattr(args, "json", False))
    override_path = getattr(args, "path", None)

    try:
        baseline = load_baseline(override_path)
    except BaselineError as exc:
        print(f"pengyos-hardening: baseline is malformed: {exc}", file=sys.stderr)
        return 3

    if as_json:
        print(json.dumps(baseline.to_dict(), indent=2, sort_keys=True))
        return 0

    print(f"pengyOS hardening baseline — {baseline.baseline_id or 'unnamed'}")
    print(f"schema_version={baseline.schema_version}  rules={len(baseline)}")
    print("")
    for rule in baseline.rules:
        print(
            f"  [{rule.severity:<8}] {rule.domain:<9} "
            f"{rule.id}  ({rule.matcher.kind})"
        )
        if rule.description:
            print(f"             {rule.description}")
    return 0
