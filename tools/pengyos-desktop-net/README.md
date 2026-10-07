# pengyos-desktop-net

Declarative **NetworkManager + wpa_supplicant + COSMIC panel defaults** for the
[PengyOS](../../README.md) Desktop — emitted as build-consumed config, with a
linter.

## Why this exists

US-DSK-1/2 and the live installer make joining Wi-Fi step one of any install.
COSMIC configures networking over NetworkManager (`cosmic-applet-network` in the
panel + the network page in `cosmic-settings`). But the **stock COSMIC default
panel does not include the network applet**, so without this there is no GUI path
to Wi-Fi.

`pengyos-desktop-net` makes the network defaults declarative and guaranteed:

- **NetworkManager** is the single network service (and `systemd-networkd` does
  not also own the Wi-Fi link).
- The **wpa_supplicant** + Wi-Fi firmware stack is referenced.
- The **COSMIC default panel** lists `cosmic-applet-network`, so the GUI path to
  Wi-Fi is present on every fresh session.

## The two lanes

```
pengyos-desktop-net emit --out DIR [--json]
pengyos-desktop-net lint --overlay DIR [--json]
```

**`emit`** renders the network profile into a drop-in Buildroot `files/` overlay:

| File | Kind | Purpose |
|------|------|---------|
| `/etc/NetworkManager/NetworkManager.conf` | networkmanager | NM as the network manager (keyfile plugin) |
| `/etc/NetworkManager/conf.d/10-pengyos.conf` | networkmanager | NM manages every real device, incl. Wi-Fi |
| `/etc/wpa_supplicant.conf` | wpa_supplicant | Wi-Fi backend (wpa_supplicant / mac80211) |
| `wifi-firmware.txt` | wifi-firmware | The Wi-Fi firmware subset reference |
| `~/.config/cosmic/panel.toml` | cosmic-panel | Default panel, lists `cosmic-applet-network` |
| `~/.config/cosmic-settings/network.toml` | cosmic-settings | cosmic-settings network defaults |

**`lint`** asserts the four invariants the spec guarantees and fails (exit 1)
when any breaks:

| Rule | Fires when |
|------|-----------|
| `nm-enabled` | no NetworkManager config in the overlay |
| `wifi-stack` | no `wpa_supplicant` config referenced |
| `wifi-firmware` | no Wi-Fi firmware manifest referenced |
| `network-applet-in-panel` | the panel default omits `cosmic-applet-network` |
| `no-networkd-ownership` | `systemd-networkd` owns a link (`.network` file / enabled service) |

The output is **golden and deterministic** — the same profile always renders the
same bytes, so it is safe to diff in CI and ship as a pinned overlay.

## Board integration

The emitted overlay is **build-consumed** by the PengyOS Desktop
buildroot-external board: `buildroot-external/board/desktop/rootfs-overlay/`
ships it byte-for-byte (the `~` user-home defaults land under
`/etc/skel/` so every fresh user gets the COSMIC panel + cosmic-settings
defaults), the conflicting `systemd-networkd` ethernet stub was removed so
NetworkManager is the sole network service, and the Desktop defconfig selects
`BR2_PACKAGE_WPA_SUPPLICANT=y` (the Wi-Fi backend NM drives) **and
`BR2_PACKAGE_NETWORK_MANAGER=y`** (NetworkManager is a stock Buildroot 2026.08
package — the real symbol has an underscore, not the
`BR2_PACKAGE_NETWORKMANAGER` that does not exist) and merges the overlay via
`BR2_ROOTFS_OVERLAY`. `cosmic-applet-network` (the GUI path) ships from the
cosmic-build release, dropped in by the board's `post-build.sh`.
`tests/test_board_overlay.py` asserts the emit output and the board overlay agree
and that the board overlay passes `lint`; `tests/test_board_network.py` asserts
the Desktop defconfig wires the real NetworkManager symbol.

The end-to-end check (a full image build + boot to a joined Wi-Fi link) is
done by hand, following [`docs/qemu-testing.md`](../../docs/qemu-testing.md); the
board's boot-readiness is pre-checked by `tests/test_board_integrity.py`.

## Development

```bash
python3 -m pytest -q        # run the suite (stdlib-only, no network/root/display)
python3 -m pengyos_desktop_net emit --out /tmp/overlay
```

Tests need no network, root, a real display, or hardware. See
[CHANGELOG.md](CHANGELOG.md) for the history and [spec.md](spec.md) for the
full specification.

## Non-goals

- The Wi-Fi kernel driver / firmware packaging (`pengyos-desktop-drivers`).
- Real radio hardware.
