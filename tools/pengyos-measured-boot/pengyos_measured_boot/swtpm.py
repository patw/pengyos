"""swtpm runner abstraction.

Boots an image under a software TPM (``swtpm``) and reads the Platform
Configuration Registers (PCRs). The subprocess execution lives behind an
injectable :data:`ProcRunner` so tests can mock it — no real ``swtpm``, no
root, and no TPM hardware are required.

The runner is deliberately split into two steps so each is independently
testable:

``build_boot_argv``
    The argv for the boot step (golden-assertable).
``build_read_pcrs_argv``
    The argv for the read step (golden-assertable).

The raw stdout of the read step is returned verbatim; parsing it into
``{index: value}`` is the next unit of work.

Standard library only.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from .errors import PengyOSMeasuredBootError

# The default measurement tool. Real integration swaps in "tpm2_getpcrs".
# For the abstraction we invoke the configured tool with a constructed argv.
DEFAULT_BOOT_TOOL = "swtpm"
DEFAULT_READ_TOOL = "tpm2_getpcrs"


@dataclass(frozen=True)
class SwtpmBootConfig:
    """Everything the runner needs to boot an image under swtpm.

    Attributes
    ----------
    image:
        Path to the boot image to measure.
    tpm2_state:
        Directory holding the swtpm state. When ``None`` the runner derives a
        deterministic, image-derived path so the boot is reproducible.
    pcr_algo:
        Digest algorithm the measurements are recorded under (e.g. ``sha256``).
    boot_tool / read_tool:
        Executable names for the boot and read steps. Overridable so integration
        tests can point at a specific install.
    """

    image: str
    tpm2_state: str | None = None
    pcr_algo: str = "sha256"
    boot_tool: str = DEFAULT_BOOT_TOOL
    read_tool: str = DEFAULT_READ_TOOL


@dataclass(frozen=True)
class ProcResult:
    """A captured subprocess result."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


# A ProcRunner replaces subprocess at call time. It receives the argv and
# returns a ProcResult. Tests inject a fake; production uses the default.
ProcRunner = Callable[[Sequence[str]], ProcResult]


def _default_proc_runner(argv: Sequence[str]) -> ProcResult:
    proc = subprocess.run(list(argv), capture_output=True, text=True)
    return ProcResult(proc.returncode, proc.stdout, proc.stderr)


def _derive_tpm2_state(config: SwtpmBootConfig) -> str:
    """A deterministic, image-derived swtpm state directory."""

    import hashlib

    digest = hashlib.sha256(config.image.encode("utf-8")).hexdigest()[:16]
    return f".swtpm/swtpm-state-{digest}"


def build_boot_argv(config: SwtpmBootConfig) -> list[str]:
    """Build the argv for the boot step.

    Deterministic given the config so the exact argv can be golden-asserted in
    tests. No process is spawned here.
    """

    state = config.tpm2_state or _derive_tpm2_state(config)
    return [
        config.boot_tool,
        "boot",
        "--image",
        config.image,
        "--tpm2-state",
        state,
        "--pcr-algo",
        config.pcr_algo,
    ]


def build_read_pcrs_argv(config: SwtpmBootConfig) -> list[str]:
    """Build the argv for the read step.

    The PCRs are emitted as JSON on stdout so the next unit of work can parse it
    deterministically.
    """

    return [
        config.read_tool,
        "--output",
        "-",
        "--format",
        "json",
        "--pcr-list",
        "0,1,2,3,4,5,6,7,8,11,12,16",
    ]


class SwtpmRunner:
    """Boot an image under swtpm and read its PCRs.

    Parameters
    ----------
    config:
        The boot configuration.
    proc_runner:
        Callable replacing ``subprocess.run``. Injectable so tests mock it; when
        omitted the runner invokes the real subprocess.
    """

    def __init__(
        self,
        config: SwtpmBootConfig,
        proc_runner: ProcRunner | None = None,
    ) -> None:
        self.config = config
        self._proc_runner: ProcRunner = proc_runner or _default_proc_runner

    def boot(self) -> ProcResult:
        """Boot the image under swtpm and return the captured result."""

        argv = build_boot_argv(self.config)
        result = self._proc_runner(argv)
        if result.returncode != 0:
            raise PengyOSMeasuredBootError(
                "swtpm boot failed (exit %d): %s"
                % (result.returncode, result.stderr.strip())
            )
        return result

    def read_pcrs(self) -> ProcResult:
        """Read the PCR values and return the raw stdout of the read step."""

        argv = build_read_pcrs_argv(self.config)
        result = self._proc_runner(argv)
        if result.returncode != 0:
            raise PengyOSMeasuredBootError(
                "failed to read PCRs (exit %d): %s"
                % (result.returncode, result.stderr.strip())
            )
        return result

    def boot_and_read_pcrs(self) -> ProcResult:
        """Boot then read; returns the raw read-step stdout."""

        self.boot()
        return self.read_pcrs()


def require_tool(name: str) -> None:
    """Raise a clear error if an external tool is not on ``PATH``.

    Used by the ``run`` backend so a missing ``swtpm``/``tpm2_getpcrs`` is
    reported as an actionable error rather than an obscure ``FileNotFoundError``.
    """

    if shutil.which(name) is None:
        raise PengyOSMeasuredBootError(
            f"required tool {name!r} not found on PATH"
        )
