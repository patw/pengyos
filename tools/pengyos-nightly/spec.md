# pengyos-nightly — the nightly maintenance loop

## Summary
Every night, find out what changed upstream for what PengyOS ships, decide what
matters, and tell the maintainer. Later phases act on it (bump, build, publish to
testing); phase 1 only observes, triages and reports.

## Purpose
PengyOS pins everything: Buildroot and its ~450 packages, our authored recipes,
the COSMIC commits, the kernel, the NVIDIA driver. Pins go stale, and stale pins
collect CVEs. This loop keeps a human informed without asking a human to read
NVD, and it runs on a local model, where tokens cost electricity
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
A Python CLI `pengyos-nightly` (standard library only) run by cron on the build host:

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
An HTML email through a machine-local mailer (`$PENGYOS_EMAIL_SCRIPT`, default
`~/.local/bin/pengyos-email`; absent means report-only), and the same content
as `<state-dir>/reports/<date>.html`.

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
act_time     = "21:00"          # when this machine's act run fires; the report and the
                                # decision page quote it, so it is policy, not a literal
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
| `patch_dir` | a stock package with a CVE NVD proves is open in the pinned version | the upstream fixes NVD names — a commit, or a pull/merge request on a host that serves one as a patch — as `buildroot-external/patches/<pkg>/<version>/*.patch`, **one file per commit** (a review request is split on its `From <sha>` heads) and **re-rooted for the package's own tarball** when upstream keeps a monorepo, reached through `BR2_GLOBAL_PATCH_DIR` (`package/pkg-utils.mk:pkg-patch-hash-dirs`); recompose the defconfigs. Only the CVEs whose fix is fetchable *and* belongs to this package are carried and claimed; the rest are recorded as `still_open` with the reason |

Hashes for a bump come from upstream's own published checksum file when the stock
`.hash` names one (GNOME publishes `<pkg>-<ver>.sha256sum`); otherwise the artifact
is downloaded and hashed here, and the plan labels it `self-attested`.

### The tree declares what it carries

A `patch_dir` action closes CVEs in the built image and in `state.json`, but the tree
itself has to say so or Buildroot's own `pkg-stats` keeps reporting those CVEs open the
next morning. Measured on the 2026-10-06 desktop scan: `ffmpeg 6.1.5` reported 37 open
CVEs and no ignores, and libxml2 reported "affected by CVEs" for all seven CVEs whose
fixes shipped in 26.10.8. The loop's own reporting was honest (`patched_by` counts as
closed); the tree's was not.

Upstream's mechanism is `<pkg>_IGNORE_CVES`, and it is the one Buildroot's manual
prescribes for exactly this case (`docs/manual/adding-packages-generic.adoc`: "typically
used when the CVE is fixed by a patch in the package", each entry preceded by a comment
naming the patch):

- `pkg-stats` reads it in `package_init_make_info` (`printvars VARS=… %_IGNORE_CVES`) and
  `show-info` emits it (`package/pkg-utils.mk`);
- it is live in our tree today: 13 packages in the desktop scan carry `ignored_cves`, all
  of them upstream's own entries (`podman v5.7.0` ignores CVE-2026-33414);
- `pkg-stats` also reports `stale_cve_ignores` — 5 packages carry ignores NVD no longer
  considers needed (`libsndfile` CVE-2022-33065) — the audit trail for an entry that
  outlived its patch;
- a br2-external tree can declare it for a **stock** package: `include $(BR2_EXTERNAL_MKS)`
  (Makefile:564) comes after `include package/Makefile.in` (Makefile:526), so the later
  assignment wins. Verified: `make printvars VARS=LIBXML2_IGNORE_CVES` in
  `output/pengyos-desktop` reports all seven, and reports nothing without the lines.
  **Run it in a configured output tree** — in the source checkout there is no `.config`,
  so it reports nothing even when the mechanism works.

Built (2026-10-07): `edits.cve_declaration_edit` writes the declarations to
`buildroot-external/patches/cve-declarations.mk`, included from `external.mk`, and the act
lane calls it **after the pre-flight**, from the patches that survived — not from what the
plan promised. A fix Buildroot rejected therefore gets no declaration: an ignore that
outlives its patch would hide a real exposure, which is a worse claim than the one this
fixes. Each block is regenerated whole, so a package left with nothing to carry loses its
block. A declaration that cannot be written stops the batch (`cve-declaration`), because
publishing a fix the loop cannot account for is how the tree ends up claiming what it
cannot show. `scripts/pengyos_gate.py:check_patch_declarations` compares every patch set in
`divergence.toml` against the tree and fails on a missing declaration, an empty patch
directory, or a declaration the ledger does not back.

Not built: retiring a declaration when a **tree move** makes it unnecessary. The list is
there (`tree_move`'s `retires`); acting on it is phase 4's executable half.

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
   seconds, before any build. **The unit of failure is a patch, not a package**:
   `apply-patches.sh` names the patch it could not apply (`Patch failed!  Please fix
   <patch>!`), so `triage_package` takes that one file out of the tree, re-extracts,
   and tries again. What survives is what the image carries, and `shrink_action`
   recomputes `closes` / `still_open` / `patches` from it, so the claim follows the
   tree. A rejected fix is recorded per fix (`patch_failures[<pkg>]["fixes"]`), not per
   component; a package whose patches all fail is dropped, and its now-empty
   `patches/<pkg>/` is pruned. A failure Buildroot does not attribute to a named patch
   stops the triage rather than guessing. If every package fails, nothing is built and
   nothing is published;
5. **builds, boot-checks, signs and publishes once** — `scripts/release.sh --publish
   --channel testing` for the **union of the editions the batch touches**, with one
   notes file listing every change and its evidence. `release.sh` already refuses a
   dirty tree and refuses to publish an image that was not boot-checked, so the boot
   test of the batch *is* the sign-off: an image that does not reach login is never
   signed. `--push` only if `push = true`;
6. **merges the branch into `main` only after a successful publish**;
7. **records** the batch: one `actions.json` entry per action plus a batch entry, a
   `pengyos-attest` record for the batch and one per action, the report, and the
   cooldown / `patched_by` entries in `state.json`;
8. **tells the maintainer** when it refused: one mail naming the step that refused, the
   changes it blocked, the editions the image would have covered and the CVE matches
   left open (added 0.14.0). Silence is the correct signal for *"nothing to do"* and
   `nothing_allowed` sends nothing; a **refusal** is not, and until now it was silent
   too — it landed only in the cron log, `actions.json` and `status`. On 2026-10-18 a
   6-change batch closing 11 CVE matches was refused at `clean_tree` by a PengyTasker
   job holding the shared checkout dirty, and the maintainer's morning question was
   *"did the act loop run at all?"*. `--no-email` suppresses it; the notice is written
   to `reports/<date>-act.html` before the mailer runs, and a mailer that fails never
   changes the act result or the exit code.

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
| `major_never` | a bump that changes the release series is never acted on unless the operator approved that version (the `approved` lane). A `tree_move` is exempt: crossing to a new Buildroot series **is** the scheduled event, and `major_never` guards package pins, not the tree |
| `tree_move_not_executable` | a `tree_move` is planned, guarded and accounted for, then refused **by name**: the act lane has no full-rebuild path yet, so publishing a plan that looks executable would hand a tree move to a lane that cannot run it and turn a design gap into an act-time build error |
| `stock_version_immovable` | the routine lane may only bump pins we own; a stock package moves with a Buildroot release, not with a nightly |
| `evidence_required` | NVD must place the shipped version inside the affected range (patch shape) or prove the target closes the CVE (bump shape) |
| `fix_commits_available` | a patch is carried per CVE, and only for a CVE NVD names a **fetchable** upstream fix for (a commit, or a pull/merge request on a host that serves `.patch`); a component with nothing fetchable is refused, and one with a mix ships the fetchable part and names the rest as still open |
| `no_fix_for_this_package` | a fetched patch must be a fix **for this package**: not a document that only adds new files, not a fix for a sibling component of a monorepo, not one spanning several components. Each refusal is recorded with its reason and the commit subject |
| `artifact_unreachable` | a `recipe_bump`'s target artifact does not resolve at the recipe's SITE, so its hash cannot be taken. The plan records the URL it tried (`action.unreachable`). Named apart from `recipe_unreadable` because the answers are opposite: a phantom version (release-monitoring naming a release upstream never tagged) means the finding is dropped, a SITE pattern that moved means the recipe is fixed, and only a read error means the tree is broken. The decision page will not accept an approval for a target the plan proved unfetchable |
| `recipe_unreadable` | the recipe the act lane must edit cannot be read — a tree defect, and only that |
| `patch_does_not_apply` | a **specific upstream fix** proved not to apply is not retried: the record is `patch_failures[<pkg>]["fixes"]`, the list of upstream fix URLs that failed, and a candidate is refused only when it wants one of them. A cherry-pick authored against a newer series says nothing about a fix that would apply cleanly, so the package stays open. A record with no fix list predates per-fix tracking and is honoured as a component-wide block until a human clears it |
| `confirmed_only` | pkg-stats `unsure_cves` are never actionable |
| `not_host_only` | a build-host package is not a security update to an image |
| `operator_denied` | the operator denied this item on the decision page (`decisions.json`); it is not considered, and is recorded as refused by this name |
| `lane_allowed` | a candidate only enters the night if its lane is enabled. `approved` is enabled by the operator's approval of that version on the decision page. `bump` is its own lane and is enabled **only** by `bump_buildroot` being set — so scheduling a Buildroot bump cannot quietly enable the kernel or the NVIDIA driver, which share the "new release series" label |
| `denied_component` | `linux`, `nvidia-open`, `buildroot` and COSMIC are report-only in this phase (a kernel bump invalidates the NVIDIA module build and every stamp chain; a Buildroot release means a new tree and a full rebuild; both need their own lane). `buildroot`'s default is lifted **only** for the event the operator scheduled (`bump_buildroot` names the target); the others stay denied whatever is scheduled |
| `bump_event_approved` | a new Buildroot release series is a *finding*; the tree move is refused unless `bump_buildroot` names exactly that target |
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

## Phase 4 — the Buildroot release-bump lane (the decision half is built; the rebuild half is not)

**Why this lane and not an LTS core.** Measured from the upstream tag list on 2026-10-07:
Buildroot releases quarterly — `2025.08.x, 2025.11.x, 2026.02.x, 2026.05.x, 2026.08` — with
point releases in between, and the `.02` series is the long-term-support one. Our tree is on
`2026.08`, the newest. A stable series gets no security maintenance of its own between
releases; what it gets is the *next release*, which arrives every quarter and carries the
packages upstream has since fixed. LTS is that mechanism stretched to two years, and it buys
the long horizon by **bumping packages inside an old series** — which is the same version
collision the newest-COSMIC/NVIDIA tree exists to avoid. The quarterly bump gets the same
security benefit at a quarter's latency and without freezing the tree.

The observation already exists: `sources.buildroot_findings` reads the tag list, calls a
newer point release *routine* and a newer series *major*, and names the LTS series when it
sees one. What is missing is the action. `buildroot` is in `DENIED` today, with the reason
already written: "a Buildroot release change means a new tree and a full rebuild of every
edition; that is a scheduled event, not a nightly one." Phase 4 makes that scheduled event
real instead of report-only.

**Shape: `tree_move`, not `recipe_bump` or `patch_dir`.** The edit is to the tree the
editions build from, and it has four parts, each one named in the plan:

1. **the line** — which Buildroot the build uses, and the checkout/verification of it;
2. **the external tree re-applied** — our `buildroot-external` layers, defconfigs and
   `BR2_GLOBAL_PATCH_DIR` against the new `Config.in`/package set;
3. **the pins re-resolved** — COSMIC commits, the NVIDIA driver version and the kernel,
   each checked against what the new line actually provides;
4. **the carried patches retired** — every patch under `patches/<pkg>/<version>/` that the
   new line already contains is *removed*, and the CVE it closed is recorded as closed by
   the release, not by us. A patch the new line still lacks moves to the new version's
   directory. This is the honest version of the "already present in the pinned version"
   problem the patch lane keeps hitting: the loop stops re-fixing what upstream fixed.

**It is its own release event.** A tree move means a full rebuild of every edition, so it
cannot share a night with a patch batch: it takes the one `max_releases` slot, and its
pre-flight is the whole build plus the boot check, not a package re-extract.

### Built (2026-10-07): the decision, its guards, and its accounting

The lane can be scheduled, planned, guarded, reviewed and reported. It cannot yet be run,
and it says so.

| Part | Where |
|---|---|
| the schedule | `config.bump_buildroot` (default `""`). Empty means the lane is **inert**: a newer series stays a finding and no candidate is even considered |
| the candidate | `planner`: appears only when scheduled, with `shape = "tree_move"`, `current` from the observation and `target` from the schedule, and `editions` read from the repo's own `targets.toml` |
| the edit | `edits.tree_move(repo, target)`: rewrites `br_version` for **every** edition — the tree choice is declared, not implied |
| the retirement accounting | `edits.carried_patch_files(repo)` lists every patch the loop currently carries, recorded in the plan as `retires` with a `retire_check` naming what has to be re-checked against the new line |
| the guards | `bump_event_approved`, `lane_allowed` (the `bump` lane), `denied_component(component, cfg)`, `major_never`'s `tree_move` exemption, `tree_move_not_executable` |
| the reviewability | the edit and the retirement list are recorded **even when the action is refused** (one local file read, no network, no build), so `plan.json` shows what would have changed and which guard stopped it |
| the report | `status` prints `bump lane: inert` or `bump lane: scheduled -> 2026.11 (not executable yet)`, plus a `!` line when a newer series is a finding with nothing scheduled, when the scheduled target is not what the observer found, or when a schedule has no finding behind it |

### Not built: the rebuild

Nothing here is implemented, and `tree_move_not_executable` exists precisely so that no plan
can imply it is:

1. fetch and **verify** the new line (download, hash, the Buildroot tree itself);
2. recompose `buildroot-external` — layers, defconfigs, `Config.in` and `BR2_GLOBAL_PATCH_DIR`
   — against the new line's package set;
3. re-resolve the pins: COSMIC commits, the NVIDIA driver, the kernel, each checked against
   what the new line actually provides;
4. the full rebuild of every edition, the boot check, and one signed release per edition set;
5. the **actual** retirement: drop a carried patch the new line already contains, move the
   ones it lacks to the new version's directory, and record each dropped patch's CVE as closed
   by the release rather than by us.

**Guards, each one named:**

| Guard | Built? | Refuses |
|---|---|---|
| `bump_event_approved` | yes | the lane runs only when the operator has scheduled it (`bump_buildroot = "2026.11"` in `nightly.toml`); a new upstream tag is a finding, never an action |
| `lane_allowed` (the `bump` lane) | yes | the lane exists only when the schedule does, so a Buildroot bump cannot enable the kernel or the driver |
| `tree_move_not_executable` | yes | any `tree_move`, by name, until the act lane has a full-rebuild path. This is the guard that keeps phase 4 honest about what is missing |
| `cosmic_compat` | no | the COSMIC pins must still build against the new line's toolchain and libraries; unproven is refused |
| `nvidia_module_build` | no | the kernel-module build must succeed against the new line's kernel headers, on real hardware — QEMU cannot prove a driver |
| `patch_retirement` | no | a carried patch the new line already contains must be dropped, not duplicated; a patch that would be silently dropped without a record is refused. (The *list* of carried patches is built; the check against the new line is not) |
| `edition_coverage` | no | every edition in the channel must be rebuilt by the event; a partial set is refused rather than publishing a half-moved channel |

**Acceptance criteria (phase 4)**

Status as of 2026-10-07, measured against the tests, not against intent:

19. **met.** A new Buildroot tag produces a *finding* and no action until `bump_buildroot`
    names a target — an inert lane produces no candidate at all — and `status` shows the
    scheduled event, its target, and that it is not executable.
20. **not met.** A tree move publishes one version for every edition in the channel, or
    publishes nothing. The plan names every edition; nothing publishes, because
    `tree_move_not_executable` refuses the action.
21. **partly met.** The carried patches are listed and the retirement check is recorded in the
    plan. Nothing is dropped and no CVE is recorded as closed by the release, because the new
    line is not fetched.
22. **not met.** `cosmic_compat` and `nvidia_module_build` are named in this spec and exist in
    no code.
23. **not met.** There is no build to fail.

## Phase 5 — the decision page (added 2026-10-16)

The report told the operator what needed a decision and offered nowhere to make it: a
major finding (a new PengyR minor, an authored recipe's new series) stayed in the email
night after night. `pengyos_nightly web` is that place: a small Flask page on the build
host listing everything the loop recommends, each with **Approve** and **Deny**.

- **What it lists.** Every finding in `observations.json` (version updates; the ones no act
  path can carry out — `denied_component`, or a stock package — are shown with the reason
  and cannot be approved), every security candidate in `plan.json` (allowed ones can be
  **held**, blocked ones dismissed; there is nothing to approve, since the security lane
  runs on its own guards), what was decided earlier (with Undo), and the recent act runs.
  A dirty working tree and `act_enabled = false` are shown, because either one means
  nothing approved gets built.
- **What it writes.** One file, `<state-dir>/decisions.json`: `{"<kind>:<component>":
  {decision, target, at}}`, `kind` being `update` or `security`. It never builds, signs,
  publishes or edits the tree.
- **What an approval means.** The planner considers the finding in the `approved` lane,
  which `lane_allowed` enables and `major_never` lets cross a series. **Every other guard
  still runs**: an approval cannot lift a cooldown, a missing fix, a report-only
  component, a dirty tree, a missing key, the gate, the boot check or the testing-only
  channel. It covers the approved version and later patch releases of the same series; a
  new series is a new question.
- **What a denial means.** The item is not considered (`operator_denied`) and leaves the
  page's waiting list, over the same span as an approval.
- **When it counts.** `act` re-plans when `decisions.json` is newer than `plan.json`, so an
  answer given after the plan is written still counts in the same night's act run. The
  plan/act schedule is machine-local (a crontab), not the tool's: the reference build host
  plans at 19:00 and acts at 02:00 since 2026-10-19, so an approval has a 7-hour window to
  land.
- **The email links it** when `web_url` is set in `nightly.toml`.

### Acceptance criteria (phase 5)
24. An approved major finding is an allowed action in the `approved` lane; the same finding
    without an approval is not a candidate. An approval covers later patch releases of the
    approved series and not the next series.
25. An approval of a `denied_component` is refused by that guard; the page will not record
    an approval for it, nor for a stock package, nor for a security candidate.
26. A denied update or a held security candidate is not an action and is refused as
    `operator_denied`.
27. `act` re-plans when a decision is newer than the plan, and only then.
28. Tests run without a server (`view()` is data; the routes use Flask's test client).

## Acceptance criteria
1. `pengyos-nightly run --no-email` on the build host writes observations, a
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
