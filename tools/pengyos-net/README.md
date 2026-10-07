# pengyos-net — PengyOS baseline network config + validator

Baseline `systemd-networkd`/`systemd-resolved` configuration plus an
`nftables` ruleset with a default-deny inbound policy, and a pytest-based
validator that checks both the config syntax and the security policy.

Part of the [PengyOS](../../README.md) project (SPEC §2.2 — a sane, secure
default network posture).

## Layout

| Path                   | What                                                                  |
|------------------------|-----------------------------------------------------------------------|
| `networkd/*.network`   | `systemd-networkd` unit files → `/etc/systemd/network/`               |
| `networkd/*.netdev`    | `systemd-networkd` virtual-device files (offline dummy test link)     |
| `networkd/resolved/`   | `systemd-resolved` drop-in → `/etc/systemd/resolved.conf.d/`          |
| `nftables/`            | `pengyos.nft` — baseline nftables ruleset (default-deny inbound)      |
| `pengyos_net/`         | Python package: the config validator (syntax + policy assertions)     |
| `tests/`               | pytest suite (unit, no root / no network required)                    |

### Baseline network config (what is shipped so far)

- `networkd/10-default.network` — DHCPv4/v6 + IPv6 RA for conventional
  physical interfaces (`en* eth* wlan* wwan*`); virtual interfaces
  (docker/veth/tap/tun/virbr/br-/wg) are deliberately not matched.
- `networkd/20-pengyos-test.netdev` + `20-pengyos-test.network` — an
  offline `dummy` link (`pengyos-test0`, `10.99.0.1/24`) so the stack can
  be integration-tested without hardware.
- `networkd/resolved/90-pengyos.conf` — resolved drop-in: stub listener
  on, DNS cache not served from the network, explicit `FallbackDNS`
  (never the 127.0.0.53 stub itself).
- `nftables/pengyos.nft` — baseline firewall: default-deny inbound
  (`input`/`forward` `policy drop`), stateful, explicit inbound allow
  list of exactly SSH (tcp/22), outbound open. Apply with
  `nft -f nftables/pengyos.nft` (requires root).

## Status

The validator and the config files are complete; see [spec.md](spec.md).

## Using the validator

`pengyos_net.nft_validate` checks the ruleset offline (no `nft` binary,
no root, no network):

```python
from pengyos_net import validate_ruleset

result = validate_ruleset("nftables/pengyos.nft")
assert result.ok, result.errors   # list of every violation
```

Policy checks: `flush ruleset` present; exactly table `inet pengyos`
with `input`/`forward`/`output`; default-deny inbound (`policy drop` on
`input` and `forward`); `output` `policy accept`; the input chain may
contain only the pinned baseline statements; the inbound new-connection
allow list is exactly tcp/22; and no `accept` may appear before the
allow table in source order.

## Development

Python 3.9+ (standard library only). Tests run without root and without
network access; real-system integration tests are opt-in via
`PENGYOS_IT=1`.

The integration test
(`tests/test_integration.py::test_pengyos_test0_dummy_link_came_up`)
additionally requires **root on a systemd-networkd host**: it installs
the two `20-pengyos-test` units, brings the `pengyos-test0` dummy link
up, pins `10.99.0.1/24` (link UP + socket bind), and tears down
everything it created. Its state machine is separately unit-tested
offline, so non-root/CI runs exercise the same logic against fakes.

```sh
python3 -m pytest -q                          # unit suite
PENGYOS_IT=1 python3 -m pytest -q             # + real bring-up (as root)
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
