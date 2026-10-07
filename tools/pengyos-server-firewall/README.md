# pengyos-netctl — PengyOS server-firewall CLI (US-SRV-5)

A default-deny `nftables` ruleset generator plus a WireGuard config
generator, with an offline validator. Part of the
[PengyOS](../../README.md) project.

Network posture for a server: expose **only** the declared ports, keep a
WireGuard tunnel for remote access, and never let private keys leak into
rendered output.

## Layout

| Path                       | What                                                            |
|----------------------------|-----------------------------------------------------------------|
| `pengyos_netctl/`          | Python package: the CLI, spec loader, and error types           |
| `nftables/ports.yaml`      | Example declared-ports config (feeds `rules`)                   |
| `wg.example.yaml`          | Example WireGuard spec (feeds `wg`)                             |
| `tests/`                   | pytest suite (unit only; no root / no network)                  |

## Install

```sh
pip install -e .          # provides the `pengyos-netctl` console script
# or run without installing:
python -m pengyos_netctl --help
```

Runtime dependencies are minimal (PyYAML for spec loading); everything else
uses the Python standard library.

The spec shapes are also importable as a small library:

```python
from pengyos_netctl import load_config, load_ports_config, load_wg_spec

cfg = load_ports_config("nftables/ports.yaml")
cfg.allow_list()   # {"tcp": [22, 443, 8080], "udp": [1194, 51820]}
```

## Usage

```sh
# Render a validated default-deny ruleset exposing only the declared ports.
pengyos-netctl rules --config nftables/ports.yaml --out nftables/server.nft

# Render a wg0 server config + per-peer configs (keys from a key dir).
pengyos-netctl wg --spec wg.example.yaml --out wg/

# Validate a rendered ruleset offline (no nft binary, no root, no network).
pengyos-netctl validate --rules nftables/server.nft
```

### Declared-ports config (`ports.yaml`)

```yaml
listen_port: 51820      # WireGuard (UDP) — always allowed
ssh_port: 22            # SSH (TCP)    — always allowed
allowed_tcp: [443, 8080]
allowed_udp: [1194]
```

The rendered allow list is exactly SSH + WireGuard + the declared ports.

### Validating a ruleset

`pengyos-netctl validate` runs an **offline** structural validator
(no `nft` binary, no root, no network) that checks a rendered ruleset
against its declared-ports config:

* `flush ruleset` is present (nothing pre-existing leaks in),
* `input` **and** `forward` chains are `policy drop` (default-deny),
* the inbound allow list is **exactly** the declared ports — no more, no
  less,
* the `input` chain carries an sshguard/fail2ban-style rate-limit/ban
  (a `limit rate` … `drop` statement) mitigating SSH brute-force attempts.

```sh
pengyos-netctl validate --rules nftables/server.nft --config nftables/ports.yaml
# OK: nftables/server.nft is default-deny and exposes only the declared ports
```

The validator lives in `pengyos_netctl/nft.py` and is importable too:

```python
from pengyos_netctl import load_ports_config, validate_ports_ruleset

cfg = load_ports_config("nftables/ports.yaml")
result = validate_ports_ruleset("nftables/server.nft", cfg)
assert result.ok, result.errors   # list of every violation
```

The reusable nft grammar parser behind it is a documented local copy of
the sibling `pengyos-net` deliverable's `pengyos_net/nft_validate.py`;
the policy layer enforces the declared-ports posture (SSH + WireGuard +
the named ports) rather than a fixed allow list.

### WireGuard spec (`wg.yaml`)

```yaml
key_dir: keys
server:
  private_key: server.private
  address: "10.8.0.1/24"
  listen_port: 51820
  dns: "10.8.0.1"
peers:
  - name: alice
    address: "10.8.0.2/24"
    private_key: alice.private
    allowed_ips: ["10.8.0.2/32"]
```

Private keys are read from `key_dir`, chmod `0600`, and never written into
any output other than the config that belongs to their owner.

## Policy

* **Default-deny inbound.** `input` and `forward` chains are `policy drop`;
  only loopback, established/related flows, and the explicit allow list pass.
* **Declared ports only.** Nothing is exposed beyond what the spec declares.
* **SSH brute-force mitigated.** New SSH connections beyond a small rate
  (`3/minute`, a 5-packet burst) are dropped — an automatic
  sshguard/fail2ban-style "ban" of repeat offenders, emitted before the
  allow list so an overflowing connection is dropped rather than
  accepted.
* **Keys never leak.** Private keys are `0600` and absent from rendered
  output other than their owner's config.

## Development

Python 3.9+ (standard library only, plus PyYAML for spec loading).

```sh
python3 -m pytest -q                          # unit suite
PENGYOS_IT=1 python3 -m pytest -q             # + opt-in real-system tests
```

Tests run without root and without network access; real-system integration
tests are opt-in via `PENGYOS_IT=1`.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
