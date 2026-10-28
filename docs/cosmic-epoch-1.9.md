# Moving COSMIC to epoch-1.9 — what is actually pinned, and what breaks

**Status:** done. The move shipped in 26.10.8, and behopc runs COSMIC 1.9 as its
daily desktop. Kept as the method for the next epoch bump: the libcosmic
coherence check and the commands at the end are what to rerun.

Written 2026-10-05, from the live repos, to support the decision rather than
describe it. Every number below came from a command; the commands are at the end.

## The headline

epoch-1.9.0 exists for **all 26** of the COSMIC repos we pin that carry
`epoch-*` tags (three of the 29 rows — cosmic-workspaces, cosmic-protocols,
libcosmic — have no such tags). We are at epoch-1.8.0 for 14 of them. The jump is
small per repo — cosmic-comp is 8 commits / 11 files between the two tags — but it
is not one change, it is 26 pins, and the failure mode we already hit once (1.9
greeter on a 1.8 compositor, no wallpaper) is not a version-number mismatch. It is
a **libcosmic mismatch**, and libcosmic is not pinned the way the backlog assumes.

## What we pin today, and what is actually true

`scripts/cosmic-components.tsv` (the host-built path, the one that ships) pins
exact commits. Checked against upstream tags:

| Component | Pinned at | Newest tag | |
|---|---|---|---|
| cosmic-comp, cosmic-panel, cosmic-applets, cosmic-launcher, cosmic-settings, cosmic-settings-daemon, cosmic-osd, cosmic-term, cosmic-files, cosmic-edit, cosmic-store, cosmic-player, cosmic-greeter, cosmic-icons | `epoch-1.8.0` | `epoch-1.9.0` (greeter: `epoch-1.9.1`) | behind |
| cosmic-session, cosmic-notifications, cosmic-screenshot, pop-launcher | `epoch-1.7.0` | `epoch-1.9.0` | behind |
| cosmic-bg | `epoch-1.6.0` | `epoch-1.9.0` | behind |
| cosmic-idle, cosmic-randr | `epoch-1.0.10` | `epoch-1.9.0` | behind |
| cosmic-sound-theme | `epoch-1.4.0` | `epoch-1.9.0` | behind |
| cosmic-app-library, cosmic-workspaces-epoch, cosmic-viewer, cosmic-monitor | `epoch-1.9.0` | `epoch-1.9.0` | **at newest** |
| cosmic-workspaces, cosmic-protocols, libcosmic | commits | no `epoch-*` tags | not tag-pinned |

So the set is not "1.8 with two 1.9 strays" — it is 1.8, 1.7, 1.6, 1.4, 1.0.10
and 1.9 in the same image. That is already the mixing hazard, held together by
each component's own `Cargo.lock`.

## The thing the backlog gets wrong, and it matters

The TODO says "move COSMIC to epoch-1.9 as a whole". The tag is not the pin that
decides what gets built. **Each component's `Cargo.lock` carries its own libcosmic
rev**, and libcosmic has no `epoch-*` tags at all. Our current image builds these
components against these libcosmic commits:

| Component | libcosmic its own Cargo.lock resolves to | that commit's date |
|---|---|---|
| cosmic-comp | `d922ac38` | 2026-07-01 |
| cosmic-panel | `20dfd7c` | 2026-07-01 |
| cosmic-greeter | `8cb10bf` | 2026-07-09 |
| cosmic-settings | `2a73fbc` | 2026-08-25 |
| cosmic-applets | `d4d71fd5` | — |

At `epoch-1.9.0` upstream, cosmic-comp still resolves to `d922ac38` (unchanged)
but **cosmic-greeter moves to `87ab817` (2026-09-15)**. That gap — a greeter
built against a September libcosmic talking to a compositor built against a July
one — is exactly the wallpaper failure we already saw.

Two consequences:

1. `libcosmic` in `cosmic-components.tsv` is pinned to `9c62f19e`, dated
   **2024-10-16**, and the TSV marks it `-` (source-only: `post-build.sh` reads
   its data files). It is *not* the libcosmic cargo builds against. The pin that
   matters is inside 26 separate `Cargo.lock` files we do not control.
2. "Move everything to 1.9 together" only works if the libcosmic each component
   pulls is coherent. The tag move does not guarantee that by itself.

## The battery patch: probably redundant, **not** proven obsolete

`buildroot-external/board/desktop/cosmic-patches/` carries two patches. The
`cosmic-applets` battery one says in its own header "Upstream: fixed in
cosmic-settings a366fc7 (epoch-1.9)", which reads like the epoch move deletes it.
Checked, and that inference does not hold up:

- At the epoch-1.9.0 applets commit (`82e7cd814add`), `cosmic-applet-battery/src/app.rs`
  is **unchanged** in the relevant part — `Update { .. }` still clears
  `no_battery` and calls `update_battery`, and `NoBattery` still just sets the flag.
  Our patch's context lines match, so it still applies.
- The upstream fix is on the **subscription** side (in cosmic-settings /
  cosmic-settings-daemon), not in the applet. Whether that subscription is the one
  this applet links at epoch-1.9 is a separate question I have not answered.

So the patch stays for now. Deleting a defensive patch that still applies, in the
same change that moves 16 components, is exactly the confounding variable to avoid.
Re-test it on a desktop with a wireless mouse/keyboard after the 1.9 image boots,
and delete it then if the 0% icon does not come back.

## Two pin sets, not one — now enforced by a test

The epoch move touches both:

- `scripts/cosmic-components.tsv` — 29 rows, the host-built path that ships today.
- `tools/pengyos-cosmic-recipes/cosmic.toml` — 22 components, the recipes lane that
  is *not yet in an image*, pinned to commit SHAs because `is_pinned` rejects
  `epoch-*` tags.

Moving one and not the other creates a tree where the shipping path and the future
path disagree. Both moved (commit `617d696b`), and
`test_every_manifest_pin_matches_the_shipping_component_set` now asserts every
component in every group pins the same commit in both files.

That test earned its keep immediately. Two things it caught:

1. **A divergence I had just created.** cosmic.toml's `libcosmic` moved to the
   coherence rev while the TSV still pinned `9c62f19e`, dated **2024-10-16**.
   Checked what uses that checkout: nothing — the row is source-only and
   `post-build.sh` reads cosmic-settings' resources, not libcosmic's. A dead clone
   of a 2024 libcosmic sitting in the build inputs. The TSV now names the coherence
   rev.
2. **Three tests that had been passing vacuously.** `test_closure_reports_lint_violation`
   corrupted a hard-coded cosmic-comp SHA in the emitted `.mk`; once the pin moved,
   the replace matched nothing, no violation was introduced, and the assertion still
   passed. `test_shell_pins_epoch_180_commit_shas` and its apps twin asserted
   `tag != "epoch-1.8.0"`, which is trivially true of any SHA. All three now assert
   against the manifest/TSV rather than a literal.

And the mutation check caught a gap in the new test itself: the first version
covered only the shell and apps groups, so drifting a **core** component survived
it. The manifest-wide version is what caught the libcosmic divergence.

## Size of the jump, per repo (1.8.0 → 1.9.0)

| Repo | commits | files changed |
|---|---:|---:|
| cosmic-comp | 8 | 11 |
| cosmic-session | 8 | 9 |
| cosmic-settings | 11 | 53 |
| cosmic-greeter | 14 | 15 |

Small. This is not a rewrite, which is why it is worth doing rather than
deferring again.

## What actually moves: 16 of 26, because an `epoch-*` tag is a release snapshot

The first pass at this table was wrong, and the reason matters. Pop tags every
repo in an epoch release, **including repos that did not change** — so several
`epoch-*` tags peel to the same commit. cosmic-idle's `epoch-1.0.10`, `1.4.0`,
`1.7.0`, `1.8.0` and `1.9.0` are all `c95d066b5b64`; cosmic-sound-theme's
`1.4.0`/`1.7.0`/`1.8.0`/`1.9.0` are all `7aabe449`.

Comparing our pinned commits against the `epoch-1.9.0` commits (greeter: 1.9.1):

- **16 rows move** (real new code): cosmic-comp, cosmic-session, cosmic-panel,
  cosmic-applets, cosmic-launcher, cosmic-settings, cosmic-settings-daemon,
  cosmic-notifications, cosmic-osd, cosmic-bg, cosmic-greeter, cosmic-term,
  cosmic-files, cosmic-edit, cosmic-store, cosmic-player.
- **10 rows change tag only**: cosmic-idle, cosmic-randr, cosmic-screenshot,
  pop-launcher, cosmic-icons, cosmic-sound-theme, cosmic-app-library,
  cosmic-monitor, cosmic-viewer, cosmic-workspaces-epoch. Their pinned commit is
  already what epoch-1.9.0 points at.
- **3 untagged**: cosmic-workspaces, cosmic-protocols, libcosmic.

So "six epochs in one image" is true of the *tags* and misleading about the code:
idle and randr have not changed since epoch-1.0.10, which is also why idle's
Cargo.lock still carries a 2025 libcosmic — the repo is stale, not the pin.

## The mechanism for option C already exists upstream

Every component declares libcosmic as an unversioned git dependency
(`libcosmic = { git = "https://github.com/pop-os/libcosmic" }`), and each
component's `Cargo.lock` freezes whatever that resolved to. But the components
already use cargo patching for exactly this problem:

- `cosmic-comp/Cargo.toml` has `[patch."https://github.com/pop-os/cosmic-protocols"]`
  and `[patch.crates-io]` pinning `smithay` to `rev = "e3d461a"`.
- `cosmic-greeter/Cargo.toml` has the same cosmic-protocols patch **and a
  commented-out `[patch."https://github.com/pop-os/libcosmic"]` block** — upstream's
  own tool for forcing one libcosmic across a component set.

So "pin libcosmic deliberately" is not a new invention: it is the mechanism the
upstream tree already uses, applied to the dependency we actually have trouble
with. A `[patch."https://github.com/pop-os/libcosmic"]` entry naming one rev (or a
local checkout, which is what `pengyos-cosmic-recipes` + Buildroot's cargo
vendoring do properly) would make the epoch move mechanical.

## Unchanged dependency

`cosmic-protocols` is pinned at rev `160b086` in cosmic-comp's `Cargo.toml` at
**both** epoch-1.8.0 and epoch-1.9.0 — unchanged, so that dependency is not part
of the move.

## The coherence spike (2026-10-05, read-only) — the number that decides it

`~/build/pengyos/cosmic-coherence-spike.py` fetched every component's `Cargo.lock`
at our pinned commit (or from the local checkout we actually built) and at its
newest `epoch-*` release, and extracted every rev sourced from
`pop-os/libcosmic`. Result:

**Our current image builds COSMIC against 14 distinct libcosmic commits. The
epoch-1.9 set would build against 13.** Overlap 8; the move introduces 5 new revs.

So moving to 1.9 does **not** cohere libcosmic. It changes 14 to 13. That settles
the plan: the epoch move alone is not the fix, and option C is the work.

| Component | pinned | libcosmic now | libcosmic at newest epoch |
|---|---|---|---|
| cosmic-idle | epoch-1.0.10 | `2f0b333` **2025-10-13** | `2f0b333` (unchanged even at 1.9.0) |
| cosmic-comp | epoch-1.8.0 | `d922ac3` 2026-07-01 | `d922ac3` (unchanged at 1.9.0) |
| cosmic-greeter | epoch-1.8.0 | `8cb10bf` 2026-07-09 | `87ab817` 2026-09-15 |
| cosmic-panel | epoch-1.8.0 | `20dfd7c` 2026-07-01 | `23f5d63` 2026-09-14 |
| cosmic-settings | epoch-1.8.0 | `2a73fbc` 2026-08-25 | **two revs**: `87ab817` + `d77e99f` |
| cosmic-applets | epoch-1.8.0 | `d4d71fd` | `03c8f93` 2026-09-21 |
| cosmic-osd, cosmic-notifications, cosmic-settings-daemon | 1.8.0 / 1.7.0 / 1.8.0 | `8cb10bf`, `8cb10bf`, `3b85b6e` | all `d921602` 2026-09-15 |
| cosmic-files, cosmic-player, cosmic-store, cosmic-term | epoch-1.8.0 | `9cc82ab` 2026-08-08 (all four) | `08a09e3` / `9cc82ab` / `23f5d63` / `08a09e3` |
| cosmic-edit | epoch-1.8.0 | `b728852` | `08a09e3` 2026-09-22 |
| cosmic-launcher | epoch-1.8.0 | `a5267e6` | `8cb8619` 2026-09-15 |
| cosmic-bg | epoch-1.6.0 | `ef162b8` 2026-07-19 | `ef162b8` |
| app-library, monitor, viewer, workspaces-epoch | epoch-1.9.0 | `8cb8619`, `03c8f93`, `eaa7a5a`, `8cb10bf` | unchanged |

Three findings that change the plan:

1. **cosmic-idle is a year behind, and 1.9 does not fix it.** Its `Cargo.lock`
   pins libcosmic `2f0b333`, dated **2025-10-13**, and it still says so at
   `epoch-1.9.0`. Whatever idle does with a ten-month-old libcosmic is happening
   today, inside the 1.8/1.9 mix.
2. **cosmic-settings at epoch-1.9.0 is internally incoherent** — its own
   `Cargo.lock` carries two different libcosmic revs (`87ab817` and `d77e99f`),
   because `libcosmic` and `cosmic-config` resolved separately. Incoherence is not
   only across components.
3. **Six components have no committed `Cargo.lock` at their tag**: cosmic-session,
   cosmic-randr, cosmic-screenshot, pop-launcher, cosmic-icons, cosmic-sound-theme.
   For those, resolution happens at build time on the build host. cosmic-session —
   the thing that orchestrates the session — is one of them. That is the concrete
   form of SPEC §6.3's "Desktop depends on the host's userspace", and it means the
   current build is not reproducible in the way the Core/Server builds are.

The revs cluster: at epoch-1.9 the shell core (panel, greeter, settings, osd,
notifications, settings-daemon, launcher) sits on libcosmic from **2026-09-14 to
2026-09-22**. That cluster is the natural single pin — newest useful is `08a09e3`
(2026-09-22), safest core-aligned is `87ab817` (2026-09-15, what greeter and
settings want).

## What was done (2026-10-05): the coherence pin, and 14 revs became 1

`scripts/cosmic-libcosmic.pin` names one libcosmic commit —
`87ab8179e1bd` (2026-09-15, "chain tasks where not already done"), chosen because
it is what cosmic-greeter and cosmic-settings resolve to at epoch-1.9 and sits
inside the 2026-09-14..22 cluster the epoch-1.9 shell core uses.
`scripts/cosmic-libcosmic-pin.py` appends or merges a
`[patch."https://github.com/pop-os/libcosmic"]` block naming that rev into each
component's `Cargo.toml`, and `scripts/build-cosmic.sh` runs it per component and
then **verifies** the result.

Result, measured on the component locks after the build:

| | before | after |
|---|---:|---:|
| distinct libcosmic commits across the 24 components that depend on it | **14** | **1** |

Three cargo behaviours had to be handled, and each one is now a test in
`scripts/tests/test_cosmic_libcosmic_pin.py` (15 tests):

1. **A patch may not point at the source it replaces.** cargo rejects
   `patch."https://github.com/pop-os/libcosmic"` entries whose git URL is the same
   URL. pop-os' own idiom is a trailing `//`, used in cosmic-greeter's
   commented-out libcosmic patch block and its live cosmic-protocols patches. We
   use `https://github.com/pop-os/libcosmic//`.
2. **Nine components already declare a libcosmic patch section**, usually spelled
   `[patch.'https://github.com/pop-os/libcosmic']` with single quotes and usually
   containing only commented-out examples. TOML treats the single-quoted and basic
   spellings as the *same key*, so appending a second section is a hard parse
   error — "duplicate key `https://github.com/pop-os/libcosmic`", which is what
   killed the first real run at cosmic-osd. Entries are merged into the existing
   section, and any active entry for a crate we pin is commented out.
3. **A patch can be accepted and still do nothing.** cargo warns
   `patch ... was not used in the crate graph` when the pinned rev's crate
   *versions* do not satisfy the graph. cosmic-idle — unchanged since epoch-1.0.10
   — wanted `iced_core 0.14.0-dev` from 2025-10-13 while the pin provides
   `0.14.0`, so it silently kept its 2025 libcosmic. `--verify` catches exactly
   that (it reads the lock, not the manifest), and build-cosmic.sh then re-resolves
   the component's lock and rebuilds. cosmic-idle compiles clean against the pin
   (18.4 s), so the year-old libcosmic is gone from the image.

The verify step is the part worth keeping even when everything is coherent: it
fails the build if any component's lock resolves libcosmic to anything other than
the pin, and it compares the *lock* because a manifest can advertise a pin that
the graph ignores.

One build-script change this required: `build-cosmic.sh` dropped local changes only
when the pinned commit moved. With the coherence pin in the working tree, a second
run re-applied it onto a tree that already had it and produced a duplicate patch
table. Local changes are now dropped unconditionally.

## The options that were on the table (decided: C then A)

**A. Whole-set move to epoch-1.9.0 (greeter to 1.9.1), in one branch.**
The honest version: no mixed epochs. Cost: one big `build-cosmic.sh` run (26 cargo
builds), a full Desktop rebuild, and the Desktop gate plus real-hardware boot
checks.

**B. Move the shell core first (comp, session, panel, applets, settings,
greeter), leave the apps on 1.8.**
Cheaper to test, and the apps are the least coupled part. Cost: it *is* the mixed
configuration that broke the lock screen once. Only defensible if the libcosmic
revs happen to agree — which is checkable before committing to it.

**C. Pin libcosmic deliberately, then move.**
Make `libcosmic` a real pin (a commit we choose, applied to every component via
cargo `[patch]` or a generated `Cargo.lock`), then the epoch move is mechanical
and reproducible. Cost: this is the fix for the actual root cause, and it is also
the thing `pengyos-cosmic-recipes` exists to do. Highest value, most work.

**D. Do nothing this cycle.** epoch-1.9.0 has been out since early September and
nothing about it is urgent. Cost: the mixing hazard stays, and the battery patch
stays a backport we maintain by hand.

## Decision (2026-10-05)

C then A, into **26.10.8**: pin one libcosmic, then move the whole set. Built and
gated the same day — 26 cargo builds, both Desktop editions rebuilt, and
`pengyos-qemu-verify desktop` passes on both installer sticks (5 checks, 0 failed
each). What is *not* done: the real-hardware round. The wallpaper/lock-screen
failure mode this whole analysis is about is a greeter-on-real-GPU thing, and
behoweather (NVIDIA), behopc and behotv still have to boot a 1.9 image.
`tools/pengyos-cosmic-recipes/cosmic.toml` moved with it: the two pin sets are now
kept equal by `test_every_manifest_pin_matches_the_shipping_component_set`, so a
future bump that moves one file and not the other fails the gate.

## Commands behind every number

```sh
# epoch tags per repo
git ls-remote --tags https://github.com/pop-os/<repo> | grep -oE 'epoch-1\.9\.[0-9]+' | sort -u

# what our checkouts resolve libcosmic to
grep -A2 'name = "libcosmic"' ~/cosmic-src/<component>/Cargo.lock | grep -oE '#[0-9a-f]+'

# upstream at the tag
curl -sL https://raw.githubusercontent.com/pop-os/cosmic-greeter/epoch-1.9.0/Cargo.lock

# jump size
curl -s https://api.github.com/repos/pop-os/cosmic-comp/compare/epoch-1.8.0...epoch-1.9.0

# the whole survey, using the tool that already does it
cd tools/pengyos-nightly && python3 - <<'PY'
from pengyos_nightly import sources
import subprocess
comps = sources.read_cosmic_components(open('../../scripts/cosmic-components.tsv').read())
tags = {c['name']: sources.parse_ls_remote(subprocess.run(['git','ls-remote','--tags',c['url']],
        capture_output=True,text=True).stdout) for c in comps}
for f in sources.cosmic_findings(comps, tags): print(f)
PY
```
