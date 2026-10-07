"""Data-driven difference classification for PengyOS repro-report.

Given two artifact payloads that are *not* byte-identical, decide whether the
difference is

* **normalized** — attributable entirely to a known non-deterministic field
  (a recorded build timestamp, an mtime, an embedded build-id, ...). Such an
  artifact is *reproducible modulo a known non-determinism* and still counts
  toward the reproducibility score, and
* **real** — the bytes differ in a way no rule explains. This is a genuine
  reproducibility failure.

The classification is driven by an explicit, pluggable *rule table*
(:data:`DEFAULT_RULES`). Each rule knows how to *explain* one class of
non-deterministic difference by locating it in the raw bytes and blanking it
out; the engine walks the table in order and the first rule whose blanked
residuals become equal fully explains the difference (``normalized``). When a
rule applies but does not fully explain it, the blanked residual is threaded
forward so a later rule can explain a second known field; if no rule explains
the difference it is ``real``.

The default table ships with a handful of generic rules that operate on
arbitrary byte strings (a trailing ISO-8601 build timestamp, an embedded
unix-timestamp, a tar-style mtime field, a ``BuildRoot``/build-id string).
Rules are data, not code: extend :func:`rule_timestamp` / :func:`rule_regex`
to add coverage for a specific artifact format without touching the engine.

Standard library only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Callable

# NOTE: ``STATUS_DIFFERS`` / ``STATUS_NORMALIZED`` live in :mod:`pengyos_repro_report.core`,
# which imports this module at package load time. Importing them here at module
# scope would create a circular import, so they are pulled in lazily inside
# :func:`classify_comparison` (by which point ``core`` is fully initialized).

# Classification verdicts live in :mod:`pengyos_repro_report.core` (imported
# lazily inside :func:`classify_comparison` to avoid a circular import).


@dataclass(frozen=True)
class Rule:
    """A single classification rule.

    A rule knows how to *locate and blank* one class of non-deterministic
    field. :meth:`explain` returns ``(blanked_a, blanked_b, matched)``: the two
    payloads with the field replaced by a fixed placeholder, plus whether the
    field was actually present in both payloads. When ``matched`` is False the
    rule did not apply and the caller should try the next rule.
    """

    name: str
    reason: str
    explain: Callable[[bytes, bytes], tuple[bytes, bytes, bool]]

    def explain_pair(self, a: bytes, b: bytes) -> tuple[bytes, bytes, bool]:
        """Apply this rule to ``(a, b)``; ``matched`` is False if it did not apply."""
        return self.explain(a, b)


def rule_regex(pattern: str, *, name: str, reason: str, placeholder: bytes = b"\x00") -> Rule:
    """Build a rule that blanks every regex match of ``pattern`` in both payloads.

    The rule matches only when the pattern is present in *both* payloads; the
    engine then compares the blanked residuals. ``placeholder`` is the fixed
    bytes the match is replaced with (so a timestamp match becomes identical in
    both payloads).
    """
    compiled = re.compile(pattern.encode() if isinstance(pattern, str) else pattern)

    def explain(a: bytes, b: bytes) -> tuple[bytes, bytes, bool]:
        new_a, count_a = compiled.subn(placeholder, a)
        new_b, count_b = compiled.subn(placeholder, b)
        return new_a, new_b, count_a > 0 and count_b > 0

    return Rule(name=name, reason=reason, explain=explain)


def rule_timestamp(*, name: str = "build-timestamp", reason: str = "recorded build timestamp") -> Rule:
    """Rule that blanks trailing ISO-8601 build timestamps (``YYYY-MM-DDTHH:MM:SS``)."""
    return rule_regex(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
        name=name,
        reason=reason,
    )


def rule_unix_timestamp(*, name: str = "unix-timestamp", reason: str = "embedded unix timestamp") -> Rule:
    """Rule that blanks 10-digit unix timestamps (seconds since the epoch)."""
    return rule_regex(
        r"\b\d{10}\b",
        name=name,
        reason=reason,
    )


def rule_mtime_field(*, name: str = "tar-mtime", reason: str = "tar-style mtime field") -> Rule:
    """Rule that blanks a tar-style octal mtime field (8 octal digits)."""
    return rule_regex(
        r"\b[0-7]{8}\b",
        name=name,
        reason=reason,
    )


def rule_build_id(*, name: str = "build-id", reason: str = "embedded build-id string") -> Rule:
    """Rule that blanks ``BuildRoot=...`` / ``BUILD_ID=...`` style identifiers."""
    return rule_regex(
        r"(?:BuildRoot|BUILD_ID|BUILD-ID)=\S+",
        name=name,
        reason=reason,
    )


def rule_crc32(*, name: str = "crc32", reason: str = "embedded CRC-32 checksum") -> Rule:
    """Rule that blanks a little-endian CRC-32 field (4 bytes) at common offsets."""
    pattern = re.compile(rb"(?<![0-9a-fA-F])[0-9a-fA-F]{8}(?![0-9a-fA-F])")

    def explain(a: bytes, b: bytes) -> tuple[bytes, bytes, bool]:
        # Only target 4-byte values that look like a CRC (already-blanked runs
        # collapse to zeros, which the caller's residual check tolerates).
        new_a = pattern.sub(b"\x00" * 4, a)
        new_b = pattern.sub(b"\x00" * 4, b)
        return new_a, new_b, new_a != a or new_b != b

    return Rule(name=name, reason=reason, explain=explain)


# The default, generic rule table. Order is significant: earlier rules win.
#
# These rules target fields that are *known* to vary across reproducible builds
# without indicating a real difference (a recorded build timestamp, a unix
# timestamp, a tar mtime, a build-id). The crc32 rule is deliberately *not* in
# the default table: an 8-hex-char run is an unreliable CRC marker and blanking
# it can hide a genuine data difference. Add it yourself when you know an
# artifact embeds a printed CRC-32:
#
#     classify_comparison(c, load_pair, rules=DEFAULT_RULES + (rule_crc32(),))
DEFAULT_RULES: tuple[Rule, ...] = (
    rule_timestamp(),
    rule_unix_timestamp(),
    rule_mtime_field(),
    rule_build_id(),
)


def classify_payloads(a: bytes, b: bytes, rules: tuple[Rule, ...] = DEFAULT_RULES) -> bool:
    """Classify whether differing payloads ``(a, b)`` differ only in a normalized field.

    Returns ``True`` when the difference is **normalized** (attributable to a
    known non-deterministic field) and ``False`` when it is **real**. The two
    payloads must already be known to differ; identical payloads short-circuit
    to ``True`` (nothing to classify).

    The engine walks the rule table in order. Each rule blanks its field from
    both payloads; the first rule whose blanked residuals become equal fully
    explains the difference and the verdict is ``normalized``. When a rule
    applies but does not fully explain it, the blanked residual is threaded
    forward so a later rule can explain the remaining known field (an artifact
    may embed more than one non-deterministic field). If no rule explains the
    difference it is a genuine reproducibility failure: ``real``.
    """
    if a == b:
        return True

    # The residual is what remains after blanking known fields; it starts as the
    # full payloads and shrinks as a rule explains away a difference.
    residual_a, residual_b = a, b
    for rule in rules:
        blanked_a, blanked_b, matched = rule.explain_pair(residual_a, residual_b)
        if not matched:
            continue
        if blanked_a == blanked_b:
            # This rule (possibly together with an earlier one) fully explained
            # the difference: reproducible modulo a known non-determinism.
            return True
        # Rule applied but did not fully explain it: keep this rule's blanked
        # form as the residual and keep trying later rules.
        residual_a, residual_b = blanked_a, blanked_b

    # No rule fully explained the residual: a genuine reproducibility failure.
    return False


def _never_loads(name: str) -> tuple[None, None]:
    """Loader that reports neither side's payload as available.

    Used for manifest-only inputs (name -> sha256), where the raw bytes are not
    reachable: every differing artifact is therefore classified as a real
    difference.
    """
    return None, None


def classify_comparison(
    comparison,
    load_pair: Callable[[str], tuple[bytes | None, bytes | None]],
    rules: tuple[Rule, ...] = DEFAULT_RULES,
) -> list[str]:
    """Classify every differing artifact in ``comparison`` in place.

    ``load_pair`` maps an artifact name to ``(payload_a, payload_b)`` — the raw
    bytes of each side, or ``None`` when the artifact is absent there (the
    caller supplies the bytes the manifest does not carry). For each artifact
    whose status is :data:`STATUS_DIFFERS`, the two payloads are classified and
    the result is written back onto the artifact:

    * ``normalized`` -> status becomes :data:`STATUS_NORMALIZED` (counts as
      matching) and ``normalized`` is set to ``True``;
    * ``real``       -> status becomes :data:`STATUS_REAL` (a genuine
      reproducibility failure) and ``normalized`` is set to ``False``.

    Missing artifacts (present on only one side) are left untouched. Returns the
    list of verdicts produced (``STATUS_NORMALIZED`` / ``STATUS_REAL``), useful
    for tests and assertions.
    """
    # Lazily imported to avoid a circular import with ``core`` (which imports
    # this module at package load time).
    from .core import STATUS_DIFFERS, STATUS_NORMALIZED, STATUS_REAL

    verdicts: list[str] = []
    for idx, artifact in enumerate(comparison.artifacts):
        if artifact.status != STATUS_DIFFERS:
            continue
        payload_a, payload_b = load_pair(artifact.name)
        # If either side is missing we cannot compare bytes; fall back to the
        # manifest-level verdict (a real difference). This should not happen for
        # STATUS_DIFFERS artifacts, but stays defensive.
        if payload_a is None or payload_b is None:
            comparison.artifacts[idx] = replace(artifact, status=STATUS_REAL, normalized=False)
            verdicts.append(STATUS_REAL)
            continue
        if classify_payloads(payload_a, payload_b, rules=rules):
            comparison.artifacts[idx] = replace(artifact, status=STATUS_NORMALIZED, normalized=True)
            verdicts.append(STATUS_NORMALIZED)
        else:
            comparison.artifacts[idx] = replace(artifact, status=STATUS_REAL, normalized=False)
            verdicts.append(STATUS_REAL)
    return verdicts
