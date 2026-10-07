# PengyOS server-monitoring (US-SRV-3)

Node-exporter + TheWatcher systemd units, a small Prometheus-style alert-rule
set stored as data, and a fixture-driven rule evaluator — proven by fixtures
where a deliberately induced failure fires an alert and a healthy series stays
silent.

This is a `python`-kind PengyOS task (`pengyos-server-monitoring`). The whole
thing runs offline: no network, no root, no real hardware at test time.

## Layout

```
.
├── exporters/            # hardened systemd unit files (*.service)
│   ├── node-exporter.service
│   └── thewatcher.service
├── rules/                # alert rules as data (TOML)
│   └── pengyos.toml
├── alerts/               # alert sink (ntfy / email) config
│   ├── ntfy.conf
│   └── email.conf
├── fixtures/             # committed metric series + rule-under-test data
│   ├── series/           # sample Prometheus-style time series
│   └── rules/            # rule sets exercised by the evaluator
├── pengyos_monitoring/   # the evaluator package (stdlib only)
│   ├── __init__.py
│   ├── rules.py          # rule loading + schema/grammar validation
│   ├── series.py         # Prometheus text-exposition parser → Series/Sample
│   ├── evaluator.py      # rule evaluation over metric series
│   └── systemd_units.py  # INI unit parser + hardening validator
├── tests/                # pytest suite (offline, fixture/mock driven)
├── pyproject.toml        # project metadata + pytest config
├── README.md
├── CHANGELOG.md
├── LICENSE               # MIT, Copyright (c) 2026 Pat Wendorf
└── .gitignore
```

## Quick start

```bash
python3 -m pytest -q          # run the offline test suite
```

Integration tests (real systemd / exporters) are opt-in and gated behind
`PENGYOS_IT=1` so the default suite never touches the host.

## Design notes

- **Alert rules are data.** `rules/pengyos.toml` describes each rule
  (`expr`, `for`, `severity`, `summary`). The evaluator loads and validates
  them; a rule with a malformed expression fails validation rather than
  silently firing or staying silent.
- **Evaluator subset.** The supported expression language is intentionally
  small and documented: simple comparisons over a series (e.g. a ratio or a
  gauge) with a sustained-for window. See `pengyos_monitoring/evaluator.py`.
- **Fixtures prove behaviour.** `fixtures/series/` holds healthy and induced-
  failure series. A failing fixture fires its rule; a healthy series fires
  nothing.
- **Alert sinks are dry-run rendered.** `alerts/*.conf` (INI-style `[ntfy]` /
  `[email]` tables) configure where a fired alert goes.
  `pengyos_monitoring.alerts` loads a sink config and renders the notification
  that *would* be delivered — an ntfy POST payload (topic/title/message/
  priority/tags) or a stdlib `EmailMessage` — without ever opening a socket or
  sending anything. See `pengyos_monitoring/alerts.py`.

## Non-goals

Standing up Prometheus or Grafana is out of scope. This repo exports metrics
units, defines alert rules as data, and evaluates them against committed
fixtures.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See `LICENSE`.
