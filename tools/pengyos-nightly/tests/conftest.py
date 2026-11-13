"""Shared fixtures for the pengyos-nightly suite."""
import pytest

from pengyos_nightly import bundled, sources


@pytest.fixture(autouse=True)
def _no_operator_env(monkeypatch):
    """The config reads these from the environment, and the environment wins over what a
    test passes. release.sh exports the real key and site remote before running the
    whole-repo gate, which made a test that gives a missing key find the real one."""
    for name in ("PENGYOS_RELEASE_KEY", "PENGYOS_SITE_REMOTE", "PENGYOS_EMAIL_SCRIPT", "PENGY_SETTINGS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_real_gate(monkeypatch):
    """observe() runs the whole-repo gate (about 10 minutes). No test may start the
    real one; a test that cares passes run_gate= itself."""
    monkeypatch.setattr(sources, "run_gate",
                        lambda repo: {"ok": True, "summary": "fake: 1/1 checks passed", "failed": []})


@pytest.fixture(autouse=True)
def _no_real_osv(monkeypatch):
    """observe() asks osv.dev about every bundled crate and Go module. No test may
    reach the network; a test that cares passes scan_bundled= or calls bundled.scan."""
    monkeypatch.setattr(bundled, "observe", lambda build_root, targets, state_dir: ([], {}))
