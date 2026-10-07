#!/usr/bin/env python3
"""Regenerate the committed TPM state fixture (tests/fixtures/tpmstate).

The fixture directory *is* a state directory: it holds one real swtpm state
file (``tpm2-00.permall`` — persistent TPM2 state) produced by a genuine
``swtpm socket --tpm2`` run and verified to be reloaded by swtpm (a fresh
``swtpm socket --tpm2 --tpmstate dir=<fixture>`` comes up against it).

Why this exact recipe (discovered from the swtpm 0.10.1 source, 2026-09-13):

* In ``--server`` (QEMU-facing unix socket) mode the TPM only starts after a
  ctrl-channel ``CMD_INIT`` handshake (QEMU's tpm-emulator backend does
  this), so a bare SIGTERM with no client never runs the TPM and persists
  nothing.
* ``--flags not-need-init`` skips that handshake: the TPM starts immediately
  (TPM2_Startup CLEAR) and, on a graceful SIGTERM, swtpm's main loop
  auto-sends TPM2_Shutdown and persists the state file before exiting.
* ``swtpm_setup`` (the standard provisioning tool) is not installed on the
  build host; this recipe produces the equivalent minimal state with the
  ``swtpm`` binary alone. ``swtpm_setup`` remains the *preferred* runtime
  path in :mod:`pengyos_qemu_verify.tpm_state` — this script is only how the
  committed fallback fixture is built (and re-built when needed).

Usage:  python3 tests/fixtures/tpmstate/GENERATE.py [--swtpm PATH]

Requires: a ``swtpm`` (TPM 2 capable) binary and no root. The directory ends
up containing this script plus exactly the state file(s); the runtime
``.lock`` file and the socket/pid/log files (which live in a short temp dir,
because this repo can sit under a deep checkout and unix-socket paths have a
~108-char limit) are never part of the fixture.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time


def _is_script(name: str) -> bool:
    return name.endswith(".py")


def generate(fixture_dir: str, swtpm_bin: str = "swtpm", ready_timeout: float = 10.0) -> "list[str]":
    """Run a real swtpm long enough to persist state into ``fixture_dir``.

    Steps: clear runtime files from ``fixture_dir`` (keeping ``.py`` scripts)
    -> start ``swtpm socket --tpm2 --flags not-need-init --tpmstate
    dir=<fixture_dir>`` with its runtime files in a short temp dir -> wait
    for the datapath socket (proof the TPM is running) -> SIGTERM (graceful:
    swtpm auto-sends TPM2_Shutdown and persists) -> drop runtime files, keep
    the state files. Returns the state file names written.
    """
    state_dir = os.path.abspath(fixture_dir)
    os.makedirs(state_dir, exist_ok=True)

    # Fresh run: drop stale runtime files, keep this script (.py files).
    for name in os.listdir(state_dir):
        if _is_script(name):
            continue
        path = os.path.join(state_dir, name)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    runtime = tempfile.mkdtemp(prefix="tpmfix-")
    sock_path = os.path.join(runtime, "tpm.sock")
    ctrl_path = os.path.join(runtime, "tpm.ctrl")
    pidfile = os.path.join(runtime, "swtpm.pid")
    out_path = os.path.join(runtime, "swtpm.out")
    err_path = os.path.join(runtime, "swtpm.err")

    argv = [
        swtpm_bin, "socket", "--tpm2", "--flags", "not-need-init",
        "--server", f"type=unixio,path={sock_path}",
        "--ctrl", f"type=unixio,path={ctrl_path}",
        "--tpmstate", f"dir={state_dir}",
        "--pid", f"file={pidfile}",
    ]
    try:
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            proc = subprocess.Popen(
                argv, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                cwd=runtime,
            )
        deadline = time.monotonic() + ready_timeout
        while not os.path.exists(sock_path):
            if proc.poll() is not None:
                err_text = open(err_path).read()
                raise SystemExit(
                    f"swtpm exited before opening its socket "
                    f"(code={proc.returncode}):\n{err_text}"
                )
            if time.monotonic() >= deadline:
                raise SystemExit(
                    f"swtpm did not open {sock_path!r} within {ready_timeout}s"
                )
            time.sleep(0.1)
        # The socket existing proves the emulator + TPM are up (with
        # not-need-init swtpm opens it only after tpmlib_start succeeds).
        os.kill(proc.pid, signal.SIGTERM)  # graceful: persists the state
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    finally:
        if "proc" in locals() and proc.poll() is None:
            proc.kill()
            proc.wait()
        shutil.rmtree(runtime, ignore_errors=True)

    # Keep only the state files (plus this script); drop swtpm's runtime
    # leftovers (e.g. the .lock file), wherever they were written.
    state_files = []
    for name in os.listdir(state_dir):
        path = os.path.join(state_dir, name)
        if not os.path.isfile(path):
            continue
        if name.startswith(("tpm2-", "tpm-")):
            state_files.append(name)
        elif not _is_script(name):
            try:
                os.remove(path)
            except OSError:
                pass
    if not state_files:
        raise SystemExit(
            f"swtpm left no state files in {state_dir!r} — fixture generation failed"
        )
    return sorted(state_files)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate the committed swtpm TPM state fixture."
    )
    parser.add_argument(
        "--swtpm", default="swtpm", metavar="PATH",
        help="swtpm binary (default: swtpm on PATH)",
    )
    parser.add_argument(
        "fixture_dir", nargs="?", default=None,
        help="fixture directory (default: this script's own directory)",
    )
    args = parser.parse_args(argv)
    fixture_dir = args.fixture_dir or os.path.dirname(os.path.abspath(__file__))
    state_files = generate(fixture_dir, swtpm_bin=args.swtpm)
    print(f"TPM state fixture generated: {fixture_dir}")
    for name in state_files:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
