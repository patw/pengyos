"""End-to-end tests for the ``detect`` -> emit -> ``diff`` pipeline.

These tie the two halves of the "Emit deterministic TOML; implement diff"
subtask together *through the CLI*: ``detect`` collects a profile from a
fixture, the emitter serialises it to ``profile.toml``, and ``diff`` compares
two such files. They assert the two guarantees the subtask exists for:

* **Determinism** — two ``detect`` runs over the same fixture are
  byte-identical (the whole collect→emit path is byte-stable);
* **Diff** — a modified fixture surfaces an added device and a changed value
  through the very same ``detect``/``emit``/``diff`` path a human would use.

No root, no network, no real hardware: the committed fixture is copied into a
temp dir, and the CLI is driven via ``main`` exactly like a real invocation.
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

from pengyos_hwprofile.cli import main

FIXTURES = Path(__file__).resolve().parents[0] / "fixtures" / "sys"


def _copy_fixture(root: Path) -> Path:
    """Copy the committed fixture tree to ``root`` (preserving symlinks)."""

    dest = root / "sys"
    shutil.copytree(FIXTURES, dest, symlinks=True)
    return dest


def _detect(source: Path, out: Path) -> int:
    """Run ``detect --from source --out out`` and return the exit code."""

    return main(["detect", "--from", str(source), "--out", str(out)])


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------


def test_two_detect_runs_over_the_same_source_are_byte_identical(tmp_path):
    src = _copy_fixture(tmp_path)
    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    assert _detect(src, a) == 0
    assert _detect(src, b) == 0
    # The collect -> emit path is byte-stable across runs.
    assert a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")


def test_emit_output_round_trips_through_tomllib(tmp_path):
    src = _copy_fixture(tmp_path)
    out = tmp_path / "profile.toml"
    assert _detect(src, out) == 0
    # The emitted profile.toml parses back to a valid document.
    doc = tomllib.loads(out.read_text(encoding="utf-8"))
    assert doc["metadata"]["hostname"] == "pengyos-laptop"
    assert doc["cpu"]["cores"] == 8
    assert len(doc["gpu"]) == 1
    assert len(doc["storage"]) == 2
    assert len(doc["network"]) == 2
    assert len(doc["peripherals"]) == 3


# ---------------------------------------------------------------------------
# Diff through the pipeline.
# ---------------------------------------------------------------------------


def test_diff_reports_added_and_changed_devices(tmp_path, capsys):
    base = _copy_fixture(tmp_path)
    a = tmp_path / "a.toml"
    assert _detect(base, a) == 0

    # A second machine: a new USB peripheral added, and the eth speed doubled.
    modified = _copy_fixture(tmp_path / "modified")
    new_dev = modified / "bus" / "usb" / "devices" / "6-3"
    new_dev.mkdir(parents=True)
    (new_dev / "idVendor").write_text("0x1234\n", encoding="utf-8")
    (new_dev / "idProduct").write_text("0x5678\n", encoding="utf-8")
    (new_dev / "product").write_text("New Widget\n", encoding="utf-8")
    (modified / "class" / "net" / "enp3s0" / "speed").write_text(
        "2500\n", encoding="utf-8"
    )

    c = tmp_path / "c.toml"
    assert _detect(modified, c) == 0

    # The diff exits non-zero because the machines differ.
    rc = main(["diff", str(a), str(c)])
    assert rc == 1
    out = capsys.readouterr().out
    # The new USB peripheral is reported as added (by its identity key).
    assert "+ peripherals:" in out
    assert "0x1234" in out  # the new device's vendor id
    # The doubled ethernet speed is reported as changed.
    assert "~ network:" in out
    # An unchanged device is not mentioned.
    assert "+ storage:" not in out
    assert "~ storage:" not in out


def test_diff_identical_sources_exit_zero(tmp_path):
    base = _copy_fixture(tmp_path)
    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    assert _detect(base, a) == 0
    assert _detect(base, b) == 0
    # Two captures of the same machine are identical -> exit 0.
    assert main(["diff", str(a), str(b)]) == 0
