# Implementation order — pengyos-motd

Each step ends with something that runs. Do not start a step before the previous
one's tests pass.

1. **Tool scaffold.** `pengyos_motd/` package, `pyproject.toml` (stdlib only,
   console script `pengyos-motd`), `tests/`, `conftest.py`, `verify.toml`
   (pytest in this directory), `LICENSE` (MIT, "Copyright (c) 2026 Pat Wendorf"),
   `README.md`, `CHANGELOG.md`. `pengyos-motd --version` works.
2. **The fact collector, injectable.** `collect_facts(probe=None)` returns a
   frozen structure: os-release fields, load, memory, uptime, disks (`/`,
   `/data`), addresses, update state, failed units. Every probe is a small
   function that either returns a value or records `unavailable: <reason>` —
   never raises. `--facts FILE` loads a captured JSON set instead.
3. **`pengyos-update status` parsing.** Three states plus the no-layout case.
   The real failure string to handle is
   `partition 3 of /dev/vda not found — this disk does not have the PengyOS A/B layout`
   (captured 2026-10-05 against a raw Server image in QEMU). No layout is not an
   error: the update block is omitted. A hard timeout on anything that could
   touch the network.
4. **Rendering as a pure function.** `render(facts, now=...)` → the text in the
   spec's order, blocks omitted when empty, total length bounded. Golden tests
   for the six cases in spec.md ("Verification").
5. **Mutation-check the suite.** For each promise, prove the test fails without
   it: a failed unit appears; a rolled-back update is named; the no-A/B case
   renders no update block and no traceback; a failing collector still renders
   the rest. A test that cannot fail is deleted or fixed, not kept.
6. **`write`.** Atomic (temp + rename) into `/run/motd.d/50-pengyos` by default,
   directory created, mode 0644, exactly one fragment. Tested against a temp dir.
7. **The units.** `pengyos-motd.service` (oneshot) + `pengyos-motd.timer`
   (`OnBootSec=10s`, `OnUnitActiveSec=15min`) as files under a `units/` dir, with
   a lint test that each names the real binary and the real fragment path.
8. **Core's path.** `/etc/motd` → symlink to the generated fragment, documented
   in `docs/state-layout.md` as an overlay state change, with the current authored
   text kept as the generator's starting fallback. Do not change Core's
   `UsePAM`/`PrintMotd` in this tool — that is a board decision, not a side effect.
9. **The update hook.** One call to `pengyos-motd write` on `pengyos-update`'s
   post-apply path. Land it as a separate commit with its own test, and keep it
   fail-soft: a missing `pengyos-motd` must not fail an update.
10. **Ground it.** Opt-in (`PENGYOS_IT=1`) integration against a real built tree:
    assert the shipped `pam_motd.so` really reads `/run/motd.d` (the strings check
    in spec.md's grounding table), and that a fragment written there shows up in a
    real SSH login to a booted guest — `pengyos-qemu-verify boot <image> --ssh
    --ssh-command 'cat /run/motd.d/50-pengyos'` is the shortest proof.

## Out of scope for this task
The console/getty surface (`/etc/pam.d/login` has no `pam_motd` line), `issue` /
`issue.net`, and anything requiring a TTY probe. Record them, do not half-fix
them.
