"""Event-log policy verification for pengyos-measured-boot.

A *policy* is the set of expected PCR values a measured boot must produce. It is
loaded from a TOML file (``policy.toml``) and compared against the PCRs read
from a boot (or committed in ``pcrs.json``).

Policy file format
------------------
The policy is a TOML document with a single ``[expected]`` table mapping PCR
index → expected value. Values are integers written as hex (``0x``-prefixed) or
decimal, mirroring the ``--expect`` CLI syntax:

.. code-block:: toml

    # Expected PCR values for the measured boot.
    [expected]
    pcr0 = "0000000000000000000000000000000000000000000000000000000000000000"
    pcr7 = "0x0000000000000000000000000000000000000000000000000000deadbeef"

The ``[expected]`` table may be omitted, in which case the top-level keys of the
document are interpreted as the expected values directly.

Standard library only.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .errors import Mismatch, PengyOSMeasuredBootError, PolicyMismatch
from .pcrs import Pcrs

# The TOML table that holds the expected PCR values. When absent, the top-level
# document itself is treated as the expected-values table.
EXPECTED_TABLE = "expected"


@dataclass(frozen=True)
class Policy:
    """An immutable mapping of PCR index → expected value.

    A policy is the ground truth a measured boot must satisfy. Values are
    integers so they compare directly against :class:`pengyos_measured_boot.pcrs.Pcrs`
    values (also integers).
    """

    expected: Mapping[int, int]

    def __getitem__(self, index: int) -> int:
        return self.expected[index]

    def __contains__(self, index: int) -> bool:
        return index in self.expected

    def __iter__(self) -> Iterable[int]:
        return iter(self.expected)

    def __len__(self) -> int:
        return len(self.expected)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Policy):
            return self.expected == other.expected
        if isinstance(other, Mapping):
            return self.expected == dict(other)
        return NotImplemented

    def get(self, index: int, default: int | None = None) -> int | None:
        """Return the expected value for ``index`` or ``default`` if absent."""

        return self.expected.get(index, default)

    def indices(self) -> frozenset[int]:
        """The set of PCR indices this policy requires."""

        return frozenset(self.expected)

    @classmethod
    def from_mapping(cls, expected: Mapping[int, int]) -> Policy:
        """Build a :class:`Policy` from an in-memory ``{index: value}`` mapping.

        Used by the ``run`` backend, whose expected values come from ``--expect``
        (already parsed into integers) rather than a policy file. The mapping
        must be non-empty: an empty policy is malformed, not trivially passing.

        Raises :class:`PengyOSMeasuredBootError` if ``expected`` is empty.
        """

        if not expected:
            raise PengyOSMeasuredBootError("policy contains no expected PCRs")
        return cls(dict(expected))

    def check(self, pcrs: Pcrs) -> list[Mismatch]:
        """Return the list of PCRs that fail the policy.

        An empty list means the boot matches the policy. A required PCR that is
        absent from ``pcrs`` counts as a mismatch (it cannot match what it does
        not have).
        """

        mismatches: list[Mismatch] = []
        for index in self.indices():
            actual = pcrs.get(index)
            if actual is None:
                mismatches.append(
                    Mismatch(index=index, expected=self.expected[index], actual=None)
                )
            elif actual != self.expected[index]:
                mismatches.append(
                    Mismatch(
                        index=index,
                        expected=self.expected[index],
                        actual=actual,
                    )
                )
        return mismatches

    def verify(self, pcrs: Pcrs) -> None:
        """Assert the boot matches the policy, else raise :class:`PolicyMismatch`.

        Silently returns when every required PCR matches. Raises
        :class:`PolicyMismatch` (naming every mismatching PCR) otherwise.
        """

        mismatches = self.check(pcrs)
        if mismatches:
            raise PolicyMismatch(mismatches)


def _coerce_values(data: Mapping) -> dict[int, int]:
    """Turn a TOML expected-values table into ``{index: value}`` integers.

    Keys are PCR indices (``"pcr7"``, ``"7"``, or ``7``); values are integers
    written as hex (``0x``-prefixed) or decimal.
    """

    values: dict[int, int] = {}
    for key, raw in data.items():
        index = _extract_index(key)
        values[index] = _extract_value(index, raw)
    return values


def _extract_index(key: object) -> int:
    """Return the PCR index from a TOML key.

    Accepts ``"pcr7"``, ``"pcr-7"``, ``"7"``, or an integer ``7``. Raises
    :class:`PengyOSMeasuredBootError` on a key with no parseable index.
    """

    if isinstance(key, bool):  # bool is a subclass of int — reject explicitly.
        raise PengyOSMeasuredBootError(f"PCR key {key!r} is not a valid index")
    if isinstance(key, int):
        index = key
    elif isinstance(key, str):
        normalized = key.strip().lower()
        digits = _strip_pcr_prefix(normalized)
        try:
            index = int(digits)
        except ValueError as exc:
            raise PengyOSMeasuredBootError(
                f"PCR key {key!r} must be an index like 'pcr7'"
            ) from exc
    else:
        raise PengyOSMeasuredBootError(
            f"PCR key {key!r} must be a string or integer index"
        )

    if index < 0:
        raise PengyOSMeasuredBootError(f"PCR key {key!r}: index must be >= 0")
    return index


def _strip_pcr_prefix(normalized: str) -> str:
    """Return the integer digits of a PCR key, dropping a leading ``pcr``.

    ``"pcr7"`` / ``"pcr-7"`` → ``"7"``; ``"7"`` stays ``"7"``. A key with no
    parseable index is returned unchanged so the caller can raise a clear error.
    """

    for prefix in ("pcr-", "pcr"):
        if normalized.startswith(prefix):
            return normalized[len(prefix):]
    return normalized


def _extract_value(index: int, raw: object) -> int:
    """Coerce a policy value to an int (hex ``0x``-prefixed or decimal)."""

    if isinstance(raw, bool):  # bool is a subclass of int — reject explicitly.
        raise PengyOSMeasuredBootError(
            f"PCR {index} value must be hex/decimal, got a boolean"
        )
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw.strip(), 0)
        except ValueError as exc:
            raise PengyOSMeasuredBootError(
                f"PCR {index} value {raw!r} is not hex/decimal"
            ) from exc
    raise PengyOSMeasuredBootError(
        f"PCR {index} value must be a hex/decimal string or integer, "
        f"got {type(raw).__name__}"
    )


def _extract_expected(data: Mapping) -> dict[int, int]:
    """Pull the expected-values table out of a parsed policy document."""

    if not isinstance(data, Mapping):
        raise PengyOSMeasuredBootError(
            "policy document must be a TOML mapping, "
            f"got {type(data).__name__}"
        )

    if EXPECTED_TABLE in data:
        table = data[EXPECTED_TABLE]
        if not isinstance(table, Mapping):
            raise PengyOSMeasuredBootError(
                f"'{EXPECTED_TABLE}' must be a table of PCR index → value"
            )
        return _coerce_values(table)

    # No explicit [expected] table: the top-level document is the policy.
    return _coerce_values(data)


def parse_policy(data: Mapping) -> Policy:
    """Build a :class:`Policy` from an already-parsed TOML document.

    Raises :class:`PengyOSMeasuredBootError` if the document has no expected
    PCRs — an empty policy is a malformed one, not a trivially-passing one.
    """

    policy = Policy(_extract_expected(data))
    if not policy.indices():
        raise PengyOSMeasuredBootError("policy contains no expected PCRs")
    return policy


def load_policy(path) -> Policy:
    """Read and parse a policy file (``policy.toml``) into a :class:`Policy`."""

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise PengyOSMeasuredBootError(f"policy file not found: {path}") from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PengyOSMeasuredBootError(f"policy file is not valid TOML: {exc}") from exc

    return parse_policy(data)
