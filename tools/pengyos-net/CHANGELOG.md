# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `tests/test_integration.py` — opt-in (`PENGYOS_IT=1`, root, systemd-networkd
  host) integration test that brings up the `pengyos-test0` dummy link via
  systemd-networkd and pins `10.99.0.1/24` (link UP + kind dummy + a socket
  bind to the address), with total teardown (link, both units, networkd
  reload) in a `finally` block. The bring-up state machine is additionally
  unit-tested OFFLINE via an injected command runner + fake clock (start-vs-
  reload choice, install ordering, deadline expiry, teardown ordering), plus
  `ip -o` output parsers that handle the live backslash-continuation
  spelling and the real runner's `install-unit`/`uninstall-unit` file-copy
  pseudo-commands.
- `tests/test_github_ready.py` — asserts the spec's GitHub-ready
  contract as tests: README exists and stays in sync (documents the
  validator API, every shipped config file, the default-deny policy,
  the `PENGYOS_IT=1` opt-in, and every local README link resolves),
  LICENSE is MIT with the exact "Copyright (c) 2026 Pat Wendorf" line,
  CHANGELOG records every shipped deliverable, `.gitignore` covers the
  Python noise set (`__pycache__/`, `*.py[cod]`, `.pytest_cache/`,
  `*.egg-info/`), and the full test suite is green (re-run in a
  subprocess with this file excluded.
- `pengyos_net/nft_validate.py` — the nftables validator package:
  stdlib-only structural parser (comment/string aware, `;`- or
  newline-terminated statements, ordered statement+block items,
  balanced-brace / duplicate-block / missing-terminator checks,
  `NftSyntaxError`) plus policy checks returning a `ValidationResult`
  (`.ok`, `.errors`, `.root`) via `validate_ruleset_text()` /
  `validate_ruleset(path)`: `flush ruleset` present, exactly table
  `inet pengyos` with input/forward/output, default-deny inbound
  (input+forward `policy drop`), output `policy accept`, pinned
  input-chain statements only, dport allow list exactly tcp/22, and
  **no `accept` before the allow table** (source order, nested blocks
  count). All violations are reported, not fail-fast.
- `tests/test_nftables.py` rewritten against the package API: parser
  unit tests (comments/strings, ordering, combined dport lists,
  service names, every error path), policy positive/negative cases
  (drop→accept flip in input/forward, output reject, extra open port,
  SSH removed, flush removed, extra table, missing chain, accept
  statement or chain before the allow table, accepts after the table
  not flagged, multi-error reporting).
- Baseline nftables ruleset `nftables/pengyos.nft`: default-deny
  inbound (`input` and `forward` chains, `policy drop`), stateful
  (established/related pass, invalid dropped), explicit inbound allow
  list of exactly SSH (tcp/22), outbound open (`output`,
  `policy accept`), applied via `flush ruleset` for a clean slate.
  Applied by `nft -f` (root; out of scope for the test suite).
- `tests/test_nftables.py`: a stdlib-only nftables structural parser
  (comment/string aware, newline- or `;`-terminated statements,
  balanced-brace + duplicate-block checks) plus policy pinning
  (default-deny present, allow list exactly tcp/22, only the pinned
  input-chain statements allowed) with negative cases (policy
  flipped, extra open port, SSH removed, unbalanced braces,
  unterminated comments).
- Baseline `systemd-networkd` units: `10-default.network` (DHCPv4/v6 +
  IPv6 RA for physical interfaces; virtual interfaces deliberately not
  matched), and an offline `dummy` test link
  (`20-pengyos-test.netdev` + `20-pengyos-test.network`,
  `pengyos-test0` at `10.99.0.1/24`) for hardware-free integration tests.
- `systemd-resolved` drop-in `networkd/resolved/90-pengyos.conf`: stub
  listener on, `CacheFromNetwork=no`, explicit `FallbackDNS`
  (never the 127.0.0.53 stub itself).
- `tests/test_networkd.py`: a small systemd-INI parser (duplicate keys,
  space-separated globs) plus structural and policy pinning tests for the
  above configs, with negative cases (malformed lines, missing DHCP,
  default unit claiming docker interfaces, self-referential DNS).
- Repo scaffold: `pyproject.toml` (setuptools + pytest, stdlib-only runtime),
  `pengyos_net/` package skeleton, `networkd/` and `nftables/` config
  directories, `tests/` with a scaffold smoke test, README, LICENSE (MIT),
  CHANGELOG, .gitignore.
