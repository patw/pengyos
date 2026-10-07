"""Alert notification sinks: ntfy + email (US-SRV-3).

The evaluator (:mod:`pengyos_monitoring.evaluator`) decides *whether* a rule
fires and produces an :class:`Alert`. This module renders that alert into the
notification a sink *would* deliver — a dry run. Nothing here opens a socket,
posts to a URL, or opens an SMTP connection; rendering is pure and offline so
it can be proven by fixtures.

Two sinks are supported:

* **ntfy** — a simple HTTP push service. An alert is a POST to a topic URL; the
  rendered payload carries the topic, title, message, priority and tags.
* **email** — an SMTP delivery. The rendered payload is a stdlib
  :class:`email.message.EmailMessage` with ``From``/``To``/``Subject``/body.

Sink configuration lives in ``alerts/*.conf`` (INI-style ``key = value`` tables,
``[ntfy]`` or ``[email]``). See :func:`load_config`.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from pengyos_monitoring.evaluator import Alert

#: ntfy message priority a given severity maps to. ntfy's scale is
#: ``min < low < default < high < urgent``.
_SEVERITY_TO_PRIORITY: dict[str, str] = {
    "info": "default",
    "warning": "high",
    "critical": "urgent",
}

#: ntfy priorities the renderer will accept (defensive; config is validated).
_VALID_PRIORITIES: frozenset[str] = frozenset(
    {"min", "low", "default", "high", "urgent"}
)


class SinkConfigError(ValueError):
    """Raised when an alert-sink config is missing, malformed, or incomplete."""


class SinkConfig:
    """Base alert-sink configuration loaded from an ``alerts/*.conf`` file.

    A plain (non-dataclass) base so the concrete sinks can each declare their
    own dataclass fields with required settings first and optional ones after,
    without a dataclass-inheritance field-order clash.
    """

    kind: str = ""
    path: Path | None = None
    raw: dict[str, str]

    def __init__(self, kind: str = "", path: Path | None = None,
                 raw: dict[str, str] | None = None) -> None:
        self.kind = kind
        self.path = path
        self.raw = raw if raw is not None else {}

    def get(self, key: str, default: str | None = None) -> str | None:
        """Return a raw config value (keys are case-insensitive)."""
        return self.raw.get(key.lower(), default)


@dataclass
class NtfyConfig(SinkConfig):
    """Configuration for the ntfy push-notification sink."""

    url: str
    topic: str
    default_priority: str
    auth_token: str = ""
    tags: tuple[str, ...] = tuple()
    path: Path | None = None
    raw: dict[str, str] = field(default_factory=dict)
    kind: str = "ntfy"


@dataclass
class EmailConfig(SinkConfig):
    """Configuration for the SMTP email sink."""

    smtp_host: str
    smtp_port: int
    from_addr: str
    to_addrs: tuple[str, ...]
    username: str = ""
    password: str = ""
    subject_prefix: str = ""
    path: Path | None = None
    raw: dict[str, str] = field(default_factory=dict)
    kind: str = "email"


def _parse_list(value: str) -> tuple[str, ...]:
    """Split a comma-separated config value into a trimmed, non-empty tuple."""
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _require(raw: dict[str, str], key: str, kind: str, source: str) -> str:
    value = raw.get(key.lower())
    if value is None or not value.strip():
        raise SinkConfigError(
            f"missing required {kind} sink setting {key!r} in {source}"
        )
    return value.strip()


def _parse_section(text: str, source: str, section: str) -> dict[str, str]:
    """Parse the ``[section]`` table of an INI-style file into a dict.

    Only that section's ``key = value`` pairs are returned (the merged
    ``DEFAULT`` section is excluded). Raises :class:`SinkConfigError` on a
    malformed line or a missing table.
    """
    parser = configparser.ConfigParser(
        interpolation=None, strict=True, allow_no_value=True
    )
    # Preserve key case (config keys are lowercase anyway; keeps intent clear).
    parser.optionxform = str  # type: ignore[assignment]
    try:
        parser.read_string(text, source)
    except configparser.Error as exc:
        raise SinkConfigError(f"invalid config in {source}: {exc}") from exc
    if section not in parser.sections():
        raise SinkConfigError(
            f"{source}: expected a [{section}] table"
        )
    return {key: value for key, value in parser.items(section)}


def load_config(path: Path | str) -> SinkConfig:
    """Load and validate an alert-sink config from ``alerts/*.conf``.

    The sink *kind* is detected from the table header: ``[ntfy]`` yields an
    :class:`NtfyConfig`, ``[email]`` yields an :class:`EmailConfig`. Raises
    :class:`SinkConfigError` if the file has no recognised table or is missing a
    required setting.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")

    parser = configparser.ConfigParser(
        interpolation=None, strict=True, allow_no_value=True
    )
    parser.optionxform = str  # type: ignore[assignment]
    try:
        parser.read_string(text, path.name)
    except configparser.Error as exc:
        raise SinkConfigError(f"invalid config in {path.name}: {exc}") from exc

    if "ntfy" in parser.sections():
        raw = _parse_section(text, path.name, "ntfy")
        url = _require(raw, "url", "ntfy", path.name)
        topic = _require(raw, "topic", "ntfy", path.name)
        default_priority = _require(raw, "default_priority", "ntfy", path.name)
        if default_priority not in _VALID_PRIORITIES:
            raise SinkConfigError(
                f"{path.name}: default_priority {default_priority!r} is not one "
                f"of {sorted(_VALID_PRIORITIES)}"
            )
        return NtfyConfig(
            url=url,
            topic=topic,
            default_priority=default_priority,
            path=path,
            raw=raw,
            auth_token=(raw.get("auth_token") or "").strip(),
            tags=_parse_list(raw.get("tags", "") or ""),
        )

    if "email" in parser.sections():
        raw = _parse_section(text, path.name, "email")
        smtp_host = _require(raw, "smtp_host", "email", path.name)
        port_raw = _require(raw, "smtp_port", "email", path.name)
        try:
            smtp_port = int(port_raw)
        except ValueError as exc:
            raise SinkConfigError(
                f"{path.name}: smtp_port {port_raw!r} is not an integer"
            ) from exc
        from_addr = _require(raw, "from", "email", path.name)
        to_addrs = _parse_list(_require(raw, "to", "email", path.name))
        if not to_addrs:
            raise SinkConfigError(f"{path.name}: 'to' must list at least one address")
        return EmailConfig(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            from_addr=from_addr,
            to_addrs=to_addrs,
            path=path,
            raw=raw,
            username=(raw.get("username") or "").strip(),
            password=(raw.get("password") or "").strip(),
            subject_prefix=(raw.get("subject_prefix") or "").strip(),
        )

    raise SinkConfigError(
        f"{path.name}: expected an [ntfy] or [email] table"
    )


def render_subject_prefix(alert: Alert, cfg: EmailConfig) -> str:
    """The rendered email subject, with the configured prefix prepended."""
    base = f"[{alert.severity.upper()}] {alert.rule_name}: {alert.summary}"
    return f"{cfg.subject_prefix}{base}" if cfg.subject_prefix else base


def render_email(alert: Alert, cfg: EmailConfig) -> EmailMessage:
    """Render ``alert`` as an :class:`EmailMessage` (dry run; no SMTP)."""
    if cfg.kind != "email":
        raise TypeError(f"render_email expects EmailConfig, got {cfg.kind!r}")

    msg = EmailMessage()
    msg["From"] = cfg.from_addr
    msg["To"] = ", ".join(cfg.to_addrs)
    msg["Subject"] = render_subject_prefix(alert, cfg)
    msg.set_content(_render_email_body(alert))
    return msg


def _render_email_body(alert: Alert) -> str:
    return (
        f"{alert.summary}\n"
        f"Rule:      {alert.rule_name}\n"
        f"Severity:  {alert.severity}\n"
        f"Expression: {alert.expr}\n"
        f"Series:    {alert.series_name}\n"
        f"Value:     {alert.value:g}\n"
        f" sustained for {alert.for_seconds}s\n"
        f"Evaluated: {alert.timestamp:g}s (unix epoch)\n"
    )


def _effective_priority(alert: Alert, cfg: NtfyConfig) -> str:
    return _SEVERITY_TO_PRIORITY.get(alert.severity, cfg.default_priority)


def render_ntfy(alert: Alert, cfg: NtfyConfig) -> dict[str, Any]:
    """Render ``alert`` as the ntfy POST payload (dry run; no network).

    Returns a plain dict describing the request that *would* be POSTed: the
    topic URL, title, message body, effective priority, tags and the HTTP
    headers that would be sent.
    """
    if cfg.kind != "ntfy":
        raise TypeError(f"render_ntfy expects NtfyConfig, got {cfg.kind!r}")

    priority = _effective_priority(alert, cfg)
    title = f"[{alert.severity.upper()}] {alert.rule_name}"
    message = (
        f"{alert.summary}\n"
        f"Series: {alert.series_name}\n"
        f"Value:  {alert.value:g} (>= {alert.for_seconds}s sustained)"
    )

    payload: dict[str, Any] = {
        "method": "POST",
        "url": f"{cfg.url.rstrip('/')}/{cfg.topic}",
        "topic": cfg.topic,
        "title": title,
        "message": message,
        "priority": priority,
        "tags": list(cfg.tags),
        "headers": {
            "X-Topic": cfg.topic,
            "X-Priority": priority,
            "Content-Type": "text/plain; charset=UTF-8",
        },
    }
    if cfg.tags:
        payload["headers"]["X-Tag"] = ",".join(cfg.tags)
    if cfg.auth_token:
        payload["headers"]["Authorization"] = f"Bearer {cfg.auth_token}"
    # ntfy accepts the title/body as headers or as the POST body.
    payload["headers"]["Title"] = title
    payload["headers"]["Body"] = message
    return payload


@dataclass
class RenderedAlert:
    """A rendered alert: the sink kind plus its rendered payload."""

    kind: str
    payload: Any


def render_alert(alert: Alert, cfg: SinkConfig) -> RenderedAlert:
    """Dispatch ``alert`` to the sink described by ``cfg`` and render it.

    Returns a :class:`RenderedAlert`. For ``ntfy`` the payload is a dict; for
    ``email`` it is an :class:`email.message.EmailMessage`.
    """
    if cfg.kind == "ntfy":
        assert isinstance(cfg, NtfyConfig)
        return RenderedAlert("ntfy", render_ntfy(alert, cfg))
    if cfg.kind == "email":
        assert isinstance(cfg, EmailConfig)
        return RenderedAlert("email", render_email(alert, cfg))
    raise SinkConfigError(f"unknown sink kind {cfg.kind!r}")


def dry_run(alert: Alert, path: Path | str) -> RenderedAlert:
    """Load the sink at ``path`` and render ``alert`` against it (dry run)."""
    cfg = load_config(path)
    return render_alert(alert, cfg)
