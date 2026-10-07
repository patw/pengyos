"""Measured-boot orchestration for pengyos-measured-boot.

This is the ``run`` backend: it boots an image under a software TPM
(``swtpm``), reads the Platform Configuration Registers (PCRs), parses them,
and verifies them against the expected values required by ``--expect``.

The whole flow sits behind the mockable :class:`pengyos_measured_boot.swtpm.SwtpmRunner`
(an injectable subprocess layer), so the orchestration is fully exercised in
tests with no real ``swtpm``, no root, and no TPM hardware.

The flow is intentionally a thin composition of the already-tested units:

1. :meth:`SwtpmRunner.boot_and_read_pcrs` — boot + read (returns raw stdout).
2. :func:`pengyos_measured_boot.pcrs.parse_pcrs` — parse raw stdout into a
   :class:`pengyos_measured_boot.pcrs.Pcrs`.
3. :meth:`pengyos_measured_boot.policy.Policy.from_mapping` +
   :meth:`pengyos_measured_boot.policy.Policy.verify` — verify against the
   expected values.

Standard library only.
"""

from __future__ import annotations

from typing import Mapping

from .pcrs import parse_pcrs
from .policy import Policy
from .swtpm import ProcRunner, SwtpmBootConfig, SwtpmRunner

# The digest algorithm the read step requests and this backend compares under.
# Mirrors ``SwtpmBootConfig.pcr_algo`` / ``DEFAULT_PCR_ALGO``.
DEFAULT_PCR_ALGO = "sha256"


def run_measured_boot(
    config: SwtpmBootConfig,
    expect: Mapping[int, int],
    *,
    proc_runner: ProcRunner | None = None,
    algo: str = DEFAULT_PCR_ALGO,
) -> dict[str, object]:
    """Boot an image, read its PCRs, and verify them against ``expect``.

    Parameters
    ----------
    config:
        The boot configuration (image, state dir, algorithm, tools).
    expect:
        The required PCR values, ``{index: value}`` integers, as parsed from
        ``--expect``.
    proc_runner:
        Callable replacing ``subprocess.run`` for the swtpm subprocess layer.
        Injectable so tests mock it; when omitted the runner invokes the real
        subprocess.
    algo:
        The digest algorithm the measurements are recorded under.

    Returns
    -------
    dict
        A result payload on success: ``{"ok": True, "image": ..., "required": ...}``.

    Raises
    ------
    pengyos_measured_boot.errors.PolicyMismatch
        If any required PCR does not match the expected value (naming every
        mismatching PCR with expected vs. actual).
    pengyos_measured_boot.errors.PengyOSMeasuredBootError
        If the boot/read step fails, or the read output cannot be parsed.
    """

    runner = SwtpmRunner(config, proc_runner=proc_runner)
    raw = runner.boot_and_read_pcrs()

    pcrs = parse_pcrs(raw.stdout, algo=algo)

    policy = Policy.from_mapping(dict(expect))
    policy.verify(pcrs)  # raises PolicyMismatch on any mismatch

    return {
        "ok": True,
        "image": config.image,
        "required": sorted(policy.indices()),
    }
