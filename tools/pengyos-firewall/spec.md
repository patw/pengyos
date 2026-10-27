# pengyos-firewall — this machine's opened ports, on the default-deny firewall

## Summary
A POSIX-shell tool shipped in the Core overlay (so every edition has it) that
opens and closes individual inbound ports on top of PengyOS's default-deny
nftables ruleset. Each opened port is one rule file under `/etc`, so it
survives OS updates without replacing the image's `/etc/nftables.conf`.

## Purpose
Every edition starts from the same default-deny ruleset (`/etc/nftables.conf`,
Core layer): ssh, mDNS, replies, nothing else. The Desktop adds an image rule
opening every port above 1024; Core and Server do not, by design (US-SRV-5:
"only intended ports are exposed"). A Server that runs a service has to open
its port, and until now the only place was `/etc/nftables.conf` itself. On an
installed machine that file lives in the `/etc` overlay, so an edited copy
shadows every later image's ruleset for good (`docs/state-layout.md`). This
tool makes opening a port one command whose result stays out of the image's
file.

## Interface
```
pengyos-firewall status [--json]
sudo pengyos-firewall allow PORT[-PORT][/tcp|/udp] [--from CIDR]
sudo pengyos-firewall remove PORT[-PORT][/tcp|/udp] [--from CIDR]
pengyos-firewall --help
pengyos-firewall --root DIR …        # test hook: write under DIR, no root needed
```
- `PORT` is 1-65535, or a range `LO-HI` with LO < HI. TCP unless `/udp`.
- `--from CIDR` limits the rule to one IPv4 or IPv6 address or network
  (`ip saddr` / `ip6 saddr`). Without it the port is open to every source,
  and `allow` says so.
- `status` is read-only and works unprivileged. It shows whether
  nftables.service is active, what is always open, the image's edition rules
  (`/usr/lib/pengyos/nftables.d/input-*.nft`), and every rule file in
  `/etc/pengyos/nftables.d/`, including ones written by hand.
- Misuse exits 2 with a message naming the misuse, and writes nothing.
  `allow` twice is one rule; `remove` of a port not opened is a no-op.

## Deliverable
| File | Ships at |
|---|---|
| `buildroot-external/board/core/rootfs-overlay/usr/bin/pengyos-firewall` | `/usr/bin/pengyos-firewall` (every edition) |
| `buildroot-external/board/core/rootfs-overlay/etc/nftables.conf` | `/etc/nftables.conf`: the ruleset, which includes the rule dir |
| `buildroot-external/board/core/rootfs-overlay/etc/pengyos/nftables.d/README` | `/etc/pengyos/nftables.d/`: the rule dir, with a README |
| `tools/pengyos-firewall/` | the test suite and its docs (not in the image) |

Runtime state: one file per opened port,
`/etc/pengyos/nftables.d/allow-<proto>-<port>[-from-<cidr>].nft`, holding a
label comment and one input-chain statement:
`tcp dport 8086 ip saddr 10.0.23.0/24 ct state new accept`.

## Behaviour
1. `allow` validates its arguments, writes the rule file, then runs
   `nft -c -f /etc/nftables.conf` against the whole ruleset with the new file
   in place. If nft rejects it, the new file is removed (an existing identical
   one is kept) and the command exits 2 with nft's error: a rule that does not
   check would stop nftables.service from loading anything at the next boot.
2. If nftables.service is active it is reloaded (`ExecReload` re-runs
   `nft -f`, which replaces only `table inet pengyos`). If it is not active,
   the file is written and applies when the firewall next starts; the
   command says so.
3. `remove` deletes the matching file, checks the remaining ruleset, and
   reloads the same way.

## Acceptance criteria
- The suite (`python3 -m pytest -q`) passes: rule files byte for byte, check
  before reload, the reject path, misuse, the root guard, status and JSON.
- The default-deny ruleset ships in the Core overlay, and its input chain
  includes both `/usr/lib/pengyos/nftables.d/input-*.nft` and
  `/etc/pengyos/nftables.d/*.nft`.
- Only the Desktop ships an image rule, and it still opens TCP and UDP
  1025-65535.
- Booted images: Core and Server reach `Finished … nftables firewall`; on a
  booted image, `allow` then `nft list chain inet pengyos input` shows the
  rule, and `remove` takes it out.

## Out of scope
- Forwarding and NAT (a router). That is the `forward` chain, and a router
  needs its own ruleset.
- Closing ports the image opens (22, mDNS, the Desktop's range).
- Rate limiting, logging, zones.
