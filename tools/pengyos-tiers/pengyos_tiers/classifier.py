"""The classifier: match a change against a ruleset → tier + reason.

The classifier is the heart of the engine. It walks the rules of a
:class:`pengyos_tiers.rules.RulesFile` in file order and returns the result of
the **first** rule that matches the change. If no rule matches, the change is
**default-denied to Tier 2** (fail safe): the engine errs on the side of
requiring a human.

Glob semantics (deliberate, documented): a rule ``path`` pattern is matched
with :func:`fnmatch`-style wildcards where ``*`` matches *any* run of
characters **including ``/``**. This lets ``/etc/*`` cover ``/etc/foo`` and
``/etc/foo/bar`` alike, which is what a policy author expects. ``?`` matches a
single character and ``[seq]`` is a character class; all other characters are
literal. Paths are lexically normalized (see :func:`normalize_path`) before
matching so ``/etc/../etc/foo`` and ``/etc//foo`` resolve identically.

Matching itself lives in :func:`pengyos_tiers.rules._glob_to_regex`; this
module only normalizes the path and walks the rules.
"""

from __future__ import annotations

from dataclasses import dataclass

from .approvals import Approval, approvals_for
from .change import Change
from .rules import RulesFile


def normalize_path(path: str) -> str:
    """Lexically normalize an absolute POSIX path (no filesystem access).

    Collapses repeated slashes and resolves ``.`` / ``..`` components. The
    result is always absolute (the schema guarantees a leading ``/``).
    """

    if not path.startswith("/"):
        raise ValueError(f"path must be absolute: {path!r}")

    root = path.startswith("/")
    parts: list[str] = []
    for component in path.split("/"):
        if component in ("", "."):
            continue
        if component == "..":
            if parts:
                parts.pop()
            continue
        parts.append(component)
    return "/" + "/".join(parts) if root else "/".join(parts)


@dataclass(frozen=True)
class Classification:
    """The outcome of classifying a :class:`Change`."""

    change: Change
    tier: int
    matched: bool  # False ⇒ the change fell through to default-deny
    rule_name: str | None  # the matched rule's label, if any
    reason: str

    @property
    def needs_human(self) -> bool:
        """True iff the tier requires a human signature (Tier 2)."""

        return self.tier == 2

    @property
    def approvals(self) -> tuple[Approval, ...]:
        """The required approval(s) for this tier (SPEC §1.5).

        Tier 0 → ``witness`` (verified/measured build), Tier 1 → ``agent-auto``,
        Tier 2 → ``human-sign``. A Tier-2 (or default-deny) classification
        therefore requires a human.
        """

        return approvals_for(self.tier)

    def to_dict(self) -> dict:
        """Serialise to a plain dict (used by ``--json`` output)."""

        return {
            "tier": self.tier,
            "matched": self.matched,
            "rule": self.rule_name,
            "needs_human": self.needs_human,
            "approvals": [a.value for a in self.approvals],
            "reason": self.reason,
        }


def classify(change: Change, rules: RulesFile) -> Classification:
    """Classify ``change`` against ``rules`` (first-match, default-deny T2)."""

    norm = normalize_path(change.path)
    for rule in rules.rules:
        if rule.matches(change.action, norm, change.target):
            label = f"rule {rule.name!r}" if rule.name else "an unnamed rule"
            return Classification(
                change=change,
                tier=rule.tier,
                matched=True,
                rule_name=rule.name,
                reason=f"{label} matched {norm!r} → Tier {rule.tier}",
            )

    return Classification(
        change=change,
        tier=rules.deny_tier,
        matched=False,
        rule_name=None,
        reason=(
            f"no rule matched {norm!r}; default-deny to "
            f"Tier {rules.deny_tier} (fail safe)"
        ),
    )
