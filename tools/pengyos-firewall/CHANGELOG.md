# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this tool keeps its own semver (only images use the repo-root `VERSION`).

## [0.2.0] - 2026-11-06

### Added
- `status` lists the hand-written files in `/etc/pengyos/nftables.d/forward/`
  and `table/` (a router's forward rules, NAT chains and sets), which
  `/etc/nftables.conf` now includes into the forward chain and at table level.
  `--json` gains `forward_files` and `table_files` (empty lists when none).
- Tests run the real `nft -c` (in `unshare -rn`) on the shipped ruleset;
  skipped where nft is missing.

## [0.1.0] - 2026-10-27

### Added
- `pengyos-firewall`, shipped in the Core overlay (`board/core/rootfs-overlay/
  usr/bin/`), so every edition has it: `status [--json]`, and `allow` / `remove
  PORT[-PORT][/tcp|/udp] [--from CIDR]`. `allow` and `remove` need root;
  `status` works unprivileged. Misuse exits 2 and writes nothing.
- Each opened port is one file, `/etc/pengyos/nftables.d/allow-*.nft`.
  `/etc/nftables.conf` includes the directory into its input chain. `allow`
  checks the whole ruleset with `nft -c` before reloading, and removes a rule
  nft rejects, so the firewall can't be left unloadable at the next boot.
- Written for Strata's port on a Server NVIDIA box (2026-10-27): the only
  place to open a port on a Server was `/etc/nftables.conf` itself, and on an
  installed machine an edited copy shadows every later image's ruleset.
