"""End-to-end CLI tests for item 7 (``run`` + ``explain`` through ``main``).

These exercise the actual command-line entry point the way a user invokes it, against the committed fixtures — no network, no root.
"""

import json
from pathlib import Path

from pengyos_update_triage.cli import main

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PINS = str(FIXTURES / "pins" / "pins_ok.toml")
ADVISORIES = str(FIXTURES / "advisories")
CHANGELOGS = str(FIXTURES / "changelogs")


def _run(argv, capsys, out):
    rc = main(
        [
            "run",
            "--pins",
            PINS,
            "--advisories",
            ADVISORIES,
            "--changelogs",
            CHANGELOGS,
            "--out",
            out,
            *argv,
        ]
    )
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_run_end_to_end(capsys, tmp_path):
    out = str(tmp_path / "proposals.json")
    rc, out_text, err = _run([], capsys, out)
    assert rc == 0
    assert "13 proposal(s)" in out_text
    assert "wrote 13 proposal(s)" in err
    doc = json.loads(Path(out).read_text(encoding="utf-8"))
    assert doc["schema"].startswith("pengyos.proposals/")
    assert doc["counts"]["proposals"] == 13
    assert doc["proposals"][0]["id"] == "advisory:CVE-2024-2392:curl"


def test_run_min_severity_flag(capsys, tmp_path):
    out = str(tmp_path / "proposals.json")
    rc, _out_text, _err = _run(["--min-severity", "high"], capsys, out)
    assert rc == 0
    doc = json.loads(Path(out).read_text(encoding="utf-8"))
    sec = [p for p in doc["proposals"] if p["category"] == "security"]
    # Rank order: critical curl first; in the high band openssl (explo 3.9)
    # precedes glibc (explo 1.9).
    assert [p["id"] for p in sec] == [
        "advisory:CVE-2024-2392:curl",
        "advisory:CVE-2024-4741:openssl",
        "advisory:CVE-2023-4911:glibc",
    ]


def test_explain_end_to_end(capsys, tmp_path):
    out = str(tmp_path / "proposals.json")
    rc, _o, _e = _run([], capsys, out)
    assert rc == 0

    rc = main(["explain", "advisory:CVE-2024-2392:curl", "--proposals", out])
    text = capsys.readouterr().out
    assert rc == 0
    assert text.startswith("Proposal: advisory:CVE-2024-2392:curl")
    assert "FIRED" in text
    assert "advisory-affected" in text

    # Unknown id → clean non-zero.
    rc = main(["explain", "bogus", "--proposals", out])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no proposal" in err


def test_run_invalid_min_severity_is_usage_error(capsys, tmp_path):
    out = str(tmp_path / "proposals.json")
    # argparse rejects an unknown severity choice with a usage error (exit 2).
    try:
        main(
            [
                "run",
                "--pins",
                PINS,
                "--advisories",
                ADVISORIES,
                "--changelogs",
                CHANGELOGS,
                "--out",
                out,
                "--min-severity",
                "apocalyptic",
            ]
        )
        raised = False
    except SystemExit as exc:
        raised = True
        code = exc.code
    assert raised
    assert code == 2
