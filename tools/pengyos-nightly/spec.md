# pengyos-nightly — the nightly maintenance loop

## Summary
Every night, find out what changed upstream for what PengyOS ships, decide what
matters, and tell the maintainer. Later phases act on it (bump, build, publish to
testing); phase 1 only observes, triages and reports.

## Purpose
PengyOS pins everything: Buildroot and its ~450 packages, our authored recipes,
the COSMIC commits, the kernel, the NVIDIA driver. Pins go stale, and stale pins
collect CVEs. This loop keeps a human informed without asking a human to read
NVD, and it runs on the local model on behoserver, where tokens cost electricity
only.

It replaces the TODO items "THE LOOP" (#110) and "automated triage" (#118), and
it gives `pengyos-upstream-watch` / `pengyos-update-triage` their real inputs.

## Three lanes (the whole design; phase 1 implements the reports)

| Lane | What | Who decides | Phase 1 | Later |
|---|---|---|---|---|
| **Routine** | Buildroot point releases (2026.08.x), kernel stable releases within the pinned series, patch-level bumps of authored recipes | the loop | listed in the report | bump on a branch → gate → build → QEMU boot → publish to **testing** |
| **Security** | CVEs against packages in an image | the loop triages; a human promotes to stable | triaged P0/P1/P2 in the report | P0: out-of-cycle build → testing → wait for a confirmed boot on a testing machine → one-tap "promote?" |
| **Major** | a new kernel series, a COSMIC epoch tag, Mesa/Wayland majors, a new NVIDIA driver, a new Buildroot LTS | a human | listed in the report | trial build + QEMU boot on a branch, then "push to testing? yes / no / ask again at X" through the propose gate |

Stable is always a human decision. Nothing in this tool runs `update-promote`.

## Phase 1 deliverable
A Python CLI `pengyos-nightly` (standard library only) run by cron on behoserver:

```
pengyos-nightly run     [--state-dir DIR] [--repo DIR] [--build-root DIR] [--no-email] [--dry-run]
pengyos-nightly observe [--state-dir DIR] ...      # write observations.json
pengyos-nightly triage  [--state-dir DIR] ...      # add a priority to every new CVE
pengyos-nightly report  [--state-dir DIR] [--no-email] [--force]
```

Defaults: `--repo ~/Desktop/Personal/pengyos`, `--build-root ~/build/pengyos`,
`--state-dir ~/build/pengyos/nightly`.

### Observe (deterministic, no model)
Each source is independent; one failing source is reported, never fatal.

* **Buildroot packages** — for each edition whose output tree is configured
  (`<build-root>/output/<target>/.config`), run Buildroot's
  `support/scripts/pkg-stats -c --json` in that tree with `aiohttp` supplied by
  `uv run --with aiohttp` (no system packages), and `--nvd-path
  <state-dir>/nvd` (a clone of fkie-cad/nvd-json-data-feeds). Keep, per package:
  current version, latest version and its status (release-monitoring.org), CVEs
  (and "unsure" CVEs), and which editions carry it. Host packages (`host-*`)
  are recorded as host-only.
* **Buildroot itself** — tags of the Buildroot git repo: a newer `2026.08.x`
  (routine) or a newer LTS `YYYY.02` (major).
* **Kernel** — `https://www.kernel.org/releases.json` against the pinned
  `BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE`: a newer release in the same series
  is routine, a newer series is major.
* **COSMIC** — for each repository in `scripts/cosmic-components.tsv`, `git
  ls-remote --tags`: the newest `epoch-*` tag and whether the pinned commit is
  behind it (major).
* **NVIDIA** — the pinned `nvidia-open` version against the newest tag of
  `NVIDIA/open-gpu-kernel-modules` (major).
* **Exploit signals** — CISA's Known Exploited Vulnerabilities catalogue and
  FIRST's EPSS scores for every CVE found.

Output: `<state-dir>/observations.json`, plus a per-source status (`ok` /
`error: …`).

### Triage (the local model, plus rules that cannot be argued with)
Only CVEs not already triaged (in `state.json`) are sent to the model.

Rules first:
* a CVE on a host-only package → **P2** ("build host only, not in any image");
* a CVE in the KEV catalogue on a package in an image → at least **P1**.

Then the model, via the OpenAI-compatible endpoint that Pengy uses
(`base_url` and `api_key` from `~/.config/pengy/settings.json`, model alias
`local` by default). A plain completion, **no tools**. The prompt gives the
CVE description, CVSS, KEV/EPSS, the package, the editions that carry it, and
what PengyOS exposes (listening services per edition); the model answers JSON:

```json
{"priority": "P0|P1|P2", "reachable": "remote|local|file|none|unknown",
 "rationale": "one or two sentences"}
```

* **P0** — actively exploited, or remotely reachable without login on
  something an edition exposes.
* **P1** — serious and plausibly reachable.
* **P2** — not reachable in PengyOS, disputed, or negligible.

An unparsable or failed answer is retried once, then falls back to rules
(KEV → P1, CVSS ≥ 9.0 → P1, else P2) marked `"source": "fallback"`. The rules'
floor always wins over the model (the model can raise a priority, never lower
a KEV CVE below P1).

### Report
An HTML email through the email skill (`~/Personal/skills/email/send_email.py`,
configurable), and the same content as `<state-dir>/reports/<date>.html`.

* New since the last report: CVEs by priority (with rationale, editions, fixed
  version if known), routine updates, major upgrades.
* Source errors, always.
* Sent when there is anything new or any source failed, and every Monday as a
  full digest (the canary: silence otherwise means "ran, nothing new").

### State
`<state-dir>/state.json`: triaged CVEs (priority, rationale, source, first
seen), the latest version seen per component, the last report date. A finding
is "new" if it is not in the state. Written atomically.

## Phase 2 — the act lane (added 2026-10-05)

Phase 1 answers *what changed*. Phase 2 answers *what the OS should do about it*,
and does it: **plan** (decide, deterministically, with evidence) and **act**
(branch → edit the pin → gate → build → boot-check → sign → publish to
**testing**). Stable stays a human decision, structurally: the tool has no path to
`update-promote` and its publish channel is a constant.

```
pengyos-nightly plan   [--state-dir DIR] [--json]      # write plan.json, change nothing
pengyos-nightly act    [--only ACTION-ID] [--apply]    # dry run by default
pengyos-nightly status                                  # includes the act lane's state
```

Policy lives in `<state-dir>/nightly.toml` (machine-local, not in the repo):

```toml
act_enabled  = true             # the kill switch: false means cron plans, never acts
push         = true             # rsync the site dir to PENGYOS_SITE_REMOTE
lanes        = ["security", "routine"]
priorities   = ["P0", "P1"]     # which triaged CVEs may trigger an action
max_actions  = 12               # candidates applied per night (the batch size)
max_releases = 1                # signed images published per night — deliberately 1
cooldown_days = 7               # per component
allow_patch_shape = true        # the shape the build proved (see Shapes)
skip_cosmic  = true             # a package change does not need COSMIC rebuilt
```

**Why the two caps are different numbers (decided 2026-10-06).** Measured on
2026-10-05, not estimated: one complete action — guards → branch → 7 patches →
recompose → gate → dirclean → build two editions → QEMU boot checks → sign →
publish → push → merge — took **18 m 14 s**, and all five editions rebuilt
incrementally took **9 m 42 s** on a 32-core host. So `max_actions = 1` was never
protecting the machine; it was capping *CVEs closed* while paying the full
verification cost once per action. The expensive, risky artifact is a **signed
image on the live feed**, and one image can carry many changes. So:

* `max_actions` is how much work the loop may **prepare and verify** in a night.
* `max_releases` is how many images it may **publish** in a night, and stays 1.

Batching raises throughput without raising blast radius: still one signed image per
night, still testing-only, still no path to stable. Machines on `testing` accept
the blast radius of several changes per image — that is what the channel is for,
and the operator's own hardware check happens the next day rather than in the loop.

The release key and the site target are never in the repo and never printed:
`PENGYOS_RELEASE_KEY` (env) else the key file `key_file` points at;
`PENGYOS_SITE_REMOTE` (env) else `~/Personal/.secrets`. Missing either one makes
`act` refuse and the loop stay report-only — a reported condition, not a crash.

### Plan (deterministic, no model, no side effects)

`plan` reads `observations.json` + `state.json` and writes `plan.json`:

* **actions** — each with `lane`, `component`, `current`, `target`, `shape`,
  `editions`, the CVE matches it closes (`closes`) and the **evidence** for each,
  the affected CVEs the action does *not* close (`still_open`), the exact
  file edits it would make, and every guard verdict it passed. `closes` is computed
  from the same function that writes the patches (`edits.carried_cves`), so the
  security claim and the patch set cannot drift apart.
* **refused** — every candidate that was considered and the named guard that
  stopped it. A plan with only actions is not auditable: the refusals are the
  proof that the guards ran.

**Evidence rule.** NVD's own affected range decides, in two readings, because the
two shapes need different proofs: `affected` (is the version we ship inside the
range — what the patch lane needs before carrying a fix) and `covered` (does an
available release close it — what a bump needs). A record that names no upstream
commit (tar's CVEs point at Red Hat errata) is not actionable: there is nothing to
carry. pkg-stats `unsure_cves` are never actionable.

### Resolution kinds

A patch shape does not move the version, so pkg-stats keeps reporting the same CVE
tomorrow. `state.json` therefore distinguishes three states per match, and every
reader (`triage`, `report`, `status`, `plan`) has to honour them or the loop cries
wolf about issues it has already shipped fixes for:

| State | Means | Consequence |
|---|---|---|
| open | flagged and not fixed here | reported, and a candidate for the act lane |
| `resolved` | the match disappeared — the package moved past it | not reported, not a candidate |
| `patched_by` | we carry the fix as a patch on the pinned version | reported under "closed by patches we carry", not re-triaged, never a candidate again |

`patched_by` records the date, the pinned version, the release that carried it and the
patch files, so the claim is checkable against the tree.

### Shapes (what an edit may be)

A stock Buildroot package **cannot be version-bumped from a br2-external tree**, and
that was proven by a build rather than read off the docs (see `edits.py` for the
repro): copying the recipe is refused outright, and reassigning `PKG_VERSION` in
`external.mk` makes Buildroot download the new version while the stamp rules stay
on the old one, so extraction fails. Two shapes only:

| Shape | For | Edit |
|---|---|---|
| `recipe_bump` | a package we author (`tree != BUILDROOT`) | `NAME_VERSION` in `buildroot-external/package/<name>/<name>.mk` + the tarball hash in its `.hash` |
| `patch_dir` | a stock package with a CVE NVD proves is open in the pinned version | the upstream fixes NVD names — a commit, or a pull/merge request on a host that serves one as a patch — as `buildroot-external/patches/<pkg>/<version>/*.patch`, reached through `BR2_GLOBAL_PATCH_DIR` (`package/pkg-utils.mk:pkg-patch-hash-dirs`); recompose the defconfigs. Only the CVEs whose fix is fetchable are carried and claimed; the rest are recorded as `still_open` |

Hashes for a bump come from upstream's own published checksum file when the stock
`.hash` names one (GNOME publishes `<pkg>-<ver>.sha256sum`); otherwise the artifact
is downloaded and hashed here, and the plan labels it `self-attested`.

### Act (a batch of actions, one release, end to end) — phase 3

Phase 2 ran one action to one release. Phase 3 runs a **batch of actions to one
release**: the batch is the unit of work, the signed image is the unit of risk.

`act` is dry-run unless `--apply` **and** `act_enabled`. With both it:

1. **refuses** unless every action in the batch is `allowed` in `plan.json`, the tree
   is clean, the key (and, when `push = true`, the site remote) is present, and no
   release has already been published today (`release_cap`);
2. **branches once** — `nightly/<date>-batch` — applies every action's edits,
   recomposes the defconfigs once if any layer changed, bumps `VERSION` once
   (reusing the current one when it has never been published), and makes **one
   commit per action** so each change keeps its own *why* while sharing one branch;
3. **runs the repo gate once** (`pengyos_gate.py run --mode full --base <branch point>`);
4. **pre-flights each package once** — `<pkg>-dirclean` then `<pkg>-patch` in every
   edition that carries it. This is where a patch that does not apply is caught, in
   seconds, before any build. A package that fails here is **dropped from the batch**
   (recorded as `patch_does_not_apply`, exactly as phase 2 recorded it) and the batch
   continues without it: one bad cherry-pick must not spend the night. If every
   package fails, nothing is built and nothing is published;
5. **builds, boot-checks, signs and publishes once** — `scripts/release.sh --publish
   --channel testing` for the **union of the editions the batch touches**, with one
   notes file listing every change and its evidence. `release.sh` already refuses a
   dirty tree and refuses to publish an image that was not boot-checked, so the boot
   test of the batch *is* the sign-off: an image that does not reach login is never
   signed. `--push` only if `push = true`;
6. **merges the branch into `main` only after a successful publish**;
7. **records** the batch: one `actions.json` entry per action plus a batch entry, a
   `pengyos-attest` record for the batch and one per action, the report, and the
   cooldown / `patched_by` entries in `state.json`.

Any step failing stops the run **before anything is published**, restores main's
working tree, leaves the branch for inspection, and reports the failure. A build or
boot failure is recorded as `batch_failure` so `status` can say the batch was blocked
rather than the loop being quiet.

**What batching deliberately does not do.** It does not verify each change in
isolation: the batch is verified as a whole, by building and booting the image that
contains all of it. That is the same evidence a single change had, and it is the
evidence that matters — the thing that boots is the image, not the diff. It also
does not publish more than `max_releases` images a night, and it never reaches
`update-promote`.

### Guards (each one named, each one tested)

| Guard | Rule |
|---|---|
| `channel_locked` | the publish channel is the constant `testing`; `update-promote` and `--channel stable` are unreachable — asserted on the argv before anything runs |
| `major_never` | a bump that changes the release series is never acted on |
| `stock_version_immovable` | the routine lane may only bump pins we own; a stock package moves with a Buildroot release, not with a nightly |
| `evidence_required` | NVD must place the shipped version inside the affected range (patch shape) or prove the target closes the CVE (bump shape) |
| `fix_commits_available` | a patch is carried per CVE, and only for a CVE NVD names a **fetchable** upstream fix for (a commit, or a pull/merge request on a host that serves `.patch`); a component with nothing fetchable is refused, and one with a mix ships the fetchable part and names the rest as still open |
| `patch_does_not_apply` | a candidate whose patch was *proved* not to apply is not retried: the pre-flight spends the night's one action slot, so a known-bad candidate would block every candidate behind it. Needs a human to adapt the patch or pick another fix |
| `confirmed_only` | pkg-stats `unsure_cves` are never actionable |
| `not_host_only` | a build-host package is not a security update to an image |
| `denied_component` | `linux`, `nvidia-open`, `buildroot` and COSMIC are report-only in this phase (a kernel bump invalidates the NVIDIA module build and every stamp chain; a Buildroot release means a new tree and a full rebuild; both need their own lane) |
| `cooldown` | the same component is not touched twice inside `cooldown_days` |
| `cap` | at most `max_actions` candidates in the night's batch (the batch size, not the release count) |
| `release_cap` | at most `max_releases` signed images published per night — checked against what was already published today, so a second `act --apply` cannot publish again |
| `batch_conflict` | two actions in one batch may not edit the same file: a conflict is a human's decision, not a merge |
| `clean_tree` / `key_present` | act refuses rather than publishes from a dirty tree or without a key |
| `gate_first` | the gate runs before any build; a failed gate publishes nothing |

### Acceptance criteria (phase 3)
12. A batch of several allowed actions produces **one** branch, one VERSION bump, one
    gate run, one `release.sh` invocation and one published version, with **one commit
    per action**.
13. `release.sh` is invoked once, for the **union** of the editions the batch touches,
    and the argv still contains no `update-promote` and no `--channel stable`.
14. A package whose pre-flight fails is dropped from the batch, recorded as
    `patch_does_not_apply`, and the remaining actions still build and publish. If every
    package fails, nothing is built and nothing is published.
15. A second `act --apply` on the same date publishes nothing (`release_cap`), and says
    why.
16. Two actions editing the same file are refused as `batch_conflict` before any branch
    is created.
17. A failed build/boot records `batch_failure` and leaves `main` where it started;
    `status` reports the blocked batch rather than reading as a quiet, healthy loop.
18. Dry run still changes nothing on disk and prints the batch composition — every
    action, the editions, and the commands it would run.

### Acceptance criteria (phase 2)
6. `plan` on the live state produces an action where NVD proves the exposure and
   names a fix, and a refusal entry naming the guard for every candidate it did not
   act on.
7. A CVE whose NVD record names no upstream commit produces **no** action.
8. `act` without `--apply` (or without `act_enabled`) changes nothing on disk and
   prints the diff and the commands it would run.
9. `act --apply` against a fake build publishes nothing when the gate, the build or
   the boot check fails, and leaves `main` where it started.
10. No argv the tool can produce contains `update-promote` or `--channel stable`.
11. Tests stay offline: NVD records, stock recipe/hash files, upstream patches and
    the build are fixtures or injected.

## Acceptance criteria
1. `pengyos-nightly run --no-email` on behoserver writes observations, a
   triaged state and an HTML report, with every source `ok` or an explicit
   error.
2. Each observer parses a recorded fixture of its real upstream format
   (pkg-stats JSON, kernel.org releases.json, `git ls-remote` output, KEV JSON,
   EPSS JSON) and classifies routine vs major correctly.
3. Triage applies the rules (host-only → P2; KEV floor P1) without the model,
   parses a valid model answer, and falls back on garbage.
4. A second run with no upstream change reports nothing new and sends no
   email (except on Mondays).
5. Tests run offline (`python3 -m pytest -q`); network and model calls are
   behind injectable functions.
