"""Tests for the alert notification sinks: ntfy + email (US-SRV-3).

The sink layer renders a fired :class:`Alert` (from
:mod:`pengyos_monitoring.evaluator`) into the notification a sink *would*
deliver — a dry run. Nothing here opens a socket, posts to a URL, or opens an
SMTP connection, so the suite is fully offline.
"""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

import pytest

from pengyos_monitoring.alerts import (
    EmailConfig,
    NtfyConfig,
    RenderedAlert,
    SinkConfigError,
    dry_run,
    load_config,
    render_alert,
    render_email,
    render_ntfy,
)
from pengyos_monitoring.evaluator import Alert
from pengyos_monitoring.rules import load_rules
from pengyos_monitoring.series import parse_series

REPO_ROOT = Path(__file__).resolve().parents[1]
ALERTS_DIR = REPO_ROOT / "alerts"
FIXTURES = REPO_ROOT / "fixtures" / "series"
RULES_FILE = REPO_ROOT / "rules" / "pengyos.toml"


def _alert(
    *,
    rule_name: str = "systemd-unit-failed",
    severity: str = "critical",
    summary: str = "A unit failed.",
    expr: str = "node_systemd_units_failed > 0",
    series_name: str = 'node_systemd_units_failed{instance="n"}',
    value: float = 1.0,
    timestamp: float = 300.0,
    for_seconds: int = 300,
) -> Alert:
    return Alert(
        rule_name=rule_name,
        severity=severity,
        summary=summary,
        expr=expr,
        series_name=series_name,
        value=value,
        timestamp=timestamp,
        for_seconds=for_seconds,
    )


# --------------------------------------------------------------------------- #
# load_config — ntfy
# --------------------------------------------------------------------------- #
def test_load_ntfy_config_from_committed_file() -> None:
    cfg = load_config(ALERTS_DIR / "ntfy.conf")
    assert isinstance(cfg, NtfyConfig)
    assert cfg.kind == "ntfy"
    assert cfg.url == "https://ntfy.sh"
    assert cfg.topic == "pengyos-alerts"
    assert cfg.default_priority == "default"
    assert cfg.tags == ("warning", "alert")
    assert cfg.auth_token == ""  # blank -> no auth header
    assert cfg.path == (ALERTS_DIR / "ntfy.conf")


def test_load_ntfy_reads_auth_token_and_priority() -> None:
    text = (
        "[ntfy]\n"
        "url = https://ntfy.example.com\n"
        "topic = ops\n"
        "default_priority = high\n"
        "auth_token = s3cret\n"
        "tags = alert,fire\n"
    )
    cfg = load_config_from_text(text)
    assert isinstance(cfg, NtfyConfig)
    assert cfg.url == "https://ntfy.example.com"
    assert cfg.default_priority == "high"
    assert cfg.auth_token == "s3cret"
    assert cfg.tags == ("alert", "fire")


# --------------------------------------------------------------------------- #
# load_config — email
# --------------------------------------------------------------------------- #
def test_load_email_config_from_committed_file() -> None:
    cfg = load_config(ALERTS_DIR / "email.conf")
    assert isinstance(cfg, EmailConfig)
    assert cfg.kind == "email"
    assert cfg.smtp_host == "mail.pengyos.example"
    assert cfg.smtp_port == 587
    assert cfg.from_addr == "alerts@pengyos.example"
    assert cfg.to_addrs == ("oncall@pengyos.example", "ops@pengyos.example")
    assert cfg.subject_prefix == "[pengyos-alerts]"
    assert cfg.username == "" and cfg.password == ""


# --------------------------------------------------------------------------- #
# load_config — errors
# --------------------------------------------------------------------------- #
def test_load_config_missing_table_raises() -> None:
    with pytest.raises(SinkConfigError, match=r"\[ntfy\] or \[email\]"):
        load_config_from_text("[other]\nurl = x\n")


def test_load_ntfy_missing_required_setting_raises() -> None:
    with pytest.raises(SinkConfigError, match="topic"):
        load_config_from_text("[ntfy]\nurl = https://ntfy.sh\n")


def test_load_ntfy_invalid_priority_raises() -> None:
    with pytest.raises(SinkConfigError, match="default_priority"):
        load_config_from_text(
            "[ntfy]\nurl = https://ntfy.sh\ntopic = ops\n"
            "default_priority = nonsense\n"
        )


def test_load_email_invalid_port_raises() -> None:
    with pytest.raises(SinkConfigError, match="smtp_port"):
        load_config_from_text(
            "[email]\nsmtp_host = h\nsmtp_port = notanumber\n"
            'from = a@b.c\nto = x@y.z\n'
        )


def test_load_email_empty_to_raises() -> None:
    with pytest.raises(SinkConfigError, match="'to'"):
        load_config_from_text(
            "[email]\nsmtp_host = h\nsmtp_port = 587\n"
            'from = a@b.c\nto =   \n'
        )


def test_load_config_duplicate_key_raises() -> None:
    # strict=True: a repeated key in a section is a malformed config.
    with pytest.raises(SinkConfigError):
        load_config_from_text(
            "[ntfy]\nurl = a\nurl = b\ntopic = ops\ndefault_priority = default\n"
        )


# --------------------------------------------------------------------------- #
# render_ntfy
# --------------------------------------------------------------------------- #
def test_render_ntfy_payload_shape() -> None:
    cfg = load_config(ALERTS_DIR / "ntfy.conf")
    payload = render_ntfy(_alert(), cfg)
    assert payload["method"] == "POST"
    assert payload["url"] == "https://ntfy.sh/pengyos-alerts"
    assert payload["topic"] == "pengyos-alerts"
    assert payload["priority"] == "urgent"  # critical -> urgent
    assert payload["tags"] == ["warning", "alert"]
    assert payload["headers"]["X-Topic"] == "pengyos-alerts"
    assert payload["headers"]["X-Priority"] == "urgent"
    # ntfy accepts the title/body as headers.
    assert payload["headers"]["Title"].startswith("[CRITICAL]")
    assert payload["title"].endswith("systemd-unit-failed")


def test_render_ntfy_no_auth_header_when_token_blank() -> None:
    cfg = load_config(ALERTS_DIR / "ntfy.conf")
    assert "Authorization" not in render_ntfy(_alert(), cfg)["headers"]


def test_render_ntfy_auth_header_when_token_set() -> None:
    cfg = load_config_from_text(
        "[ntfy]\nurl = https://ntfy.sh\ntopic = ops\n"
        "default_priority = default\nauth_token = s3cret\n"
    )
    headers = render_ntfy(_alert(), cfg)["headers"]
    assert headers["Authorization"] == "Bearer s3cret"


@pytest.mark.parametrize(
    "severity,expected",
    [("critical", "urgent"), ("warning", "high"), ("info", "default")],
)
def test_render_ntfy_priority_maps_severity(severity: str, expected: str) -> None:
    cfg = load_config_from_text(
        "[ntfy]\nurl = https://ntfy.sh\ntopic = ops\n"
        "default_priority = low\n"
    )
    payload = render_ntfy(_alert(severity=severity), cfg)
    assert payload["priority"] == expected


def test_render_ntfy_empty_tags_omits_tag_header() -> None:
    cfg = load_config_from_text(
        "[ntfy]\nurl = https://ntfy.sh\ntopic = ops\n"
        "default_priority = default\n"
    )
    headers = render_ntfy(_alert(), cfg)["headers"]
    assert "X-Tag" not in headers


def test_render_ntfy_trailing_slash_url_is_normalised() -> None:
    cfg = load_config_from_text(
        "[ntfy]\nurl = https://ntfy.sh/\ntopic = ops\n"
        "default_priority = default\n"
    )
    payload = render_ntfy(_alert(), cfg)
    assert payload["url"] == "https://ntfy.sh/ops"


def test_render_ntfy_rejects_non_ntfy_config() -> None:
    with pytest.raises(TypeError):
        render_ntfy(_alert(), load_config(ALERTS_DIR / "email.conf"))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# render_email
# --------------------------------------------------------------------------- #
def test_render_email_headers_and_subject() -> None:
    cfg = load_config(ALERTS_DIR / "email.conf")
    msg = render_email(_alert(), cfg)
    assert isinstance(msg, EmailMessage)
    assert msg["From"] == "alerts@pengyos.example"
    assert msg["To"] == "oncall@pengyos.example, ops@pengyos.example"
    assert msg["Subject"] == "[pengyos-alerts][CRITICAL] systemd-unit-failed: A unit failed."


def test_render_email_subject_without_prefix() -> None:
    cfg = load_config_from_text(
        "[email]\nsmtp_host = h\nsmtp_port = 587\n"
        'from = a@b.c\nto = x@y.z\n'
    )
    msg = render_email(_alert(), cfg)
    assert msg["Subject"] == "[CRITICAL] systemd-unit-failed: A unit failed."


def test_render_email_body_contains_details() -> None:
    cfg = load_config(ALERTS_DIR / "email.conf")
    body = msg_body(render_email(_alert(), cfg))
    assert "A unit failed." in body
    assert "Expression: node_systemd_units_failed > 0" in body
    assert "sustained for 300s" in body


def test_render_email_rejects_non_email_config() -> None:
    with pytest.raises(TypeError):
        render_email(_alert(), load_config(ALERTS_DIR / "ntfy.conf"))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# render_alert dispatch + dry_run
# --------------------------------------------------------------------------- #
def test_render_alert_dispatches_by_kind() -> None:
    for path, kind in [
        (ALERTS_DIR / "ntfy.conf", "ntfy"),
        (ALERTS_DIR / "email.conf", "email"),
    ]:
        rendered = render_alert(_alert(), load_config(path))
        assert isinstance(rendered, RenderedAlert)
        assert rendered.kind == kind


def test_dry_run_committed_configs_never_raise() -> None:
    for name in ("ntfy.conf", "email.conf"):
        rendered = dry_run(_alert(), ALERTS_DIR / name)
        assert rendered.kind in ("ntfy", "email")


def test_dry_run_end_to_end_over_a_real_alert() -> None:
    """A real fired alert (from the evaluator) renders into both sinks."""
    rules = load_rules(RULES_FILE)
    series = parse_series((FIXTURES / "failure-unit-failed.prom").read_text())
    fired = [a for a in (evaluate_rule(r, series) for r in rules) if a]
    assert len(fired) == 1
    alert = fired[0]
    assert alert.rule_name == "systemd-unit-failed"

    ntfy = dry_run(alert, ALERTS_DIR / "ntfy.conf")
    assert ntfy.kind == "ntfy"
    assert "systemd-unit-failed" in ntfy.payload["title"]
    assert ntfy.payload["priority"] == "urgent"

    email = dry_run(alert, ALERTS_DIR / "email.conf")
    assert email.kind == "email"
    assert "systemd-unit-failed" in email.payload["Subject"]


# --------------------------------------------------------------------------- #
# Committed config files are loadable (part of the deliverable).
# --------------------------------------------------------------------------- #
def test_committed_alert_configs_are_loadable() -> None:
    assert load_config(ALERTS_DIR / "ntfy.conf").kind == "ntfy"
    assert load_config(ALERTS_DIR / "email.conf").kind == "email"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def load_config_from_text(text: str) -> "NtfyConfig | EmailConfig":
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".conf", delete=False
    ) as fh:
        fh.write(text)
        tmp = Path(fh.name)
    try:
        return load_config(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def evaluate_rule(rule, series):
    from pengyos_monitoring.evaluator import evaluate

    return evaluate(rule, series)


def msg_body(msg: EmailMessage) -> str:
    return msg.get_content()
