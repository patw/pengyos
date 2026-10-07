"""The repro report contract.

A repro report is the JSON document ``pengyos-repro compare --json``
writes into the build output dir (``checks.REPRO_REPORT_FILENAME``).
Its schema (fixed by pengyos-repro's report-emission contract):

    {"score": <float 0.0-1.0>, "identical": <bool>,
     "differing": [{"path": ..., "reason": ...}, ...],
     "missing": [str, ...]}

The verifier's check (criterion in ``checks.py``) needs: the report to
parse as a JSON object, ``score`` to be a real number in ``[0, 1]``
(a JSON ``bool`` is NOT a number, so ``true`` is rejected),
``identical`` to be a boolean, and the optional ``differing`` /
``missing`` fields to be lists when present (their contents are
evidence, not part of the validity rule). Then: pass iff
``score >= threshold``.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional, Tuple

REQUIRED_FIELDS: Tuple[str, ...] = ("score", "identical")
OPTIONAL_LIST_FIELDS: Tuple[str, ...] = ("differing", "missing")


def repro_report_errors(data: Any) -> List[str]:
    """The validity errors of a parsed repro report (``[]`` iff valid).

    ``data`` is the already-parsed JSON value (parse errors are the
    caller's concern). Checks, in order: top level is an object;
    ``score`` present, numeric (not bool), within [0, 1]; ``identical``
    present, boolean; ``differing`` / ``missing`` lists when present.
    """
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["report is not a JSON object"]
    if "score" not in data:
        errors.append("missing field 'score'")
    else:
        score = data["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            errors.append(
                f"score must be a number, got {type(score).__name__}"
            )
        elif not 0.0 <= score <= 1.0:
            errors.append(f"score {score!r} is outside [0, 1]")
    if "identical" not in data:
        errors.append("missing field 'identical'")
    elif not isinstance(data["identical"], bool):
        errors.append(
            f"identical must be a boolean, got {type(data['identical']).__name__}"
        )
    for field_name in OPTIONAL_LIST_FIELDS:
        if field_name in data and not isinstance(data[field_name], list):
            errors.append(f"{field_name} must be a list")
    return errors


def score_error(data: Any, threshold: float) -> Optional[str]:
    """The threshold verdict for a valid report: None iff score >= threshold.

    Call after :func:`repro_report_errors` has returned ``[]``.
    """
    score = data["score"]
    if score >= threshold:
        return None
    return f"repro score {score!r} is below threshold {threshold!r}"


def parse_repro_report(text: str) -> Tuple[Optional[dict], List[str]]:
    """Parse report text: ``(dict_or_None, errors)``.

    ``errors`` carries the JSON parse error when the text is not valid
    JSON (or not an object), and the :func:`repro_report_errors`
    validity errors otherwise.
    """
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, [f"not valid JSON: {exc}"]
    errors = repro_report_errors(data)
    if errors:
        return None, errors
    return data, []
