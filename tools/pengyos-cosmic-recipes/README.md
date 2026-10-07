# pengyos-cosmic-recipes

Manifest-driven [Buildroot](https://buildroot.net) cargo recipes for the
[COSMIC](https://pop-os.github.io/cosmic/) desktop components, built on top of
the **pengyos-cosmic-pkg** engine, with a dependency-closure linter.

SPEC §Summary: PengyOS is an operating system an agent can operate, so the
Desktop edition ships the whole COSMIC stack (compositor, shell, apps) from
source. This repo turns a component manifest into pinned, lint-clean Buildroot
recipes — each COSMIC component is *data*, not a bespoke port.

## What this repo is

A **recipes lane** over the pengyos-cosmic-pkg engine (`pengyos_cosmic_pkg`):

- **manifest** — load the COSMIC component manifest (`cosmic.toml`) and list it,
  grouped (`core` | `login` | `shell` | `apps`), optionally as JSON. *Implemented.*
- **emit** — drive the engine to emit a Buildroot cargo package
  (`Config.in` / `<pkg>.mk` / `<pkg>.hash`) for every component in a group.
  *Implemented.*
- **lint** — close the dependency loop: assert every component and every
  inter-component dependency has a lint-clean Buildroot recipe (the *closure
  linter*). *Implemented.*
- **non_cargo** — list the non-cargo components the recipes lane recognises and
  the *separate handling path* each follows (a non-cargo Buildroot recipe in a
  different lane, out of scope for the cargo recipes lane). Lists by default;
  `non_cargo emit --out DIR` authors the non-cargo Buildroot recipe for
  `cosmic-workspaces` (a GNOME shell-extension install path) into `--out`.
  *Implemented.*

All four subcommands are wired into the CLI (`pengyos-cosmic-recipes`); each
supports `--json`.

The engine itself owns the three primitives the recipes lane composes:

| Engine subcommand | Module | Responsibility |
|-------------------|------------------|----------------|
| `vendor` | `pengyos_cosmic_pkg.vendor` | deterministic vendored source tree (rewrites git deps) |
| `recipe` | `pengyos_cosmic_pkg.recipe` | emit the Buildroot cargo package (version + license pinned) |
| `lint` | `pengyos_cosmic_pkg.lint` | hash present, version pinned, license declared, no floating refs |

See the engine's [`spec.md`](../pengyos-cosmic-pkg/spec.md) for its contract.

## Dependency: the pengyos-cosmic-pkg engine

This repo has exactly one external dependency: the sibling
**pengyos-cosmic-pkg** engine. It is **not** pip-installed — it is imported
from its own source tree, which keeps the suite hermetic (no network, no
install step).

`conftest.py` puts the engine's `tools/pengyos-cosmic-pkg` directory on
`sys.path` so `import pengyos_cosmic_pkg` resolves to the pinned engine source.
Resolution prefers the engine's own workspace monorepo, then an already
importable (pip/pipx) copy, then an in-tree sibling — see the docstring in
[`conftest.py`](conftest.py).

The engine is standard-library-only at runtime, so this repo is too: no third
party dependencies at test time.

## Layout

```
pengyos_cosmic_recipes/   the recipes package (manifest data + CLI)
tests/                    pytest suite (engine import, scaffold, manifest, …)
conftest.py               repo root on sys.path + the engine import
cosmic.toml               the COSMIC component manifest (core + login + shell + apps)
```

### The manifest: `cosmic.toml`

`cosmic.toml` is the single source of truth for the recipes this repo authors:
one `[[component]]` per COSMIC git-Rust component, each pinned to a concrete
reference (a commit SHA or a `v`-prefixed version tag — never a floating
branch), with its license and its inter-component dependencies (declared by
*name*). It is loaded and validated by
[`pengyos_cosmic_recipes.manifest`](pengyos_cosmic_recipes/manifest.py), which
exposes it grouped (`core` | `login` | `shell` | `apps`) and expands each
component's name-only deps to the target's `git_url` / `tag` before handing it
to the engine's `recipe` builder (so the emitted recipe pins the same reference
the manifest declares).

The **core group** (a bootable COSMIC session) is committed:

| Component          | Pinned reference            | License       | Deps                     |
|--------------------|-----------------------------|---------------|--------------------------|
| `cosmic-protocols` | `c0cff4d…` (commit on `main`) | MIT           | —                        |
| `libcosmic`        | `v0.12`                     | MPL-2.0       | `cosmic-protocols`       |
| `cosmic-comp`      | `a557859…` (epoch-1.8.0)    | GPL-3.0-only  | `libcosmic`, `cosmic-protocols` |
| `cosmic-session`   | `3bdf117…` (epoch-1.8.0)    | GPL-3.0-only  | —                        |

> `cosmic-comp` / `cosmic-session` ship `epoch-*` tags, which the shared
> `is_pinned` predicate does not accept, so they are pinned to the epoch-1.8.0
> commit SHA. `cosmic-protocols` has no version tag, so it is pinned to a
> concrete commit on `main`.

The **login group** provides the login/greeter:

| Component        | Pinned reference          | License     | Deps             |
|------------------|---------------------------|-------------|------------------|
| `cosmic-greeter` | `ff31b59…` (epoch-1.8.0)  | GPL-3.0-only| `libcosmic`      |

`cosmic-greeter` is the greetd greeter (it speaks the agreetd protocol that the
`greetd` login daemon drives, and runs inside `cosmic-comp`). It is built on the
`libcosmic` crate; `greetd` itself is a Buildroot package
(`BR2_PACKAGE_GREETD`), a non-Rust system dep out of scope for this recipes lane.
The login group therefore closes only once the core group's recipes are present
(cosmic-greeter → libcosmic).

The **shell group** is the interactive shell: panel, launcher, applets,
settings (+daemon), osd, notifications, background, icons, idle and randr.
Every component is pinned to the epoch-1.8.0 commit SHA (the `epoch-*` tag is
not pinned), ships its cargo binary(ies), and declares its inter-component deps
by name.

| Component                 | Pinned reference        | License          | Deps                                              |
|---------------------------|-------------------------|------------------|---------------------------------------------------|
| `cosmic-panel`            | `f664a34…` (epoch-1.8.0)| GPL-3.0-only     | `libcosmic`, `cosmic-protocols`                   |
| `cosmic-launcher`         | `9d649e2…` (epoch-1.8.0)| GPL-3.0-only     | `libcosmic`                                       |
| `cosmic-applets`          | `b933e53…` (epoch-1.8.0)| GPL-3.0-only     | `libcosmic`, `cosmic-protocols`                   |
| `cosmic-bg`               | `1685f7f…` (epoch-1.8.0)| MPL-2.0          | `libcosmic`                                       |
| `cosmic-randr`            | `6e8e795…` (epoch-1.8.0)| MPL-2.0          | —                                                 |
| `cosmic-settings`         | `3cb538f…` (epoch-1.8.0)| GPL-3.0-only     | `libcosmic`, `cosmic-randr`, `cosmic-bg`, `cosmic-comp`, `cosmic-idle`, `cosmic-panel` |
| `cosmic-settings-daemon`  | `67c995d…` (epoch-1.8.0)| GPL-3.0-or-later | `libcosmic`, `cosmic-comp`                        |
| `cosmic-osd`              | `aee6ee9…` (epoch-1.8.0)| GPL-3.0-or-later | `libcosmic`, `cosmic-comp`, `cosmic-randr`, `cosmic-settings-daemon` |
| `cosmic-notifications`    | `8486bd6…` (epoch-1.8.0)| GPL-3.0-only     | `libcosmic`, `cosmic-panel`                       |
| `cosmic-idle`             | `c95d066…` (epoch-1.8.0)| GPL-3.0-only     | `libcosmic`, `cosmic-settings-daemon`             |
| `cosmic-icons`            | `343c007…` (commit)     | CC-BY-SA-4.0     | —                                                 |

`cosmic-applets` is a workspace of applets and forwards all of them as cargo
binaries; `cosmic-icons` is a binary-less icon theme. The shell graph is
cross-referenced (the settings/build order respects it), so the shell group
closes only once the core group's recipes are present.

The **apps group** is the COSMIC application set: cosmic-files, cosmic-edit,
cosmic-term, cosmic-store, cosmic-screenshot and cosmic-player. Each is a Rust
cargo component pinned to its epoch-1.8.0 commit SHA, GPL-3.0-only, ships its
cargo binary, and declares its inter-component deps by name.

| Component            | Pinned reference        | License      | Deps                                         |
|----------------------|-------------------------|--------------|----------------------------------------------|
| `cosmic-files`       | `089ad2b…` (epoch-1.8.0)| GPL-3.0-only | `libcosmic`, `cosmic-protocols`              |
| `cosmic-edit`        | `26f6ffd…` (epoch-1.8.0)| GPL-3.0-only | `cosmic-files`, `libcosmic`                  |
| `cosmic-term`        | `4f0f0b6…` (epoch-1.8.0)| GPL-3.0-only | `cosmic-files`, `libcosmic`                  |
| `cosmic-store`       | `b8e6a64…` (epoch-1.8.0)| GPL-3.0-only | `cosmic-panel`, `libcosmic`                  |
| `cosmic-screenshot`  | `5ddcf18…` (epoch-1.8.0)| GPL-3.0-only | —                                            |
| `cosmic-player`      | `a297c24…` (epoch-1.8.0)| GPL-3.0-only | `libcosmic`                                  |

The app graph is cross-referenced too: cosmic-edit and cosmic-term build on
cosmic-files; cosmic-store builds on the shell's cosmic-panel. So the apps group
closes only once the core and shell recipes are present.

> `cosmic-workspaces` — the task list names it among the apps, but
> `pop-os/cosmic-workspaces` is a **JavaScript** GNOME Shell extension (no Rust,
> no `Cargo.toml`, no `is_pinned`-acceptable tag), not a cargo component. It has
> no place in this Buildroot *cargo* recipes manifest.

### Non-cargo components — the separate handling path

The recipes lane authors Buildroot *cargo* recipes for Rust components only.
`cosmic-workspaces` is named among the apps but is a JavaScript GNOME Shell
extension — not a cargo component. Its handling is **explicit and positive**,
not an accident of absence:

- `pengyos_cosmic_recipes/non_cargo.py` **registers** it (and any future
  non-cargo component) with the kind of non-cargo recipe it needs and the lane
  that owns it.
- The recipes lane **recognises** it by name (`is_non_cargo`, `recipe_kind`).
- The manifest loader **rejects** it from the cargo manifest — adding it to
  `cosmic.toml` now fails loudly with a `ManifestError`, so the exclusion is
  structural.
- The `non_cargo` CLI subcommand lists every registered non-cargo component and
  its handling path.

The actual non-cargo Buildroot recipe (a GNOME shell-extension install path:
`GNOME_EXTENSION_UUID`, `metadata.json`, `extension.js`, gschema) is authored by
`pengyos_cosmic_recipes/non_cargo_recipe.py` in a separate non-cargo lane —
still out of scope for the cargo recipes lane, but now written and emitted via
`non_cargo emit --out DIR`. It installs the extension files under the GNOME shell
extension directory (keyed by the extension UUID) and the gschema under glib's
schema directory, and is validated against Buildroot's own Kconfig parser.

## Emit + lint

The two recipes-lane subcommands compose the engine's primitives into the
workflow the manifest needs:

```sh
# 1. emit the core group's recipes into a Buildroot package/ directory.
pengyos-cosmic-recipes emit --out buildroot/package --group core

# 2. close the loop: every component + every inter-component dep has a recipe.
pengyos-cosmic-recipes lint --dir buildroot/package
```

`emit` expands each component's name-only deps to the target's `git_url` /
`tag` (via the manifest loader) and hands the single-component manifest to the
engine's `recipe` builder, so the emitted recipe pins exactly the reference the
manifest declares. `lint` runs the engine's `lint_dir` on each recipe and
additionally asserts every component and every inter-component dependency has a
recipe on disk — a missing recipe or an unclosed dep is a hard failure (exit 1).

### The digest seam

The engine's `recipe` builder pins the sha256 of the *vendored* source tarball
(the digest the `vendor` engine computes). Vendoring needs the git sources over
the network and is out of scope for the recipes lane, so `emit` takes an
optional `--hash HEX` (a supplied digest is pinned verbatim). Without it, each
recipe gets a deterministic placeholder digest (`0` × 64 hex) — well-formed and
lint-clean (the lint rule only requires the `.hash` file to be present) — and
every such component is flagged in the report (`missing_digest`) and in the
`--json` output (`placeholder_hash: true`), so a later `emit --hash …` with the
real vendored digest is unambiguous.

## Verification

The suite is stdlib-only and needs no network, root, display, or hardware:

```sh
python3 -m pytest -q
```

Real system integration (emitting real recipes, running the closure linter over
the full manifest, building) is out of scope and opt-in behind
`PENGYOS_IT=1`.

## Docs

- [`spec.md`](spec.md) — the contract (deliverable, interface, verification).

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
