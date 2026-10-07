"""PCR value parsing for pengyos-measured-boot.

The read step (``tpm2_getpcrs --format json``) emits the Platform Configuration
Registers as JSON. This module parses that raw stdout into a structured
:class:`Pcrs` mapping of *PCR index → value* (an integer), and serialises it
back out so the parsed PCRs can be committed as a fixture for ``verify``.

Two input shapes are accepted, so the same parser serves both the live read
step and the committed fixture:

``tpm2-tools JSON`` (``tpm2_getpcrs --format json``)
    .. code-block:: json

        {"pcr-0": {"sha256": "0000..."}, "pcr-1": {"sha256": "1111..."}, ...}

    Each entry is a map of algorithm → hex digest. The digest is taken from the
    requested algorithm (``sha256`` by default) or, failing that, the first
    algorithm present.

``committed fixture format`` (``pcrs.json`` for ``verify``)
    A flat map of index → hex string, accepting either ``pcrN`` or ``pcr-N``
    keys so it reads naturally either way:

    .. code-block:: json

        {"pcr-0": "0000...", "pcr-1": "1111...", ...}

Standard library only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .errors import PengyOSMeasuredBootError

# The algorithm the read step requests (see ``build_read_pcrs_argv``). The
# digest for this algorithm is what we parse and compare.
DEFAULT_PCR_ALGO = "sha256"

# The PCRs the read step requests. Mirrors ``build_read_pcrs_argv``.
DEFAULT_PCR_LIST = (0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 16)


def _extract_index(key: str) -> int:
    """Return the PCR index from a JSON key like ``"pcr-0"`` / ``"pcr7"``.

    Raises :class:`PengyOSMeasuredBootError` on a key with no parseable index.
    """

    normalized = key.strip().lower()
    for prefix in ("pcr-", "pcr"):
        if normalized.startswith(prefix):
            digits = normalized[len(prefix) :]
            try:
                return int(digits)
            except ValueError as exc:
                raise PengyOSMeasuredBootError(
                    f"PCR key {key!r} has no integer index"
                ) from exc
    raise PengyOSMeasuredBootError(f"PCR key {key!r} must start with 'pcr'")


def _extract_digest(entry, algo: str) -> int:
    """Return the integer value of a PCR's digest from a JSON entry.

    ``entry`` is either a hex string (fixture format) or a map of algorithm →
    hex digest (tpm2-tools format). ``algo`` is the preferred algorithm.
    """

    if isinstance(entry, Mapping):
        if algo in entry:
            hex_digest = entry[algo]
        elif len(entry) == 1:
            hex_digest = next(iter(entry.values()))
        else:
            raise PengyOSMeasuredBootError(
                f"multiple algorithms present; request {algo!r} explicitly"
            )
    else:
        hex_digest = entry

    if not isinstance(hex_digest, str):
        raise PengyOSMeasuredBootError(
            f"PCR digest must be a hex string, got {type(hex_digest).__name__}"
        )

    try:
        return int(hex_digest.strip(), 16)
    except ValueError as exc:
        raise PengyOSMeasuredBootError(
            f"PCR digest {hex_digest!r} is not a hex string"
        ) from exc


@dataclass(frozen=True)
class Pcrs:
    """An immutable mapping of PCR index → value (an integer).

    Values are integers so they compare directly against ``--expect`` values
    (also integers) without a second conversion.
    """

    values: Mapping[int, int]

    def __getitem__(self, index: int) -> int:
        return self.values[index]

    def __contains__(self, index: int) -> bool:
        return index in self.values

    def __iter__(self) -> Iterable[int]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Pcrs):
            return self.values == other.values
        if isinstance(other, Mapping):
            return self.values == dict(other)
        return NotImplemented

    def get(self, index: int, default: int | None = None) -> int | None:
        """Return the value for ``index`` or ``default`` if the PCR is absent."""

        return self.values.get(index, default)

    def indices(self) -> frozenset[int]:
        """The set of PCR indices present."""

        return frozenset(self.values)

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialise to the committed fixture format (``pcrN`` keys, hex values)."""

        payload = {f"pcr{n}": format(self.values[n], "064x") for n in sorted(self.values)}
        return json.dumps(payload, indent=indent, sort_keys=True)


def parse_pcrs(raw: str, algo: str = DEFAULT_PCR_ALGO) -> Pcrs:
    """Parse raw read-step stdout into a :class:`Pcrs`.

    ``raw`` is the stdout of ``tpm2_getpcrs --format json`` (or the committed
    fixture format). ``algo`` selects the digest algorithm to read from a
    tpm2-tools entry. Raises :class:`PengyOSMeasuredBootError` on malformed
    input.
    """

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PengyOSMeasuredBootError(f"PCR output is not valid JSON: {exc}") from exc

    if not isinstance(data, Mapping):
        raise PengyOSMeasuredBootError(
            "PCR output must be a JSON object keyed by PCR name"
        )

    values: dict[int, int] = {}
    for key, entry in data.items():
        index = _extract_index(key)
        values[index] = _extract_digest(entry, algo)

    if not values:
        raise PengyOSMeasuredBootError("PCR output contained no PCRs")

    return Pcrs(values)


def parse_pcrs_file(path, algo: str = DEFAULT_PCR_ALGO) -> Pcrs:
    """Read and parse a PCR values file (``pcrs.json``) into a :class:`Pcrs`."""

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise PengyOSMeasuredBootError(f"PCR file not found: {path}") from exc
    return parse_pcrs(text, algo=algo)
