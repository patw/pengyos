# pengyos-tiers

The **3-tier change policy engine** for [PengyOS](../../README.md).

`pengyos-tiers` classifies a *proposed change* (its paths, action, and target) into
one of three policy tiers and reports the approvals that gate it:

| Tier | Surface | Who may change it | Required approval |
| --- | --- | --- | --- |
| **0 — Base** | Kernel, core libs, agent runtime, the image itself | Nobody at runtime | Rebuilt → verified → signed → measured |
| **1 — Agent-mutable** | Config files, services, driver config, bootloader entries/cmdline, packages | Pengy (the agent), freely | `agent-auto` (snapshotted + journaled + attested) |
| **2 — Critical surface** | Bootloader binaries/keys, kernel itself, crypto/identity, the agent's own privilege, the update trust root | Human only | `human-sign` |

This is the gate that enforces SPEC §1.5 / §9: the AI drafts anything, but nothing
crosses the security boundary without a human.

## Usage

```console
$ pengyos-tiers --help
usage: pengyos-tiers [-h] [--version] <command> ...

Classify a proposed change into a PengyOS policy tier (0 = image,
1 = agent-mutable, 2 = human-signed) and gate it.

positional arguments:
  <command>
    version   print the pengyos-tiers version and exit
    classify  classify a change (paths/action/target) into a tier + approvals
    check     classify a change described in JSON against a rules file and
              gate
```

Classification is driven by a **declarative rules file** (`tiers.toml`): glob paths
plus action types map to a tier, the first matching rule wins, and anything that
matches no rule is **default-denied to Tier 2** (fail safe).

```console
$ pengyos-tiers classify --action modify --path /etc/foo --json
{"tier": 1, "matched": true, "rule": "system configuration files are agent-mutable", "needs_human": false, "approvals": ["agent-auto"], "reason": "..."}

$ echo '{"action":"modify","path":"/boot/efi/EFI/pengyos/keys.db"}' > change.json
$ pengyos-tiers check --rules tiers.toml change.json
{"tier": 2, "matched": true, "rule": "bootloader signing keys / EFI databases are human-signed", "needs_human": true, "approvals": ["human-sign"], "reason": "..."}
```

The CLI exit code reflects whether a change needs a human: `check` exits non-zero
(code 3, distinct from the error code 1) when the resolved tier is `human-sign`,
so it can gate a pipeline. `classify` is informational and always exits 0 on
success. `check` also reads the change from stdin when given `-`.

## Rules file

The rules file is a TOML array of `[[rule]]` entries. Each rule carries glob `path`
patterns and optional `action`/`target` filters, and resolves to a `tier`. The first
rule (in file order) that matches wins; unmatched changes fall through to the
built-in default-deny (Tier 2). See `spec.md` for the authoritative schema.

## Development

Requires Python ≥ 3.11 (standard library only at runtime; `tomllib` for the rules
file).

```sh
python3 -m pytest -q        # run the test suite
python3 -m pengyos_tiers    # run the CLI without installing
```

To install the `pengyos-tiers` entry point: `pip install .` (or `pipx`).

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
