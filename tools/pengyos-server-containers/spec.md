# PengyOS server-containers — rootless podman + declarative services (US-SRV-2)

## Summary
Rootless podman configuration plus a declarative 'container service' generator: a small service.yaml becomes a reboot-safe systemd --user unit with published ports and persistent volumes.

## Purpose
USE-CASES US-SRV-2: a container service starts on boot, survives reboot, is reachable on the LAN, and its data persists across updates.

## Deliverable
A Python CLI `pengyos-podman` with `gen --service service.yaml --out units/` (emit a rootless podman systemd user unit + volume declarations), `validate`, and `render --json`; podman/systemd are never executed in tests.

## Interface
- pengyos-podman gen --service service.yaml --out units/
- pengyos-podman validate --service service.yaml
- pengyos-podman render --service service.yaml --json
- pengyos-podman check — report on the rootless-podman host prerequisites
  (subuid / subgid ranges + a `storage.conf`). Report-only.

## Host prerequisite check (report-only)

Rootless podman needs the *current user* to have a subordinate UID and GID range
and a container storage configuration. `check` probes the host for exactly those
three prerequisites and prints an aligned report; it **never runs podman/systemd
and never modifies the host**.

The three prerequisites (documented here so they stay frozen / golden-testable):

1. **subuid** — a subordinate UID range for the user, from `/etc/subuid` or
   `~/.config/subuid` (or `getent subuid <user>`).
2. **subgid** — a subordinate GID range for the user, from `/etc/subgid` or
   `~/.config/subgid` (or `getent subgid <user>`).
3. **storage.conf** — a container storage configuration, found under
   `~/.config/containers/storage.conf` or `/etc/containers/storage.conf`.

`check` exits `0` always — a missing prerequisite is a reported finding, not a
code error (so it is safe to wire into pipelines as a diagnostic). Each
prerequisite reports `OK` / `MISSING` with the range(s) found or the paths
inspected.

Every probe is injectable (a `read_file` callable and a command `run` callable,
plus the path lists), so the check is fully testable with fixtures and needs no
root, network, or real podman. The optional `getent` fallback is used only when
the subuid/subgid files are absent (and is tolerated as "no entry" when
`getent`/its database is unavailable, as on minimal images).

## Generated unit

`gen` emits a rootless podman **Quadlet** `.container` file named `<name>.quadlet`
(a `systemd --user` unit source, the modern replacement for
`podman generate systemd`). It is deterministic and golden-testable and contains,
at minimum, these components: `[Unit]`, `[Container]`, `[Service]`, `[Install]`,
`PodmanArgs=`, `Image=`, `--publish`, `--volume`, `Restart=`,
`WantedBy=default.target`.

The podman options (`--rm`, `--name`, `--user`, `--publish`, `--volume`,
`--env`) live on the documented `PodmanArgs=` key inside `[Container]` (with
`Image=`); the `[Service]` section holds the `ExecStartPre` / `ExecStart=podman
run` / `ExecStop` / `Restart=` lifecycle. `PodmanArgs=` is only a valid, loadable
podman systemd directive inside a `[Container]` section of a `.container` file,
which is why a Quadlet is emitted rather than an inline
`ExecStart=podman run …` `.service`.

`User=` is emitted into `[Service]` when set. It is a valid systemd key; note
that podman's rootless Quadlet mode runs as the logged-in user, so the effective
container user is governed by that, not by `User=` (documented caveat).

## Semantic policy (validation layer 2)

`validate` runs two layers. Layer 1 (structural, item 2) checks field shapes and
enums. Layer 2 (semantic policy, item 4) enforces the US-SRV-2 guarantees and is
raised as a distinct `PolicyError` (separate from the structural `SchemaError`):

1. **Reachability** — a *reachable* service must publish at least one port. A
   service with no published ports cannot be reached on the LAN, which is the
   whole point of US-SRV-2 ("reachable on the LAN"). This is **always fatal**:
   `validate` and `gen` exit 1. **Opt-out**: a service with `reachable: false`
   (the new optional field, default `true`) is a non-network service (worker,
   cron, batch job) that legitimately publishes no ports, so the reachability
   rule is skipped for it. This resolves the "where declared" ambiguity of item 4.
2. **Volume persistence** — the *host* side of every volume must be a real host
   data directory, not a path inside the image/container. A bind mount whose
   source is a known image/system directory (`/usr/share`, `/etc`, `/lib`,
   `/bin`, … — see `IMAGE_SYSTEM_PATHS`) is almost certainly a mistake (the user
   meant to reference a container path) and that source is not persistent (it is
   replaced when the image is updated). This rule is **advisory by default** (a
   `policy warning:` line; `validate`/`gen` still exit 0 and `gen` still emits
   the unit) and only **fatal under `--strict`**. This rule is **never**
   exempted, even for `reachable: false` services.

The two rules are deliberately separate so each has its own failing fixture and
a distinct severity. `gen` and `validate` run layer 1 then layer 2; a *fatal*
policy failure prints a clean "policy error" line and exits 1 without emitting
anything.

### `--strict` (item 6)

`validate` and `gen` accept a `--strict` flag. In `--strict` mode the
volume-persistence rule is also **fatal** (a `policy error:`, exit 1, nothing
emitted); reachability is fatal in both modes. By default (non-strict)
volume-persistence is advisory, so a service that mis-mounts under an image path
still emits a correct unit but is warned about it — the operator opts into
treating it as a hard error.

### `reachable` field

`reachable` is an optional boolean (`true` by default). `reachable: false` is
the escape hatch that exempts a service from the reachability rule. The loader
parses it permissively (accepting `true`/`false`, `1`/`0`, `yes`/`no`, `on`/`off`
— case-insensitive) and rejects an ambiguous value with a clean load error
(exit 2). It roundtrips through `render --json`.

## Verification (tests are first-class)
- golden unit for a sample service; deterministic output.
- invalid schema -> clean error (no traceback).
- published-port and external-volume validation rules each have a failing fixture.
- semantic policy rules (reachability, volume persistence) each have a failing
  fixture and a distinct error type (`PolicyError` vs `SchemaError`).

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

`test_github_ready` proves the repo is genuinely GitHub-ready two ways: it
asserts each required artifact is present with the right metadata, and it runs the
whole suite (minus itself) in a subprocess so a green run is proof. Beyond mere
presence, it enforces that the docs stay *in sync* with the code (the "keep them
in sync" clause) via cross-consistency checks:

- **LICENSE is the full canonical MIT body** (the `Permission is hereby
  granted…` / `THE SOFTWARE IS PROVIDED…` / `AUTHORS OR COPYRIGHT HOLDERS BE
  LIABLE…` fragments), not a bare "MIT" stub.
- **The LICENSE copyright holder matches the author(s) declared in
  `pyproject.toml`** (currently "Pat Wendorf").
- **The CHANGELOG documents `pyproject.toml`'s current version** (`0.1.0`).
- **The README documents every CLI subcommand** — the subcommand names are read
  from the real argparse parser (`build_parser`), so a command added to code but
  not documented in the README is caught.
- **`.gitignore` ignores the emitted `.quadlet` units** (`/units/` + `*.quadlet`),
  so a stray generated unit can never be committed.

## Acceptance criteria
- A service.yaml yields a correct, reboot-safe rootless user unit.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Running podman
- Building images

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
