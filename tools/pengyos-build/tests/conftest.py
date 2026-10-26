"""Suite-wide fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _no_build_jobs_env(monkeypatch):
    # A build host may export PENGYOS_BUILD_JOBS (behopc's box sets 16); the
    # suite's -j$(nproc) expectations must not depend on the host running it.
    monkeypatch.delenv("PENGYOS_BUILD_JOBS", raising=False)
