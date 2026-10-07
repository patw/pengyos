"""Tests for :mod:`pengyos_hwprofile.cli` — the ``detect`` and ``diff`` subcommands.

These drive the CLI the way a human would (calling ``main`` with an argv list),
against the committed fixture tree — no root, no network, no real hardware. The
live-system path is covered by the opt-in integration tests
(``PENGYOS_IT=1``).
"""

from __future__ import annotations

import os
import tomllib

import pytest

from pengyos_hwprofile.cli import build_parser, main
from pengyos_hwprofile.schema import validate

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "sys")


def _write_profile(path: str, document: dict) -> None:
    """Write a validated document to ``path`` as TOML via the emitter."""

    from pengyos_hwprofile.emitter import emit

    from pengyos_hwprofile.schema import load_profile

    load_profile(document)  # must be valid before we emit it
    path.write_text(emit(document), encoding="utf-8")


# ---------------------------------------------------------------------------
# Parser surface.
# ---------------------------------------------------------------------------


def test_parser_registers_detect_and_diff():
    parser = build_parser()
    help_text = parser.format_help()
    assert "detect" in help_text
    assert "diff" in help_text


def test_bare_invocation_prints_help_and_returns_zero(capsys):
    assert main([]) == 0
    assert "usage" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# detect.
# ---------------------------------------------------------------------------


def test_detect_from_writes_valid_profile(tmp_path, capsys):
    out = tmp_path / "profile.toml"
    rc = main(["detect", "--from", FIXTURES, "--out", str(out)])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    doc = tomllib.loads(text)  # must parse
    validate(doc)  # and conform to the schema
    assert doc["metadata"]["hostname"] == "pengyos-laptop"
    assert doc["cpu"]["cores"] == 8


def test_detect_from_stdout(capsys):
    rc = main(["detect", "--from", FIXTURES])
    assert rc == 0
    doc = tomllib.loads(capsys.readouterr().out)
    validate(doc)
    assert doc["cpu"]["model"].startswith("AMD Ryzen 7")


def test_detect_without_from_refuses_without_integration_flag(monkeypatch):
    # With PENGYOS_IT unset, a bare detect must refuse (opt-in) rather than
    # read the live machine.
    monkeypatch.delenv("PENGYOS_IT", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        main(["detect"])
    assert excinfo.value.code != 0
    assert "PENGYOS_IT" in str(excinfo.value)


def test_detect_from_rejects_missing_directory():
    with pytest.raises(SystemExit) as excinfo:
        main(["detect", "--from", "/nonexistent/pengyos-hwprofile-fixture"])
    assert excinfo.value.code != 0


# ---------------------------------------------------------------------------
# diff.
# ---------------------------------------------------------------------------


def _base_document() -> dict:
    return {
        "metadata": {"hostname": "host-a", "kernel": "6.8.0-pengyos", "tool_version": "0.3.0"},
        "cpu": {"vendor": "AMD", "model": "Ryzen", "cores": 8, "threads": 16, "mhz": 1800.0},
        "storage": [
            {"bus": "nvme", "model": "Kept SSD", "size_bytes": 500000000000},
        ],
        "gpu": [{"vendor": "AMD", "model": "Original", "bus": "0000:00:02.0", "driver": "amdgpu"}],
        "network": [{"interface": "enp3s0", "mac": "aa:bb:cc:dd:ee:ff", "speed_mbps": 1000}],
    }


def test_diff_identical_returns_zero(tmp_path):
    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    _write_profile(a, _base_document())
    _write_profile(b, _base_document())
    assert main(["diff", str(a), str(b)]) == 0


def test_diff_reports_added_removed_and_changed(tmp_path, capsys):
    base = _base_document()
    updated = {
        "metadata": dict(base["metadata"]),
        "cpu": dict(base["cpu"]),
        # removed the original GPU, added a new one, changed the network speed.
        "gpu": [{"vendor": "NVIDIA", "model": "New Card", "bus": "0000:10:00.0", "driver": "nvidia"}],
        "storage": base["storage"],
        "network": [{"interface": "enp3s0", "mac": "aa:bb:cc:dd:ee:ff", "speed_mbps": 2500}],
    }
    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    _write_profile(a, base)
    _write_profile(b, updated)

    rc = main(["diff", str(a), str(b)])
    assert rc == 1  # they differ
    out = capsys.readouterr().out
    # Removed the original GPU.
    assert "- gpu:" in out
    # Added the new GPU.
    assert "+ gpu:" in out
    # The network device changed (speed 1000 -> 2500).
    assert "~ network:" in out
    # The unchanged storage device is not mentioned.
    assert "storage" not in out


def test_diff_missing_file_errors(tmp_path):
    a = tmp_path / "a.toml"
    _write_profile(a, _base_document())
    with pytest.raises(SystemExit) as excinfo:
        main(["diff", str(a), str(tmp_path / "nope.toml")])
    assert excinfo.value.code != 0


def test_diff_invalid_toml_errors(tmp_path):
    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    a.write_text("this is = = not toml\n", encoding="utf-8")
    _write_profile(b, _base_document())
    with pytest.raises(SystemExit) as excinfo:
        main(["diff", str(a), str(b)])
    assert excinfo.value.code != 0


def test_diff_invalid_schema_errors(tmp_path):
    # ``b`` is valid TOML but violates the schema (no ``cpu`` section), so
    # ``diff`` must refuse it with a non-zero exit.
    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    _write_profile(a, _base_document())
    b.write_text(
        '[metadata]\n'
        'hostname = "h"\n'
        'kernel = "k"\n'
        'tool_version = "0.3.0"\n',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as excinfo:
        main(["diff", str(a), str(b)])
    assert excinfo.value.code != 0
