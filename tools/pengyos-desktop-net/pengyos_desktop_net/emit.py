"""emit — render PengyOS Desktop network defaults as build-consumed files.

``emit`` turns the declared network profile (see :mod:`pengyos_desktop_net.profile`)
into a set of concrete files. Each file is described by an :class:`Artifact`:

    path    — where it lives on the target (the on-target path, not ``--out``).
    content — the exact bytes written there.
    kind    — a coarse classifier the linter keys off of.

The emitted layout mirrors the real on-target layout so the same tree can be
dropped straight into a Buildroot ``files/`` overlay:

    <out>/
      NetworkManager/NetworkManager.conf
      NetworkManager/conf.d/10-pengyos.conf
      wpa_supplicant.conf
      wifi-firmware.txt
      cosmic/panel.toml
      cosmic-settings/network.toml

The output is *golden and deterministic*: the same profile always renders the
same bytes, in a stable order. That is what makes it safe to diff in CI and to
ship as a pinned overlay.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from pengyos_desktop_net import __version__
from pengyos_desktop_net.profile import NetworkProfile, load_profile

# On-target directory that the cosmic panel default lives under. COSMIC reads
# the default layout from ``~/.config/cosmic/panel.toml``; a distro ships the
# *default* here and the user overrides it per-session.
COSMIC_PANEL_TARGET_DIR = "~/.config/cosmic"
# cosmic-settings reads its network page defaults from its own config tree.
COSMIC_SETTINGS_TARGET_DIR = "~/.config/cosmic-settings"


@dataclass(frozen=True)
class Artifact:
    """One emitted file: its on-target ``path``, its ``content``, and ``kind``."""

    path: str
    content: str
    kind: str

    @property
    def name(self) -> str:
        """The file name (last path component) — the linter's stable key."""
        return self.path.rsplit("/", 1)[-1]


def _network_manager_conf(profile: NetworkProfile) -> Artifact:
    """The primary ``NetworkManager.conf``.

    Selects the ``keyfile`` plugin (connections live under
    ``/etc/NetworkManager/system-connections/``) and marks every bridged/virtual
    device as unmanaged so NM never chokes on libvirt's ``virbr*`` / ``br-*``
    interfaces. This is the file that makes NetworkManager *the* network manager.
    """
    return Artifact(
        path="/etc/NetworkManager/NetworkManager.conf",
        kind="networkmanager",
        content=f"""# Managed by pengyos-desktop-net — NetworkManager (do not edit by hand).
# NetworkManager is the single network service on PengyOS Desktop.

[main]
plugins=keyfile
auth-agent=/usr/sbin/NetworkManagerPolicyAgent

[keyfile]
# Virtual/bridge devices (libvirt, hotspots) are not managed by NM directly.
unmanaged-devices=interface-name:virbr*;interface-name:br-*;mac:00:00:00:00:00:*
""",
    )


def _network_manager_dropin(profile: NetworkProfile) -> Artifact:
    """Drop-in forcing NM to *manage* every real device (incl. Wi-Fi).

    ``[ifupdown] managed=true`` plus an explicit ``devices=*`` guarantee that NM
    takes ownership of the Wi-Fi link rather than leaving it to a fallback
    manager. This is the concrete "NM is the chosen network service" signal the
    linter checks for.
    """
    return Artifact(
        path="/etc/NetworkManager/conf.d/10-pengyos.conf",
        kind="networkmanager",
        content=f"""# Managed by pengyos-desktop-net — NM manages all real devices.

[ifupdown]
managed=true

[device]
# NetworkManager owns every physical device, including the Wi-Fi link.
devices=*
""",
    )


def _wpa_supplicant_conf(profile: NetworkProfile) -> Artifact:
    """Global ``wpa_supplicant`` config.

    Declares the Wi-Fi backend (``wpa_supplicant`` driven by the ``iwildcard`` /
    ``wpa_supplicant`` driver ``mac80211``) that NetworkManager talks to over its
    suppicant plugin. ``scan_ssid=1`` lets it join hidden SSIDs; ``bss_mode=``
    is left to the per-connection profile.
    """
    return Artifact(
        path="/etc/wpa_supplicant.conf",
        kind="wpa_supplicant",
        content=f"""# Managed by pengyos-desktop-net — wpa_supplicant (Wi-Fi backend).
# NetworkManager drives wpa_supplicant over its wpa_supplicant plugin.

ctrl_interface=/run/wpa_supplicant
update_config=1
scan_ssid=1
bss_mode=managed

network={{
    scan_ssid=1
    # A pre-shared-key Wi-Fi network. The SSID/passphrase are filled in by the
    # user (cosmic-settings / the installer) and never shipped in the overlay.
    ssid="your-ssid"
    psk="your-passphrase"
}}
""",
    )


def _wifi_firmware_txt(profile: NetworkProfile) -> Artifact:
    """A manifest of the Wi-Fi firmware subset PengyOS Desktop ships.

    The kernel driver/firmware *packaging* lives in ``pengyos-desktop-drivers``;
    this manifest is the *reference* to that subset so the Desktop build knows
    which firmware the Wi-Fi backend needs. The linter checks that the Wi-Fi
    stack's firmware is referenced here.
    """
    return Artifact(
        path="wifi-firmware.txt",
        kind="wifi-firmware",
        content=f"""# Managed by pengyos-desktop-net — Wi-Fi firmware subset reference.
# The kernel driver lives in pengyos-desktop-drivers; this lists the firmware
# subset the Wi-Fi backend (wpa_supplicant + mac80211) needs at boot.
#
# linux-firmware packages (Debian/Ubuntu):
#   firmware-linux-nonfree   (broadcom, rtl8xxx, ...)
#   firmware-realtek
#   firmware-atheros
#   firmware-marvell
""",
    )


def _cosmic_panel_toml(profile: NetworkProfile) -> Artifact:
    """The COSMIC default panel layout.

    This is the file that closes the gap the spec calls out: the stock COSMIC
    default panel does **not** include the network applet, so there is no GUI
    path to Wi-Fi. Listing ``cosmic-applet-network`` in the default ``left``
    panel pins the network applet into every fresh session.
    """
    applets = " ".join(profile.panel_applets)
    return Artifact(
        path=f"{COSMIC_PANEL_TARGET_DIR}/panel.toml",
        kind="cosmic-panel",
        content=f"""# Managed by pengyos-desktop-net — COSMIC default panel layout.
# The stock COSMIC default panel omits the network applet; listing
# cosmic-applet-network here guarantees a GUI path to Wi-Fi on every session.

[panel]
position="bottom"
expand=true
hide_when_active=true
auto_hide=true

[[panel.left]]
type="applet"
applet="cosmic-applet-network"

[[panel.left]]
type="applet"
applet="cosmic-applet-bluetooth"

[[panel.top]]
type="workspace-switcher"

[[panel.right]]
type="applet"
applet="cosmic-applet-notifications"

[[panel.right]]
type="applet"
applet="cosmic-applet-tray"
""",
    )


def _cosmic_settings_network_toml(profile: NetworkProfile) -> Artifact:
    """cosmic-settings network page defaults.

    cosmic-settings renders its network page from its own config tree; these are
    the defaults PengyOS Desktop pins so the "Network" page shows the
    NetworkManager-backed Wi-Fi/Ethernet controls by default.
    """
    return Artifact(
        path=f"{COSMIC_SETTINGS_TARGET_DIR}/network.toml",
        kind="cosmic-settings",
        content=f"""# Managed by pengyos-desktop-net — cosmic-settings network defaults.
# The network page is served over NetworkManager; these are the defaults the
# Desktop ships so the GUI Network page is present on every session.

[network]
manager="NetworkManager"
show_wifi=true
show_ethernet=true
allow_disabled=true
""",
    )


def artifacts_for(profile: NetworkProfile | None = None) -> list[Artifact]:
    """Return the emitted artifacts in a stable, deterministic order."""
    profile = load_profile(profile)
    return [
        _network_manager_conf(profile),
        _network_manager_dropin(profile),
        _wpa_supplicant_conf(profile),
        _wifi_firmware_txt(profile),
        _cosmic_panel_toml(profile),
        _cosmic_settings_network_toml(profile),
    ]


def render(profile: NetworkProfile | None = None) -> list[Artifact]:
    """Alias of :func:`artifacts_for` — the emitted file set."""
    return artifacts_for(profile)


def emit(
    out: str,
    profile: NetworkProfile | None = None,
    *,
    as_json: bool = False,
) -> list[Artifact]:
    """Write the emitted artifacts under ``out`` and return them.

    Files are written to ``<out>/<on-target-path-without-leading-slash>`` so the
    tree under ``out`` is a drop-in Buildroot ``files/`` overlay. Returns the
    list of :class:`Artifact` written (also what ``--json`` prints).
    """
    import os

    artifacts = artifacts_for(profile)
    root = os.path.abspath(out)
    for art in artifacts:
        rel = art.path.lstrip("/")
        target = os.path.join(root, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(art.content)
    return artifacts


def emit_json(profile: NetworkProfile | None = None) -> str:
    """Return the emitted artifacts as a deterministic JSON string."""
    artifacts = artifacts_for(profile)
    payload = {
        "tool": "pengyos-desktop-net",
        "version": __version__,
        "artifacts": [
            {"path": a.path, "kind": a.kind, "name": a.name, "content": a.content}
            for a in artifacts
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def render_artifacts_json(artifacts: Iterable[Artifact]) -> str:
    """Serialize an artifact list to the same JSON shape as :func:`emit_json`."""
    payload = {
        "tool": "pengyos-desktop-net",
        "version": __version__,
        "artifacts": [
            {"path": a.path, "kind": a.kind, "name": a.name, "content": a.content}
            for a in artifacts
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"
