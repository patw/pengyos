"""Shared fixtures for the pengyos-nightly suite."""
import pytest

from pengyos_nightly import bundled, sources


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
