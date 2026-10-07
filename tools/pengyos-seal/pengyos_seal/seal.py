"""The seal driver — executes the provenance stages.

The seal is the closing glue for US-CORE-6: given a pengyos-build output dir and
a signing key it runs the provenance closure in order — **sign** the expected
artifacts, **name** the SBOM to the verifier's conventional root, **surface** the
repro report, **build** the witness layout, and **verify** with
`pengyos-verifier`.

This module owns the *driver*: it performs the side effects of the seal stages
through the injected runner seam (:mod:`.runner`). The **sign**, **name**,
**repro**, **witness** and **verify** stages are wired into :meth:`Seal.run` in provenance order.

## The machine view

:meth:`SealResult.to_dict` / :meth:`SealResult.to_json` serialise the seal's
outcome for ``pengyos-seal run --json``: the sealed plan, the artifacts signed,
the names normalised to the verifier's conventional root names, the repro
status, and the verifier's per-check verdicts (`checks`), overall `verdict`, and
`exit_code`. The per-check verdicts come from the verifier's own structured
stdout (:func:`_verifier_checks`) — the seal records only what the verifier
reports, never invents a check.

## The sign stage

For each ``EXPECTED_ARTIFACT`` (in the pinned order ``images/rootfs.ext4`` then
``images/bzImage``) the seal:

1. checks the artifact exists in the build dir (build-relative path); a missing
   artifact is a clean :class:`SealError` naming it — the seal never signs a
   phantom;
2. invokes ``pengyos-sign sign <artifact> --key <seed> --sig <artifact>.sig``
   through the runner.

The signature is ``pengyos-sig-v1`` (ed25519 over the artifact's SHA-256
*digest*, per ``pengyos-sign`` FORMAT.md). The seal **never reimplements**
ed25519 or the signature scheme — it always calls ``pengyos-sign`` so the
convention stays single-sourced. Signing is **idempotent**: re-running the seal
re-invokes ``pengyos-sign``, which overwrites the ``.sig`` sibling.

## The name stage

The build emits its SPDX SBOM at the discovered path (``plan.sbom_source``, e.g.
``images/m2.spdx.json``); the witness and verifier read ``sbom.spdx.json`` at the
build root. The name stage symlinks the former to the latter — a symlink, not a
copy, so the ~100 KB SPDX doc is not duplicated in the build dir and the
canonical document stays the single source of truth. A build with no discovered
SBOM is a recorded note, not a failure (the follow-up witness/verify stages
surface that absence).

## The repro stage

The verifier reads ``repro-report.json`` at the build root; the build emits its
report at the discovered path (``plan.repro_source``, e.g.
``images/repro-report.json``) or, when a diff is run, at the witness path
``images/repro-report.json``. The repro stage surfaces it at the conventional
root name, symlinking (not copying) so the build dir does not duplicate it. The
outcome is deterministic from the plan (``plan.repro_status``):

* **existing** — a report was discovered. If it is already at the root name,
  nothing is done; otherwise it is symlinked to ``repro-report.json``.
* **diff** — no report was present but ``--prev`` was given, so the seal runs
  ``pengyos-repro-report diff --a <build> --b <prev>`` and symlinks its result
  (re-discovered, so it lands wherever the tool writes) to the root.
* **skip** — no report and no ``--prev``: a single build cannot compare itself,
  so this is a recorded skip (a non-fatal note, not a failure).

A diff that runs but exits non-zero, or produces nothing discoverable, is a
clean :class:`SealError` — the seal never masks a failed diff as a clean skip.

## The witness stage

The external witness builds its layout from the sealed build:
``pengyos-witness-bundle run --build DIR [--pub PUB]``. The witness stage runs
it through the runner; a non-zero exit is a clean :class:`SealError` naming the
tool and folding in its stderr (the witness is a prerequisite of verification,
so a failed witness never reaches the verifier). The outcome is recorded on
``SealResult.witness``.

## The verify stage

The final step is ``pengyos-verifier check --build DIR [--attest LOG] [--pub
PUB]``. The verifier is the closing gate of the seal: its exit code *is* the
verdict (0 = PASS, any non-zero = FAIL), so the seal surfaces the verifier's
verdict and returns its exit code. The verdict is parsed from the verifier's
stdout when it emits a structured ``{"verdict": ...}`` summary (so a rich verdict
is available for the machine view); otherwise it is derived from the exit code,
which is the single source of truth. A non-zero exit is a clean
:class:`SealError` — the seal never returns a failing verifier as a success.

Stdlib only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional, Tuple

from . import cli  # noqa: E402  (bind the module; attributes accessed lazily)
from .plan import (
    REPRO_DIFF,
    REPRO_EXISTING,
    REPRO_SKIP,
    SealPlan,
    discover_repro,
)
from .runner import RunResult, Runner

# SealError is imported lazily inside the stage methods to avoid a circular
# import (cli imports this module at package load, so it is only partially
# initialised when this module is first imported).


def _verdict_from_result(result: "RunResult") -> str:
    """The verifier's verdict (``PASS`` / ``FAIL``) for one run result.

    The verifier's exit code is the single source of truth for the verdict:
    ``rc == 0`` → ``PASS``, any non-zero → ``FAIL``. When the verifier emits a
    structured ``{"verdict": ...}`` summary on stdout, its value is honoured
    instead (so a rich verdict is available for the machine view) — but only when
    it is one of the known verdicts; anything else falls back to the exit code.
    """
    verdict = _structured_verdict(result.stdout)
    if verdict is not None:
        return verdict
    return VERDICT_PASS if result.rc == 0 else VERDICT_FAIL


def _structured_verdict(stdout: str) -> Optional[str]:
    """The verifier's structured verdict from stdout, or ``None``.

    Parses the first ``{"verdict": "<PASS|FAIL>", ...}`` JSON object on stdout;
    returns the verdict lower-cased/normalised to :data:`VERDICT_PASS` /
    :data:`VERDICT_FAIL`, or ``None`` when stdout is not that structure.
    """
    stripped = stdout.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("verdict")
    if isinstance(value, str):
        normalised = value.strip().upper()
        if normalised in (VERDICT_PASS, VERDICT_FAIL):
            return normalised
    return None


#: The canonical verifier check names (the five ``pengyos-verifier check``
#: gates, from the Core wiring doc). The seal does not import the external
#: verifier — it only *records* whatever checks the verifier reports in its
#: structured stdout — but these names document what a passing run surfaces.
VERIFIER_CHECKS: tuple[str, ...] = (
    "sbom",
    "signatures",
    "repro-score",
    "expected-files",
    "attest-chain",
)


def _verifier_checks(stdout: str) -> Optional[dict[str, str]]:
    """The verifier's per-check verdicts from structured stdout, or ``None``.

    Parses the first ``{"verdict": ..., "checks": {name: "<PASS|FAIL>", ...}}``
    JSON object on stdout and returns the ``checks`` map (values normalised to
    upper-case), or ``None`` when stdout is not that structure. This is what the
    machine view surfaces as "verifier checks passed/failed" — a structured
    summary, when the verifier emits one; otherwise the seal has no per-check
    data and reports ``None`` rather than inventing it.
    """
    stripped = stdout.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        return None
    normalised: dict[str, str] = {}
    for name, status in checks.items():
        if isinstance(name, str) and isinstance(status, str):
            normalised[name] = status.strip().upper()
    return normalised or None


@dataclass(frozen=True)
class ReproResult:
    """The outcome of the repro stage (surfacing the reproducibility report).

    Attributes:
        status:      The deterministic repro status:
                     * ``existing`` — a report was discovered in the build tree;
                     * ``diff`` — no report was present but ``--prev`` was given,
                       so a diff was run and its result surfaced;
                     * ``skip`` — no report and no ``--prev``: a single build
                       cannot compare itself, so this is a recorded skip (a
                       non-fatal note, not a failure).
        symlinked:   The ``(source, root)`` build-relative paths the repro report
                     was symlinked from to the verifier's conventional root name
                     (``repro-report.json``), or ``None`` when no symlink was
                     needed (already at the root, or skipped).
    """

    status: str
    symlinked: Optional[Tuple[str, str]] = None


# The witness outcome values (recorded on SealResult.witness).
WITNESS_BUILT = "built"
WITNESS_NONE = "none"


@dataclass(frozen=True)
class WitnessResult:
    """The outcome of the witness stage (building the witness layout).

    Attributes:
        status:   ``built`` when ``pengyos-witness-bundle`` ran, else ``none``.
        argv:     The ``pengyos-witness-bundle`` argv that ran (or would have
                  run), or ``None`` when the witness did not run.
    """

    status: str
    argv: Optional[Tuple[str, ...]] = None


# The verifier verdict values (recorded on SealResult.verifier).
VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"


@dataclass(frozen=True)
class VerifierResult:
    """The outcome of the verify stage (running ``pengyos-verifier``).

    Attributes:
        exit_code: The verifier's process exit code — the single source of truth
                   for the verdict (0 = PASS, any non-zero = FAIL).
        verdict:   The verifier's verdict (``PASS`` / ``FAIL``). Parsed from the
                   verifier's structured stdout when it emits one; otherwise
                   derived from the exit code.
        stdout:    The verifier's raw stdout (for the machine view / debugging).
        checks:    The verifier's per-check verdicts (``{name: "PASS"/"FAIL"}``)
                   when the verifier emits a structured ``checks`` summary — this
                   is what the machine view surfaces as "verifier checks
                   passed/failed". ``None`` when the verifier emits no such
                   summary (the seal records only what the verifier reports).
    """

    exit_code: int
    verdict: str
    stdout: str = ""
    checks: Optional[dict[str, str]] = None


@dataclass
class SealResult:
    """The outcome of running the seal stages that exist so far.

    Attributes:
        build_dir:        The build dir the seal ran against.
        plan:             The :class:`~pengyos_seal.plan.SealPlan` the seal ran
                          against (the machine view serialises it), or ``None``
                          when no plan was available.
        signed:           The expected artifacts that were signed, in signed
                          order.
        sbom_symlinked:   The ``(source, root)`` build-relative paths the name
                          stage symlinked the SBOM from the build's real name to
                          the verifier's conventional root name, or ``None``
                          when no SBOM was normalised.
        repro:            The :class:`ReproResult` for the repro stage, or
                          ``None`` when no repro stage has run yet.
        witness:          The :class:`WitnessResult` for the witness stage, or
                          ``None`` when no witness stage has run yet.
        verifier:         The :class:`VerifierResult` for the verify stage, or
                          ``None`` when no verify stage has run yet.
        errors:           Human-readable notes for stages that could not run (a
                          missing SBOM, a repro skip). Non-fatal — they do not
                          raise, they are surfaced on stderr by the CLI.
    """

    build_dir: str
    plan: Optional["SealPlan"] = None
    signed: list[str] = field(default_factory=list)
    sbom_symlinked: Optional[Tuple[str, str]] = None
    repro: Optional[ReproResult] = None
    witness: Optional[WitnessResult] = None
    verifier: Optional[VerifierResult] = None
    errors: list[str] = field(default_factory=list)

    # --- machine view -------------------------------------------------------

    def to_dict(self) -> dict:
        """The machine-readable seal result.

        The shape mirrors the spec's ``--json`` contract: the sealed plan, the
        artifacts signed, the names normalised to the verifier's conventional
        root names, the repro status, and the verifier's verdict + per-check
        results + exit code. Every field is JSON-native (``None`` where a stage
        did not run) so a downstream tool can consume it without a second pass.
        """
        plan_dict = self.plan.to_dict() if self.plan is not None else None
        sbom = self.sbom_symlinked
        repro_link = self.repro.symlinked if self.repro is not None else None
        return {
            "build_dir": self.build_dir,
            "plan": plan_dict,
            "signed": list(self.signed),
            "names": {
                "sbom": (
                    {"source": sbom[0], "root": sbom[1]}
                    if sbom is not None
                    else None
                ),
                "repro": (
                    {"source": repro_link[0], "root": repro_link[1]}
                    if repro_link is not None
                    else None
                ),
            },
            "repro": {
                "status": self.repro.status if self.repro is not None else None,
            },
            "verifier": (
                {
                    "exit_code": self.verifier.exit_code,
                    "verdict": self.verifier.verdict,
                    "checks": self.verifier.checks,
                }
                if self.verifier is not None
                else None
            ),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialise the machine-readable seal result to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


class Seal:
    """The seal driver.

    Wraps a build dir, a signing key, and the injected runner, and exposes the
    seal stages as methods. All five stages exist now — ``sign``,
    ``normalise_sbom``, ``surface_repro``, ``build_witness`` and
    ``run_verifier`` — and are wired into :meth:`run` in provenance order.
    """

    def __init__(
        self,
        build_dir: str,
        key: str,
        runner: Runner,
        *,
        plan: Optional[SealPlan] = None,
        pub: Optional[str] = None,
        attest: Optional[str] = None,
        prev: Optional[str] = None,
    ) -> None:
        self.build_dir = str(build_dir)
        self.key = key
        self.runner = runner
        self.plan = plan
        self.pub = pub
        self.attest = attest
        self.prev = prev
        # Accumulated stage state: the sign stage appends signed artifacts, the
        # name stage appends non-fatal notes (a missing SBOM), the repro stage
        # records its outcome, the witness/verify stages record theirs. `run`
        # returns the final result, so this must outlive any single stage.
        self.signed: list[str] = []
        self.sbom_symlinked: Optional[Tuple[str, str]] = None
        self.repro: Optional[ReproResult] = None
        self.witness: Optional[WitnessResult] = None
        self.verifier: Optional[VerifierResult] = None
        self.errors: list[str] = []

    # --- path helper --------------------------------------------------------

    def _qualify(self, rel: str) -> str:
        """A build-dir-qualified path handed to the runner.

        The runner's filesystem methods (``exists``/``read_bytes``/...) are keyed
        by build-dir-qualified paths: the virtual tree in tests is seeded at
        ``build_dir/<rel>`` and ``walk(build_dir)`` strips that prefix to yield
        build-relative names, and ``FileSystemRunner`` resolves them against the
        real build dir. So the seal always hands the runner build-dir-qualified
        POSIX paths (``build_dir + "/" + rel``), never bare build-relative ones.
        """
        build_dir = self.build_dir
        if build_dir in ("", "."):
            return rel
        return f"{build_dir}/{rel}"

    # --- stages -------------------------------------------------------------

    def sign(self) -> SealResult:
        """Sign every expected artifact through ``pengyos-sign``.

        Returns a :class:`SealResult` recording the artifacts signed. Raises a
        clean :class:`SealError` naming the first artifact that is missing or
        that ``pengyos-sign`` fails on — the seal never raises a traceback.
        """
        # Imported lazily: cli imports this module, so SealError is only
        # available once cli has finished loading (deferred access avoids the
        # circular import).
        from .cli import SealError

        for artifact in cli.EXPECTED_ARTIFACTS:
            if not self.runner.exists(self._qualify(artifact)):
                raise SealError(
                    f"cannot sign {artifact}: file not found in build dir "
                    f"'{self.build_dir}'"
                )
            result = self.runner.run_command(self._sign_argv(artifact))
            if result.rc != 0:
                detail = result.stderr.strip() or f"exit code {result.rc}"
                raise SealError(
                    f"pengyos-sign failed for {artifact}: {detail}"
                )
            self.signed.append(artifact)
        return SealResult(
            build_dir=self.build_dir,
            signed=self.signed,
            sbom_symlinked=self.sbom_symlinked,
            errors=self.errors,
        )

    def _sign_argv(self, artifact: str) -> tuple[str, ...]:
        """The ``pengyos-sign`` argv for one artifact (real key filled in)."""
        sig_path = f"{artifact}{cli.SIGNATURE_SUFFIX}"
        return (
            "pengyos-sign",
            "sign",
            artifact,
            "--key",
            self.key,
            "--sig",
            sig_path,
        )

    def normalise_sbom(self) -> SealResult:
        """Symlink the SBOM to the verifier's conventional root name.

        The build emits its SPDX document at the discovered path
        (``self.plan.sbom_source``, e.g. ``images/m2.spdx.json``); the witness
        and verifier read ``sbom.spdx.json`` at the build root. This stage
        symlinks the former to the latter. A symlink (not a copy) is used for
        the ~100 KB SPDX doc, so the build dir does not double its SBOM size and
        the canonical document is the single source of truth.

        A no-op (a recorded note, not an error) when no SBOM was discovered — a
        build without an SBOM has nothing to normalise, and the follow-up
        witness/verify stages surface that absence.

        Returns the :class:`SealResult` updated in place with ``sbom_symlinked``.
        """
        source = self.plan.sbom_source if self.plan is not None else None
        if source is None:
            self.errors.append(
                f"no SBOM discovered in '{self.build_dir}' — nothing to "
                "normalise to sbom.spdx.json"
            )
            return SealResult(
                build_dir=self.build_dir,
                signed=self.signed,
                sbom_symlinked=None,
                errors=self.errors,
            )
        # The plan discovered the source by walking the tree, so it exists;
        # confirm once more through the runner (a missing source is a note).
        if not self.runner.exists(self._qualify(source)):
            self.errors.append(
                f"SBOM '{source}' discovered but missing — cannot normalise "
                "to sbom.spdx.json"
            )
            return SealResult(
                build_dir=self.build_dir,
                signed=self.signed,
                sbom_symlinked=None,
                errors=self.errors,
            )
        self.runner.symlink(
            self._qualify(source), self._qualify(cli.SBOM_ROOT_FILENAME)
        )
        self.sbom_symlinked = (source, cli.SBOM_ROOT_FILENAME)
        return SealResult(
            build_dir=self.build_dir,
            signed=self.signed,
            sbom_symlinked=self.sbom_symlinked,
            errors=self.errors,
        )

    def surface_repro(self) -> SealResult:
        """Surface the repro report at the verifier's conventional root name.

        The verifier reads ``repro-report.json`` at the build root; the build
        emits its report at the discovered path (``self.plan.repro_source``, e.g.
        ``images/repro-report.json``) or, when a diff is run, at the witness path
        ``images/repro-report.json``. This stage symlinks the report to the
        conventional root name so the witness and verifier both find it — a
        symlink, not a copy, so the build dir does not duplicate the report.

        The outcome is deterministic from the plan (``self.plan.repro_status``):

        * ``existing`` — a report was discovered. If it is already at the root
          name, nothing is done (it is already in place); otherwise it is
          symlinked to the root.
        * ``diff`` — no report was discovered but ``--prev`` was given, so a diff
          is run (``pengyos-repro-report diff --a <build> --b <prev>``) and its
          result — re-discovered, so it lands wherever the tool writes — is
          symlinked to the root.
        * ``skip`` — no report and no ``--prev``: a single build cannot compare
          itself, so this is a recorded skip (a non-fatal note, not a failure).

        Raises a clean :class:`SealError` naming it when the diff runs but
        ``pengyos-repro-report`` exits non-zero — the seal never masks a failed
        diff as a clean skip.

        Returns the :class:`SealResult` updated in place with ``repro``.
        """
        # Imported lazily: cli imports this module, so SealError is only
        # available once cli has finished loading (deferred access avoids the
        # circular import).
        from .cli import SealError

        if self.plan is None:
            self.errors.append(
                f"no seal plan — cannot surface repro report in "
                f"'{self.build_dir}'"
            )
            return SealResult(
                build_dir=self.build_dir,
                signed=self.signed,
                sbom_symlinked=self.sbom_symlinked,
                repro=None,
                errors=self.errors,
            )

        status = self.plan.repro_status

        # existing — a report was discovered in the build tree.
        if status == REPRO_EXISTING:
            source = self.plan.repro_source
            assert source is not None  # existing implies a discovered source
            if source == cli.REPRO_REPORT_FILENAME:
                # Already at the verifier's conventional root name — nothing to
                # normalise; record the existing report, no symlink.
                self.repro = ReproResult(status=REPRO_EXISTING, symlinked=None)
                return SealResult(
                    build_dir=self.build_dir,
                    signed=self.signed,
                    sbom_symlinked=self.sbom_symlinked,
                    repro=self.repro,
                    errors=self.errors,
                )
            self._symlink_repro(source)
            return SealResult(
                build_dir=self.build_dir,
                signed=self.signed,
                sbom_symlinked=self.sbom_symlinked,
                repro=self.repro,
                errors=self.errors,
            )

        # diff — no report present, but --prev was given: run the diff and
        # surface its result.
        if status == REPRO_DIFF:
            result = self.runner.run_command(self.plan.repro_argv)
            if result.rc != 0:
                detail = result.stderr.strip() or f"exit code {result.rc}"
                raise SealError(
                    f"pengyos-repro-report diff failed: {detail}"
                )
            # The diff writes the report at the witness path; re-discover so the
            # seal symlinks whatever the tool actually produced, wherever it
            # landed. A report that never appeared is a clean SealError naming
            # the tool — the seal never fakes a repro it did not run.
            source = discover_repro(self.build_dir, self.runner)
            if source is None:
                raise SealError(
                    "pengyos-repro-report diff ran but produced no repro "
                    "report where it could be discovered"
                )
            self._symlink_repro(source)
            return SealResult(
                build_dir=self.build_dir,
                signed=self.signed,
                sbom_symlinked=self.sbom_symlinked,
                repro=self.repro,
                errors=self.errors,
            )

        # skip — a single build cannot compare itself: a recorded skip, not a
        # failure.
        self.errors.append(
            f"no repro report in '{self.build_dir}' and no --prev given — "
            "skipping repro (a single build cannot compare itself)"
        )
        self.repro = ReproResult(status=REPRO_SKIP, symlinked=None)
        return SealResult(
            build_dir=self.build_dir,
            signed=self.signed,
            sbom_symlinked=self.sbom_symlinked,
            repro=self.repro,
            errors=self.errors,
        )

    def _symlink_repro(self, source: str) -> None:
        """Symlink a discovered/witness repro report to the root root name."""
        self.runner.symlink(
            self._qualify(source), self._qualify(cli.REPRO_REPORT_FILENAME)
        )
        self.repro = ReproResult(
            status=REPRO_EXISTING,
            symlinked=(source, cli.REPRO_REPORT_FILENAME),
        )

    def build_witness(self) -> SealResult:
        """Build the witness layout via ``pengyos-witness-bundle``.

        The external witness builds its layout from the sealed build:
        ``pengyos-witness-bundle run --build DIR [--pub PUB]`` (the argv the plan
        enumerated, ``self.plan.witness_argv``). It runs through the injected
        runner; a non-zero exit is a clean :class:`SealError` naming the tool and
        folding in its stderr (the witness is a prerequisite of verification, so
        a failed witness never reaches the verifier).

        Returns the :class:`SealResult` updated in place with ``witness``.
        """
        # Imported lazily: cli imports this module, so SealError is only
        # available once cli has finished loading (deferred access avoids the
        # circular import).
        from .cli import SealError

        argv = self.plan.witness_argv if self.plan is not None else None
        if argv is None:
            self.errors.append(
                f"no seal plan — cannot build the witness layout in "
                f"'{self.build_dir}'"
            )
            return SealResult(
                build_dir=self.build_dir,
                signed=self.signed,
                sbom_symlinked=self.sbom_symlinked,
                repro=self.repro,
                witness=None,
                errors=self.errors,
            )
        result = self.runner.run_command(argv)
        if result.rc != 0:
            detail = result.stderr.strip() or f"exit code {result.rc}"
            raise SealError(
                f"pengyos-witness-bundle failed: {detail}"
            )
        self.witness = WitnessResult(status=WITNESS_BUILT, argv=argv)
        return SealResult(
            build_dir=self.build_dir,
            signed=self.signed,
            sbom_symlinked=self.sbom_symlinked,
            repro=self.repro,
            witness=self.witness,
            errors=self.errors,
        )

    def run_verifier(self) -> SealResult:
        """Run ``pengyos-verifier`` and surface its verdict + exit code.

        The verifier is the closing gate of the seal: ``pengyos-verifier check
        --build DIR [--attest LOG] [--pub PUB]`` (the argv the plan enumerated,
        ``self.plan.verify_argv``). Its exit code *is* the verdict (0 = PASS, any
        non-zero = FAIL), so the seal surfaces the verifier's verdict and records
        its exit code. The verdict is parsed from the verifier's structured
        stdout when it emits a ``{"verdict": ...}`` summary (so a rich verdict is
        available for the machine view); otherwise it is derived from the exit
        code, which is the single source of truth.

        The seal never raises on a failing verifier — it records the verdict and
        returns the verifier's exit code unchanged, so the verifier's own exit
        code propagates as the seal's return code.

        Returns the :class:`SealResult` updated in place with ``verifier``.
        """
        argv = self.plan.verify_argv if self.plan is not None else None
        if argv is None:
            self.errors.append(
                f"no seal plan — cannot run the verifier in '{self.build_dir}'"
            )
            return SealResult(
                build_dir=self.build_dir,
                signed=self.signed,
                sbom_symlinked=self.sbom_symlinked,
                repro=self.repro,
                witness=self.witness,
                verifier=None,
                errors=self.errors,
            )
        result = self.runner.run_command(argv)
        # The verifier is the closing gate of the seal: its exit code *is* the
        # seal's return code (0 = PASS, any non-zero = FAIL). The seal surfaces
        # the verdict and returns the exit code unchanged — it never raises on a
        # failing verifier, so the verifier's own exit code propagates.
        self.verifier = VerifierResult(
            exit_code=result.rc,
            verdict=_verdict_from_result(result),
            stdout=result.stdout,
            checks=_verifier_checks(result.stdout),
        )
        return SealResult(
            build_dir=self.build_dir,
            signed=self.signed,
            sbom_symlinked=self.sbom_symlinked,
            repro=self.repro,
            witness=self.witness,
            verifier=self.verifier,
            errors=self.errors,
        )

    # --- driver -------------------------------------------------------------

    def run(self) -> SealResult:
        """Run the seal stages wired in, in provenance order.

        ``sign`` (item 3) → ``normalise_sbom`` (item 4) → ``surface_repro``
        (item 5) → ``build_witness`` / ``run_verifier`` (item 6). The verifier is
        the closing gate: its exit code is the seal's return code.
        """
        self.sign()
        self.normalise_sbom()
        self.surface_repro()
        self.build_witness()
        self.run_verifier()
        return SealResult(
            build_dir=self.build_dir,
            plan=self.plan,
            signed=self.signed,
            sbom_symlinked=self.sbom_symlinked,
            repro=self.repro,
            witness=self.witness,
            verifier=self.verifier,
            errors=self.errors,
        )


def run_seal(
    build_dir: str,
    key: str,
    runner: Runner,
    *,
    plan: Optional[SealPlan] = None,
    pub: Optional[str] = None,
    attest: Optional[str] = None,
    prev: Optional[str] = None,
) -> SealResult:
    """Convenience wrapper: build a :class:`Seal` and run it."""
    return Seal(build_dir, key, runner, plan=plan, pub=pub, attest=attest, prev=prev).run()


__all__ = [
    "SealResult",
    "ReproResult",
    "WitnessResult",
    "VerifierResult",
    "WITNESS_BUILT",
    "WITNESS_NONE",
    "VERDICT_PASS",
    "VERDICT_FAIL",
    "VERIFIER_CHECKS",
    "Seal",
    "run_seal",
]
