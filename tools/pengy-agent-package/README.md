# pengy-agent-package

A [Buildroot](https://buildroot.net) **external package** for
[PengyOS](../../README.md) that builds the
[PengyR](https://github.com/patw/PengyR) agent's **`pengy-cli`** and **`pengy-web`**
(Rust, **headless** — no Qt GUI) from a pinned source, ships them plus a default
config, and validates the recipe with a pytest suite.

SPEC §Purpose: PengyOS is an operating system an agent can operate, so the agent
ships in Core (SPEC §2.1/§3, §M3).

## What ships in `package/pengy/`

| File | Purpose |
|------|---------|
| `pengy.mk` | The Buildroot recipe: pinned `PENGY_VERSION`, source URL, `LICENSE=MIT`, builds **only** the `pengy-cli` and `pengy-web` Rust targets (cargo `--bin pengy-cli --bin pengy-web`), no Qt6 / C++ / CMake. |
| `pengy.hash` | The SHA-256 of the pinned release tarball, for reproducible fetches. |
| `settings.json` | Default `/etc/pengy/settings.json` — OpenAI-compatible base URL, no API key, no model. |

**No systemd unit ships here.** `pengy-web` has no login and exposes
`run_bash`/`run_python`, so serving it is a per-user, opt-in act, not something
a recipe can enable on every machine that builds it. The unit is the system
template `buildroot-external/board/server/rootfs-overlay/etc/systemd/system/pengy-web@.service`,
enabled per user by [`pengyos-webui`](../pengyos-webui/) — `User=%i`,
`WorkingDirectory=%h`, loopback by default, no `DynamicUser`, no `ProtectHome`.

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
- **systemd unit: none** — the recipe installs no unit and the package directory
  carries none (the UI is enabled per user by `pengyos-webui`);
- **default config** — `settings.json` is valid JSON, points at an
  OpenAI-compatible endpoint, has no API key and no model baked in.

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
| Default `settings.json` (no unit: the UI is per-user, see `pengyos-webui`) | ✅ done |
| Recipe tests (`pengyos-recipe-lint` compliance, headless-only, config) | ✅ done |
| `test_github_ready` (GitHub-ready contract) | ✅ done |

See `spec.md` for the contract.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
