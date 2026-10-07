import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def _no_mailer_on_the_wire(monkeypatch):
    """The suite must never reach a real mailer.

    pengyos-nightly resolves its mailer from $PENGYOS_EMAIL_SCRIPT, so on a host
    that has one configured a test which forgot to inject a fake `send` would
    send a real email out of the test suite. Pin the variable to a path that
    does not exist: the worst a test can then do is report-only behaviour.
    """
    monkeypatch.setenv("PENGYOS_EMAIL_SCRIPT", "/nonexistent/pengyos-email")
