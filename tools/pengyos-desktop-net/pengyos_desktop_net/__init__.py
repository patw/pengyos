"""pengyos_desktop_net — PengyOS Desktop network defaults.

Declarative, build-consumed config for wiring PengyOS Desktop networking so a
person can get online (including Wi-Fi) with no CLI:

* **NetworkManager** enabled as the sole network service (and
  ``systemd-networkd`` explicitly *not* claiming the Wi-Fi link).
* **wpa_supplicant** + the Wi-Fi firmware stack referenced.
* The **COSMIC** default panel layout including ``cosmic-applet-network``
  (the GUI path to joining Wi-Fi) plus the ``cosmic-settings`` network defaults.

The package has two lanes:

``emit``
    Render the above as concrete build-consumed files (NetworkManager drop-ins,
    ``wpa_supplicant`` config, the COSMIC panel default, the
    ``cosmic-settings`` network defaults).

``lint``
    Check an emitted/installed overlay for the invariants the spec guarantees:
    NetworkManager is the chosen network service, the Wi-Fi stack + firmware are
    referenced, the panel default includes the network applet, and no conflicting
    ``networkd`` ownership exists.

The whole suite is standard-library-only at test time and needs no network,
root, display, or hardware.
"""

from __future__ import annotations

__version__ = "0.5.0"

__all__ = ["__version__"]
