"""CVSS base-score computation and severity-band mapping.

This is the severity source for the classification subtask. Given a CVSS *value* carried on an advisory's ``severity`` entry, it
computes the **base score** and maps it to the tool's
``low / medium / high / critical`` severity vocabulary.

Supported inputs (dispatched by :func:`cvss_kind`)
--------------------------------------------------
* **CVSS v3.1** base vectors — the FIRST CVSS v3.1 specification
  (``cvss-v31-specification.pdf``), Sections 7.1 / 7.4 / 7.5:

      ISS          = 1 - [(1-C) * (1-I) * (1-A)]
      Impact       = 6.42 * ISS                                  (Scope Unchanged)
      Impact       = 7.52*(ISS - 0.029) - 3.25*(ISS - 0.02)**15  (Scope Changed)
      Exploitability = Roundup(8.22 * AV * AC * PR * UI)
      BaseScore    = Roundup(min(Impact + Exploitability, 10))            (S:U)
      BaseScore    = Roundup(min(1.08 * (Impact + Exploitability), 10))   (S:C)

* **CVSS v3.0** base vectors — the *same* closed-form as v3.1, but with the
  **original (2015) Privileges Required multipliers** (Table 16 of the CVSS v3.0
  specification). The only 3.0↔3.1 difference is the PR table:

      Privileges Required (3.0)   Unchanged  N=0.85  L=0.77  H=0.44
                                 Changed    N=0.85  L=0.77  H=0.50
      Privileges Required (3.1)   Unchanged  N=0.85  L=0.62  H=0.27
                                 Changed    N=0.85  L=0.68  H=0.50

  .. note::
     FIRST's re-issued *CVSS 3.0 Specification (v1.9)* restates the PR values to
     match 3.1 (L=0.62 / H=0.27). This tool implements the **legacy 2015 3.0**
     multipliers (L=0.77 / H=0.44), which are the values that make 3.0
     semantically distinct from 3.1 (per the task) and that the reference 3.0
     calculators and the original 2015 specification use. See spec.md.

* **CVSS v4.0** vectors — **full CVSS-BTE**. CVSS v4.0 has **no closed-form
  base-score formula**. Per the FIRST CVSS v4.0 Specification (Sections 8.2 /
  8.3) the score of a vector is the score of its **MacroVector** (a 6-digit
  equivalence class) from an authoritative **lookup table** (:data:`V4_LOOKUP`),
  minus an **interpolated** proportional severity-distance within the
  MacroVector. This module ports that reference algorithm
  (FIRST ``cvss-v4-calculator``) directly. Per spec §8.2 the MacroVector is
  computed from the *effective* metric values — so a vector's score already
  reflects its **Threat** metric (``E``, defaulted to the worst case ``E:A``
  when absent) and its **Environmental** group (``CR/IR/AR`` defaulted to the
  worst case ``H``, the 12 ``M*`` modified base metrics overriding their base
  counterparts when not ``X``, and ``MSI/MSA`` feeding EQ4). Therefore:

  - :func:`base_score` on a v4.0 vector returns the **CVSS-B** score — the
    score of the vector's Base metrics only (``E`` / Environmental / all
    other non-base metrics treated as absent → their worst-case defaults),
    exactly as published in the reference tables;
  - :func:`bte_score` on a v4.0 vector returns the **CVSS-BTE** score — the
    score of the *full* vector as given (a ``CVSS-BE`` / ``CVSS-BT`` / plain
    ``CVSS-B`` score when the corresponding groups are absent). For a
    Base-only vector ``bte_score`` equals :func:`base_score`. (For non-v4
    values — v3.0 / v3.1 / raw — ``bte_score`` is the base score; CVSS v3.x
    temporal scoring is a non-goal.)

  Supplemental metrics (``S/AU/R/V/RE/U``) are accepted but never affect any
  score (spec §5). For any v4.0 vector the severity band and the
  classification's ``severity_source`` are derived from the **BTE** score (a
  Base-only vector's BTE score *is* its Base score, so published CVSS-B
  vectors are unchanged); the classification reports which metric groups the
  applied vector covers in its rationale detail.

  Spec-verified against the FIRST v4.0 *Examples* page corpus (every
  ``Base`` / ``Base+Threat`` / ``Base+Environmental`` / ``BTE`` row), see
  ``tests/test_cvss.py``.

* **Raw numeric scores** — a bare number in ``0.0..10.0`` (e.g. ``"9.8"``)
  carried on a CVSS-typed ``severity`` entry (no vector). It is used as the base
  score directly; no Exploitability sub-score can be derived from it.

Scope
-----
A value that is none of the above, or a malformed vector, is rejected with
:class:`CvssError`; the classification layer treats that as an *unquantified*
severity rather than a hard failure.
"""

from __future__ import annotations

import math
import re
from typing import Dict

from .models import SEVERITY_NONE, SEVERITY_LEVELS  # noqa: F401  (SEVERITY_LEVELS re-exported)

__all__ = [
    "CvssError",
    "roundup",
    "parse_vector",
    "parse_v4_vector",
    "cvss_version",
    "cvss_kind",
    "base_score",
    "bte_score",
    "exploitability",
    "exploitability_source",
    "severity_for_score",
    "severity_from_vector",
    "score_components",
    "EXPLOITABILITY_MAX",
    "CVSS_VERSIONS",
    "V4_LOOKUP",
    "V4_BASE_METRICS",
    "V4_THREAT_METRICS",
    "V4_ENVIRONMENTAL_METRICS",
    "V4_SUPPLEMENTAL_METRICS",
]


class CvssError(ValueError):
    """A CVSS value could not be parsed or is not a supported CVSS value.

    This is *not* an input-schema error: an advisory may carry a severity in a
    scheme the tool cannot quantify, in which case classification reports an
    unquantified severity (``none``) instead of failing the run.
    """


# The CVSS versions this module computes, from oldest to newest.
CVSS_VERSIONS: "tuple[str, ...]" = ("3.0", "3.1", "4.0")

# A bare CVSS version prefix, e.g. the "CVSS:3.1" in "CVSS:3.1/AV:N/...".
_VERSION_PREFIX = re.compile(r"^CVSS[:/]\s*([0-9]+(?:\.[0-9]+)?)\s*/", re.IGNORECASE)
# A value that is *exactly* a bare decimal number (a raw numeric score).
_RAW_SCORE = re.compile(r"^[+-]?([0-9]+(?:\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?$")


def roundup(x: float) -> float:
    """CVSS v3.1 ``Roundup`` (Section 7.5): ceil ``x`` to one decimal.

    Implemented as the spec's recommended integer form (multiply by 100000,
    round, ceiling-divide by 10000) so binary-float error such as
    ``0.1 + 0.2 -> 0.30000000000000004`` cannot push a value across a decimal
    boundary.
    """
    int_input = round(x * 100000)
    if int_input % 10000 == 0:
        return int_input / 100000.0
    return (int_input // 10000 + 1) / 10.0


# --- CVSS v3.0 / v3.1 metric multipliers (FIRST, Table 16) -------------------
_V3_ATTACK_VECTOR = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.20}
# Low complexity is *more* exploitable than high: 0.77 vs 0.44.
_V3_ATTACK_COMPLEXITY = {"L": 0.77, "H": 0.44}
_V3_USER_INTERACTION = {"N": 0.85, "R": 0.62}
_V3_IMPACT_METRIC = {"N": 0.0, "L": 0.22, "H": 0.56}
# Privileges Required, keyed by (version, scope). Only PR differs between 3.0
# and 3.1 — everything else (AV/AC/UI/impact, all formulae) is shared.
_V3_PRIVILEGES = {
    "3.0": {
        "U": {"N": 0.85, "L": 0.77, "H": 0.44},  # legacy 2015 unchanged scope
        "C": {"N": 0.85, "L": 0.77, "H": 0.50},  # legacy 2015 changed scope
    },
    "3.1": {
        "U": {"N": 0.85, "L": 0.62, "H": 0.27},
        "C": {"N": 0.85, "L": 0.68, "H": 0.50},
    },
}

_V3_REQUIRED_METRICS = ("AV", "AC", "PR", "UI", "S", "C", "I", "A")


def cvss_version(value: str) -> "str | None":
    """Return the declared CVSS version (e.g. ``"3.1"``) or ``None`` if absent."""
    m = _VERSION_PREFIX.match((value or "").strip())
    return m.group(1) if m else None


def _strip_prefix(value: str) -> str:
    return _VERSION_PREFIX.sub("", (value or "").strip(), count=1)


def _is_raw_score(value: str) -> bool:
    return bool(_RAW_SCORE.match((value or "").strip()))


def parse_vector(vector: str) -> Dict[str, str]:
    """Parse a **CVSS v3** base vector string into a ``{metric: value}`` dict.

    An optional ``CVSS:3.1/`` (or ``3.0/``) prefix is stripped. All eight v3
    base metrics (``AV AC PR UI S C I A``) must be present; anything else raises
    :class:`CvssError`. Extra (non-base) metrics are ignored so temporal/
    environmental tails do not break base scoring. CVSS v4.0 vectors do **not**
    use these metric names and must go through :func:`base_score` (which
    dispatches on the version).
    """
    v = (vector or "").strip()
    if not v:
        raise CvssError("empty CVSS vector")
    v = _strip_prefix(v)
    if not v:
        raise CvssError(f"CVSS vector has no base metrics: {vector!r}")

    metrics: Dict[str, str] = {}
    for token in v.split("/"):
        if not token:
            continue
        if ":" not in token:
            raise CvssError(f"malformed CVSS metric {token!r} in {vector!r}")
        key, value = token.split(":", 1)
        metrics[key] = value

    for req in _V3_REQUIRED_METRICS:
        if req not in metrics:
            raise CvssError(f"CVSS vector is missing metric {req!r}: {vector!r}")
    return metrics


def parse_v4_vector(vector: str) -> Dict[str, str]:
    """Parse a **CVSS v4.0** vector into a ``{metric: value}`` dict.

    All **32** metrics are recognised — the 11 Base (``AV AC AT PR UI VC VI
    VA SC SI SA``), the Threat metric (``E``), the 15 Environmental (``CR IR
    AR`` plus the 12 modified base metrics ``MAV MAC MAT MPR MUI MVC MVI MVA
    MSC MSI MSA``) and the 6 Supplemental (``S AU R V RE U``; accepted for
    validation, never scored — spec §5). The 11 Base metrics are mandatory;
    every metric that IS present must use a value from its group's allowed set
    (each non-base group allows ``X`` = not defined). Metric order is not
    enforced (the tool stays lenient where the spec's fixed ordering is
    strict) and unrecognised metric names are ignored, so a fuller future
    vector still parses. Anything else (a missing base metric, an invalid
    value, a duplicate metric) raises :class:`CvssError`.
    """
    v = _strip_prefix(vector)
    if not v:
        raise CvssError(f"CVSS 4.0 vector has no base metrics: {vector!r}")

    metrics: Dict[str, str] = {}
    for token in v.split("/"):
        if not token:
            continue
        if ":" not in token:
            raise CvssError(f"malformed CVSS 4.0 metric {token!r} in {vector!r}")
        key, value = token.split(":", 1)
        if key in _V4_KNOWN:
            if key in metrics:
                raise CvssError(f"duplicate CVSS 4.0 metric {key!r} in {vector!r}")
            metrics[key] = value

    for req in _V4_REQUIRED:
        if req not in metrics:
            raise CvssError(f"CVSS 4.0 vector is missing metric {req!r}: {vector!r}")
    for key, value in metrics.items():
        if value not in _V4_VALUES[key]:
            raise CvssError(
                f"invalid {key} value {value!r} in CVSS 4.0 vector {vector!r} "
                f"(expected one of {sorted(_V4_VALUES[key])})"
            )
    return metrics


def _parse_v4(vector: str) -> Dict[str, str]:
    """The Base-metric subset of :func:`parse_v4_vector` (CVSS-B scoring)."""
    full = parse_v4_vector(vector)
    return {k: full[k] for k in _V4_REQUIRED}


def cvss_kind(value: str) -> str:
    """Classify a CVSS value as ``"3.0" | "3.1" | "4.0" | "raw"``.

    A declared ``CVSS:x.y/`` prefix wins. Otherwise a bare decimal number is a
    *raw* score, and a version-less vector is inferred from its metric names
    (v4 uses ``AT/VC/VI/VA/SC/SI/SA``; v3 uses ``S/C/I/A``). Raises
    :class:`CvssError` for an empty or unrecognised value.
    """
    v = (value or "").strip()
    if not v:
        raise CvssError("empty CVSS value")
    version = cvss_version(v)
    if version in CVSS_VERSIONS:
        return version
    if version is not None:
        # A declared CVSS version the tool does not support (e.g. CVSS 2.0).
        raise CvssError(f"unsupported CVSS version {version!r}")
    if _is_raw_score(v):
        return "raw"
    keys = set()
    for token in _strip_prefix(v).split("/"):
        if ":" in token:
            keys.add(token.split(":", 1)[0])
    if keys & {"AT", "VC", "VI", "VA", "SC", "SI", "SA"}:
        return "4.0"
    if keys & {"S", "C", "I", "A"}:
        return "3.1"
    raise CvssError(f"unrecognised CVSS value: {value!r}")


def _lookup(table: Dict[str, float], metric: str, value: str, vector: str) -> float:
    if value not in table:
        raise CvssError(
            f"invalid {metric} value {value!r} in CVSS vector {vector!r} "
            f"(expected one of {sorted(table)})"
        )
    return table[value]


def _base_score_v3(vector: str, version: str) -> float:
    """CVSS v3.0 / v3.1 base score (the only difference is the PR table).

    Per FIRST spec §7.1 the *Exploitability sub-score used in the base formula*
    is the unrounded product ``8.22 * AV * AC * PR * UI``; only the final
    BaseScore is ``Roundup``-ed. (The separately reported sub-score in
    :func:`exploitability` is rounded to one decimal for display/ranking,
    matching the official calculators.)
    """
    m = parse_vector(vector)

    c = _lookup(_V3_IMPACT_METRIC, "C", m["C"], vector)
    i = _lookup(_V3_IMPACT_METRIC, "I", m["I"], vector)
    a = _lookup(_V3_IMPACT_METRIC, "A", m["A"], vector)
    iss = 1.0 - (1.0 - c) * (1.0 - i) * (1.0 - a)

    if m["S"] == "U":
        impact = 6.42 * iss
    elif m["S"] == "C":
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
    else:
        raise CvssError(f"invalid S value {m['S']!r} in CVSS vector {vector!r}")

    if impact <= 0:
        return 0.0

    av = _lookup(_V3_ATTACK_VECTOR, "AV", m["AV"], vector)
    ac = _lookup(_V3_ATTACK_COMPLEXITY, "AC", m["AC"], vector)
    pr_table = _V3_PRIVILEGES[version]["U" if m["S"] == "U" else "C"]
    pr = _lookup(pr_table, "PR", m["PR"], vector)
    ui = _lookup(_V3_USER_INTERACTION, "UI", m["UI"], vector)
    exploitability = 8.22 * av * ac * pr * ui  # unrounded, per spec §7.1

    if m["S"] == "U":
        return roundup(min(impact + exploitability, 10.0))
    return roundup(min(1.08 * (impact + exploitability), 10.0))


# --- CVSS v4.0 MacroVector lookup + interpolation (FIRST spec §8) ------------
# The authoritative score table for every MacroVector (equivalence class).
# Source: FIRST CVSS v4.0 Specification §8.3 — the ``cvss_lookup.js`` file of
# the reference implementation (FIRSTdotorg/cvss-v4-calculator), mirrored in
# RedHat's cvss-v4-calculator ``cvss40.js``. Copyright FIRST, Red Hat, and
# contributors (SPDX-License-Identifier: BSD-2-Clause).
V4_LOOKUP: Dict[str, float] = {
    "000000": 10.0, "000001": 9.9, "000010": 9.8, "000011": 9.5, "000020": 9.5, "000021": 9.2,
    "000100": 10.0, "000101": 9.6, "000110": 9.3, "000111": 8.7, "000120": 9.1, "000121": 8.1,
    "000200": 9.3, "000201": 9.0, "000210": 8.9, "000211": 8.0, "000220": 8.1, "000221": 6.8,
    "001000": 9.8, "001001": 9.5, "001010": 9.5, "001011": 9.2, "001020": 9.0, "001021": 8.4,
    "001100": 9.3, "001101": 9.2, "001110": 8.9, "001111": 8.1, "001120": 8.1, "001121": 6.5,
    "001200": 8.8, "001201": 8.0, "001210": 7.8, "001211": 7.0, "001220": 6.9, "001221": 4.8,
    "002001": 9.2, "002011": 8.2, "002021": 7.2, "002101": 7.9, "002111": 6.9, "002121": 5.0,
    "002201": 6.9, "002211": 5.5, "002221": 2.7,
    "010000": 9.9, "010001": 9.7, "010010": 9.5, "010011": 9.2, "010020": 9.2, "010021": 8.5,
    "010100": 9.5, "010101": 9.1, "010110": 9.0, "010111": 8.3, "010120": 8.4, "010121": 7.1,
    "010200": 9.2, "010201": 8.1, "010210": 8.2, "010211": 7.1, "010220": 7.2, "010221": 5.3,
    "011000": 9.5, "011001": 9.3, "011010": 9.2, "011011": 8.5, "011020": 8.5, "011021": 7.3,
    "011100": 9.2, "011101": 8.2, "011110": 8.0, "011111": 7.2, "011120": 7.0, "011121": 5.9,
    "011200": 8.4, "011201": 7.0, "011210": 7.1, "011211": 5.2, "011220": 5.0, "011221": 3.0,
    "012001": 8.6, "012011": 7.5, "012021": 5.2, "012101": 7.1, "012111": 5.2, "012121": 2.9,
    "012201": 6.3, "012211": 2.9, "012221": 1.7,
    "100000": 9.8, "100001": 9.5, "100010": 9.4, "100011": 8.7, "100020": 9.1, "100021": 8.1,
    "100100": 9.4, "100101": 8.9, "100110": 8.6, "100111": 7.4, "100120": 7.7, "100121": 6.4,
    "100200": 8.7, "100201": 7.5, "100210": 7.4, "100211": 6.3, "100220": 6.3, "100221": 4.9,
    "101000": 9.4, "101001": 8.9, "101010": 8.8, "101011": 7.7, "101020": 7.6, "101021": 6.7,
    "101100": 8.6, "101101": 7.6, "101110": 7.4, "101111": 5.8, "101120": 5.9, "101121": 5.0,
    "101200": 7.2, "101201": 5.7, "101210": 5.7, "101211": 5.2, "101220": 5.2, "101221": 2.5,
    "102001": 8.3, "102011": 7.0, "102021": 5.4, "102101": 6.5, "102111": 5.8, "102121": 2.6,
    "102201": 5.3, "102211": 2.1, "102221": 1.3,
    "110000": 9.5, "110001": 9.0, "110010": 8.8, "110011": 7.6, "110020": 7.6, "110021": 7.0,
    "110100": 9.0, "110101": 7.7, "110110": 7.5, "110111": 6.2, "110120": 6.1, "110121": 5.3,
    "110200": 7.7, "110201": 6.6, "110210": 6.8, "110211": 5.9, "110220": 5.2, "110221": 3.0,
    "111000": 8.9, "111001": 7.8, "111010": 7.6, "111011": 6.7, "111020": 6.2, "111021": 5.8,
    "111100": 7.4, "111101": 5.9, "111110": 5.7, "111111": 5.7, "111120": 4.7, "111121": 2.3,
    "111200": 6.1, "111201": 5.2, "111210": 5.7, "111211": 2.9, "111220": 2.4, "111221": 1.6,
    "112001": 7.1, "112011": 5.9, "112021": 3.0, "112101": 5.8, "112111": 2.6, "112121": 1.5,
    "112201": 2.3, "112211": 1.3, "112221": 0.6,
    "200000": 9.3, "200001": 8.7, "200010": 8.6, "200011": 7.2, "200020": 7.5, "200021": 5.8,
    "200100": 8.6, "200101": 7.4, "200110": 7.4, "200111": 6.1, "200120": 5.6, "200121": 3.4,
    "200200": 7.0, "200201": 5.4, "200210": 5.2, "200211": 4.0, "200220": 4.0, "200221": 2.2,
    "201000": 8.5, "201001": 7.5, "201010": 7.4, "201011": 5.5, "201020": 6.2, "201021": 5.1,
    "201100": 7.2, "201101": 5.7, "201110": 5.5, "201111": 4.1, "201120": 4.6, "201121": 1.9,
    "201200": 5.3, "201201": 3.6, "201210": 3.4, "201211": 1.9, "201220": 1.9, "201221": 0.8,
    "202001": 6.4, "202011": 5.1, "202021": 2.0, "202101": 4.7, "202111": 2.1, "202121": 1.1,
    "202201": 2.4, "202211": 0.9, "202221": 0.4,
    "210000": 8.8, "210001": 7.5, "210010": 7.3, "210011": 5.3, "210020": 6.0, "210021": 5.0,
    "210100": 7.3, "210101": 5.5, "210110": 5.9, "210111": 4.0, "210120": 4.1, "210121": 2.0,
    "210200": 5.4, "210201": 4.3, "210210": 4.5, "210211": 2.2, "210220": 2.0, "210221": 1.1,
    "211000": 7.5, "211001": 5.5, "211010": 5.8, "211011": 4.5, "211020": 4.0, "211021": 2.1,
    "211100": 6.1, "211101": 5.1, "211110": 4.8, "211111": 1.8, "211120": 2.0, "211121": 0.9,
    "211200": 4.6, "211201": 1.8, "211210": 1.7, "211211": 0.7, "211220": 0.8, "211221": 0.2,
    "212001": 5.3, "212011": 2.4, "212021": 1.4, "212101": 2.4, "212111": 1.2, "212121": 0.5,
    "212201": 1.0, "212211": 0.3, "212221": 0.1,
}

# The highest-severity (representative) vectors of each MacroVector level, used
# to measure a vector's severity-distance inside its MacroVector (FIRST spec
# §8.2, ``max_composed.js``). ``"eq3"`` is the joint EQ3+EQ6 subgroup keyed by
# (eq3, eq6).
_V4_MAX_COMPOSED = {
    "eq1": {
        0: ["AV:N/PR:N/UI:N/"],
        1: ["AV:A/PR:N/UI:N/", "AV:N/PR:L/UI:N/", "AV:N/PR:N/UI:P/"],
        2: ["AV:P/PR:N/UI:N/", "AV:A/PR:L/UI:P/"],
    },
    "eq2": {
        0: ["AC:L/AT:N/"],
        1: ["AC:H/AT:N/", "AC:L/AT:P/"],
    },
    "eq3": {
        0: {
            "0": ["VC:H/VI:H/VA:H/CR:H/IR:H/AR:H/"],
            "1": ["VC:H/VI:H/VA:L/CR:M/IR:M/AR:H/",
                  "VC:H/VI:H/VA:H/CR:M/IR:M/AR:M/"],
        },
        1: {
            "0": ["VC:L/VI:H/VA:H/CR:H/IR:H/AR:H/",
                  "VC:H/VI:L/VA:H/CR:H/IR:H/AR:H/"],
            "1": ["VC:L/VI:H/VA:L/CR:H/IR:M/AR:H/",
                  "VC:L/VI:H/VA:H/CR:H/IR:M/AR:M/",
                  "VC:H/VI:L/VA:H/CR:M/IR:H/AR:M/",
                  "VC:H/VI:L/VA:L/CR:M/IR:H/AR:H/",
                  "VC:L/VI:L/VA:H/CR:H/IR:H/AR:M/"],
        },
        2: {"1": ["VC:L/VI:L/VA:L/CR:H/IR:H/AR:H/"]},
    },
    "eq4": {
        0: ["SC:H/SI:S/SA:S/"],
        1: ["SC:H/SI:H/SA:H/"],
        2: ["SC:L/SI:L/SA:L/"],
    },
    "eq5": {
        0: ["E:A/"],
        1: ["E:P/"],
        2: ["E:U/"],
    },
}

# Max severity distance of each MacroVector level (FIRST spec §8.2,
# ``max_severity.js``); scaled by the 0.1 step in :func:`_base_score_v4`.
_V4_MAX_SEVERITY = {
    "eq1": {0: 1, 1: 4, 2: 5},
    "eq2": {0: 1, 1: 2},
    "eq3eq6": {0: {0: 7, 1: 6}, 1: {0: 8, 1: 8}, 2: {1: 10}},
    "eq4": {0: 6, 1: 5, 2: 4},
    "eq5": {0: 1, 1: 1, 2: 1},
}

# Severity-distance ordering of each metric's values (smaller = more severe).
_V4_LEVELS = {
    "AV": {"N": 0.0, "A": 0.1, "L": 0.2, "P": 0.3},
    "PR": {"N": 0.0, "L": 0.1, "H": 0.2},
    "UI": {"N": 0.0, "P": 0.1, "A": 0.2},
    "AC": {"L": 0.0, "H": 0.1},
    "AT": {"N": 0.0, "P": 0.1},
    "VC": {"H": 0.0, "L": 0.1, "N": 0.2},
    "VI": {"H": 0.0, "L": 0.1, "N": 0.2},
    "VA": {"H": 0.0, "L": 0.1, "N": 0.2},
    "SC": {"H": 0.1, "L": 0.2, "N": 0.3},
    "SI": {"S": 0.0, "H": 0.1, "L": 0.2, "N": 0.3},
    "SA": {"S": 0.0, "H": 0.1, "L": 0.2, "N": 0.3},
    "CR": {"H": 0.0, "M": 0.1, "L": 0.2},
    "IR": {"H": 0.0, "M": 0.1, "L": 0.2},
    "AR": {"H": 0.0, "M": 0.1, "L": 0.2},
}

# CVSS v4.0 metric groups and their allowed values (FIRST spec §2 / §7;
# the reference calculator's ``expectedMetricOrder``). Note SI/SA accept
# ``S`` (Safety) in the distance/interpolation tables but only N/L/H as a
# *base* value; the modified-subsequent metrics MSI/MSA additionally accept
# ``S`` as a value. ``X`` (not defined) is allowed on every non-base metric.
V4_BASE_METRICS: "tuple[str, ...]" = (
    "AV", "AC", "AT", "PR", "UI", "VC", "VI", "VA", "SC", "SI", "SA"
)
V4_THREAT_METRICS: "tuple[str, ...]" = ("E",)
V4_ENVIRONMENTAL_METRICS: "tuple[str, ...]" = (
    "CR", "IR", "AR", "MAV", "MAC", "MAT", "MPR", "MUI",
    "MVC", "MVI", "MVA", "MSC", "MSI", "MSA",
)
V4_SUPPLEMENTAL_METRICS: "tuple[str, ...]" = ("S", "AU", "R", "V", "RE", "U")

_V4_VALUES: Dict[str, "set[str]"] = {
    # Base (all mandatory in a vector string).
    "AV": {"N", "A", "L", "P"},
    "AC": {"L", "H"},
    "AT": {"N", "P"},
    "PR": {"N", "L", "H"},
    "UI": {"N", "P", "A"},
    "VC": {"N", "L", "H"},
    "VI": {"N", "L", "H"},
    "VA": {"N", "L", "H"},
    "SC": {"N", "L", "H"},
    "SI": {"N", "L", "H"},
    "SA": {"N", "L", "H"},
    # Threat (E defaults to the worst case A when absent — spec §8.2 Table 28).
    "E": {"X", "A", "P", "U"},
    # Environmental: security requirements + modified base metrics.
    "CR": {"X", "H", "M", "L"},
    "IR": {"X", "H", "M", "L"},
    "AR": {"X", "H", "M", "L"},
    "MAV": {"X", "N", "A", "L", "P"},
    "MAC": {"X", "L", "H"},
    "MAT": {"X", "N", "P"},
    "MPR": {"X", "N", "L", "H"},
    "MUI": {"X", "N", "P", "A"},
    "MVC": {"X", "H", "L", "N"},
    "MVI": {"X", "H", "L", "N"},
    "MVA": {"X", "H", "L", "N"},
    "MSC": {"X", "H", "L", "N"},
    "MSI": {"X", "S", "H", "L", "N"},
    "MSA": {"X", "S", "H", "L", "N"},
    # Supplemental (validated, never scored — spec §5).
    "S": {"X", "N", "P"},
    "AU": {"X", "N", "Y"},
    "R": {"X", "A", "U", "I"},
    "V": {"X", "D", "C"},
    "RE": {"X", "L", "M", "H"},
    "U": {"X", "Clear", "Green", "Amber", "Red"},
}
_V4_KNOWN: "tuple[str, ...]" = tuple(_V4_VALUES)
_V4_REQUIRED = V4_BASE_METRICS

# The 14 metrics whose severity-distance is measured inside a MacroVector.
_V4_DISTANCE_METRICS = (
    "AV", "PR", "UI", "AC", "AT", "VC", "VI", "VA",
    "SC", "SI", "SA", "CR", "IR", "AR",
)


def _v4_effective(sel: Dict[str, str], metric: str) -> str:
    """A metric's effective value: defaults + environmental override (spec §4.2)."""
    selected = sel.get(metric, "X")
    if metric == "E" and selected == "X":
        return "A"
    if metric in ("CR", "IR", "AR") and selected == "X":
        return "H"
    modified = "M" + metric
    if modified in sel and sel[modified] != "X":
        return sel[modified]
    return selected


def _v4_macrovector(sel: Dict[str, str]) -> str:
    """Compute the 6-digit MacroVector (EQ1..EQ6) for a parsed v4 vector."""
    av, pr, ui = (
        _v4_effective(sel, "AV"), _v4_effective(sel, "PR"), _v4_effective(sel, "UI"))
    if av == "N" and pr == "N" and ui == "N":
        eq1 = 0
    elif (av == "N" or pr == "N" or ui == "N") and \
            not (av == "N" and pr == "N" and ui == "N") and av != "P":
        eq1 = 1
    else:
        eq1 = 2

    ac, at = _v4_effective(sel, "AC"), _v4_effective(sel, "AT")
    eq2 = 0 if (ac == "L" and at == "N") else 1

    vc, vi, va = (
        _v4_effective(sel, "VC"), _v4_effective(sel, "VI"), _v4_effective(sel, "VA"))
    if vc == "H" and vi == "H":
        eq3 = 0
    elif not (vc == "H" and vi == "H") and (vc == "H" or vi == "H" or va == "H"):
        eq3 = 1
    else:
        eq3 = 2

    msi, msa = _v4_effective(sel, "MSI"), _v4_effective(sel, "MSA")
    sc, si, sa = (
        _v4_effective(sel, "SC"), _v4_effective(sel, "SI"), _v4_effective(sel, "SA"))
    if msi == "S" or msa == "S":
        eq4 = 0
    elif not (msi == "S" or msa == "S") and (sc == "H" or si == "H" or sa == "H"):
        eq4 = 1
    else:
        eq4 = 2

    eq5 = {"A": 0, "P": 1, "U": 2}.get(_v4_effective(sel, "E"), 0)

    cr, ir, ar = (
        _v4_effective(sel, "CR"), _v4_effective(sel, "IR"), _v4_effective(sel, "AR"))
    if (cr == "H" and vc == "H") or (ir == "H" and vi == "H") or (ar == "H" and va == "H"):
        eq6 = 0
    else:
        eq6 = 1

    return f"{eq1}{eq2}{eq3}{eq4}{eq5}{eq6}"


def _v4_extract_metric_value(metric: str, composed: str) -> str:
    idx = composed.find(metric)
    extracted = composed[idx + len(metric) + 1:]
    if "/" in extracted:
        return extracted.split("/", 1)[0]
    return extracted


def _round1(x: float) -> float:
    """Round to one decimal, exactly like the reference calculator.

    The official FIRST ``cvss-v4-calculator`` ends with
    ``Math.round(value * 10) / 10`` — for non-negative values that is
    ``floor(x*10 + 0.5)/10`` (halves round up). We match it bit-for-bit.

    .. note::
       This deliberately differs from the ``Decimal(x + 1e-6)`` half-up
       rounding used by some third-party Python ports. On a few hundred
       boundary vectors (raw values like ``8.549999999999999``) the
       epsilon port says ``8.6`` while the official calculator says
       ``8.5``; the official behaviour wins here, because the FIRST v4.0
       spec (§8.3) points at that calculator as the authoritative
       implementation.
    """
    return math.floor(x * 10 + 0.5) / 10


def _v4_score(value: str, full: bool) -> float:
    """Score a CVSS v4.0 vector (spec §8) as CVSS-B or full CVSS-BTE.

    ``full=False`` scores the Base metrics only (non-base metrics treated as
    absent → their worst-case defaults) — the CVSS-B score. ``full=True``
    scores the *complete* vector as given, so the Threat metric and the
    Environmental group (modified base metrics, security requirements,
    MSI/MSA) take effect — the CVSS-BTE score. Both use the same
    MacroVector lookup + interpolation algorithm; per spec §8.2 the
    MacroVector is built from the *effective* metric values, which is exactly
    what :func:`_v4_macrovector` / :func:`_v4_effective` do.
    """
    sel = parse_v4_vector(value) if full else _parse_v4(value)

    # No-impact shortcut: a vector with impact on neither system is 0.0.
    if all(_v4_effective(sel, m) == "N" for m in ("VC", "VI", "VA", "SC", "SI", "SA")):
        return 0.0

    mv = _v4_macrovector(sel)
    value_score = V4_LOOKUP[mv]
    eq1, eq2, eq3, eq4, eq5, eq6 = (int(c) for c in mv)

    # Next-lower MacroVector along each EQ axis (its score is the "floor").
    eq1_lower = f"{eq1 + 1}{eq2}{eq3}{eq4}{eq5}{eq6}"
    eq2_lower = f"{eq1}{eq2 + 1}{eq3}{eq4}{eq5}{eq6}"
    if (eq3, eq6) in ((1, 1), (0, 1)):
        eq36_lower = f"{eq1}{eq2}{eq3 + 1}{eq4}{eq5}{eq6}"
    elif eq3 == 1 and eq6 == 0:
        eq36_lower = f"{eq1}{eq2}{eq3}{eq4}{eq5}{eq6 + 1}"
    elif eq3 == 0 and eq6 == 0:
        eq36_lower_left = f"{eq1}{eq2}{eq3}{eq4}{eq5}{eq6 + 1}"
        eq36_lower_right = f"{eq1}{eq2}{eq3 + 1}{eq4}{eq5}{eq6}"
    else:
        eq36_lower = f"{eq1}{eq2}{eq3 + 1}{eq4}{eq5}{eq6 + 1}"
    eq4_lower = f"{eq1}{eq2}{eq3}{eq4 + 1}{eq5}{eq6}"
    eq5_lower = f"{eq1}{eq2}{eq3}{eq4}{eq5 + 1}{eq6}"

    floor_eq1 = V4_LOOKUP.get(eq1_lower)
    floor_eq2 = V4_LOOKUP.get(eq2_lower)
    if eq3 == 0 and eq6 == 0:
        left = V4_LOOKUP.get(eq36_lower_left)
        right = V4_LOOKUP.get(eq36_lower_right)
        if left is not None and right is not None:
            floor_eq36 = max(left, right)
        else:
            floor_eq36 = left if left is not None else right
    else:
        floor_eq36 = V4_LOOKUP.get(eq36_lower)
    floor_eq4 = V4_LOOKUP.get(eq4_lower)
    floor_eq5 = V4_LOOKUP.get(eq5_lower)

    # Highest-severity vectors of this MacroVector (to measure the distance).
    eq1_maxes = _V4_MAX_COMPOSED["eq1"][eq1]
    eq2_maxes = _V4_MAX_COMPOSED["eq2"][eq2]
    eq36_maxes = _V4_MAX_COMPOSED["eq3"][eq3][str(eq6)]
    eq4_maxes = _V4_MAX_COMPOSED["eq4"][eq4]
    eq5_maxes = _V4_MAX_COMPOSED["eq5"][eq5]
    max_vectors = [
        a + b + c + d + e
        for a in eq1_maxes for b in eq2_maxes for c in eq36_maxes
        for d in eq4_maxes for e in eq5_maxes
    ]

    # Find the first highest-severity vector whose metrics are all >= ours.
    dists: "list[float]" = [0.0] * len(_V4_DISTANCE_METRICS)
    for composed in max_vectors:
        d = [
            _V4_LEVELS[mt][_v4_effective(sel, mt)]
            - _V4_LEVELS[mt][_v4_extract_metric_value(mt, composed)]
            for mt in _V4_DISTANCE_METRICS
        ]
        if any(x < 0 for x in d):
            continue
        dists = d
        break

    cur = {
        1: dists[0] + dists[1] + dists[2],                 # AV+PR+UI
        2: dists[3] + dists[4],                             # AC+AT
        36: dists[5] + dists[6] + dists[7] + dists[11] + dists[12] + dists[13],
        4: dists[8] + dists[9] + dists[10],                 # SC+SI+SA
        5: 0.0,
    }
    step = 0.1
    max_dist = {
        1: _V4_MAX_SEVERITY["eq1"][eq1] * step,
        2: _V4_MAX_SEVERITY["eq2"][eq2] * step,
        36: _V4_MAX_SEVERITY["eq3eq6"][eq3][eq6] * step,
        4: _V4_MAX_SEVERITY["eq4"][eq4] * step,
    }

    floors = {1: floor_eq1, 2: floor_eq2, 36: floor_eq36, 4: floor_eq4, 5: floor_eq5}
    n_existing = 0
    total = 0.0
    for axis in (1, 2, 36, 4, 5):
        floor = floors[axis]
        if floor is None:
            continue
        n_existing += 1
        if axis == 5:  # EQ5's proportional distance is always 0
            continue
        total += (value_score - floor) * (cur[axis] / max_dist[axis])

    mean = (total / n_existing) if n_existing else 0.0
    value_score -= mean
    value_score = max(0.0, min(10.0, value_score))
    return _round1(value_score)


def base_score(value: str) -> float:
    """Compute the **base score** (0.0–10.0, one decimal) for a CVSS value.

    Dispatches on :func:`cvss_kind`: CVSS v3.0 / v3.1 base vectors (closed
    form), CVSS v4.0 base vectors (MacroVector lookup + interpolation), or a
    raw numeric score. Raises :class:`CvssError` for a malformed value or a
    CVSS version the tool does not support.
    """
    kind = cvss_kind(value)
    if kind == "raw":
        s = float(value.strip())
        if not (0.0 <= s <= 10.0):
            raise CvssError(f"raw CVSS score {value!r} is outside 0.0–10.0")
        return round(s, 1)
    if kind == "4.0":
        return _v4_score(value, full=False)
    return _base_score_v3(value, kind)


def bte_score(value: str) -> float:
    """The **CVSS-BTE** score (0.0–10.0, one decimal) for a CVSS value.

    For a CVSS v4.0 vector this is the score of the *complete* vector as
    given (spec §8.2/§8.3, applied to the full metric set): the MacroVector
    is built from the effective metric values, so the Threat metric (``E``;
    ``E:X``/absent → worst case ``E:A``) and the Environmental group
    (``CR/IR/AR`` → worst case ``H`` when absent, ``M*`` overrides,
    ``MSI/MSA``) shape it. A Base-only v4.0 vector therefore scores exactly
    as its CVSS-B (see :func:`base_score`). For non-v4 values (v3.0 / 3.1
    vectors, raw numeric scores) there is no v4 Threat/Environmental group,
    so the BTE score is the base score. Raises :class:`CvssError` like
    :func:`base_score`.
    """
    kind = cvss_kind(value)
    if kind == "4.0":
        return _v4_score(value, full=True)
    return base_score(value)


def exploitability(value: str) -> float:
    """The CVSS **Exploitability sub-score** for a value (the ranking signal).

    For a CVSS v3.0 / v3.1 vector this is the spec's
    ``Roundup(8.22 * AV * AC * PR * UI)`` (0.2–3.9, §7.2) — the documented
    *exploitability signal* the ranking subtask (item 6) orders by after
    severity. CVSS v4.0 vectors and raw numeric scores have **no** v3-style
    sub-score, so this returns ``0.0`` for them (see
    :func:`exploitability_source`). Raises :class:`CvssError` like
    :func:`base_score` for malformed values / unsupported versions.
    """
    kind = cvss_kind(value)
    if kind not in ("3.0", "3.1"):
        return 0.0
    m = parse_vector(value)
    av = _lookup(_V3_ATTACK_VECTOR, "AV", m["AV"], value)
    ac = _lookup(_V3_ATTACK_COMPLEXITY, "AC", m["AC"], value)
    pr_table = _V3_PRIVILEGES[kind]["U" if m["S"] == "U" else "C"]
    pr = _lookup(pr_table, "PR", m["PR"], value)
    ui = _lookup(_V3_USER_INTERACTION, "UI", m["UI"], value)
    return roundup(8.22 * av * ac * pr * ui)


def exploitability_source(value: str) -> str:
    """``"cvss"`` when a v3 Exploitability sub-score is computable, else ``"none"``.

    A CVSS v3.0 / v3.1 vector yields ``"cvss"``; a CVSS v4.0 vector or a raw
    numeric score yields ``"none"`` (the sub-score is 0.0 and must not be
    over-claimed, mirroring the unquantified-severity policy).
    """
    kind = cvss_kind(value)
    return "cvss" if kind in ("3.0", "3.1") else "none"


# The maximum CVSS 3.x exploitability sub-score (AV:N/AC:L/PR:N/UI:N). The
# ranking module quotes the raw sub-score (0.0–3.9); this constant documents
# the top of the scale.
EXPLOITABILITY_MAX: float = 3.9


def severity_for_score(score: float) -> str:
    """Map a numeric CVSS base score to the ``low/medium/high/critical`` band.

    Bands follow the CVSS qualitative rating scale (NVD): ``0.0`` is treated
    as :data:`~.models.SEVERITY_NONE` (no impact), then ``low`` (0.1–3.9),
    ``medium`` (4.0–6.9), ``high`` (7.0–8.9), ``critical`` (9.0–10.0).
    """
    if score <= 0.0:
        return SEVERITY_NONE
    if score <= 3.9:
        return "low"
    if score <= 6.9:
        return "medium"
    if score <= 8.9:
        return "high"
    return "critical"


def severity_from_vector(value: str) -> "tuple[str, float]":
    """Return ``(severity, score)`` for a CVSS value (any supported kind).

    The score is the **BTE** score (:func:`bte_score`) — for a CVSS v4.0
    vector that is the full-vector score (equal to the Base score for a
    Base-only vector); for v3.0 / 3.1 vectors and raw numeric scores it is
    the base score. Convenience wrapper for the classification layer; raises
    :class:`CvssError` like :func:`base_score`.
    """
    score = bte_score(value)
    return severity_for_score(score), score


def score_components(value: str) -> "tuple[str, ...]":
    """Which scoring groups a CVSS value's score reflects.

    Always starts with ``"base"``. A CVSS v4.0 vector additionally carries
    ``"threat"`` when it names an ``E`` metric (``E:X`` still counts as
    naming it — the worst case is applied) and ``"environmental"`` when it
    names any Environmental metric. v3.0 / 3.1 vectors and raw numeric
    scores have only the base group (v3.x temporal scoring is a non-goal).
    Raises :class:`CvssError` like :func:`base_score`.
    """
    kind = cvss_kind(value)
    if kind != "4.0":
        return ("base",)
    m = parse_v4_vector(value)
    groups = ["base"]
    if "E" in m:
        groups.append("threat")
    if any(k in m for k in V4_ENVIRONMENTAL_METRICS):
        groups.append("environmental")
    return tuple(groups)
