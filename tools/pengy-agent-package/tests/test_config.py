"""Default-config tests.

Validates the config this package ships, ``package/pengy/settings.json``: the
default ``/etc/pengy/settings.json`` points the agent at an OpenAI-compatible
endpoint (``http://127.0.0.1:11434/v1``) with **no API key baked in** and **no
default model**, and the recipe installs it and nothing else beyond the two
binaries.

It also asserts the recipe ships **no systemd unit**. It used to ship
``pengy-web.service``, written for PengyR: ``--config /etc/pengy/settings.json``
(the binary takes ``--config-dir``), ``DynamicUser=yes`` and ``ProtectHome=yes``
— which hide ``~/.config/pengy``, ``~/skills`` and ``~/.secrets``, all the
agent's state — run as root at ``multi-user.target``. Serving the UI has no
authentication, so it is a per-user, opt-in act: the unit is the system
template ``board/server/rootfs-overlay/etc/systemd/system/pengy-web@.service``,
enabled per user by ``pengyos-webui on`` (see ``tools/pengyos-webui``).

These read the committed files only (no build, no network, no root).
"""

from __future__ import annotations

import json
from pathlib import Path

from tests import recipe_check

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "package" / "pengy"
SETTINGS_FILE = PACKAGE_DIR / "settings.json"
MK_FILE = PACKAGE_DIR / "pengy.mk"

# The OpenAI-compatible endpoint the agent is shipped pointing at by default
# (a local Ollama instance). No API key, no model — the caller supplies those.
DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"


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


def test_recipe_installs_no_systemd_unit():
    units = recipe_check.installed_systemd_units(MK_FILE.read_text(encoding="utf-8"))
    assert units == [], f"the recipe installs systemd units: {units}"
    assert not (PACKAGE_DIR / "pengy-web.service").exists(), (
        "the package directory must not carry a pengy-web unit; the UI is enabled "
        "per user by pengyos-webui from the server overlay's pengy-web@.service"
    )
