"""The PengyOS Desktop network profile as data.

The whole lane turns this one declarative description into concrete files
(:mod:`pengyos_desktop_net.emit`) and checks it (:mod:`pengyos_desktop_net.lint`).
Keeping it as plain data — not bespoke port code — is the point of the repo.

``NetworkProfile`` captures the three things the spec calls out:

* ``manager`` — the network service (``NetworkManager``).
* ``wifi_backend`` / ``wifi_firmware`` — the Wi-Fi stack + firmware reference.
* ``panel_applets`` — the COSMIC panel applet set, which must include
  ``cosmic-applet-network`` so there is a GUI path to Wi-Fi.
* ``cosmic_settings_manager`` — the backend cosmic-settings' network page serves
  over (``NetworkManager``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# The applet that provides the GUI path to Wi-Fi. The spec pins this; the linter
# checks the panel default lists it.
COSMIC_APPLET_NETWORK = "cosmic-applet-network"


@dataclass(frozen=True)
class NetworkProfile:
    """The declarative PengyOS Desktop network profile."""

    manager: str = "NetworkManager"
    wifi_backend: str = "wpa_supplicant"
    wifi_firmware: str = "linux-firmware"
    panel_applets: tuple[str, ...] = field(
        default_factory=lambda: (
            COSMIC_APPLET_NETWORK,
            "cosmic-applet-bluetooth",
            "cosmic-applet-notifications",
            "cosmic-applet-tray",
        )
    )
    cosmic_settings_manager: str = "NetworkManager"

    def __post_init__(self) -> None:
        # The whole point of the Desktop network defaults: the panel must expose
        # the network applet, or there is no GUI path to Wi-Fi.
        if COSMIC_APPLET_NETWORK not in self.panel_applets:
            raise ValueError(
                f"panel_applets must include {COSMIC_APPLET_NETWORK!r}"
            )
        if self.manager != "NetworkManager":
            raise ValueError(
                "manager must be 'NetworkManager' (the single network service)"
            )


def default_profile() -> NetworkProfile:
    """The PengyOS Desktop default network profile."""
    return NetworkProfile()


def load_profile(
    profile: Optional[NetworkProfile] = None,
) -> NetworkProfile:
    """Return ``profile`` if given, else the default (validated)."""
    return profile if profile is not None else default_profile()
