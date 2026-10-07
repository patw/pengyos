"""The five checks, implemented.

Each check definition in :mod:`pengyos_verifier.checks` is realized here
as one **pluggable, individually-testable function**:

    def check_<name>(ctx: CheckContext) -> Finding

A check function

* takes a single :class:`CheckContext` (the build output dir, the
  attest log path, and the repro threshold) and returns exactly one
  :class:`~pengyos_verifier.model.Finding` for its
  ``checks.<CHECK_*>`` id;
* never raises for build-side conditions — a missing or unreadable
  target is a FAIL finding with evidence, because a witness that
  cannot see the evidence cannot pass it;
* is runnable alone (tests do this) or through the registry
  :data:`CHECK_RUNNERS` / :func:`run_checks`.

The registry maps the stable check ids to their functions in report
order (``checks.CHECK_IDS``), so a new check plugs in by writing one
such function and adding it to :data:`CHECK_RUNNERS`; nothing else in
the tool knows about the individual functions.

Aggregating the findings into a
:class:`~pengyos_verifier.model.Report` is :func:`build_report` (the
"Aggregate into a report" subtask); the CLI wiring lives in
:mod:`pengyos_verifier.cli`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple

from pengyos_verifier import attest, checks, ed25519, repro_format, \
    sbom_format, signature_format
from pengyos_verifier.model import Finding, Report, Status


@dataclass(frozen=True)
class CheckContext:
    """Everything a check may need, in one place.

    Attributes:
        build_dir:       the build output directory to inspect.
        attest_path:     the attest log (given by ``--attest``; NOT
                         required to live under ``build_dir``).
        repro_threshold: minimum repro score for the ``repro-score``
                         check (``checks.DEFAULT_REPRO_THRESHOLD`` by
                         default — the witness default is a
                         byte-identical rebuild).
        public_key:      the 32-byte ed25519 public key to verify the
                         ``.sig`` signatures against (given by
                         ``--pub FILE``); ``None`` keeps the
                         key-independent verification only.
    """

    build_dir: str
    attest_path: str
    repro_threshold: float = checks.DEFAULT_REPRO_THRESHOLD
    public_key: Optional[bytes] = None


CheckFunc = Callable[["CheckContext"], "Finding"]


def _join(base: str, rel: str) -> str:
    return os.path.join(base, rel)


# ----------------------------------------------------------------------
# 1. attest-chain
# ----------------------------------------------------------------------

def check_attest_chain(ctx: CheckContext) -> Finding:
    """Walk the attest log as an append-only hash chain.

    A missing or empty log is a valid empty chain (PASS). Any walk
    failure (unreadable log, bad JSON, schema, self-hash, seq
    contiguity, or linkage) is a FAIL naming the failing line/seq.
    """
    check_id = checks.CHECK_ATTEST_CHAIN
    if not os.path.exists(ctx.attest_path):
        return Finding(
            check_id, Status.PASS,
            f"attest log {ctx.attest_path} is missing — valid empty chain",
            (),
        )
    try:
        with open(ctx.attest_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return Finding(
            check_id, Status.FAIL,
            f"attest log {ctx.attest_path} could not be read",
            (f"{ctx.attest_path}: {exc}",),
        )
    result = attest.verify_attest_text(text)
    if result.ok:
        evidence = (f"{ctx.attest_path}: {result.record_count} records, "
                    "chain valid",)
        return Finding(check_id, Status.PASS,
                       "attest chain valid", evidence)
    evidence = [
        f"{ctx.attest_path}: line {result.first_bad_line}: {result.reason}"
    ]
    if result.first_bad_seq is not None:
        evidence.append(f"seq {result.first_bad_seq}")
    return Finding(check_id, Status.FAIL,
                   f"attest chain broken: {result.reason}",
                   tuple(evidence))


# ----------------------------------------------------------------------
# 2. sbom
# ----------------------------------------------------------------------

def check_sbom(ctx: CheckContext) -> Finding:
    """The build dir contains exactly one conventional, valid SBOM."""
    check_id = checks.CHECK_SBOM
    found = [name for name in checks.SBOM_FILENAMES
             if os.path.isfile(_join(ctx.build_dir, name))]
    if not found:
        return Finding(
            check_id, Status.FAIL, "no SBOM found under conventional names",
            tuple(f"{_join(ctx.build_dir, name)}: missing"
                  for name in checks.SBOM_FILENAMES),
        )
    if len(found) > 1:
        return Finding(
            check_id, Status.FAIL,
            "multiple SBOMs found under conventional names — exactly one "
            "is expected",
            tuple(_join(ctx.build_dir, name) for name in found),
        )
    name = found[0]
    path = _join(ctx.build_dir, name)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return Finding(check_id, Status.FAIL,
                       f"SBOM {path} could not be read",
                       (f"{path}: {exc}",))
    try:
        data = json.loads(text)
    except ValueError as exc:
        return Finding(check_id, Status.FAIL,
                       f"SBOM {path} is not valid JSON",
                       (f"{path}: {exc}",))
    errors = sbom_format.sbom_errors(data)
    if errors:
        return Finding(check_id, Status.FAIL,
                       f"SBOM {path} is not structurally valid: "
                       f"{errors[0]}",
                       tuple(f"{path}: {err}" for err in errors))
    fmt = sbom_format.detect_format(data)
    return Finding(check_id, Status.PASS,
                   f"SBOM {name} is a valid {fmt} document",
                   (f"{path}: format={fmt}",))


# ----------------------------------------------------------------------
# 3. signatures
# ----------------------------------------------------------------------

def check_signatures(ctx: CheckContext) -> Finding:
    """Every expected artifact has a bound, well-formed ``.sig``.

    Always enforces the key-independent part of ``pengyos-sig-v1``:
    wire format plus the header's sha256 equal to the artifact's
    actual sha256 (the signature is bound to this exact artifact).
    When ``ctx.public_key`` is supplied (``--pub``), each artifact's
    signature is additionally verified: the header ``keyid`` must be
    ``SHA-256(public_key)`` (the signature is bound to THE supplied
    key) and the 64-byte body must be a valid ed25519 signature over
    the artifact's SHA-256 digest (the file is streamed to that digest,
    so large images are never held in memory). All expected artifacts
    are inspected and
    every problem is reported, not just the first.
    """
    check_id = checks.CHECK_SIGNATURES
    problems: list = []
    pass_evidence: list = []
    for artifact in checks.EXPECTED_ARTIFACTS:
        art_path = _join(ctx.build_dir, artifact)
        sig_path = _join(ctx.build_dir, artifact + checks.SIGNATURE_SUFFIX)
        art_missing = not os.path.isfile(art_path)
        sig_missing = not os.path.isfile(sig_path)
        if art_missing:
            problems.append(f"{art_path}: missing (cannot verify its "
                            f"signature)")
            if sig_missing:
                problems.append(f"{sig_path}: missing")
            continue
        if sig_missing:
            problems.append(f"{sig_path}: missing")
            continue
        try:
            with open(sig_path, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            problems.append(f"{sig_path}: unreadable: {exc}")
            continue
        errors = signature_format.sig_file_errors(raw)
        if errors:
            problems.append(f"{sig_path}: {errors[0]}")
            continue
        # Structurally valid: re-parse the header for its digest.
        header_line = raw.split(b"\n", 1)[0].decode("ascii")
        header = signature_format.parse_sig_header_line(header_line)
        if header is None:  # unreachable for sig_file_errors == []
            problems.append(f"{sig_path}: header failed to parse")
            continue
        try:
            actual = signature_format.sha256_file(art_path)
        except OSError as exc:
            problems.append(f"{art_path}: unreadable: {exc}")
            continue
        if header.hash != actual:
            problems.append(
                f"{sig_path}: embedded hash {header.hash} does not match "
                f"artifact sha256 {actual} (signature not bound to this "
                "artifact)"
            )
            continue
        if ctx.public_key is not None:
            expected_keyid = signature_format.keyid_of(ctx.public_key)
            if header.keyid != expected_keyid:
                problems.append(
                    f"{sig_path}: keyid {header.keyid} does not match "
                    f"SHA-256 of the supplied public key ({expected_keyid})"
                )
                continue
            body = raw.split(b"\n", 1)[1]
            # pengyos-sig-v1 (pengyos-sign FORMAT.md, normative): the ed25519
            # body is over the artifact's SHA-256 *digest* (the header.hash),
            # NOT the raw file bytes. `actual` is that digest, streamed above,
            # so a multi-GB image is still never held in memory.
            if not ed25519.verify(bytes.fromhex(actual), body, ctx.public_key):
                problems.append(
                    f"{sig_path}: ed25519 signature does not verify "
                    f"against the supplied public key (keyid "
                    f"{header.keyid})"
                )
                continue
            pass_evidence.append(
                f"{artifact}: signature verified "
                f"(ed25519, sha256 {actual}, keyid {header.keyid})"
            )
        else:
            pass_evidence.append(
                f"{artifact}: signature bound "
                f"(sha256 {actual}, keyid {header.keyid})"
            )
    if problems:
        return Finding(check_id, Status.FAIL,
                       f"{len(problems)} signature problem(s) across "
                       f"{len(checks.EXPECTED_ARTIFACTS)} expected "
                       "artifacts",
                       tuple(problems))
    return Finding(check_id, Status.PASS,
                   "every expected artifact is signed and bound to it",
                   tuple(pass_evidence))


# ----------------------------------------------------------------------
# 4. repro-score
# ----------------------------------------------------------------------

def check_repro(ctx: CheckContext) -> Finding:
    """``repro-report.json`` exists, is a valid report, and scores >=
    the threshold (default 1.0 — a byte-identical rebuild)."""
    check_id = checks.CHECK_REPRO
    path = _join(ctx.build_dir, checks.REPRO_REPORT_FILENAME)
    if not os.path.isfile(path):
        return Finding(check_id, Status.FAIL,
                       "repro report not found in build dir", (path,))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return Finding(check_id, Status.FAIL,
                       f"repro report {path} could not be read",
                       (f"{path}: {exc}",))
    data, errors = repro_format.parse_repro_report(text)
    if data is None:
        return Finding(check_id, Status.FAIL,
                       f"repro report {path} is not a valid repro report",
                       tuple(f"{path}: {err}" for err in errors))
    score_err = repro_format.score_error(data, ctx.repro_threshold)
    if score_err is not None:
        return Finding(
            check_id, Status.FAIL,
            f"repro score below threshold: {score_err}",
            (f"{path}: score {data['score']!r} < threshold "
             f"{ctx.repro_threshold!r}",),
        )
    return Finding(
        check_id, Status.PASS,
        f"repro score {data['score']!r} >= threshold "
        f"{ctx.repro_threshold!r}",
        (f"{path}: score={data['score']!r} identical={data['identical']!r}",),
    )


# ----------------------------------------------------------------------
# 5. expected-files
# ----------------------------------------------------------------------

def check_expected_files(ctx: CheckContext) -> Finding:
    """Every expected artifact exists under the build dir as a regular,
    non-empty file."""
    check_id = checks.CHECK_EXPECTED_FILES
    problems: list = []
    pass_evidence: list = []
    for artifact in checks.EXPECTED_ARTIFACTS:
        path = _join(ctx.build_dir, artifact)
        if not os.path.exists(path):
            problems.append(f"{path}: missing")
        elif not os.path.isfile(path):
            problems.append(f"{path}: not a regular file")
        elif os.path.getsize(path) == 0:
            problems.append(f"{path}: empty")
        else:
            pass_evidence.append(f"{path}: {os.path.getsize(path)} bytes")
    if problems:
        return Finding(check_id, Status.FAIL,
                       f"{len(problems)} expected artifact(s) missing or "
                       "not regular, non-empty files",
                       tuple(problems))
    return Finding(check_id, Status.PASS,
                   "all expected artifacts exist as regular, non-empty "
                   "files",
                   tuple(pass_evidence))


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------

def _make_runner(check_id: str, func: CheckFunc) -> CheckFunc:
    """Tag a check function with the id it reports under."""
    func.check_id = check_id  # type: ignore[attr-defined]
    return func


#: The registry: stable check id -> implementation function, in report
#: order (``checks.CHECK_IDS``). A check plugs in by being present here;
#: :func:`run_checks` knows nothing about the individual functions.
CHECK_RUNNERS: "Dict[str, CheckFunc]" = {
    defn.check_id: _make_runner(defn.check_id, func)
    for defn, func in zip(
        checks.CHECKS,
        (check_attest_chain, check_sbom, check_signatures,
         check_repro, check_expected_files),
    )
}


def run_checks(ctx: CheckContext,
               check_ids: Optional[Sequence[str]] = None) -> Tuple[Finding, ...]:
    """Run the given checks (default: all, in report order) and return
    their findings in the same order."""
    ids = tuple(check_ids) if check_ids is not None else checks.CHECK_IDS
    unknown = [c for c in ids if c not in CHECK_RUNNERS]
    if unknown:
        raise KeyError(f"unknown check id(s): {unknown!r}")
    return tuple(CHECK_RUNNERS[c](ctx) for c in ids)


def build_report(ctx: CheckContext,
                 check_ids: Optional[Sequence[str]] = None) -> Report:
    """Run the checks and aggregate their findings into a
    :class:`~pengyos_verifier.model.Report`.

    The report is self-describing: ``build`` and ``attest`` carry the
    labels the caller gave (so a ``--json OUT`` report says exactly what
    was inspected), and ``findings`` is the output of
    :func:`run_checks` in report order. The pass/fail verdict and exit
    code live on the report itself (``Report.ok`` / ``Report.exit_code``):
    any single FAIL makes the verdict FAIL and the exit code non-zero.
    """
    findings = run_checks(ctx, check_ids=check_ids)
    return Report(build=ctx.build_dir, attest=ctx.attest_path,
                  findings=findings)
