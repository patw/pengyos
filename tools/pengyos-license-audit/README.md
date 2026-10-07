# PengyOS license-audit

Verify each PengyOS component's **license claim** and enforce the **freedom
policy**; emit `LICENSES.md`.

Every PengyOS
component ships a *claimed* license. This tool ingests the pins/license manifest,
classifies each claim, applies the freedom policy, and reports what passes,
warns, or fails — so no opaque blob quietly crosses the core.

## What it does

- **Ingests** a pins/license manifest (`pins.toml`) and a policy file (`policy.toml`).
- **Classifies** each license claim into a family: permissive, weak-copyleft,
  strong-copyleft, or proprietary (SPDX-ish expression parsing).
- **Applies the policy**: permissive preferred; copyleft in the *core* tier is
  flagged; documented component-level exceptions are allowed.
- **Emits** `LICENSES.md` (components grouped by license) and `report.json`
  (machine-readable pass/warn/fail per component).
- **Exits non-zero** when the policy is violated without an exception.

## Install

The tool is **standard-library only** at runtime (Python ≥ 3.10). For
development/testing, install the test extra:

```bash
pip install -e ".[test]"      # or: pip install pytest
```

> On PEP 668 "externally managed" environments you may need
> `pip install -e ".[test]" --break-system-packages`.

## Usage

```bash
pengyos-license-audit run \
    --pins pins.toml \
    --policy policy.toml \
    --out LICENSES.md \
    --json report.json
```

Or as a module:

```bash
python -m pengyos_license_audit run --pins pins.toml --policy policy.toml \
    --out LICENSES.md --json report.json
```

### Exit codes

| Code | Meaning                                              |
|:----:|------------------------------------------------------|
| `0`  | success                                              |
| `1`  | audit/runtime error (bad input, I/O failure)         |
| `2`  | usage error (bad arguments)                          |

## Data model

- **Pin** — one manifest entry: `id`, claimed `license`, `tier` (`core`/`leaf`),
  `link` (`static`/`dynamic`).
- **Policy** — `allowed_families`, `copyleft_in_core`, and `exceptions`
  (component ids exempt from the policy).

See `fixtures/pins.toml` and `fixtures/policy.toml` for a runnable example.

## Development

```bash
python -m pytest -q
```

The suite is fully offline (fixtures + in-process mocks) and needs no root,
network, display, or hardware. Integration tests against a real build tree are
opt-in behind `PENGYOS_IT=1`.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
