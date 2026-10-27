# pengyos-firewall

Open and close **this machine's own ports** on top of PengyOS's default-deny
firewall, from the shell, on every edition.

```sh
pengyos-firewall status [--json]                      # what is open
sudo pengyos-firewall allow 8086 --from 10.0.23.0/24  # one TCP port, LAN only
sudo pengyos-firewall allow 22000/udp                 # a UDP port, from anywhere
sudo pengyos-firewall remove 8086 --from 10.0.23.0/24 # close it again
```

Every edition boots with the same ruleset, `/etc/nftables.conf`. It lets in
ssh (22), mDNS (5353/udp) and replies to connections this machine started, and
drops everything else. The **Desktop** also opens every port above 1024 (LAN
games, Steam Remote Play, LocalSend), through an image rule. **Core and Server
open nothing more** until you say so, and this tool is how you say so.

Without `--from`, a port is open to every address that can reach the machine.
On a cloud VM or any public network, that means the internet.

## What it writes

One file per opened port, in `/etc/pengyos/nftables.d/`:

```
# /etc/pengyos/nftables.d/allow-tcp-8086-from-10.0.23.0_24.nft
# pengyos-firewall: 8086/tcp from 10.0.23.0/24
# Written by pengyos-firewall allow; 'remove' with the same arguments deletes it.
tcp dport 8086 ip saddr 10.0.23.0/24 ct state new accept
```

`/etc/nftables.conf` includes every `*.nft` file there into its input chain.
`/etc` survives OS updates, so the opened ports do too. Because they are
separate files, they don't replace the image's `nftables.conf`, and the image
can still change that file.

Before reloading, `allow` checks the whole ruleset with `nft -c`. A rule nft
rejects is removed again, so a typo can never leave the firewall unable to
load at the next boot. You can also drop a hand-written `.nft` file in the
directory (input-chain statements only), and `status` lists it as "by hand".

## Tests

```sh
cd tools/pengyos-firewall && python3 -m pytest -q
```

The suite runs the real script against a sandbox root (`--root DIR`), with a
fake `nft` and a fake `systemctl`. It needs no root and no nftables. That the
rules load for real is proven by booting the images: see `spec.md`.
