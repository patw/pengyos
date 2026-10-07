# pengyos-motd — a login message that says something true

## Summary
Generate the message of the day from the machine's actual state — edition and
image version, load, memory, disk, addresses, uptime, update state, and failed
units only when there are any — and write it where the image's existing login
path already looks. Stdlib only.

## Purpose
A login prompt is the first thing a new PengyOS machine says. Today it says a
static 240 bytes (`/etc/motd`, authored for Server) and nothing about whether
this box is healthy, what image it runs, or whether an update is waiting. Ubuntu
gets this right with `update-motd`; upstream PAM gets it right with a directory
of fragments. PengyOS should do it without Ubuntu's patch and without a network
round trip at login.

## Grounding (verified in the built 26.10.7 trees, 2026-10-05)

These are facts, not assumptions; each was checked against the shipped image.

| Fact | Where it was checked |
|---|---|
| Server's sshd is PAM-driven: `UsePAM yes`, `PrintMotd no` | `output/pengyos-server/target/etc/ssh/sshd_config` |
| Server's PAM stack already calls it: `session optional pam_motd.so` | `output/pengyos-server/target/etc/pam.d/sshd` |
| The shipped `pam_motd.so` is **upstream**, and upstream already supports a directory: its defaults are `motd=/etc/motd:/run/motd:/usr/lib/motd` and `motd_dir=/etc/motd.d:/run/motd.d:/usr/lib/motd.d` | `strings target/lib/security/pam_motd.so` |
| So no Ubuntu `update-motd` patch is needed: writing a fragment into `/run/motd.d/` is enough | same |
| Core's sshd is the other path: `UsePAM no`, `PrintMotd yes`, so it reads **only** `/etc/motd` | `output/pengyos-core/target/etc/ssh/sshd_config` |
| `/etc/pam.d/login` has no `pam_motd` line, so a console (getty) login is a **third** surface this tool does not yet cover | `output/pengyos-server/target/etc/pam.d/login` |
| `pengyos-update status` **fails** on a disk that has no A/B layout: "partition 3 of /dev/vda not found — this disk does not have the PengyOS A/B layout" (seen running against a raw Server image in QEMU over `pengyos-qemu-verify --ssh`) | 2026-10-05 |

The last row is the design constraint that matters most: **the update section has
three states, not two** — an update is available, an update is applied and
waiting for a reboot, or there is no A/B layout at all (a live stick, a freshly
written image). A generator that assumes the command succeeds is wrong on exactly
the images a new user boots first.

## Interface

```
pengyos-motd render [--facts FILE] [--now ISO8601]     # print the message to stdout
pengyos-motd write  [--out PATH] [--facts FILE]        # write it; default PATH /run/motd.d/50-pengyos
pengyos-motd facts  [--json]                           # the collected facts, for debugging
```

- `--facts FILE` injects a captured fact set (JSON) instead of probing the live
  system. This is what makes the suite deterministic and what lets a test assert
  the *rolled-back update* and *failed units* cases without a machine that has
  them.
- `write` creates the directory if needed, writes atomically (temp + rename),
  mode 0644, and never fails the login: any collector that errors contributes a
  line saying so and the rest still renders. Exit 0 unless the write itself
  failed.
- The fragment is capped so a pathological value cannot produce a 10 KB login
  banner; `pam_motd` itself refuses "too many motd files", so this tool writes
  **one** fragment, not several.

## What the message says

Order is fixed, and each block is omitted when it has nothing true to say:

1. `Welcome to PengyOS <Edition> <image version> (<build id>)` — from
   `/etc/os-release` (`PRETTY_NAME`, `IMAGE_VERSION`, `BUILD_ID`).
2. Docs line: `https://pengyos.catbee.ca/docs`.
3. Load average, memory used/total, uptime.
4. Disk: `/` and `/data` used/total. `/data` is the btrfs data subvolume; on an
   image without it, the block says so once rather than printing a phantom.
5. Addresses: the machine's routable interface addresses (skip `lo`, skip
   link-local).
6. Update state, one line, from `pengyos-update status`:
   - newer image on the feed → `update available: <version> (run pengyos-update apply)`
   - applied, awaiting reboot → `update <version> applied — reboot to start it`
   - last update rolled back → `last update was rolled back`
   - no A/B layout → **omit the block** (a live stick is not "broken")
   - feed unreachable → `update check: feed unreachable` (never a hang: a hard
     timeout on the network call, and the login must work offline)
7. Failed units — **only when there are any**, as `N units failed: <names>`. An
   empty `systemctl --failed` prints nothing. A login banner that always says
   "no problems" trains people to ignore the one time it matters.

## How it runs

- `pengyos-motd.service` — a `Type=oneshot` unit that runs `pengyos-motd write`.
- `pengyos-motd.timer` — `OnBootSec=10s`, `OnUnitActiveSec=15min`,
  `Persistent=false`. The message is a snapshot, not a live feed; 15 minutes is
  cheap and keeps it honest without a daemon.
- After an update: `pengyos-update`'s post-apply path (or its check step) runs
  `pengyos-motd write` once so the next login reflects the new state even before
  the timer does. Wiring that call is part of this tool's deliverable, and it is
  the only place this tool touches another tool's flow.
- **Core** has no PAM, so `PrintMotd yes` reads `/etc/motd`. Deliver the Core
  case as `/etc/motd` → a symlink to the generated fragment, and keep the
  current authored text as the fallback the generator starts from (Core's
  `/etc/motd` is Tier-1 overlay content, so the symlink is a state change to
  document in `docs/state-layout.md`, not just a file write).

## Non-goals
- Colour, ASCII art, or anything that needs a TTY probe.
- A network fetch at login. Everything here is local; the update line comes from
  a command that already has its own cached state.
- Replacing `issue`/`issue.net` (the pre-auth banner). Different file, different
  rules — `issue` is shown before authentication and must not leak state to an
  unauthenticated reader.
- The console/getty surface (`/etc/pam.d/login` has no `pam_motd` line today).
  Note it, do not silently half-fix it.

## Verification (tests are first-class)
- Rendering is a pure function of the fact set. Golden text for: a healthy
  Server, a Desktop with no `/data`, a machine with an update waiting, a machine
  whose last update rolled back, a machine with two failed units, and a machine
  with **no A/B layout** (the `pengyos-update status` failure above must not
  produce a traceback or a false "update check failed").
- Mutation checks, one per promise: a failed unit must appear; a rolled-back
  update must be named; removing the failed-units branch must make the suite
  fail; a collector that raises must still render the rest.
- `write` is tested against a temp dir: atomic rename, 0644, directory created,
  exactly one fragment.
- The units are text fixtures with a lint that they name the real binary and the
  real fragment path.
- Grounded, not invented: the shipped `pam_motd.so` behaviour above is asserted
  against a real built tree in an opt-in integration test (`PENGYOS_IT=1`), not
  assumed.
