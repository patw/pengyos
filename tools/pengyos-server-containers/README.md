# pengyos-podman

**PengyOS server-containers — rootless podman + declarative services (US-SRV-2).**

A tiny, standard-library-friendly Python CLI that turns a small `service.yaml`
into a **reboot-safe rootless `podman` `systemd --user` unit** with published
ports and persistent volumes.

> USE-CASES US-SRV-2: a container service starts on boot, survives reboot, is
> reachable on the LAN, and its data persists across image updates.

`podman` and `systemd` are **never executed** by this tool — it only parses,
validates and emits text. See `spec.md`.

## Install

```bash
pip install -e .            # from this repo
# or, for tests only:
pip install -e '.[test]'
```

Requires Python 3.11+ (uses the stdlib `tomllib`). Runtime dependency:
[PyYAML](https://pyyaml.org/).

## Usage

```bash
# Validate a service definition (clean errors, no tracebacks):
pengyos-podman validate --service samples/nginx.yaml

# Emit the rootless podman systemd --user unit into ./units/:
pengyos-podman gen --service samples/nginx.yaml --out units/

# Print the parsed service as JSON:
pengyos-podman render --service samples/nginx.yaml --json

# Check the host is ready for rootless podman (report only, never modifies it):
pengyos-podman check
```

Equivalent module form: `python -m pengyos_podman ...`.

### Generated unit

`gen` writes `<name>.quadlet` — a rootless podman **Quadlet** (a `systemd --user`
unit source, the modern replacement for `podman generate systemd`). The
`[Container]` section drives the container: `Image=` selects the image and
`PodmanArgs=` carries the `podman run` options (`--rm`, `--name`, `--user`,
`--publish`, `--volume`, `--env`). The `[Service]` section holds the lifecycle
(`ExecStartPre` / `ExecStart=podman run` / `ExecStop` / `Restart=`) and
`[Install]` the `WantedBy=default.target` boot hook, so the service starts on
login/boot and survives reboots.

`PodmanArgs=` is the one documented podman systemd key that maps straight onto
`podman run` options, and it is only valid inside a `[Container]` section of a
`.container` file — that is why a Quadlet is emitted rather than an inline
`ExecStart=podman run …` `.service`.

To use it, point podman at the output directory and enable the unit:

```bash
export QUADLET_UNIT_DIRS=$PWD/units
systemctl --user daemon-reload
systemctl --user enable --now nginx.quadlet
```

## Validation

`validate` runs two layers and exits non-zero (with a clean message, no
traceback) on the first *fatal* failure:

1. **Schema** (structural) — required fields present, `restart` in the valid
   `Restart=` enum, and every `ports`/`volumes`/`env` shaped correctly.
2. **Policy** (semantic, US-SRV-2 guarantees):
   - **Reachability** — a reachable service publishes at least one port. Set
     `reachable: false` for non-network services (workers, cron, batch jobs)
     that legitimately publish no ports — the escape hatch.
   - **Volume persistence** — every volume's *host* source is a real host
     directory (not a path inside the image), so data survives image updates.
     (Never exempted by `reachable: false`.)

`--strict` turns the volume-persistence rule into a fatal error too; reachability
is always fatal. By default volume-persistence is **advisory**: `validate`/`gen`
print a `policy warning:` line (and `gen` still writes the unit) but exit `0`.

A fatal policy failure exits `1` with a `policy error:` line; a schema failure
exits `1` with a `validation error:` line; a load failure (missing file, bad
YAML) exits `2`.

## Host check

Rootless podman needs the *current user* to have a subordinate UID and GID range
and a container storage configuration. `check` probes the host for exactly those
three prerequisites and prints an aligned report (exit `0` always — a missing
prerequisite is a reported finding, not an error):

```text
$ pengyos-podman check
Rootless podman host prerequisites (report only — nothing modified)
==================================================================
subuid      : OK
    subordinate UID range configured
    alex:100000:65536
subgid      : OK
    subordinate GID range configured
    alex:100000:65536
storage.conf: MISSING
    no container storage configuration found
    checked: ~/.config/containers/storage.conf, /etc/containers/storage.conf
    create one, e.g. ~/.config/containers/storage.conf (see podman-storage.conf(5))
```

`check` never runs podman/systemd and never modifies the host. It inspects
`/etc/subuid` + `~/.config/subuid` (subuid), `/etc/subgid` + `~/.config/subgid`
(subgid), and the standard `storage.conf` locations, falling back to `getent`
when the files are absent.

## `service.yaml` schema

| Field     | Required | Description                                                        |
|-----------|:--------:|--------------------------------------------------------------------|
| `name`    | yes      | Service identity; used as the container name and unit filename.    |
| `image`   | yes      | Container image reference.                                         |
| `user`    | no       | UID/GID the container runs as (rootless podman user).              |
| `restart` | no       | systemd `Restart=` value (`on-failure` by default).                |
| `ports`   | no       | Published ports, `"<host>:<container>"` (host side optional).      |
| `volumes` | no       | Bind mounts, `"<host>:<container>"`. Host paths persist data.      |
| `env`     | no       | Environment variables exported into the container.                 |
| `reachable` | no     | `true` by default. Set to `false` for non-network services (workers,   |
|           |          | cron, batch jobs) that legitimately publish no ports — exempts the    |
|           |          | reachability policy (the "escape hatch").                            |

See [`samples/nginx.yaml`](samples/nginx.yaml) for a worked example.

## Development

```bash
pytest -q          # run the suite
```

Tests need no network, root, or real hardware. System integration is opt-in via
`PENGYOS_IT=1` (not yet implemented).

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
