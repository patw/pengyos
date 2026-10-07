"""Tests for the command line interface."""

from __future__ import annotations

import json

import pytest

from pengyos_propose import __version__
from pengyos_propose.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_render_writes_markdown(sample_proposal_path, capsys):
    rc = main(["render", sample_proposal_path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Bump systemd in the base image" in out
    assert "Tier 1 (agent-mutable)" in out


def test_render_html_unsupported(sample_proposal_path, capsys):
    rc = main(["render", sample_proposal_path, "--format", "html"])
    assert rc == 1
    assert "HTML rendering is not implemented" in capsys.readouterr().err


def test_submit_dry_run_prints_body(sample_proposal_path, capsys):
    rc = main(["submit", "--proposal", sample_proposal_path, "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    body = json.loads(out)
    assert body["title"] == "Bump systemd in the base image"
    assert body["from"] == "systemd 256"
    assert body["human_approved"] is False


def test_submit_dry_run_forwards_human_flag(sample_proposal_path, capsys):
    rc = main(["submit", "--proposal", sample_proposal_path, "--dry-run", "--human-approved"])
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert body["human_approved"] is True


def test_submit_requires_base_url_or_env(sample_proposal_path, monkeypatch):
    monkeypatch.delenv("PENGYOS_GATE_URL", raising=False)
    # Without --base-url and without env, submit defaults to localhost and tries
    # the (mockable) transport.  Point the real transport at a fake so no socket
    # is opened.
    import pengyos_propose.gate as gate_mod

    def fake(url, payload):
        return 200, {"ok": True, "proposal_id": "p1"}

    monkeypatch.setattr(gate_mod, "_http_post", fake)
    rc = main(["submit", "--proposal", sample_proposal_path])
    assert rc == 0


def test_submit_reports_failure(sample_proposal_path, monkeypatch):
    import pengyos_propose.gate as gate_mod

    def refuse(url, payload):
        return 500, {"ok": False, "error": "boom"}

    monkeypatch.setattr(gate_mod, "_http_post", refuse)
    rc = main(["submit", "--proposal", sample_proposal_path])
    assert rc == 1


def test_submit_missing_file_exits_nonzero(capsys):
    rc = main(["submit", "--proposal", "/nonexistent/nope.json", "--dry-run"])
    assert rc == 1  # SystemExit(msg) -> exit code 1 with the message on stderr
    assert "no such proposal file" in capsys.readouterr().err


def test_submit_tier2_without_human_flag_is_refused(
    tier2_proposal_path, capsys, monkeypatch
):
    """A Tier 2 without --human-approved is refused (exit 1) before any send."""
    import pengyos_propose.gate as gate_mod

    def boom(url, payload):  # pragma: no cover - must not be called
        raise AssertionError("transport must not run when the gate refuses")

    monkeypatch.setattr(gate_mod, "_http_post", boom)
    rc = main(["submit", "--proposal", tier2_proposal_path])
    assert rc == 1
    assert "human approval" in capsys.readouterr().err


def test_submit_tier2_with_human_flag_succeeds(
    tier2_proposal_path, capsys, monkeypatch
):
    """A Tier 2 with --human-approved is forwarded to the gate."""
    import pengyos_propose.gate as gate_mod

    def ok(url, payload):
        return 200, {"ok": True, "proposal_id": "p7"}

    monkeypatch.setattr(gate_mod, "_http_post", ok)
    rc = main(["submit", "--proposal", tier2_proposal_path, "--human-approved"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "proposal created: p7" in out
    assert "submitted to gate" in out


def test_submit_tier2_dry_run_still_works(tier2_proposal_path, capsys):
    """Dry-run previews the body without enforcing the gate."""
    rc = main(["submit", "--proposal", tier2_proposal_path, "--dry-run"])
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert body["tier"] == "tier2"
    assert body["human_approved"] is False


def test_render_missing_required_fields_is_refused(
    write_proposal, capsys
):
    """`render` refuses a proposal with no human-usable field before any output."""
    path = write_proposal("empty.json", {})
    rc = main(["render", path])
    assert rc == 1
    assert "human-usable" in capsys.readouterr().err


def test_submit_missing_required_fields_is_refused(
    write_proposal, capsys, monkeypatch
):
    """`submit` refuses a proposal with no human-usable field before any send."""
    import pengyos_propose.gate as gate_mod

    def boom(url, payload):  # pragma: no cover - must not be called
        raise AssertionError("transport must not run when fields are missing")

    monkeypatch.setattr(gate_mod, "_http_post", boom)
    path = write_proposal("empty.json", {})
    rc = main(["submit", "--proposal", path])
    assert rc == 1
    assert "human-usable" in capsys.readouterr().err


def test_submit_missing_required_fields_dry_run_is_refused(
    write_proposal, capsys
):
    """Even --dry-run refuses a proposal with no human-usable field (load gate)."""
    path = write_proposal("empty.json", {})
    rc = main(["submit", "--proposal", path, "--dry-run"])
    assert rc == 1
    assert "human-usable" in capsys.readouterr().err
