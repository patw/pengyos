# A PengyOS Server as a router: example files

Copy what you need into `/etc` (same paths as here), then change the MACs,
addresses, domain and port forwards. Nothing here is active until it is in
`/etc`; nothing in the image changes.

    sudo cp -r /usr/share/pengyos/examples/router/etc/. /etc/
    # edit: 01-*.link MACs, 05-lan.network address, dnsmasq.conf, wg0, nftables.d
    sudo nft -c -f /etc/nftables.conf          # the firewall must check
    sudo dnsmasq --test -C /etc/dnsmasq.conf
    sudo systemctl enable --now dnsmasq
    sudo reboot                                # renames the NICs, applies sysctl

What goes where, and why:

| File | Does |
|---|---|
| `systemd/network/01-wan.link`, `01-lan.link` | name the ports `wan0`, `lan0` by MAC, so nothing else names hardware |
| `systemd/network/05-wan.network`, `05-lan.network` | WAN DHCP + IPv6 PD, LAN static + a delegated /64. `05-` sorts before the image's `10-pengyos-ethernet.network`, which needs no deleting |
| `systemd/network/25-wg0.netdev`, `25-wg0.network` | WireGuard by networkd; the private key is a file you make |
| `sysctl.d/90-router.conf` | forwarding on |
| `systemd/resolved.conf.d/90-router.conf` | the router's own lookups go to its dnsmasq |
| `dnsmasq.conf` | DHCPv4, RA + SLAAC, DNS; `bind-dynamic` so it coexists with resolved |
| `pengyos/nftables.d/router-input.nft` | the router's own services (DNS, DHCP, WireGuard) |
| `pengyos/nftables.d/forward/router.nft` | LAN/VPN out; no new IPv6 connections in |
| `pengyos/nftables.d/table/router.nft` | NAT, port forwards, the WAN ssh guard |

The image's `/etc/nftables.conf` includes the three `nftables.d` places, so
none of this edits a file the image owns, and OS updates keep updating the
firewall. Check with `pengyos-firewall status` and `sudo nft list table inet pengyos`.
Never `nft flush ruleset` (it deletes rootful podman's tables too).

Only one DHCP server and one RA sender per LAN: stop the old router's before
starting this one.
