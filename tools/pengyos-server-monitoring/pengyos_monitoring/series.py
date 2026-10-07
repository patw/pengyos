"""Prometheus-style metric series parsing (US-SRV-3).

Parses the Prometheus text exposition format used by the committed fixtures in
``fixtures/series/``. This is a small, offline parser (stdlib only) — it does
not talk to Prometheus or any exporter.

Supported line types
--------------------
* ``# HELP <metric> <text>`` and ``# TYPE <metric> <type>`` — recognised and
  skipped (metadata is not used by the evaluator).
* ``<metric>{<labels>} <value> [<timestamp>]`` — a single sample.
* ``<metric> <value> [<timestamp>]`` — a sample with no labels.

Timestamps are optional (Prometheus uses unix seconds); when omitted a
monotonic index is assigned so samples stay ordered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# metric{label="value", label2="value2"} value [timestamp]
_SAMPLE_RE = re.compile(
    r"^\s*"
    r"(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?"
    r"\s+(?P<value>-?\d+(?:\.\d+)?)"
    r"(?:\s+(?P<ts>-?\d+(?:\.\d+)?))?"
    r"\s*$"
)

# label="value", label2="value2"
_LABEL_RE = re.compile(r'''([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"''')


class SeriesParseError(ValueError):
    """Raised when a fixture line cannot be parsed."""


@dataclass(frozen=True)
class Sample:
    """A single (timestamp, value) point on a series."""

    timestamp: float
    value: float


@dataclass
class Series:
    """A named, labelled metric series with an ordered list of samples."""

    name: str
    labels: dict[str, str]
    samples: list[Sample] = field(default_factory=list)

    def value_at(self, timestamp: float) -> float | None:
        """Return the sample value at ``timestamp``, or ``None`` if absent."""
        for s in self.samples:
            if s.timestamp == timestamp:
                return s.value
        return None

    @property
    def latest(self) -> Sample | None:
        return self.samples[-1] if self.samples else None

    def samples_timestamps(self) -> list[float]:
        """The sample timestamps, ascending (samples are stored in order)."""
        return [s.timestamp for s in self.samples]

    def signature(self) -> frozenset[tuple[str, str]]:
        """Label-set identity used to match series across a ratio."""
        return frozenset(self.labels.items())

    def matches(self, want: dict[str, str]) -> bool:
        """Whether this series' labels satisfy ``want`` (all keys must match)."""
        return all(self.labels.get(k) == v for k, v in want.items())


def parse_series(text: str) -> list[Series]:
    """Parse Prometheus text-exposition ``text`` into a list of :class:`Series`.

    Samples are appended in document order; a monotonic index is used as the
    timestamp when a line omits one.
    """
    by_key: dict[tuple[str, frozenset[tuple[str, str]]], Series] = {}
    index = 0
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _SAMPLE_RE.match(line)
        if m is None:
            raise SeriesParseError(f"line {lineno}: {raw!r}")
        name = m.group("name")
        labels: dict[str, str] = {}
        if m.group("labels"):
            for lm in _LABEL_RE.finditer(m.group("labels")):
                labels[lm.group(1)] = lm.group(2)
        value = float(m.group("value"))
        ts = float(m.group("ts")) if m.group("ts") is not None else float(index)
        index += 1
        sig = frozenset(labels.items())
        key = (name, sig)
        series = by_key.get(key)
        if series is None:
            series = Series(name=name, labels=labels)
            by_key[key] = series
        series.samples.append(Sample(timestamp=ts, value=value))
    return list(by_key.values())


def parse_series_file(path: Path | str) -> list[Series]:
    """Parse a Prometheus text-exposition file into a list of series."""
    return parse_series(Path(path).read_text(encoding="utf-8"))
