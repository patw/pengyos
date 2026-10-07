"""TPM state provisioning for the PengyOS boot-test harness.

This module implements the *provisioning* half of ``--tpm`` boots: making sure the
``--tpmstate`` directory handed to ``swtpm`` contains a valid,
initialised TPM2 state, so a guest that actually exercises TPM2 commands
does not hit an uninitialised emulator.

It is deliberately split so every half is testable without ``swtpm`` or
``swtpm_setup`` installed:

* :func:`build_swtpm_setup_argv` — a *pure* builder that renders the exact
  ``swtpm_setup`` command line that provisions a state directory.
* :func:`find_swtpm_setup_bin` — locate a real ``swtpm_setup`` on ``PATH``
  (returns ``None`` when absent; never raises).
* :func:`provision_tpm_state` — the provisioning policy: keep existing
  state, run ``swtpm_setup`` when available, else seed from the committed
  state fixture (``tests/fixtures/tpmstate``), and raise
  :class:`TpmStateError` when no source can yield a state directory.

The committed fixture is a *real* state directory: it was produced by a
genuine ``swtpm socket --tpm2`` run on the build host (see
``tests/fixtures/tpmstate/GENERATE.py``) and is verified loadable by swtpm.

The QEMU side (``-chardev``/``-tpmdev``) and the swtpm process lifecycle
live in :mod:`.argv` / :mod:`.tpm`; this module owns only the *state*.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "SWTPM_SETUP_BIN",
    "TpmStateError",
    "TpmStateProvision",
    "build_swtpm_setup_argv",
    "find_swtpm_setup_bin",
    "default_fixture_dir",
    "has_tpm_state",
    "provision_tpm_state",
]

# Default provisioning tool; overridable for tests/fakes.
SWTPM_SETUP_BIN: str = "swtpm_setup"

# Where swtpm persists its state files (see swtpm(8), --tpmstate):
#   TPM2 -> tpm2-00.permall (+ tpm2-00.save on a TPM2_Shutdown STATE)
#   TPM 1.2 -> tpm-00.permall / tpm-00.save
# swtpm also drops a ``.lock`` file in the directory while running — that is
# not state and must never be treated as one (or copied as one).
STATE_FILE_PREFIXES: "tuple[str, ...]" = ("tpm2-", "tpm-")
NON_STATE_BASENAMES: "tuple[str, ...]" = (".lock",)


class TpmStateError(RuntimeError):
    """Raised when no usable TPM state could be provisioned.

    This is a *pre-flight* error (like ``ImageNotFound`` / ``OvmfNotFound``):
    with ``--tpm`` set, provisioning happens before QEMU is launched, so the
    CLI reports it through the normal "always writes JSON" pre-flight path.
    """


@dataclass(frozen=True)
class TpmStateProvision:
    """Outcome of provisioning one TPM state directory."""

    state_dir: str
    method: str  # 'existing' | 'swtpm_setup' | 'fixture'
    detail: str = ""

    def to_dict(self) -> "dict":
        return {
            "state_dir": self.state_dir,
            "method": self.method,
            "detail": self.detail,
        }


def build_swtpm_setup_argv(
    state_dir: str,
    *,
    swtpm_setup_bin: str = SWTPM_SETUP_BIN,
    extra: "tuple[str, ...] | list[str] | None" = None,
) -> "list[str]":
    """Build the ``swtpm_setup`` command line that provisions ``state_dir``.

    The canonical invocation is ``swtpm_setup --tpm2 --tpmstate
    dir=<state_dir>``: it initialises a fresh TPM2 in that directory (owner
    hierarchy / primary seeds / EK certificate material), exactly the state a
    first ``swtpm socket --tpm2 --tpmstate dir=<state_dir>`` would persist on
    a clean shutdown.

    Args:
        state_dir: the state directory to provision into. Required, non-empty.
            It is created (with parents) by the *caller* before the command
            runs — ``swtpm_setup`` itself does not create it.
        swtpm_setup_bin: path/name of the swtpm_setup executable (default
            :data:`SWTPM_SETUP_BIN`). Injectable so tests use a fake.
        extra: optional extra tokens appended verbatim at the end (e.g.
            ``--key file=...``, ``--cert file=...`` for an EK certificate).

    Returns:
        The command vector, including ``swtpm_setup_bin`` as ``argv[0]``.

    Raises:
        ValueError: if ``state_dir`` or ``swtpm_setup_bin`` is empty.
    """
    if not state_dir:
        raise ValueError("state_dir must be a non-empty string")
    if not swtpm_setup_bin:
        raise ValueError("swtpm_setup_bin must be a non-empty string")
    argv: "list[str]" = [swtpm_setup_bin, "--tpm2", "--tpmstate", f"dir={state_dir}"]
    if extra:
        argv += list(extra)
    return argv


def find_swtpm_setup_bin() -> "str | None":
    """Return the path to a real ``swtpm_setup`` binary if on ``PATH``.

    Returns ``None`` (never raises) when it is not installed, so callers
    degrade to the committed state fixture.
    """
    return shutil.which(SWTPM_SETUP_BIN)


def default_fixture_dir() -> str:
    """Return the repo's committed TPM state fixture directory.

    The fixture lives in *tests*/fixtures/tpmstate (the repo root is the
    parent of this package directory) and holds a real, swtpm-generated
    state file. Raises :class:`TpmStateError` when the fixture is missing or
    holds no state files — a checked-out repo must always carry it.
    """
    fixture = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "tpmstate"
    if not fixture.is_dir():
        raise TpmStateError(
            f"committed TPM state fixture not found: {fixture} "
            "(regenerate it with tests/fixtures/tpmstate/GENERATE.py)"
        )
    if not has_tpm_state(str(fixture)):
        raise TpmStateError(
            f"committed TPM state fixture is empty: {fixture} "
            "(regenerate it with tests/fixtures/tpmstate/GENERATE.py)"
        )
    return str(fixture)


def _state_files(directory: str) -> "list[str]":
    """The swtpm state files in ``directory`` (never ``.lock`` / temp files)."""
    names = []
    for name in os.listdir(directory):
        if name in NON_STATE_BASENAMES or name.startswith("TMP"):
            continue
        if name.startswith(STATE_FILE_PREFIXES):
            names.append(name)
    return sorted(names)


def has_tpm_state(directory: "str | Path") -> bool:
    """True when ``directory`` already holds at least one swtpm state file.

    A ``.lock`` file (swtpm's runtime lock) or swtpm temp files (``TMP*``) do
    not count. A missing/undirectory raises :class:`TpmStateError`.
    """
    path = os.fspath(directory)
    if not os.path.isdir(path):
        raise TpmStateError(f"not a directory: {path!r}")
    return bool(_state_files(path))


def provision_tpm_state(
    state_dir: str,
    *,
    swtpm_setup_bin: "str | None" = None,
    fixture_dir: "str | None" = None,
    setup_timeout: float = 30.0,
) -> TpmStateProvision:
    """Ensure ``state_dir`` contains a valid TPM2 state; return how.

    Policy (first match wins):

    1. **existing** — ``state_dir`` already holds state files: leave them
       untouched (re-running ``swtpm_setup`` on a used state would fail, and
       a reused state is exactly what repeated boots of the same image want).
    2. **swtpm_setup** — a ``swtpm_setup`` binary is available (the explicit
       ``swtpm_setup_bin`` argument, or one on ``PATH``): run ``swtpm_setup
       --tpm2 --tpmstate dir=<state_dir>`` and require state files to appear.
       If the run fails, fall through to the fixture instead of erroring.
    3. **fixture** — seed ``state_dir`` from the committed state fixture
       (``fixture_dir`` when given, else :func:`default_fixture_dir`): the
       state files are *copied*, so the fixture itself is never mutated or
       locked.

    The state directory is created when missing. No usable source raises
    :class:`TpmStateError`.

    Args:
        state_dir: the directory swtpm will be given via ``--tpmstate
            dir=``. Required, non-empty.
        swtpm_setup_bin: explicit ``swtpm_setup`` path. ``None`` (default)
            auto-detects via :func:`find_swtpm_setup_bin` — so the *default*
            path is deterministic even where swtpm_setup is not installed:
            the committed fixture is used. Pass an empty string to force the
            fixture even when swtpm_setup is installed (tests use this).
        fixture_dir: explicit fixture directory; ``None`` (default) uses the
            committed repo fixture.
        setup_timeout: seconds to wait for a ``swtpm_setup`` run (it is a
            fast, offline tool; the bound is a safety net).

    Returns:
        The :class:`TpmStateProvision` describing what happened.

    Raises:
        TpmStateError: no existing state, no usable swtpm_setup, and no
            fixture (or the chosen source produced no state files).
        ValueError: empty ``state_dir`` or non-positive ``setup_timeout``.
    """
    if not state_dir:
        raise ValueError("state_dir must be a non-empty string")
    if setup_timeout <= 0:
        raise ValueError(f"setup_timeout must be positive (got {setup_timeout!r})")
    state_dir = os.path.abspath(state_dir)

    # 1. Existing state wins — never re-provision over a used TPM.
    if os.path.isdir(state_dir) and has_tpm_state(state_dir):
        return TpmStateProvision(
            state_dir,
            "existing",
            f"kept {len(_state_files(state_dir))} existing state file(s)",
        )

    os.makedirs(state_dir, exist_ok=True)

    # 2. swtpm_setup, when available (explicit or on PATH).
    setup_bin = find_swtpm_setup_bin() if swtpm_setup_bin is None else swtpm_setup_bin
    if setup_bin:
        try:
            argv = build_swtpm_setup_argv(state_dir, swtpm_setup_bin=setup_bin)
            subprocess.run(
                argv,
                check=True,
                timeout=setup_timeout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                cwd=state_dir,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # A failed/absent swtpm_setup must not kill a --tpm boot: the
            # fixture is the fallback source. (Recorded and raised only when
            # the fixture cannot provide state either.)
            return _seed_from_fixture(
                state_dir,
                fixture_dir,
                attempted=f"swtpm_setup failed ({exc.__class__.__name__}: {exc})",
            )
        if has_tpm_state(state_dir):
            return TpmStateProvision(
                state_dir,
                "swtpm_setup",
                f"ran {setup_bin}",
            )
        return _seed_from_fixture(
            state_dir,
            fixture_dir,
            attempted=f"swtpm_setup succeeded but wrote no state files: {setup_bin}",
        )

    # 3. The committed state fixture.
    return _seed_from_fixture(state_dir, fixture_dir, attempted=None)


def _seed_from_fixture(
    state_dir: str,
    fixture_dir: "str | None",
    attempted: "str | None",
) -> TpmStateProvision:
    """Copy the fixture's state files into ``state_dir`` (the fallback)."""
    try:
        src = os.path.abspath(fixture_dir) if fixture_dir else default_fixture_dir()
    except TpmStateError:
        detail = f"and no state fixture is available ({attempted})" if attempted \
            else "no swtpm_setup on PATH and no state fixture available"
        raise TpmStateError(
            f"could not provision TPM state for {state_dir!r}: {detail}"
        ) from None
    if not os.path.isdir(src):
        raise TpmStateError(f"TPM state fixture directory not found: {src!r}")
    files = _state_files(src)
    if not files:
        raise TpmStateError(f"TPM state fixture has no state files: {src!r}")
    for name in files:
        shutil.copy2(os.path.join(src, name), os.path.join(state_dir, name))
    detail = f"seeded {len(files)} state file(s) from fixture {src}"
    if attempted:
        detail = f"{attempted}; {detail}"
    return TpmStateProvision(state_dir, "fixture", detail)
