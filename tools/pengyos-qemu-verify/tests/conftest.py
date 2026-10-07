"""Test configuration for the pengyos-qemu-verify suite.

This conftest makes the repository root (which contains the
``pengyos_qemu_verify`` package) importable without a prior ``pip install``.
That keeps the suite self-sufficient when a bare ``pytest -q`` runs from the repo root, and it lets the same suite also run against an
installed copy.

It also scopes the suite's temporary directories. :func:`boot.boot_image`
*deliberately keeps* the workdir it creates (it holds the serial log, the
evidence for the assertions), so every unit test that boots through the fake
qemu used to leave a ``/tmp/pengyos-verify-*`` directory behind — 2 152 of them
had accumulated on behoserver by 2026-09-14, growing by roughly one per boot on
every nightly loop iteration. Pointing ``TMPDIR`` at a session-scoped directory
lets ``mkdtemp`` land there and lets pytest clean up after itself, instead of
changing the harness's documented keep-the-evidence behaviour.
"""

import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@pytest.fixture(scope="session", autouse=True)
def _scoped_tmpdir(tmp_path_factory):
    """Run the whole session with ``TMPDIR`` inside pytest's temp tree.

    ``tempfile`` caches its temp directory in the module global
    ``tempfile.tempdir`` the first time it is used, so setting the environment
    variable alone is not enough (anything that called ``mkdtemp`` first —
    import time, or an earlier test — would pin ``/tmp``). Set both.
    """
    scoped = tmp_path_factory.mktemp("pengyos-verify-tmp")
    previous_env = os.environ.get("TMPDIR")
    previous_tempdir = tempfile.tempdir
    os.environ["TMPDIR"] = str(scoped)
    tempfile.tempdir = str(scoped)
    try:
        yield scoped
    finally:
        tempfile.tempdir = previous_tempdir
        if previous_env is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = previous_env
