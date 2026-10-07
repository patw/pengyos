"""Service-unit and default-config tests.

Validates the two files authored in this item:

* ``pengy-web.service`` — a hardened systemd unit with the required
  ``[Unit]``/``[Service]``/``[Install]`` sections, runs unprivileged
  (``DynamicUser=yes``, never as root), never gains privileges
  (``NoNewPrivileges=yes``), and restarts on failure.
* ``settings.json`` — the default ``/etc/pengy/settings.json`` points the agent
  at an OpenAI-compatible endpoint (``http://127.0.0.1:11434/v1``) with **no API
  key baked in** and **no default model**.

These read the committed files only (no build, no network, no root).
"""

from __future__ import annotations

import json
from pathlib import Path

from tests import recipe_check

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "package" / "pengy"
SERVICE_FILE = PACKAGE_DIR / "pengy-web.service"
SETTINGS_FILE = PACKAGE_DIR / "settings.json"
MK_FILE = PACKAGE_DIR / "pengy.mk"

# The OpenAI-compatible endpoint the agent is shipped pointing at by default
# (a local Ollama instance). No API key, no model — the caller supplies those.
DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"

# Option names the spec hardens on.
_REQUIRED_SECTIONS = ("Unit", "Service", "Install")
_FORBIDDEN_ROOT_MARKERS = ("user=root", "group=root")


def _parse_unit(path: Path) -> dict[str, list[tuple[str, str]]]:
    """Parse a systemd unit file into ``{section: [(key, value), ...]}``.

    Handles ``[Section]`` headers, full-line ``#``/``;`` comments, and inline
    ``#`` comments (systemd strips everything after a ``#`` on an option line).
    Option names keep their original case (systemd option names are
    case-sensitive).
    """
    sections: dict[str, list[tuple[str, str]]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if "#" in line:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        if "=" in line and current is not None:
            key, _, value = line.partition("=")
            sections[current].append((key.strip(), value.strip()))
    return sections


def _option(sections: dict[str, list[tuple[str, str]]], section: str, key: str) -> str | None:
    for found_key, found_value in sections.get(section, []):
        if found_key == key:
            return found_value
    return None


# --- systemd unit -----------------------------------------------------------


def test_service_file_exists_and_nonempty():
    assert SERVICE_FILE.is_file(), "pengy-web.service must exist"
    assert SERVICE_FILE.read_text(encoding="utf-8").strip(), "pengy-web.service is empty"


def test_service_has_required_sections():
    sections = _parse_unit(SERVICE_FILE)
    for name in _REQUIRED_SECTIONS:
        assert name in sections, f"pengy-web.service missing [{name}] section"


def test_service_has_exec_start_for_pengy_web():
    exec_start = _option(_parse_unit(SERVICE_FILE), "Service", "ExecStart")
    assert exec_start, "pengy-web.service must define ExecStart"
    assert "pengy-web" in exec_start, f"ExecStart does not launch pengy-web: {exec_start!r}"
    assert exec_start.startswith("/usr/bin/pengy-web"), (
        f"ExecStart should use the installed absolute path: {exec_start!r}"
    )


def test_service_runs_unprivileged():
    """DynamicUser=yes means the agent runs as an unprivileged user (never root)."""
    assert _option(_parse_unit(SERVICE_FILE), "Service", "DynamicUser") == "yes"


def test_service_no_new_privileges():
    assert _option(_parse_unit(SERVICE_FILE), "Service", "NoNewPrivileges") == "yes"


def test_service_restart_on_failure():
    assert _option(_parse_unit(SERVICE_FILE), "Service", "Restart") == "on-failure"


def test_service_has_filesystem_and_kernel_hardening():
    sections = _parse_unit(SERVICE_FILE)
    assert _option(sections, "Service", "ProtectSystem") in ("yes", "full", "strict"), (
        "ProtectSystem should be enabled (strict preferred) for a hardened service"
    )
    assert _option(sections, "Service", "ProtectHome") == "yes"
    assert _option(sections, "Service", "PrivateTmp") == "yes"
    assert _option(sections, "Service", "ProtectControlGroups") == "yes"


def test_service_does_not_pin_root_user():
    """No ``User=root`` / ``Group=root`` anywhere in the unit (Core runs it unprivileged)."""
    text = SERVICE_FILE.read_text(encoding="utf-8").lower()
    for marker in _FORBIDDEN_ROOT_MARKERS:
        assert marker not in text, f"hardened unit must not pin {marker.split('=')[0]}=root"


def test_service_wants_multi_user_target():
    """A normally-enabled service is WantedBy multi-user.target."""
    assert _option(_parse_unit(SERVICE_FILE), "Install", "WantedBy") == "multi-user.target"


# --- default config ---------------------------------------------------------


def test_settings_file_exists_and_nonempty():
    assert SETTINGS_FILE.is_file(), "settings.json must exist"
    assert SETTINGS_FILE.read_text(encoding="utf-8").strip(), "settings.json is empty"


def test_settings_is_valid_json_object():
    data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "settings.json must be a JSON object"


def test_settings_base_url_is_openai_compatible():
    data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    assert data.get("base_url") == DEFAULT_BASE_URL, (
        f"default base_url {data.get('base_url')!r} is not the OpenAI-compatible "
        f"endpoint {DEFAULT_BASE_URL!r}"
    )


def test_settings_has_no_api_key_baked_in():
    data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    for key in ("api_key", "apiKey", "apikey", "token", "access_token"):
        value = data.get(key)
        assert not value, f"{key} must not be baked into the default settings.json"


def test_settings_has_no_default_model():
    data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    assert "model" not in data, "settings.json must not bake in a default model"


# --- recipe wiring (install destinations) -----------------------------------


def test_recipe_installs_the_two_bins():
    dests = recipe_check.installed_init_files(MK_FILE.read_text(encoding="utf-8"))
    for bin_name in ("pengy-cli", "pengy-web"):
        assert f"usr/bin/{bin_name}" in dests, (
            f"recipe does not install {bin_name} to $(TARGET_DIR)/usr/bin"
        )


def test_recipe_installs_default_config():
    dests = recipe_check.installed_init_files(MK_FILE.read_text(encoding="utf-8"))
    assert "etc/pengy/settings.json" in dests, (
        "recipe does not install settings.json to $(TARGET_DIR)/etc/pengy/"
    )


def test_recipe_installs_systemd_unit():
    units = recipe_check.installed_systemd_units(MK_FILE.read_text(encoding="utf-8"))
    assert "systemd/system/pengy-web.service" in units, (
        f"recipe does not install the systemd unit; got {units}"
    )
