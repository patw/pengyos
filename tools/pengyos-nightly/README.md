# pengyos-nightly

The nightly maintenance loop for [PengyOS](../../README.md). Every evening it finds
out what changed upstream for everything PengyOS ships, triages the CVEs with the
local model, emails the maintainer a report — and **does something
about it**: it batches up to `max_actions` changes, verifies them as one build, and
publishes **one** boot-checked, signed image to **testing**.

Phase 1 is **observe, triage, report**. Phase 2 is **plan, act**. Phase 3 is
**batch, one release**. Phase 5 is **decide**: a page where the maintainer approves or
denies what the report recommends. See [`spec.md`](spec.md), the contract for all of them.

## What it looks at

| Source | How | Lane |
|---|---|---|
| Every Buildroot package in each configured edition | Buildroot's own `pkg-stats` (NVD CVEs, release-monitoring.org versions) | security; version gaps only for our recipes |
| Buildroot | git tags: `2026.08.x` point releases, newer series, LTS (`.02`) | routine / major |
| Linux | kernel.org `releases.json` vs `BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE` | same series routine, new series or EOL major |
| COSMIC | `epoch-*` tags of every repo in `scripts/cosmic-components.tsv` | major |
| NVIDIA | tags of `NVIDIA/open-gpu-kernel-modules` vs `NVIDIA_OPEN_VERSION` | major |
| Exploit signals | CISA Known Exploited Vulnerabilities, FIRST EPSS | feeds triage |

## Triage

Rules first: a CVE in a build-host-only package is P2 without asking the model,
and a CVE in CISA's KEV catalogue is never below P1. Everything else goes to the
local model (Pengy's gateway, model alias `local`) as a plain completion with
no tools: the CVE text, CVSS, KEV/EPSS, the package, the editions shipping it,
and what each edition exposes. It answers P0 / P1 / P2 with a reason. A bad
answer is retried once, then rules decide (`fallback` in the report).

Each CVE match is triaged once; `state.json` remembers it. A match that
disappears (the package moved past it) is marked resolved.

## Plan and act (phases 2 and 3)

`plan` decides from the loop's own state and changes nothing. `act` runs the night's
batch end to end into **testing**: one branch, one commit per change, one gate, one
build, one boot check, one signature, one published image. Stable is unreachable in
code: the publish channel is a constant and every argv is re-checked before it runs,
so `update-promote` and `--channel stable` cannot be assembled.

The batch is the unit of work and the signed image is the unit of risk, which is why
there are two caps and they are different numbers:

| Cap | What it limits | Why |
|---|---|---|
| `max_actions` | how many changes the loop may prepare and verify in a night | the work is cheap: one full action end to end measured **18 m 14 s** on 2026-10-05, all five editions rebuilt in **9 m 42 s** |
| `max_releases` | how many signed images may reach the feed in a night — **1** | this is the blast radius. Twelve changes in one image is still one image testing-channel machines receive |

A package whose patch fails its pre-flight is **dropped from the batch**, recorded as
`patch_does_not_apply` so the planner refuses it by name next time, and its edits are
taken back out of the tree — one bad cherry-pick does not spend the night, and a
change that never built is never published or merged. A build or boot failure
publishes nothing and records `batch_failure`, which `status` reports as a blocked
batch rather than a quiet night.

```sh
python3 -m pengyos_nightly plan                       # plan.json: actions + named refusals
python3 -m pengyos_nightly act                        # dry run: the diff and the commands
python3 -m pengyos_nightly act --apply                # branch, build, sign, publish, push
python3 -m pengyos_nightly act --only security-libxml2-2.15.3 --apply
```

**Two edit shapes, because Buildroot says so.** A stock package cannot be
version-bumped from a br2-external tree: reassigning its version in `external.mk` makes
Buildroot download the new tarball while the stamp rules stay on the old one, and
extraction dies (repro in `edits.py`). So:

| Shape | For | What it edits |
|---|---|---|
| `recipe_bump` | a package we author (`alsa-ucm-conf`, `libmtp`, `pengy`, …) | `NAME_VERSION` in its own `.mk` + the tarball hash in its `.hash` |
| `patch_dir` | a stock package with a CVE NVD proves is open in the pinned version | the upstream fix commits NVD names, as `buildroot-external/patches/<pkg>/<version>/*.patch` through `BR2_GLOBAL_PATCH_DIR` |

**Evidence, not vibes.** NVD's own affected range decides, in two readings: *affected*
(is the version we ship inside the range — what a patch needs) and *covered* (does an
available release close it — what a bump needs). A CVE whose record names no upstream
commit (tar's point at Red Hat errata) is reported, never acted on. pkg-stats'
`unsure_cves` are never actionable. `linux`, `nvidia-open`, `buildroot` and COSMIC are
report-only in this phase.

**A patch does not move a version, so the loop has to remember what it fixed.**
`state.json` keeps three states per CVE match — *open*, `resolved` (the package moved
past it) and `patched_by` (we carry the fix as a patch, with the date, the release and
the patch files). `triage`, `report`, `status` and `plan` all honour it: a carried CVE
is reported under "closed by patches we carry", is not re-triaged every night, and is
never a candidate again. Otherwise pkg-stats re-flags the same 7 libxml2 CVEs forever
and the loop cries wolf about issues it has already shipped fixes for.

**The act order is the safety property:** guards → branch → edits → recompose
defconfigs → VERSION → commit → **gate** → `<pkg>-dirclean` + `<pkg>-patch` per edition
→ `release.sh --publish --channel testing` → merge to `main` → record. `release.sh` is
the first step that can affect a machine, and it already refuses a dirty tree and
refuses to publish an image that was not boot-checked. `main` moves only after a
successful publish; any earlier failure restores main's working tree, keeps the branch
for inspection, and publishes nothing. The dirclean/patch pre-flight matters twice:
Buildroot cannot see a new patch in an already-built tree, and a cherry-picked commit
can apply to the source while failing on upstream's own test files.

Policy is `<state-dir>/nightly.toml` (machine-local, not in the repo):

```toml
act_enabled  = true          # false = cron plans but never acts
push         = true          # rsync the site dir to PENGYOS_SITE_REMOTE
lanes        = ["security", "routine"]
priorities   = ["P0", "P1"]
max_actions  = 12            # changes prepared and verified per night (the batch)
max_releases = 1             # signed images published per night — the blast radius
cooldown_days = 7            # per component, consumed only by a published action
allow_patch_shape = true
skip_cosmic  = true
```

The signing key and the site target never enter the repo and are never printed:
`PENGYOS_RELEASE_KEY` (env) else the `key_file`, and `PENGYOS_SITE_REMOTE` (env) else
`~/Personal/.secrets`. Missing either one makes `act` refuse and the loop stays
report-only — a reported condition, not a crash.

## Decide (the web page)

The report says what needs a decision; the page is where you make it.

```sh
python3 -m pengyos_nightly web --host 0.0.0.0 --port 5050   # needs Flask
```

It lists every recommendation from the last run: version updates waiting for you (a
new series first), security fixes the loop handles itself, what you decided earlier,
and the last few act runs. **Approve** writes your yes into `decisions.json` next to
the plan; the `act` run re-plans if a decision is newer than the plan, and builds the
approved bump as the `approved` lane, through every other guard, into **testing**.
**Deny** (or **Hold** for a security fix) keeps it out until upstream offers a new
series. Things no act path can carry out (the kernel, the NVIDIA driver, COSMIC,
Buildroot, stock packages) say so and cannot be approved. Set `web_url` in
`nightly.toml` and the email links the page.

On behoserver it runs as the user unit `pengyos-nightly-web.service` (port 5050).

## Usage

```sh
cd tools/pengyos-nightly
python3 -m pengyos_nightly run                 # observe + triage + report (email)
python3 -m pengyos_nightly run --no-email      # report to reports/<date>.html only
python3 -m pengyos_nightly observe             # just refresh observations.json
python3 -m pengyos_nightly triage --max-triage 50 --jobs 2
python3 -m pengyos_nightly report --force-email
python3 -m pengyos_nightly plan                    # decide, change nothing
python3 -m pengyos_nightly act                     # dry run of the night's batch
python3 -m pengyos_nightly act --max 4             # cap this run's batch below max_actions
python3 -m pengyos_nightly act --only <id>         # one action, as a batch of one
python3 -m pengyos_nightly status                  # alive? and what is queued?
python3 -m pengyos_nightly web                     # the decision page (127.0.0.1:5050)
```

`status` also reports the Buildroot release-bump lane (spec phase 4):

```
bump lane: inert | Buildroot 2026.08 | no newer Buildroot release found
bump lane: inert | Buildroot 2026.08 | finding: 2026.11 (new release series)
bump lane: scheduled -> 2026.11 (not executable yet) | Buildroot 2026.08 | ...
```

with a `!` line when a newer series is a finding and nothing is scheduled. Those lines
are attention, not failure: they do not change the exit code.

State lives in `~/build/pengyos/nightly/` (`--state-dir`): `observations.json`,
`state.json`, `plan.json`, `actions.json`, `decisions.json`, `nightly.toml`, `attest.jsonl` (the
`pengyos-attest` hash chain of every action), `reports/`, the per-edition
`pkgstats-*.json`, and `nvd/` (a clone of fkie-cad/nvd-json-data-feeds that
`pkg-stats` updates).

Needs: configured Buildroot output trees under `~/build/pengyos/output/`, `uv`
(for `pkg-stats`' `aiohttp`), `git`, network, a model gateway's settings
(`~/.config/pengy/settings.json`, or `$PENGY_SETTINGS`), and a mailer at
`$PENGYOS_EMAIL_SCRIPT` (default `~/.local/bin/pengyos-email`). The mailer is
not in this repository: with none configured the loop stays report-only, the
HTML report still lands on disk, and the queue is not marked as reported.

Scheduled example — one run per evening, ahead of any overnight agent work:

```
0 19 * * * cd "$HOME/pengyos/tools/pengyos-nightly" && python3 -m pengyos_nightly run >> "$HOME/logs/pengyos-nightly.log" 2>&1
```

## Tests

```sh
python3 -m pytest -q
```

Offline: the observers parse recorded upstream formats in `tests/fixtures/`
(captured 2026-10-03), and the network, `pkg-stats` and the model are injected.

## Rehearsal (not a test)

```sh
python3 rehearse.py                    # every allowed action in tonight's plan
python3 rehearse.py ffmpeg libsndfile  # only these
python3 rehearse.py --edition pengyos-server
```

The unit suite runs a fake `make`. `rehearse.py` runs the real
`make <pkg>-dirclean` / `<pkg>-patch` against the real output trees, on a scratch
branch, and restores `main` — publishing nothing and touching no state file. It
refuses to run on a dirty tree, for the same reason the act lane does, and exits
non-zero if HEAD moved or the tree is left dirty.

Run it before leaving the act lane armed. Every defect this tool has found, unit
tests could not — the `set -e` patch-culprit line, Buildroot's discarded `-pN`
field, the concatenated MR `.patch`, and which of the planned fixes actually apply
on the pinned versions (2026-10-07: 18 of 31 patches, 14 CVE matches closed, not
25). Takes a few minutes.
