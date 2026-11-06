"""A Server can be a router: dnsmasq ships, starts nowhere by itself, and the
example router bundle is complete.

dnsmasq moved from the Desktop layer to the Server layer (the Desktop inherits
it for NetworkManager's hotspot). The danger of that move is a DHCP server on
every Server and Desktop: Buildroot runs `systemctl preset-all`, and a unit
that no preset file names is enabled (10-pengyos-core.preset). So the image's
dnsmasq.service must be named `disable` in a preset, and no overlay may carry
an enable link for it. The ruleset side (the example's .nft files parse with
the real nft) is tested in tools/pengyos-firewall.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "buildroot-external"
LAYERS = EXT / "configs" / "layers"
BOARD = EXT / "board"
SERVER = BOARD / "server" / "rootfs-overlay"
UNIT = SERVER / "usr" / "lib" / "systemd" / "system" / "dnsmasq.service"
PRESET = SERVER / "usr" / "lib" / "systemd" / "system-preset" / "10-pengyos-server.preset"
EXAMPLE = SERVER / "usr" / "share" / "pengyos" / "examples" / "router"


def symbols(layer: str) -> set[str]:
    return {
        ln.strip() for ln in (LAYERS / f"{layer}.config").read_text().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    }


def test_dnsmasq_is_in_the_server_layer_only():
    assert "BR2_PACKAGE_DNSMASQ=y" in symbols("server")
    for layer in ("core", "desktop", "nvidia-compute", "nvidia"):
        assert "BR2_PACKAGE_DNSMASQ=y" not in symbols(layer), layer
    # Every edition that has NetworkManager's hotspot still gets it.
    for d in ("pengyos_desktop_defconfig", "pengyos_desktop_nvidia_defconfig",
              "pengyos_server_defconfig", "pengyos_server_nvidia_defconfig"):
        assert "BR2_PACKAGE_DNSMASQ=y" in (EXT / "configs" / d).read_text().splitlines(), d
    assert "BR2_PACKAGE_DNSMASQ=y" not in (EXT / "configs" / "pengyos_core_defconfig").read_text()


def test_dnsmasq_unit_ships_and_is_disabled_by_preset():
    text = UNIT.read_text()
    assert "ConditionPathExists=/etc/dnsmasq.conf" in text
    assert "ExecStart=/usr/sbin/dnsmasq --keep-in-foreground -C /etc/dnsmasq.conf" in text
    assert "WantedBy=multi-user.target" in text
    lines = [ln.strip() for ln in PRESET.read_text().splitlines()]
    assert "disable dnsmasq.service" in lines
    # No preset file anywhere enables it, and no overlay carries an enable link.
    for preset in BOARD.glob("*/rootfs-overlay/usr/lib/systemd/system-preset/*.preset"):
        assert "enable dnsmasq.service" not in preset.read_text(), preset
    links = list(BOARD.glob("*/rootfs-overlay/etc/systemd/system/*.wants/dnsmasq.service"))
    assert links == []


def test_dnsmasq_lease_directory_is_made():
    conf = SERVER / "usr" / "lib" / "tmpfiles.d" / "pengyos-dnsmasq.conf"
    assert "d /var/lib/misc 0755 root root -" in conf.read_text().splitlines()


def test_example_router_bundle_is_complete_and_names_no_hardware():
    want = {
        "README.md",
        "etc/dnsmasq.conf",
        "etc/sysctl.d/90-router.conf",
        "etc/systemd/resolved.conf.d/90-router.conf",
        "etc/systemd/network/01-wan.link",
        "etc/systemd/network/01-lan.link",
        "etc/systemd/network/05-wan.network",
        "etc/systemd/network/05-lan.network",
        "etc/systemd/network/25-wg0.netdev",
        "etc/systemd/network/25-wg0.network",
        "etc/pengyos/nftables.d/router-input.nft",
        "etc/pengyos/nftables.d/forward/router.nft",
        "etc/pengyos/nftables.d/table/router.nft",
        "etc/pengyos/update-health.d/50-router",
    }
    got = {str(p.relative_to(EXAMPLE)) for p in EXAMPLE.rglob("*") if p.is_file()}
    assert got == want
    # Only the .link files name hardware; everything else says wan0 / lan0.
    for rel in want - {"etc/systemd/network/01-wan.link", "etc/systemd/network/01-lan.link"}:
        text = (EXAMPLE / rel).read_text()
        assert "enp" not in text and "eth0" not in text, rel
    # The .network files sort before the image's 10-pengyos-ethernet.network,
    # so they win without deleting it (networkd applies the first match).
    image_net = sorted(p.name for p in BOARD.glob("*/rootfs-overlay/etc/systemd/network/*.network"))
    for name in ("05-wan.network", "05-lan.network"):
        assert all(name < other for other in image_net), (name, image_net)
    dnsmasq = (EXAMPLE / "etc" / "dnsmasq.conf").read_text().splitlines()
    assert "bind-dynamic" in dnsmasq  # coexists with resolved's stub on :53
