# pengyos-install

The edition-agnostic install engine for [PengyOS](../../README.md).

**`install = first update`.** One engine that lays a signed release onto a blank
disk: `(release, target, layout) -> an ordered plan -> execute via an injectable
runner`, writing the rootfs to slot A, the UKI to the ESP, the loader entries,
the data partition, and first-boot provisioning — the same artifact the updater
consumes.

This package is **standard-library only** at runtime. TOML parsing ships with
Python 3.11+; a tiny shim (`pengyos_install._toml_compat`) backfills
`tomllib` on 3.10.

## Design

The engine is split into three units:

- **`plan`** (pure): `release` manifest + `layout` + `install.toml` -> an ordered
  list of steps, **every step a shell-quotable argv**. Deterministic / golden.
- **`install`** (execute): runs the plan through an injectable
  [`Runner`](src/pengyos_install/runner.py). Refuses without `--yes`, re-checks
  the target each run, hard-fails on a required step, and tears down (unmount +
  sync) on success **and** on failure. The write of slot A is atomic: write the
  inactive slot, then commit.
- **`provision-plan`**: renders the first-boot steps (fstab, hostname, users /
  SSH keys, network, timezone) from `install.toml`, pinning exact file content.

Everything that touches the outside world goes through the `Runner` seam:
production uses `SubprocessRunner`; tests swap in `FakeRunner` to assert on the
exact argv and to avoid needing root, a real device, network, or hardware.

## Install

```bash
pip install -e .
# dev/test extras:
pip install -e ".[test]"
```

Or run straight from the tree (no install needed; `conftest.py` puts `src` on
`sys.path` for tests):

```bash
python -m pengyos_install --help
```

## CLI

```
pengyos-install plan --release rel.json --layout plan.json [--config install.toml] [--json]
pengyos-install install --release rel.json --layout plan.json [--config install.toml] --yes [--runner CMD]
pengyos-install provision-plan --config install.toml [--layout layout.json] [--slot a] [--json]
pengyos-install disks [--json]
pengyos-install image --target DEV --hostname H --user U --yes [--payload DIR]
                      [--password-stdin] [--authorized-keys FILE]
                      [--progress text|json]
```

> **Status — PARKED.** `plan` / `install` / `provision-plan` target the parked A/B + UKI release
> (see `docs/install-update-architecture.md`), which no build produces yet, and
> their argv is not executable against a real disk (overlapping `0% 100%`
> partitions, `dd` of a `.zst` onto a partition, writes to paths *under* a block
> device, an `install-bootctl` that does not exist, a `signature.verified` flag
> that is trusted rather than checked). **`disks` + `image` are the install path
> the live medium runs today.**

- **`disks`** — list the whole disks this machine could install to: path, size,
  model, removable, and whether it is the disk the running system (or the
  install payload) lives on — that one is never installable. `--json` for
  front-ends (the GUI and TUI both use it).
- **`image`** — install the live medium's payload onto `--target`, erasing it,
  in the partitioned layout of `docs/state-layout.md` (ESP 256 MiB, root-a and
  root-b 8 GiB each, data = the rest). The payload (`manifest.json` format
  `pengyos-layout-1`) holds the release's `rootfs.ext4.zst` and a ready
  `esp.vfat.zst` (GRUB + `slot-a/bzImage`). Steps: verify each file's sha256,
  partition, stream the ESP and the rootfs into place (checking the raw
  sha256s), grow root-a to its slot with a fresh UUID, write a `grub.cfg` for this
  disk's PARTUUIDs + `grubenv` (`pengyos_slot=a`), format data and seed `/var`
  and `/root` from the image, then — through the same `/etc` overlay and binds
  the booted system uses — set the hostname, create `--user` (in `wheel`,
  `sudo`, `seat`, `video`, `audio`, `input`, `render` where the image has them)
  and — on a Desktop image, only with `--autologin` ("Log in automatically";
  off by default, so the desktop starts at its login screen) — make the
  desktop log them in at boot (`/etc/pengyos/autologin.conf`, changeable later
  with `pengyos-autologin`), give them the SSH public keys in
  `--authorized-keys FILE` (the Server's sshd accepts keys only), set the user's and root's password (random salt each), and copy the
  live session's NetworkManager profiles (e.g. the Wi-Fi joined on the stick). Any SSH host keys in the image are
  dropped; each machine makes its own on first boot. The password is read from
  stdin with `--password-stdin`, or prompted for on a terminal — never taken on
  the command line. `--payload DIR` overrides the payload location (default
  `/run/pengyos/payload`). `--progress json` emits one `{"phase", "fraction",
  "message"}` object per line on stdout (phases `verify`, `partition`, `write`,
  `root`, `boot`, `provision`, `done`, or `error`). Refuses without `--yes`, and
  refuses the boot disk, a disk with mounted filesystems, or one under 18.25 GiB.

- **`plan`** — render the ordered install plan (one argv per step). `--json`
  emits JSON instead of a shell script. The trailing first-boot step points at
  `--layout`/`--slot` so the fstab the first boot renders lists the right
  partitions.
- **`install`** — execute the plan against the target. `--yes` acknowledges the
  destructive write (refused without it). `--runner CMD` injects an external
  runner.
- **`provision-plan`** — render the first-boot provisioning steps (fstab,
  hostname, users/SSH keys, network) from `install.toml`, pinning exact file
  content. `--layout` supplies the fstab partition paths, `--slot` which root
  the fstab lists first (default `a`); `--json` emits JSON instead of a shell
  script.

The only console script is `pengyos-install` (the subcommands are not exposed
as separate scripts — a script named `install` would shadow coreutils'
`install` on `PATH`).

## Layout

```
src/pengyos_install/    the package (stdlib only)
  __init__.py           version
  runner.py             injectable Runner / FakeRunner / RunResult
  cli.py                argparse + dispatch
  models.py             validated data models (Release / Layout / Step / Plan /
                        ProvisionConfig) — the data layer of the engine
  plan.py               the pure install-plan renderer (release + layout ->
                        ordered, shell-quotable-argv steps; golden)
  provision.py          the first-boot renderer: render the first-boot steps
                        (fstab / hostname / users+ssh / network) from
                        install.toml, pinning exact file content
  install.py            the install engine: run the plan through the Runner
                        seam — refuse without `--yes`, re-check the target,
                        hard-fail on a step, tear down (unmount + sync) on
                        success **and** failure
  _toml_compat.py       tomllib backfill for Python 3.10
tests/                  pytest suite (no network / root / hardware)
conftest.py             src path setup + PENGYOS_IT opt-in integration fixture
```

## Tests

```bash
pytest -q
```

The default suite needs neither network, root, a real display, nor hardware.
Real end-to-end integration is opt-in:

```bash
PENGYOS_IT=1 pytest -q
```

## Non-goals

- Writing to a real device at test time.
- The wizard UX (a separate TUI/GUI task).

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
