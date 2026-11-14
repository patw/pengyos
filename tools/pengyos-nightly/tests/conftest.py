"""Shared fixtures for the pengyos-nightly suite."""
import pytest

from pengyos_nightly import bundled, report, sources


@pytest.fixture(autouse=True)
def _no_operator_env(monkeypatch, tmp_path):
    """The config reads these from the environment, and the environment wins over what a
    test passes. release.sh exports the real key and site remote before running the
    whole-repo gate, which made a test that gives a missing key find the real one.

    Clearing PENGYOS_EMAIL_SCRIPT alone is not enough: the mailer then falls back to
    ~/.local/bin/pengyos-email, the operator's real one. On the build host every gate
    run mailed them a fake "[act lane] ... published nothing" from
    test_act_exit_codes_distinguish..., and on 2026-11-14 the mailer hung and the test
    timed out, turning a release gate red. A test that wants a mailer names one with
    PENGYOS_EMAIL_SCRIPT."""
    for name in ("PENGYOS_RELEASE_KEY", "PENGYOS_SITE_REMOTE", "PENGYOS_EMAIL_SCRIPT", "PENGY_SETTINGS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(report, "DEFAULT_EMAIL_SCRIPT", tmp_path / "no-default-mailer")


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
