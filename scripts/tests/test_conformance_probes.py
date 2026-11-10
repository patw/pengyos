"""scripts/conformance-probes.{toml,py}: the probe list is well-formed, and each
probe's command means what the file says (holds / still absent / skipped)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("conformance_probes", REPO / "scripts/conformance-probes.py")
cp = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cp  # dataclasses look their module up
spec.loader.exec_module(cp)


def sh(cmd: str) -> int:
    return subprocess.run(["sh", "-c", cmd], capture_output=True).returncode


def test_every_probe_says_why_and_ids_are_names():
    probes = cp.load()
    assert len(probes) >= 15
    for p in probes:
        assert p.why.strip(), p.id
        assert p.id.replace("-", "").isalnum() and p.id.islower(), p.id


def test_report_name_starts_with_the_probe_id():
    # qemu-verify derives the check name from the command (ssh:<flattened>[:48]).
    for p in cp.load():
        assert cp.command(p).startswith(f": {p.id}; ")


def test_holding_probe_passes_and_failing_one_fails():
    assert sh(cp.command(cp.Probe("t", "true", "w"))) == 0
    assert sh(cp.command(cp.Probe("t", "false", "w"))) != 0


def test_when_skips_the_probe():
    assert sh(cp.command(cp.Probe("t", "false", "w", when="false"))) == 0
    assert sh(cp.command(cp.Probe("t", "false", "w", when="true"))) != 0


def test_absent_must_stay_absent():
    assert sh(cp.command(cp.Probe("t", "false", "w", absent="on purpose"))) == 0
    assert sh(cp.command(cp.Probe("t", "true", "w", absent="on purpose"))) != 0


def test_quotes_survive(tmp_path):
    # Probes carry quotes and $vars; the command must run them unchanged.
    f = tmp_path / "it's here"
    f.write_text("x")
    assert sh(cp.command(cp.Probe("t", f"test -e \"{f}\" && x=1 && test \"$x\" = 1", "w"))) == 0


def test_unknown_key_is_an_error(tmp_path):
    bad = tmp_path / "p.toml"
    bad.write_text('[[probe]]\nid = "a"\nrun = "true"\nwhy = "w"\nexpect = "ok"\n')
    try:
        cp.load(bad)
    except ValueError as e:
        assert "expect" in str(e)
    else:
        raise AssertionError("unknown key accepted")
