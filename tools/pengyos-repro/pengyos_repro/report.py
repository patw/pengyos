"""Reproducibility score and report emission.

It turns a
:class:`~pengyos_repro.classify.Diff` (produced by walking + hashing +
classifying two build trees) into two things:

* a single **reproducibility score** in ``[0.0, 1.0]``, and
* a **report** — both a machine-readable ``dict`` (the exact JSON schema) and a
  human-readable multi-line string.

The score and report are the whole point of the pipeline: a low score points at
a nondeterministic input, and the report lists *exactly which* artifacts differ
or are missing.

Scoring
-------
The score is defined as the fraction of the compared paths that are
byte-identical::

    score = identical / (identical + differing + only_in_a + only_in_b)

i.e. ``len(diff.identical) / total`` where ``total`` is every path that survived
the ignore filter (see :func:`~pengyos_repro.patterns.filter_manifest`).

Consequences, all deterministic:

* **Identical trees score ``1.0``.** Every path is identical, so the ratio is 1.
* **A differing or missing path lowers the score.** A path that differs (or
  exists in only one tree) counts toward ``total`` but not toward ``identical``,
  so ``score`` drops below 1.0 — by ``1/total`` per offending path.
* **Empty trees score ``1.0``.** With no paths to compare the ratio is defined as
  1.0 (vacuously reproducible) rather than dividing by zero.
* **Ignore patterns raise the score as documented.** Filtering a noisy path out
  of both manifests removes it from ``total`` *and* from ``differing``/``missing``,
  lifting the ratio back toward 1.0 — so the score reflects *real* divergence, not
  expected nondeterminism.
* The score is rounded to 4 decimal places so the emitted JSON/text is stable
  across runs (floating-point sha256 ratios are deterministic, but the rounded
  form is what the report prints and serialises).

A low score is a valid *result*, not an error: the CLI still exits ``0`` when it
successfully computes and reports a score.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .classify import Diff, DiffEntry

# Number of decimal places the score is rounded to before it is emitted. Keeps
# the JSON/text output byte-stable across runs.
SCORE_PRECISION = 4

# The curated nondeterminism heuristics, in a fixed order, used to *explain* a
# metadata-only difference (see ``metadata_analysis``). Each is reported by its
# ``heuristic:<name>`` alias rather than the expanded glob.
_METADATA_HEURISTICS: Tuple[str, ...] = ("timestamps", "embedded-user", "random-seeds")


@dataclass
class MetadataAnalysis:
    """The outcome of the ``--strip-metadata`` second-pass comparison.

    Produced by :func:`metadata_analysis` from the raw (pass 1) and
    metadata-stripped (pass 2) :class:`~pengyos_repro.classify.Diff` objects.

    Attributes
    ----------
    stripped_score:
        The reproducibility score after the curated heuristics hide the classic
        nondeterminism sources (pass 2). Always ``>= raw_score`` — stripping can
        only remove paths from the differing/missing sets, never add them.
    explained:
        ``(path, reason)`` pairs for paths that differ in the raw pass but are
        identical once metadata is stripped — i.e. *metadata-only* differences.
        ``reason`` is ``"heuristic:<name>"`` when a heuristic explained the path
        (the earliest-matching one), or the literal ``--ignore`` glob otherwise.
        Sorted by path.
    """

    stripped_score: float
    explained: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Number of metadata-only differences that were explained away."""
        return len(self.explained)


def score(diff: Diff) -> float:
    """Return the reproducibility score for ``diff`` in ``[0.0, 1.0]``.

    ``score = len(identical) / total`` where ``total`` is every path that
    survived the ignore filter. Identical trees score ``1.0``; a differing or
    missing path lowers it; empty trees score ``1.0`` (vacuously reproducible).
    See the module docstring for the full definition.
    """
    total = (
        len(diff.identical)
        + len(diff.differing)
        + len(diff.only_in_a)
        + len(diff.only_in_b)
    )
    if total == 0:
        return 1.0
    return round(len(diff.identical) / total, SCORE_PRECISION)


def metadata_analysis(
    raw_diff: Diff,
    stripped_diff: Diff,
    user_patterns: "Sequence[str]",
) -> MetadataAnalysis:
    """Compare the raw (pass 1) and metadata-stripped (pass 2) diffs.

    A path that differs in the raw pass but is identical once the curated
    heuristics hide the classic nondeterminism sources is a *metadata-only*
    difference — expected noise rather than a real divergence.
    :func:`explain_ignore` records which heuristic explained each such path.

    ``stripped_score`` is always ``>= raw_score``: stripping only removes paths
    from the differing/missing sets, never adds them.

    ``user_patterns`` is the caller's own ``--ignore`` list (used verbatim for
    the ``heuristic:`` alias detection); the curated heuristics are what actually
    explain the paths, and each is reported by its ``heuristic:<name>`` alias.
    """
    from .patterns import explain_ignore  # local import

    raw_score = score(raw_diff)
    stripped_score = score(stripped_diff)

    # The explain list is the caller's patterns followed by one entry per
    # heuristic *alias* (not the expanded globs), so a heuristic match reports
    # as ``"heuristic:timestamps"`` rather than the underlying ``*.pyc`` glob.
    explain_patterns = list(user_patterns) + [
        f"heuristic:{name}" for name in _METADATA_HEURISTICS
    ]

    # A path is metadata-only when it differs in the raw pass yet is identical in
    # the stripped pass. It must appear in the raw differing set (this is only
    # meaningful for paths present in both trees); only-in-* paths are handled
    # by the score, not by metadata stripping.
    raw_differing = {entry.path for entry in raw_diff.differing}
    stripped_differing = {entry.path for entry in stripped_diff.differing}
    metadata_paths = raw_differing - stripped_differing

    explained = sorted(
        (path, explain_ignore(path, explain_patterns))
        for path in metadata_paths
    )
    return MetadataAnalysis(
        stripped_score=stripped_score, explained=explained
    )


def report_dict(
    diff: Diff,
    *,
    analysis: "MetadataAnalysis | None" = None,
) -> Dict[str, Any]:
    """Build the machine-readable report as a plain ``dict``.

    The schema is exactly::

        {
          "score": <float in [0.0, 1.0]>,
          "identical": [<path>, ...],
          "differing": [{"path": <path>, "reason": <str>}, ...],
          "missing": [<path>, ...]
        }

    * ``score`` — see :func:`score`.
    * ``identical`` — paths present in both trees with byte-identical content.
    * ``differing`` — paths present in both but differing, each as a
      ``{"path", "reason"}`` object (``reason`` names the size mismatch first,
      then a same-size content mismatch).
    * ``missing`` — paths present in exactly one tree (``only_in_a`` +
      ``only_in_b``, per :meth:`Diff.missing`).

    When ``analysis`` (the result of :func:`metadata_analysis`) is given, two
    extra keys are appended: ``stripped_score`` (the second-pass score) and
    ``metadata_only`` (``[{"path", "reason"}, ...]`` for the paths explained by a
    nondeterminism heuristic). Every list is sorted by path. This dict
    serialises directly to JSON with :func:`json.dumps`.
    """
    rep: Dict[str, Any] = {
        "score": score(diff),
        "identical": list(diff.identical),
        "differing": [
            {"path": entry.path, "reason": entry.reason}
            for entry in diff.differing
        ],
        "missing": list(diff.missing),
    }
    if analysis is not None:
        rep["stripped_score"] = analysis.stripped_score
        rep["metadata_only"] = [
            {"path": path, "reason": reason}
            for path, reason in analysis.explained
        ]
    return rep


def dumps_report(
    diff: Diff, *, indent: int | None = 2, analysis: "MetadataAnalysis | None" = None
) -> str:
    """Serialise ``diff`` to a JSON report string (via :func:`report_dict`)."""
    return json.dumps(
        report_dict(diff, analysis=analysis), indent=indent, sort_keys=False
    )


def format_report(
    diff: Diff,
    *,
    analysis: "MetadataAnalysis | None" = None,
) -> str:
    """Return a human-readable, multi-line report for ``diff``.

    The first line is the score and a verdict; the remaining lines list the
    differing paths (with reasons) and the missing paths. Identical paths are
    summarised by count only (listing every identical path would bury the signal
    in the noise). A perfectly reproducible pair prints a one-line success
    message.

    When ``analysis`` (the result of :func:`metadata_analysis`, i.e. the
    ``--strip-metadata`` second pass) is given, the report additionally prints
    the stripped score and a "Metadata-only" section listing the paths explained
    by a nondeterminism heuristic.
    """
    lines: List[str] = []
    sc = score(diff)
    if sc == 1.0:
        lines.append(f"REPRODUCIBLE (score {sc:.{SCORE_PRECISION}f})")
        if diff.identical:
            lines.append(f"  {len(diff.identical)} identical artifact(s).")
        else:
            lines.append("  No artifacts in either tree.")
        if analysis is not None:
            # Identical raw pass: nothing to explain, but report the stripped
            # score (which equals the raw score) for completeness.
            lines.append(
                f"Metadata-stripped score {analysis.stripped_score:.{SCORE_PRECISION}f} "
                f"(no metadata-only differences)."
            )
        return "\n".join(lines)

    lines.append(f"NOT REPRODUCIBLE (score {sc:.{SCORE_PRECISION}f})")
    lines.append("")

    if diff.differing:
        lines.append(f"Differing ({len(diff.differing)}):")
        for entry in diff.differing:
            lines.append(f"  - {entry.path}: {entry.reason}")
        lines.append("")

    if diff.missing:
        only_a = [p for p in diff.only_in_a]
        only_b = [p for p in diff.only_in_b]
        lines.append(f"Missing ({len(diff.missing)}):")
        for p in only_a:
            lines.append(f"  - {p} (only in A)")
        for p in only_b:
            lines.append(f"  - {p} (only in B)")
        lines.append("")

    if analysis is not None:
        lines.append(
            f"Metadata-stripped score "
            f"{analysis.stripped_score:.{SCORE_PRECISION}f} "
            f"(from {sc:.{SCORE_PRECISION}f})."
        )
        if analysis.explained:
            lines.append(
                f"Metadata-only (explained by nondeterminism heuristics, "
                f"{analysis.count}):"
            )
            for path, reason in analysis.explained:
                lines.append(f"  - {path}: {reason}")
            lines.append("")
        else:
            lines.append("No metadata-only differences explained.")
            lines.append("")

    lines.append(
        f"Identical: {len(diff.identical)} | "
        f"Differing: {len(diff.differing)} | "
        f"Missing: {len(diff.missing)}"
    )
    return "\n".join(lines)


def emit(
    diff: Diff,
    *,
    json_out: str | None = None,
    stream: Any = None,
    analysis: "MetadataAnalysis | None" = None,
) -> Dict[str, Any]:
    """Emit the report for ``diff`` and return the report dict.

    Writes the human-readable report to ``stream`` (``sys.stdout`` by default)
    and, when ``json_out`` is given, writes the JSON report to that path. Returns
    the report ``dict`` (the single source of truth for both views). This is the
    helper the CLI calls for ``compare``.

    When ``analysis`` (the result of :func:`metadata_analysis`, i.e. the
    ``--strip-metadata`` second pass) is given, the report includes the stripped
    score and the metadata-only section in both the human and JSON views.
    """
    rep = report_dict(diff, analysis=analysis)
    text = format_report(diff, analysis=analysis)
    if stream is not None:
        print(text, file=stream)
    if json_out is not None:
        with open(json_out, "w", encoding="utf-8") as fh:
            fh.write(dumps_report(diff, analysis=analysis))
    return rep
