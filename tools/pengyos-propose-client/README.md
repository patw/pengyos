# pengyos-propose-client

A Python CLI that **formats change proposals and routes them to the
human-gated propose/approve gate** for [PengyOS](../../README.md).

Proposals come from triage / upstream-watch. They are **never auto-applied**:
they flow to a human reviewer, who decides whether to build them. This client
only *formats* and *submits* — the gate does the gating.

## Install

```bash
pip install -e .
```

This installs the `pengyos-propose` console script. Standard library only at
runtime; `pytest` is an optional test dependency.

## Usage

```bash
# Preview a human-readable view of a proposal (Markdown)
pengyos-propose render proposal.json

# Format + submit a proposal to the gate
pengyos-propose submit --proposal proposal.json --human-approved

# Preview the exact request body without sending it
pengyos-propose submit --proposal proposal.json --dry-run
```

### Commands

| Command | Purpose |
|---------|---------|
| `render <path>` | Print a Markdown view of the proposal (`--format md|html`). |
| `submit --proposal <path>` | POST the proposal to the gate. |

### `submit` options

| Flag | Meaning |
|------|---------|
| `--proposal, -p` | Path to the proposal JSON file (required). |
| `--dry-run` | Print the request body instead of sending it. |
| `--human-approved` | Declare explicit human approval (required for Tier 2). |
| `--base-url` | Gate base URL (falls back to `$PENGYOS_GATE_URL`). |

## Proposal schema

A proposal is a JSON object:

```json
{
  "title": "Bump systemd in the base image",
  "source": "upstream-watch",
  "type": "package-bump",
  "component": "systemd",
  "from": "systemd 256",
  "to": "systemd 257",
  "rationale": "Security fix backported from upstream.",
  "evidence": ["CVE-2026-1234", "build-green-on-x86_64"],
  "tier": "tier1"
}
```

Tiers follow the [pengyos-tiers](../../pengyos-tiers) policy:

- **tier0** — image / baseline
- **tier1** — agent-mutable
- **tier2** — human-signed (requires explicit `--human-approved`)

## Tier gating

Tier 2 proposals sit in the human-signed band and must never cross the security
boundary without an explicit human decision. The client **enforces this locally**:
`pengyos-propose submit` on a Tier 2 proposal without `--human-approved` refuses
with a non-zero exit code and a clear message *before any request is sent* — the
same refusal the gate server applies. `--dry-run` previews the body without
enforcing the gate.

## Required fields

A proposal must carry at least one human-usable field — a non-empty `title`,
`component`, or `tier`. A proposal missing all of them is refused by the loader
with a clear error (exit 1) *before* `render` or `submit` do anything.

## Development

```bash
python -m pip install -e ".[test]"
python -m pytest -q
```

Tests are fully offline (the gate's HTTP layer is mocked); integration tests
against a real gate are opt-in behind `PENGYOS_IT=1`.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
