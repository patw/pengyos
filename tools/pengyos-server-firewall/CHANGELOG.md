# Changelog

All notable changes to this project are documented in this file. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- SSH brute-force mitigation in the rendered ruleset: an
  sshguard/fail2ban-style rate-limit/ban
  (`tcp dport <ssh_port> ct state new limit rate over 3/minute burst 5
  packets drop`) is emitted before the allow list, and the offline
  validator (`pengyos_netctl.nft`) now *asserts* one is present — a
  ruleset without a `limit rate` … `drop` statement in the `input`
  chain fails validation. `pengyos_netctl.render.mitigation_statements`
  renders it (referencing the configured `ssh_port`), and
  `find_rate_limit_drop` detects it. The committed
  `nftables/server.nft` and `tests/fixtures/good_rules.nft` now carry it.
- `pengyos-netctl wg --spec wg.yaml --out wg/` command: renders a `wg0`
  server config plus one per-peer config from a WireGuard spec. Private
  keys are read from (or generated into) the spec's `key_dir` at mode
  `0600`, injected only into the config that belongs to each key, and
  never echoed to stdout. Output is deterministic for a given key set,
  and a bad spec (missing file, corrupt key) fails cleanly with a
  one-line `exit 2` error.
- `pengyos_netctl.wireguard` module: a stdlib-only `x25519` (RFC 7748
  Montgomery ladder, verified against the RFC 7748 §5.2 / §6.1 test
  vectors and the `cryptography` library), base64 private-key
  encode/decode, `private_to_public`, and `wg0`/peer config rendering.
  `generate()` now reports the paths of any key files it allocated on
  `WgOutput.keys_written`.
- Tests for the WireGuard generator (`tests/test_wireguard.py`) and the
  `wg` CLI command (`tests/test_wg_cmd.py`), including RFC 7748 vectors,
  `0600` key perms, no-private-key-leak across configs, and deterministic
  output.

- `pengyos-netctl rules --config ports.yaml --out server.nft` command:
  renders a validated, **deterministic** default-deny `nftables` ruleset
  from a declared-ports config (SSH + WireGuard + the declared ports
  only), validates the rendered text before writing, and writes it to
  `--out` (creating parent directories). Ships the committed
  `nftables/server.nft` deliverable.
- `pengyos_netctl.render` module: `render_ports_ruleset` (the deterministic
  renderer, guaranteed to satisfy the offline validator), the
  `render_ports`/`render_allow_list` helpers, and `write_ruleset`.
- Tests for the renderer (`tests/test_render.py`) and the `rules` command
  (`tests/test_rules_cmd.py`), plus a GitHub-ready check that the committed
  `nftables/server.nft` exists and validates.

- Scaffold of the `pengyos-netctl` CLI package (US-SRV-5): `pyproject.toml`
  with the `pengyos-netctl` entry point, the `pengyos_netctl` package
  (argparse-based CLI with a command registry, YAML/JSON spec loader,
  typed `PortsConfig`/`WgSpec`/`PeerSpec`/`ServerSpec` validators), the
  `nftables/` example spec, and `.gitignore`.
- Offline spec loader (`pengyos_netctl.config`) that parses YAML/JSON specs
  and validates the declared-ports and WireGuard shapes.
- Structural nftables validator (`pengyos_netctl.nft`): an offline,
  standard-library-only parse of nft grammar plus a declared-ports policy
  check — `flush ruleset` present, `input`/`forward` chains `policy drop`,
  and the inbound allow list equal to the declared ports (no more, no
  less). The reusable parser is a documented local copy of the sibling
  `pengyos-net` deliverable (`pengyos_net/nft_validate.py`); the policy
  layer enforces the declared-ports posture rather than the baseline's
  "exactly SSH" posture.
- `pengyos-netctl validate --rules server.nft --config ports.yaml`
  command: renders nothing, just runs the offline validator and exits
  non-zero with every violation listed.
- `tests/fixtures/good_rules.nft`: a committed default-deny ruleset that
  satisfies `nftables/ports.yaml`, used by the validator tests and as the
  reference output for the `rules` task.
- pytest suite (unit only; integration opt-in via `PENGYOS_IT=1`) and the
  GitHub-ready contract test.
