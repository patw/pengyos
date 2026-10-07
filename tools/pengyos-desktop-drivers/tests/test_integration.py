"""Opt-in end-to-end integration test.

Skipped unless ``PENGYOS_IT=1``. It exercises the real pipeline: ``emit`` writes
the Desktop profile, then ``lint`` validates the produced fragment + firmware
list. This is the cheapest rung that proves the two modules compose; it requires
the emit/lint features to be implemented.

No network, root, display, or hardware is needed -- everything is written to and
validated from the temp directory.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from pengyos_desktop_drivers import emit, lint

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.mark.skipif(
    not os.environ.get("PENGYOS_IT"),
    reason="integration tests are opt-in; set PENGYOS_IT=1 to run",
)
def test_emit_then_lint_end_to_end(tmp_path: pathlib.Path) -> None:
    result = emit.emit_desktop_profile(tmp_path)
    fragment = tmp_path / emit.FRAGMENT_FILENAME
    firmware = tmp_path / emit.FIRMWARE_FILENAME
    assert fragment.exists()
    assert firmware.exists()

    report = lint.lint_fragment(str(fragment), str(firmware))
    assert report["ok"], report["errors"]
