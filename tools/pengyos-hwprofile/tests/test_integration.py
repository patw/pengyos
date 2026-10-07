"""Opt-in integration tests: the live-system ``detect`` / ``diff`` path.

These point the CLI at the real ``/proc`` + ``/sys`` (``detect`` with no
``--from``, which requires ``PENGYOS_IT=1``) and assert the emitted profile is
valid and self-consistent. They are skipped unless ``PENGYOS_IT`` is set (see
``conftest.py``), so the default suite needs no root, no real hardware, and no
read of the live machine.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib

import pytest

from pengyos_hwprofile.cli import main
from pengyos_hwprofile.schema import validate

REQUIRE_PENGYOS_IT = os.environ.get("PENGYOS_IT") in ("1", "true", "True")


@pytest.mark.integration
@pytest.mark.skipif(not REQUIRE_PENGYOS_IT, reason="live-system integration (PENGYOS_IT=1)")
def test_live_detect_emits_a_valid_profile(tmp_path):
    out = tmp_path / "live.toml"
    rc = main(["detect", "--out", str(out)])
    assert rc == 0, f"detect exited {rc}"

    text = out.read_text(encoding="utf-8")
    doc = tomllib.loads(text)  # must be valid TOML
    validate(doc)  # and conform to the schema

    # A real machine reports a hostname and kernel.
    assert doc["metadata"]["hostname"]
    assert doc["metadata"]["kernel"]
    # And a CPU is always present.
    assert doc["cpu"]["vendor"]
    assert doc["cpu"]["cores"] >= 1


@pytest.mark.integration
@pytest.mark.skipif(not REQUIRE_PENGYOS_IT, reason="live-system integration (PENGYOS_IT=1)")
def test_live_diff_runs_and_exits_cleanly(tmp_path):
    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    assert main(["detect", "--out", str(a)]) == 0
    assert main(["detect", "--out", str(b)]) == 0
    # Two live captures of the same machine differ only by tool_version / timing,
    # so diff may report changes; the point is that it runs and exits cleanly.
    rc = main(["diff", str(a), str(b)])
    assert rc in (0, 1)


@pytest.mark.integration
@pytest.mark.skipif(not REQUIRE_PENGYOS_IT, reason="live-system integration (PENGYOS_IT=1)")
def test_live_detect_via_module_entrypoint():
    # The console entry point (not just main()) reaches the live path too.
    result = subprocess.run(
        [sys.executable, "-m", "pengyos_hwprofile", "detect"],
        capture_output=True,
        text=True,
        env={**os.environ, "PENGYOS_IT": "1"},
    )
    assert result.returncode == 0, result.stderr
    doc = tomllib.loads(result.stdout)
    validate(doc)
    assert doc["cpu"]["vendor"]
