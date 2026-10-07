"""Tests for pengyos_desktop_net.emit."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from pengyos_desktop_net import __version__
from pengyos_desktop_net import emit as em
from pengyos_desktop_net import profile as prof


def _by_kind(artifacts: list[em.Artifact]) -> dict[str, list[em.Artifact]]:
    out: dict[str, list[em.Artifact]] = {}
    for a in artifacts:
        out.setdefault(a.kind, []).append(a)
    return out


def _one(artifacts: list[em.Artifact], kind: str, path: str) -> em.Artifact:
    matches = [a for a in artifacts if a.kind == kind and a.path == path]
    assert len(matches) == 1, f"expected exactly one {kind} at {path!r}"
    return matches[0]


def test_artifacts_present_and_kinds():
    kinds = set(_by_kind(em.artifacts_for()))
    assert kinds == {
        "networkmanager",
        "wpa_supplicant",
        "wifi-firmware",
        "cosmic-panel",
        "cosmic-settings",
    }


def test_networkmanager_conf():
    conf = _one(em.artifacts_for(), "networkmanager", "/etc/NetworkManager/NetworkManager.conf")
    assert "[main]" in conf.content
    assert "keyfile" in conf.content


def test_networkmanager_dropin_manages_wifi():
    """The drop-in forces NM to manage the Wi-Fi link (the lint signal)."""
    dropin = _one(
        em.artifacts_for(),
        "networkmanager",
        "/etc/NetworkManager/conf.d/10-pengyos.conf",
    )
    assert "devices=*" in dropin.content
    assert "managed=true" in dropin.content


def test_wpa_supplicant_backend():
    wsc = _one(em.artifacts_for(), "wpa_supplicant", "/etc/wpa_supplicant.conf")
    assert "ctrl_interface" in wsc.content


def test_wifi_firmware_referenced():
    fw = _one(em.artifacts_for(), "wifi-firmware", "wifi-firmware.txt")
    assert "linux-firmware" in fw.content


def test_panel_includes_network_applet():
    panel = _one(
        em.artifacts_for(),
        "cosmic-panel",
        "~/.config/cosmic/panel.toml",
    )
    assert prof.COSMIC_APPLET_NETWORK in panel.content
    # It is a real applet entry, not a stray substring.
    assert f'applet="{prof.COSMIC_APPLET_NETWORK}"' in panel.content


def test_cosmic_settings_defaults():
    cs = _one(
        em.artifacts_for(),
        "cosmic-settings",
        "~/.config/cosmic-settings/network.toml",
    )
    assert "NetworkManager" in cs.content


def test_emit_is_deterministic():
    first = em.artifacts_for()
    second = em.artifacts_for()
    assert [(a.path, a.content, a.kind) for a in first] == [
        (a.path, a.content, a.kind) for a in second
    ]


def test_emit_json_is_valid_and_contains_applet():
    payload = json.loads(em.emit_json())
    assert payload["tool"] == "pengyos-desktop-net"
    assert payload["version"] == __version__
    contents = "\n".join(a["content"] for a in payload["artifacts"])
    assert prof.COSMIC_APPLET_NETWORK in contents


def test_emit_writes_files(tmp_path: Path):
    out = str(tmp_path / "overlay")
    artifacts = em.emit(out)
    assert len(artifacts) == 6
    # Each artifact is written to <out>/<on-target-path-without-leading-slash>.
    for art in artifacts:
        rel = art.path.lstrip("/")
        written = (Path(out) / rel).read_text("utf-8")
        assert written == art.content


def test_emit_json_flag_writes_nothing(tmp_path: Path):
    # --json must not write files; it just prints the artifacts.
    artifacts = em.emit_json()
    assert isinstance(artifacts, str)
    assert not list(tmp_path.rglob("*"))
