"""Declarative smoke matrix — the machine-checkable form of SPEC §556's
"passes the smoke matrix" acceptance criterion.

SPEC's Core-done gate lists six criteria, one of which was, until now, prose:
*"passes the smoke matrix"*. This module turns it into data plus a runner:

* a **matrix file** (TOML) declares the *boot variants* a Core image must
  survive (e.g. plain UEFI, UEFI + TPM2) and the *checks* each variant must
  satisfy;
* :func:`run_matrix` boots each variant with the existing harness
  (:func:`.boot.boot_image`) and evaluates the declared checks over the serial
  log, returning one :class:`VariantOutcome` per variant;
* :class:`MatrixOutcome` aggregates them and renders JSON + JUnit XML, so the
  gate criterion becomes one command with one exit code and one artifact.

Design rules (deliberately strict — a matrix that cannot fail is not a test):

* **Every check is blocking.** There is no "informational" check kind: if a
  fact is worth listing it is worth enforcing, and a soft check that can never
  fail is exactly the neutered-config smell the project avoids elsewhere.
* **A variant with no checks is a configuration error.** Validation rejects a
  matrix where any variant ends up with zero checks, so an empty/partial
  matrix can never pass vacuously.
* **Checks that did not run are a failure.** The runner asserts that the boot
  returned exactly one result per declared check; a boot that silently dropped
  the checks fails the variant instead of passing by default.
* **Text-only, no privileges.** Checks are regular expressions over the serial
  log, so the whole matrix runs headless and unprivileged. Nothing here needs QEMU: the
  runner takes an injectable ``boot_fn``, so sub-30 ms unit tests use fakes.

Checks are grounded in real captures: the shipped ``smoke/core.toml`` was
written against actual Core serial logs, not invented.
"""

from __future__ import annotations

import json
import os
import time
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from . import reports
from .argv import DEFAULT_QEMU_BIN, build_argv
from .assertions import KIND_ABSENT, KIND_PRESENT, Assertion, CheckResult
from .boot import DEFAULT_SETTLE_SECONDS, DEFAULT_TIMEOUT, BootResult, boot_image

__all__ = [
    "MATRIX_VERSION",
    "DEFAULT_MATRIX_FILE",
    "TPM_SOCKET_PLACEHOLDER",
    "MatrixError",
    "SmokeCheck",
    "SmokeVariant",
    "SmokeMatrix",
    "VariantOutcome",
    "MatrixOutcome",
    "default_matrix_path",
    "resolve_image",
    "build_root",
    "load_matrix",
    "matrix_from_dict",
    "run_matrix",
    "plan_argv",
    "write_json",
    "write_junit",
]

#: Schema version of the TOML matrix format this module understands.
MATRIX_VERSION: int = 1

#: Name of the shipped matrix inside the ``smoke/`` directory.
DEFAULT_MATRIX_FILE: str = "core.toml"

#: Placeholder datapath socket used when *rendering* (not running) a TPM
#: variant — a dry-run has no live swtpm to point at. Mirrors
#: ``cli.render_argv``.
TPM_SOCKET_PLACEHOLDER: str = "tpm.sock"

_SMOKE_DIR = Path(__file__).resolve().parent.parent / "smoke"


class MatrixError(ValueError):
    """A matrix file is missing, malformed, or semantically invalid."""


# --- the declarations --------------------------------------------------------


@dataclass(frozen=True)
class SmokeCheck:
    """One blocking fact a variant must satisfy, evaluated over the serial log.

    Attributes:
        id: stable identifier (also the pytest/JUnit testcase name).
        kind: :data:`~.assertions.KIND_PRESENT` (must appear in the log) or
            :data:`~.assertions.KIND_ABSENT` (must not appear).
        pattern: a Python regular expression searched over the whole log.
        description: why this fact matters (carried into reports/help).
        variants: variant ids this check applies to; empty means *all*
            variants. An id that no variant declares is a config error.
    """

    id: str
    kind: str
    pattern: str
    description: str = ""
    variants: "tuple[str, ...]" = ()

    def __post_init__(self) -> None:
        if not str(self.id).strip():
            raise MatrixError("SmokeCheck.id must be a non-empty string")
        if self.kind not in (KIND_PRESENT, KIND_ABSENT):
            raise MatrixError(
                f"SmokeCheck {self.id!r}: kind must be {KIND_PRESENT!r} or "
                f"{KIND_ABSENT!r} (got {self.kind!r})"
            )
        if not self.pattern:
            raise MatrixError(f"SmokeCheck {self.id!r}: pattern must be non-empty")
        object.__setattr__(self, "variants", tuple(str(v) for v in self.variants))

    def applies_to(self, variant_id: str) -> bool:
        """True when this check runs for ``variant_id``."""
        return not self.variants or variant_id in self.variants

    def to_assertion(self) -> Assertion:
        """The :class:`~.assertions.Assertion` this check evaluates to."""
        return Assertion(self.id, self.pattern, self.kind)


@dataclass(frozen=True)
class SmokeVariant:
    """One way of booting the image that the matrix must cover.

    Attributes:
        id: stable identifier (used in reports and ``--variant`` selection).
        description: one line of intent, for the report and ``--dry-run``.
        uefi: boot through OVMF/UEFI firmware (required for PengyOS images).
        tpm: attach the emulated swtpm TPM2 device.
        kernel: bare-metal ``-kernel`` bzImage path (mutually exclusive with
            ``uefi``); ``None`` for firmware/disk boot.
        timeout: readiness timeout in seconds for this variant.
    """

    id: str
    description: str = ""
    uefi: bool = False
    tpm: bool = False
    kernel: "str | None" = None
    timeout: float = DEFAULT_TIMEOUT

    def __post_init__(self) -> None:
        if not str(self.id).strip():
            raise MatrixError("SmokeVariant.id must be a non-empty string")
        if self.uefi and self.kernel:
            raise MatrixError(
                f"SmokeVariant {self.id!r}: uefi and kernel are mutually "
                "exclusive boot modes"
            )
        if float(self.timeout) <= 0:
            raise MatrixError(f"SmokeVariant {self.id!r}: timeout must be positive")

    def boot_kwargs(self) -> "dict":
        """The keyword arguments this variant passes to the boot function."""
        return {
            "kernel": self.kernel,
            "uefi": self.uefi,
            "tpm": self.tpm,
            "timeout": float(self.timeout),
        }


@dataclass(frozen=True)
class SmokeMatrix:
    """A validated matrix: the variants to boot and the checks that must hold.

    Attributes:
        name: matrix name (e.g. ``"core"``) — used in report and JUnit names.
        variants: the boot variants, in run order.
        checks: the checks, in report order.
        description: one-line summary of what this matrix covers.
        image: default image path, relative to the repo root (the CLI
            ``--image`` flag overrides it).
        version: the schema version this matrix was written against.
    """

    name: str
    variants: "tuple[SmokeVariant, ...]"
    checks: "tuple[SmokeCheck, ...]"
    description: str = ""
    image: str = ""
    version: int = MATRIX_VERSION

    def __post_init__(self) -> None:
        if not str(self.name).strip():
            raise MatrixError("SmokeMatrix.name must be a non-empty string")
        if int(self.version) != MATRIX_VERSION:
            raise MatrixError(
                f"matrix {self.name!r}: unsupported version {self.version!r} "
                f"(this build understands {MATRIX_VERSION})"
            )
        if not self.variants:
            raise MatrixError(f"matrix {self.name!r}: no [[variant]] declared")
        if not self.checks:
            raise MatrixError(f"matrix {self.name!r}: no [[check]] declared")

        ids = [v.id for v in self.variants]
        if len(set(ids)) != len(ids):
            raise MatrixError(f"matrix {self.name!r}: duplicate variant id in {ids}")

        check_ids = [c.id for c in self.checks]
        if len(set(check_ids)) != len(check_ids):
            raise MatrixError(
                f"matrix {self.name!r}: duplicate check id in {check_ids}"
            )

        known = set(ids)
        for check in self.checks:
            unknown = [v for v in check.variants if v not in known]
            if unknown:
                raise MatrixError(
                    f"matrix {self.name!r}: check {check.id!r} references "
                    f"unknown variant(s) {unknown} (known: {sorted(known)})"
                )
        # A variant no check applies to could never fail — reject it rather
        # than let a matrix pass vacuously.
        for variant in self.variants:
            if not [c for c in self.checks if c.applies_to(variant.id)]:
                raise MatrixError(
                    f"matrix {self.name!r}: variant {variant.id!r} has no checks; "
                    "every variant must assert at least one fact"
                )

    def variant(self, variant_id: str) -> SmokeVariant:
        """The variant with ``variant_id``.

        Raises:
            MatrixError: no such variant.
        """
        for v in self.variants:
            if v.id == variant_id:
                return v
        raise MatrixError(
            f"matrix {self.name!r}: unknown variant {variant_id!r} "
            f"(known: {[v.id for v in self.variants]})"
        )

    def checks_for(self, variant_id: str) -> "tuple[SmokeCheck, ...]":
        """The checks that apply to ``variant_id``, in declaration order."""
        return tuple(c for c in self.checks if c.applies_to(variant_id))


# --- loading -----------------------------------------------------------------


def default_matrix_path() -> str:
    """Path of the shipped matrix (``smoke/core.toml`` in this tool)."""
    return str(_SMOKE_DIR / DEFAULT_MATRIX_FILE)


def build_root() -> Path:
    """Where ``pengyos-build`` writes its output: ``$PENGYOS_BUILD_ROOT``, else
    ``~/build/pengyos``. A matrix's relative ``image`` resolves against it, so
    the shipped matrix finds a fresh build from any working directory.
    """
    env = os.environ.get("PENGYOS_BUILD_ROOT")
    return Path(env).expanduser() if env else Path.home() / "build" / "pengyos"


def resolve_image(
    matrix: SmokeMatrix,
    override: "str | None" = None,
    *,
    base: "str | None" = None,
) -> str:
    """The image path to boot for ``matrix``.

    An ``override`` (the CLI ``--image``) is used verbatim — it is the
    operator's own path, so a relative one resolves against their working
    directory. The matrix's *own* relative ``image`` resolves against the
    build root (:func:`build_root`), falling back to the working directory
    when that is where the file actually is.

    Raises:
        MatrixError: neither the override nor the matrix names an image.
    """
    raw = override if override else matrix.image
    if not raw:
        raise MatrixError(
            f"matrix {matrix.name!r} declares no image and no --image was given"
        )
    path = Path(raw)
    if override or path.is_absolute():
        return str(path)
    candidate = (Path(base) if base else build_root()) / path
    if not candidate.exists() and path.exists():
        return str(path)
    return str(candidate)


def _variant_from_dict(raw: "dict", source: str) -> SmokeVariant:
    if not isinstance(raw, dict):
        raise MatrixError(f"{source}: each [[variant]] must be a table")
    try:
        vid = raw["id"]
    except KeyError:
        raise MatrixError(f"{source}: a [[variant]] is missing its 'id'") from None
    return SmokeVariant(
        id=str(vid),
        description=str(raw.get("description", "")),
        uefi=bool(raw.get("uefi", False)),
        tpm=bool(raw.get("tpm", False)),
        kernel=(str(raw["kernel"]) if raw.get("kernel") else None),
        timeout=float(raw.get("timeout", DEFAULT_TIMEOUT)),
    )


def _check_from_dict(raw: "dict", source: str) -> SmokeCheck:
    if not isinstance(raw, dict):
        raise MatrixError(f"{source}: each [[check]] must be a table")
    for required_key in ("id", "kind", "pattern"):
        if required_key not in raw:
            raise MatrixError(
                f"{source}: a [[check]] is missing '{required_key}' "
                f"(got keys {sorted(raw)})"
            )
    variants = raw.get("variants", ())
    if isinstance(variants, str):
        variants = (variants,)
    return SmokeCheck(
        id=str(raw["id"]),
        kind=str(raw["kind"]),
        pattern=str(raw["pattern"]),
        description=str(raw.get("description", "")),
        variants=tuple(str(v) for v in variants),
    )


def matrix_from_dict(data: "dict", source: str = "<dict>") -> SmokeMatrix:
    """Build a :class:`SmokeMatrix` from a parsed TOML mapping.

    Raises:
        MatrixError: the mapping is not a valid matrix (missing/unknown keys,
            empty variant or check lists, dangling variant references, …).
    """
    if not isinstance(data, dict):
        raise MatrixError(f"{source}: the matrix must be a TOML table")
    if "name" not in data:
        raise MatrixError(f"{source}: the matrix is missing 'name'")
    return SmokeMatrix(
        name=str(data["name"]),
        description=str(data.get("description", "")),
        image=str(data.get("image", "")),
        version=int(data.get("version", MATRIX_VERSION)),
        variants=tuple(
            _variant_from_dict(v, source) for v in data.get("variant", ())
        ),
        checks=tuple(_check_from_dict(c, source) for c in data.get("check", ())),
    )


def load_matrix(path: "str | None" = None) -> SmokeMatrix:
    """Load and validate a matrix file (default: the shipped ``core``).

    Raises:
        MatrixError: the file is missing, not valid TOML, or not a valid
            matrix.
    """
    resolved = Path(path) if path else Path(default_matrix_path())
    try:
        with open(resolved, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise MatrixError(f"smoke matrix not found: {resolved}") from None
    except IsADirectoryError:
        raise MatrixError(f"smoke matrix path is a directory: {resolved}") from None
    except tomllib.TOMLDecodeError as exc:
        raise MatrixError(f"smoke matrix {resolved} is not valid TOML: {exc}") from None
    return matrix_from_dict(data, source=str(resolved))


# --- running -----------------------------------------------------------------


@dataclass
class VariantOutcome:
    """The result of one variant: its boot, its check results, and its verdict.

    Attributes:
        variant: the variant that was booted.
        boot: the :class:`~.boot.BootResult`, or ``None`` when the boot never
            produced one (a harness-level error, kept in :attr:`error`).
        checks: one :class:`~.assertions.CheckResult` per declared check.
        error: a harness-level failure for this variant (e.g. the boot dropped
            the checks, or raised); ``None`` on a normal run.
    """

    variant: SmokeVariant
    boot: "BootResult | None" = None
    checks: "list[CheckResult]" = field(default_factory=list)
    error: "str | None" = None

    @property
    def passed(self) -> bool:
        """True when the boot was ok *and* every check passed."""
        if self.error is not None or self.boot is None:
            return False
        return self.boot.ok and all(c.passed for c in self.checks)

    @property
    def failed_checks(self) -> "list[str]":
        """Names of the failed checks (empty when everything passed)."""
        return [c.name for c in self.checks if not c.passed]

    def to_dict(self) -> "dict":
        """The JSON shape for one variant."""
        return {
            "id": self.variant.id,
            "result": reports.PASS_RESULT if self.passed else reports.FAIL_RESULT,
            "boot_ok": bool(self.boot.ok) if self.boot is not None else False,
            "ready": bool(self.boot.ready) if self.boot is not None else False,
            "duration": round(self.boot.duration, 3) if self.boot is not None else 0.0,
            "serial_log": self.boot.serial_log if self.boot is not None else "",
            "checks": [c.to_dict() for c in self.checks],
            "failed_checks": self.failed_checks,
            "error": self.error,
        }


@dataclass
class MatrixOutcome:
    """The verdict for a whole matrix: every variant's outcome plus totals."""

    matrix: SmokeMatrix
    image: str
    variants: "list[VariantOutcome]" = field(default_factory=list)
    duration: float = 0.0

    @property
    def passed(self) -> bool:
        """True when at least one variant ran and every one of them passed."""
        return bool(self.variants) and all(v.passed for v in self.variants)

    @property
    def result(self) -> str:
        """``"pass"`` / ``"fail"`` — the matrix verdict."""
        return reports.PASS_RESULT if self.passed else reports.FAIL_RESULT

    @property
    def checks_run(self) -> int:
        """Total number of checks evaluated across all variants."""
        return sum(len(v.checks) for v in self.variants)

    @property
    def checks_failed(self) -> int:
        """Total number of failed checks across all variants."""
        return sum(len(v.failed_checks) for v in self.variants)

    def to_dict(self) -> "dict":
        """The matrix JSON report (variant list + totals + verdict)."""
        return {
            "matrix": self.matrix.name,
            "description": self.matrix.description,
            "image": self.image,
            "result": self.result,
            "duration": round(self.duration, 3),
            "variants_total": len(self.variants),
            "variants_failed": sum(1 for v in self.variants if not v.passed),
            "checks_total": self.checks_run,
            "checks_failed": self.checks_failed,
            "variants": [v.to_dict() for v in self.variants],
        }

    def to_json(self) -> str:
        """The JSON report as a string (2-space indent, trailing newline)."""
        return json.dumps(self.to_dict(), indent=2) + "\n"

    def to_junit(self) -> str:
        """The JUnit XML report: one ``<testsuite>`` per variant.

        Each suite carries the variant's boot-outcome testcase and one testcase
        per check, mirroring :func:`.reports.junit_xml`'s shape (so the same
        consumers — CI, :mod:`.verify_generic`'s ``expect = "junit:…`` — read
        it unchanged).
        """
        tests = 0
        failures = 0
        for outcome in self.variants:
            tests += 1 + len(outcome.checks) + (1 if outcome.error else 0)
            failures += len(outcome.failed_checks)
            if not (outcome.boot is not None and outcome.boot.ok):
                failures += 1
            if outcome.error:
                failures += 1

        root = ET.Element(
            "testsuites",
            {
                "name": reports.SUITE_NAME,
                "tests": str(tests),
                "failures": str(failures),
                "errors": "0",
                "skipped": "0",
                "time": f"{self.duration:.3f}",
            },
        )
        for outcome in self.variants:
            boot = outcome.boot
            suite = ET.SubElement(
                root,
                "testsuite",
                {
                    "name": f"smoke:{self.matrix.name}:{outcome.variant.id}",
                    "matrix": self.matrix.name,
                    "variant": outcome.variant.id,
                    "image": self.image,
                    "result": (
                        reports.PASS_RESULT if outcome.passed else reports.FAIL_RESULT
                    ),
                    "tests": str(1 + len(outcome.checks) + (1 if outcome.error else 0)),
                    "failures": str(
                        len(outcome.failed_checks)
                        + (0 if (boot is not None and boot.ok) else 1)
                        + (1 if outcome.error else 0)
                    ),
                    "errors": "0",
                    "skipped": "0",
                    "time": f"{boot.duration if boot is not None else 0.0:.3f}",
                },
            )
            boot_case = ET.SubElement(
                suite,
                "testcase",
                {"name": reports.BOOT_TESTCASE, "classname": "pengyos-qemu-verify.boot"},
            )
            if outcome.error:
                ET.SubElement(
                    boot_case,
                    "failure",
                    {"message": outcome.error, "type": "MatrixError"},
                ).text = outcome.error
            elif boot is None or not boot.ok:
                ET.SubElement(
                    boot_case,
                    "failure",
                    {
                        "message": "boot did not reach readiness",
                        "type": "BootFailure",
                    },
                ).text = (
                    boot.error
                    if boot is not None and boot.error
                    else (
                        "timeout: readiness markers not seen before the deadline"
                        if boot is not None and boot.timed_out
                        else "boot did not become ready"
                    )
                )

            for result in outcome.checks:
                case = ET.SubElement(
                    suite,
                    "testcase",
                    {
                        "name": result.name,
                        "classname": f"pengyos-qemu-verify.smoke.{outcome.variant.id}",
                    },
                )
                if not result.passed:
                    ET.SubElement(
                        case,
                        "failure",
                        {
                            "message": f"check failed: {result.name}",
                            "type": "CheckFailed",
                        },
                    ).text = result.evidence or (
                        "expected pattern to appear in the serial log"
                        if result.evidence is None
                        else result.evidence
                    )
        return ET.tostring(root, encoding="unicode") + "\n"


def plan_argv(
    variant: SmokeVariant,
    image: str,
    *,
    ovmf: "object" = None,
    qemu_bin: str = DEFAULT_QEMU_BIN,
) -> "list[str]":
    """Render (but do not run) the QEMU argv a variant would use.

    A TPM variant renders the placeholder datapath socket
    (:data:`TPM_SOCKET_PLACEHOLDER`) because a dry-run has no live swtpm.
    """
    return build_argv(
        image,
        kernel=variant.kernel,
        uefi=variant.uefi,
        ovmf=ovmf,
        tpm_socket=(TPM_SOCKET_PLACEHOLDER if variant.tpm else None),
        qemu_bin=qemu_bin,
    )


def run_matrix(
    matrix: SmokeMatrix,
    image: str,
    *,
    boot_fn=None,
    variants: "list[str] | tuple[str, ...] | None" = None,
    boot_kwargs: "dict | None" = None,
    settle: float = DEFAULT_SETTLE_SECONDS,
) -> MatrixOutcome:
    """Boot every (selected) variant and evaluate the matrix's checks.

    Args:
        matrix: the validated matrix to run.
        image: the image path to boot.
        boot_fn: the boot implementation — defaults to
            :func:`.boot.boot_image`. Injected in tests (no QEMU needed).
            Called as ``boot_fn(image, assertions=<checks>, **variant kwargs)``
            and must return a :class:`~.boot.BootResult` whose ``checks``
            correspond 1:1 to the assertions passed in.
        variants: subset of variant ids to run (default: all, in matrix order).
        boot_kwargs: extra keyword arguments merged into every boot call
            (e.g. ``{"kvm": False}`` to force TCG, or a shared ``workdir``).

    Returns:
        A :class:`MatrixOutcome`; it never raises for a failed boot or a failed
        check — those are recorded as failures (the exit code carries the
        verdict). Per-variant harness errors (a raising ``boot_fn``, or a boot
        that did not return the declared checks) are captured on the variant
        and fail it.

    Raises:
        MatrixError: ``variants`` selects nothing, or names an unknown variant.
    """
    if boot_fn is None:
        boot_fn = boot_image
    if variants is not None:
        selected = [matrix.variant(v) for v in variants]  # validates the ids
        if not selected:
            raise MatrixError("no variants selected")
    else:
        selected = list(matrix.variants)

    extra = dict(boot_kwargs or {})
    extra.setdefault("settle", settle)
    started = time.monotonic()
    outcomes: "list[VariantOutcome]" = []
    for variant in selected:
        declared = matrix.checks_for(variant.id)
        assertions = tuple(c.to_assertion() for c in declared)
        kwargs = variant.boot_kwargs()
        kwargs.update(extra)
        try:
            boot = boot_fn(image, assertions=assertions, **kwargs)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            outcomes.append(
                VariantOutcome(
                    variant=variant, error=f"{type(exc).__name__}: {exc}"
                )
            )
            continue
        checks = list(getattr(boot, "checks", []) or [])
        outcome = VariantOutcome(variant=variant, boot=boot, checks=checks)
        if len(checks) != len(assertions):
            # A boot that dropped the checks must fail, not pass vacuously.
            outcome.error = (
                f"boot returned {len(checks)} check result(s) for "
                f"{len(assertions)} declared check(s): the matrix checks did "
                "not run"
            )
        outcomes.append(outcome)
    return MatrixOutcome(
        matrix=matrix,
        image=image,
        variants=outcomes,
        duration=time.monotonic() - started,
    )

# --- reports -----------------------------------------------------------------


def _write(path: str, text: str) -> str:
    """Write ``text`` to ``path``, creating parent directories as needed.

    Mirrors :func:`.reports._write` for the matrix reports (a different report
    type — variant suites rather than a single boot); returns the path written.
    """
    target = Path(path)
    if str(target.parent):
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return str(target)


def write_json(path: str, outcome: MatrixOutcome) -> str:
    """Write the matrix JSON report to ``path``; returns the path written."""
    return _write(path, outcome.to_json())


def write_junit(path: str, outcome: MatrixOutcome) -> str:
    """Write the matrix JUnit XML report to ``path``; returns the path written."""
    return _write(path, outcome.to_junit())
