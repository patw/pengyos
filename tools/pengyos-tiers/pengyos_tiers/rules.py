"""The declarative rules file: glob paths + action types → tier.

The rules file is a TOML document holding an array of ``[[rule]]`` tables.
Each rule maps one or more glob ``path`` patterns to a ``tier`` (0, 1, or 2),
optionally narrowed by an ``action`` filter and/or a ``target`` name. The
classifier (task 3) walks the rules in file order and takes the first match;
anything that matches no rule is default-denied to Tier 2.

Schema (per ``[[rule]]`` entry):

- ``tier``      required, integer in {0, 1, 2}. The policy tier to assign.
- ``path``      required, a string or array of non-empty glob patterns.
- ``action``    optional, a string or array of lowercase identifiers. When
                present, the change's ``action`` must be one of them.
- ``target``    optional string. When present, the change's ``target`` must
                equal it (exact match).
- ``name``      optional string, a human label echoed in reports.

The first rule that matches a change wins (file order = precedence).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .errors import RulesError
from .change import _ACTION_RE

#: The tiers the engine knows about, in ascending strictness.
TIER_VALUES = (0, 1, 2)

#: Default tier for an unmatched change (fail safe / default-deny).
DEFAULT_DENY_TIER = 2


@lru_cache(maxsize=None)
def _glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """Translate a glob ``path`` pattern into a full-match regex.

    Cached: ``Rule.matches`` and the classifier share this single engine.

    This is the *single* glob engine for the whole project: ``Rule.matches``
    and the classifier both match against it, so they can never diverge.

    Wildcards are ``fnmatch``-style but with one deliberate policy-friendly
    change: ``*`` matches *any* run of characters **including ``/``**, so
    ``/etc/*`` covers ``/etc/foo`` and ``/etc/foo/bar`` alike. ``?`` matches a
    single character and ``[seq]`` is a character class; every other
    character is matched literally.
    """

    out = ["^"]
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            out.append(".*")
            i += 1
        elif c == "?":
            out.append(".")
            i += 1
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:  # unmatched '[' -> literal
                out.append(re.escape("["))
                i += 1
            else:
                inside = pattern[i + 1 : j]
                if inside.startswith("!"):
                    inside = "^" + inside[1:]
                out.append("[" + inside + "]")
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def _as_str_list(value: object, what: str) -> list[str]:
    """Coerce a TOML scalar or array into a list of non-empty strings."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item:
                raise RulesError(f"{what} entries must be non-empty strings")
            out.append(item)
        return out
    raise RulesError(f"{what} must be a string or array of strings")


def _validate_rule(raw: object, index: int) -> "Rule":
    """Validate a single ``[[rule]]`` table and build a :class:`Rule`."""

    if not isinstance(raw, dict):
        raise RulesError(f"rule[{index}] must be a TOML table")

    # tier: required, integer in {0,1,2}. bool is a subclass of int, so reject
    # it explicitly (TOML `true`/`false`).
    tier = raw.get("tier")
    if isinstance(tier, bool) or not isinstance(tier, int):
        raise RulesError(f"rule[{index}].tier is required and must be an integer")
    if tier not in TIER_VALUES:
        raise RulesError(
            f"rule[{index}].tier must be one of {TIER_VALUES}, got {tier!r}"
        )

    # path: required, at least one non-empty glob.
    if "path" not in raw or raw["path"] is None:
        raise RulesError(f"rule[{index}] must declare at least one 'path'")
    paths = _as_str_list(raw["path"], "rule[%d].path" % index)
    if not paths:
        raise RulesError(f"rule[{index}] must declare at least one 'path'")
    for pat in paths:
        if not pat.startswith("/"):
            raise RulesError(
                f"rule[{index}] path {pat!r} must be absolute (start with '/')"
            )

    # action: optional filter.
    action = None
    if "action" in raw and raw["action"] is not None:
        action = tuple(_as_str_list(raw["action"], f"rule[{index}].action"))
        for a in action:
            if not _ACTION_RE.match(a):
                raise RulesError(
                    f"rule[{index}] action {a!r} must be a lowercase identifier"
                )

    # target: optional exact-match label.
    target = raw.get("target")
    if target is not None:
        if not isinstance(target, str) or not target:
            raise RulesError(f"rule[{index}].target must be a non-empty string")

    # name: optional human label.
    name = raw.get("name")
    if name is not None and not isinstance(name, str):
        raise RulesError(f"rule[{index}].name must be a string")

    return Rule(
        tier=tier,
        paths=tuple(paths),
        action=action,
        target=target,
        name=name,
    )


@dataclass(frozen=True)
class Rule:
    """A single policy rule: globs + optional filters → tier."""

    tier: int
    paths: tuple[str, ...]
    action: tuple[str, ...] | None = None
    target: str | None = None
    name: str | None = None

    def matches(self, action: str, path: str, target: str | None = None) -> bool:
        """Return True if this rule applies to the given change fields.

        Glob matching uses the project's single engine
        (:func:`_glob_to_regex`) against the *normalized* path (see
        :func:`pengyos_tiers.classifier.normalize_path`); the rules file
        stores patterns verbatim here.
        """

        if self.action is not None and action not in self.action:
            return False
        if self.target is not None and self.target != target:
            return False
        return any(pat_match.match(path) for pat_match in (_glob_to_regex(p) for p in self.paths))


@dataclass
class RulesFile:
    """A parsed, validated rules file: an ordered list of :class:`Rule`."""

    rules: list[Rule] = field(default_factory=list)
    source: str = "<inline>"

    @property
    def deny_tier(self) -> int:
        """Tier assigned when no rule matches (default-deny / fail safe)."""

        return DEFAULT_DENY_TIER

    def __len__(self) -> int:
        return len(self.rules)

    @classmethod
    def load(cls, path: str | Path) -> "RulesFile":
        """Parse and validate a rules file from disk."""

        path = Path(path)
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except FileNotFoundError as exc:
            raise RulesError(f"rules file not found: {path}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise RulesError(f"invalid TOML in {path}: {exc}") from exc

        return cls.from_dict(data, source=str(path))

    @classmethod
    def from_dict(cls, data: object, source: str = "<inline>") -> "RulesFile":
        """Build a :class:`RulesFile` from a parsed TOML document."""

        if not isinstance(data, dict):
            raise RulesError("rules file must be a TOML table of [[rule]] entries")

        rules_raw = data.get("rule")
        if rules_raw is None:
            raise RulesError("rules file contains no [[rule]] entries")
        if not isinstance(rules_raw, list):
            raise RulesError("'[[rule]]' must be an array of tables, not a table")

        rules = [_validate_rule(raw, i) for i, raw in enumerate(rules_raw)]
        if not rules:
            raise RulesError("rules file contains no valid [[rule]] entries")

        return cls(rules=rules, source=source)
