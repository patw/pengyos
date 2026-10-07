"""Tests for pengyos_desktop_net.lint.

The lint suite is a mutation suite: start from a clean emitted overlay, then
break one invariant at a time and assert exactly the matching rule fires.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from pengyos_desktop_net import emit as em
from pengyos_desktop_net import lint as ln
from pengyos_desktop_net import profile as prof


def _clean_overlay(tmp_path: Path) -> Path:
    """Write a clean emitted overlay under ``tmp_path`` and return its path."""
    overlay = tmp_path / "overlay"
    em.emit(str(overlay))
    return overlay


def _rules(result: ln.Result) -> set[str]:
    return {v.rule for v in result.violations}


def test_clean_overlay_passes(tmp_path: Path):
    overlay = _clean_overlay(tmp_path)
    result = ln.lint_overlay(str(overlay))
    # Guard: this test itself is only valid if the overlay is clean.
    assert result.ok, result.messages


def test_missing_nm_fires_nm_enabled(tmp_path: Path):
    overlay = _clean_overlay(tmp_path)
    shutil.rmtree(overlay / "etc" / "NetworkManager")
    result = ln.lint_overlay(str(overlay))
    assert not result.ok
    assert "nm-enabled" in _rules(result)


def test_missing_wpa_supplicant_fires_wifi_stack(tmp_path: Path):
    overlay = _clean_overlay(tmp_path)
    (overlay / "etc" / "wpa_supplicant.conf").unlink()
    result = ln.lint_overlay(str(overlay))
    assert not result.ok
    assert "wifi-stack" in _rules(result)


def test_missing_firmware_fires_wifi_firmware(tmp_path: Path):
    overlay = _clean_overlay(tmp_path)
    (overlay / "wifi-firmware.txt").unlink()
    result = ln.lint_overlay(str(overlay))
    assert not result.ok
    assert "wifi-firmware" in _rules(result)


def test_missing_applet_fires_network_applet(tmp_path: Path):
    overlay = _clean_overlay(tmp_path)
    panel = overlay / "~" / ".config" / "cosmic" / "panel.toml"
    text = panel.read_text("utf-8").replace(
        f'applet="{prof.COSMIC_APPLET_NETWORK}"', ""
    )
    panel.write_text(text)
    result = ln.lint_overlay(str(overlay))
    assert not result.ok
    assert "network-applet-in-panel" in _rules(result)


def test_networkd_ownership_fires(tmp_path: Path):
    overlay = _clean_overlay(tmp_path)
    # A systemd-networkd .network file claiming the Wi-Fi link.
    net_dir = overlay / "etc" / "systemd" / "network"
    net_dir.mkdir(parents=True, exist_ok=True)
    (net_dir / "10-wifi.network").write_text("[Network]\n[Match]\nName=wlan0\n")
    result = ln.lint_overlay(str(overlay))
    assert not result.ok
    assert "no-networkd-ownership" in _rules(result)


def test_missing_overlay_is_failure():
    result = ln.lint_overlay("/nonexistent/path/definitely/missing")
    assert not result.ok
    assert result.violations[0].rule == "overlay-exists"


def test_lint_json_ok(tmp_path: Path):
    overlay = _clean_overlay(tmp_path)
    payload = json.loads(ln.lint_json(str(overlay)))
    assert payload["ok"] is True
    assert payload["violations"] == []


def test_lint_json_failure(tmp_path: Path):
    overlay = _clean_overlay(tmp_path)
    (overlay / "wifi-firmware.txt").unlink()
    payload = json.loads(ln.lint_json(str(overlay)))
    assert payload["ok"] is False
    assert any(v["rule"] == "wifi-firmware" for v in payload["violations"])
