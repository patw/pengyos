# pengy-agent-package

A [Buildroot](https://buildroot.net) **external package** for
[PengyOS](../../README.md) that builds the
[PengyR](https://github.com/patw/PengyR) agent's **`pengy-cli`** and **`pengy-web`**
(Rust, **headless** — no Qt GUI) from a pinned source, ships them plus a
`pengy-web` systemd unit and a default config, and validates the recipe with a
pytest suite.

SPEC §Purpose: PengyOS is an operating system an agent can operate, so the agent
ships in Core (SPEC §2.1/§3, §M3).

## What ships in `package/pengy/`

| File | Purpose |
|------|---------|
| `pengy.mk` | The Buildroot recipe: pinned `PENGY_VERSION`, source URL, `LICENSE=MIT`, builds **only** the `pengy-cli` and `pengy-web` Rust targets (cargo `--bin pengy-cli --bin pengy-web`), no Qt6/C++/CMake. |
| `pengy.hash` | The SHA-256 of the pinned release tarball, for reproducible fetches. |
| `pengy-web.service` | A hardened systemd unit (`DynamicUser`, `NoNewPrivileges`, `Restart=on-failure`). |
| `settings.json` | Default `/etc/pengy/settings.json` — OpenAI-compatible base URL, no API key, no model. |

The recipe pins the upstream PengyR release and builds the two headless Rust
binaries only. See `spec.md` for the exact contract the tests enforce.

## Verification (machine-checked by tests)

The suite asserts, against the committed recipe:

- **version pinned** — `PENGY_VERSION` is set and matches the source tarball name;
- **hash present** — `pengy.hash` carries the tarball SHA-256;
- **license declared** — the recipe names the `MIT` license;
- **no unverified downloads** — the download site is an `https` mirror;
- **headless only** — the recipe builds only `pengy-cli` + `pengy-web`, and does
  **not** pull Qt6 / C++ / CMake;
- **systemd unit + config** — `pengy-web.service` has the required sections and
  sane hardening, and the default `settings.json` is valid.

Run the suite:

```sh
python3 -m pytest -q
```

Real system integration (building the recipe, booting a target) is out of scope
and opt-in behind `PENGYOS_IT=1`.

## Status

| Subsystem | State |
|-----------|-------|
| Project scaffold (packaging, pytest harness, GitHub-ready docs) | ✅ done |
| Recipe: `pengy.mk` (pinned, `MIT`, builds only `pengy-cli` + `pengy-web`) | ✅ done |
| Recipe: `pengy.hash` | ✅ done |
| `pengy-web.service` + default `settings.json` | ✅ done |
| Recipe tests (`pengyos-recipe-lint` compliance, headless-only, unit + config) | ✅ done |
| `test_github_ready` (GitHub-ready contract) | ✅ done |

See `spec.md` for the contract.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
