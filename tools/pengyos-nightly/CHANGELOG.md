# Changelog

All notable changes to `pengyos-nightly` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

## [0.5.0] - 2026-10-06

### Changed
- **Phase 3: a batch of changes, one release.** `act` no longer runs one action to one
  image. It applies the night's allowed actions to one branch, pre-flights each
  package, commits one change per commit, then runs the gate, the build, the boot
  check, the signature and the publish **once** for the union of the editions the
  batch touches. The batch is the unit of work; the signed image is the unit of risk.
- **Two caps, deliberately different numbers.** `max_actions` 1 → **12** (how much the
  loop may prepare and verify in a night); new `max_releases` = **1** (how many signed
  images may reach the feed in a night). The old cap was never a hardware limit: one
  full action end to end measured 18 m 14 s on 2026-10-05 and all five editions
  rebuilt incrementally in 9 m 42 s on 32 cores, so capping actions at 1 capped CVEs
  closed while paying the full verification cost once per action.
- **Pre-flight now decides what is real, before anything is committed.** A package
  whose `<pkg>-dirclean`/`<pkg>-patch` fails is dropped from the batch, its edits are
  reverted out of the tree (including the empty directories a deleted patch leaves
  behind), and it is recorded as `patch_does_not_apply` so the planner refuses it by
  name next night. Previously the edits were committed before the pre-flight, so a
  dropped change could be published and merged into `main` without ever being built.
- **`status` reports a blocked batch.** A batch that ran and published nothing sets
  `ok = false` and names the reason, instead of reading as a quiet, healthy night.
  `status` also prints the batch policy: `up to N change(s) -> M release(s)/night`.

### Added
- `guards.release_cap` — at most `max_releases` signed images per night, checked
  against what the loop already published today, so a second `act --apply` is a no-op
  rather than a second release. `guards.batch_conflict` — two changes in one batch may
  not edit the same file; refused before a branch exists, because sequencing them is a
  human's decision.
- `act.run_batch` / `act.record_batch`, `batch_notes` (one notes file naming every
  change and its evidence), `batch_commit_message`, `batch_merge_message`,
  `published_today`.
- `act --max N` to cap one run's batch below `max_actions`.
- `tests/test_batch.py` — 14 tests: one image for a 3-change and a 12-change batch;
  `release.sh` invoked once for the union of editions and never with `update-promote`
  or `--channel stable`; a dropped package does not sink the batch and does not stay in
  the tree; every-package-fails publishes nothing; a build failure records
  `batch_failure` and leaves `main` unmoved; a second run the same night is refused by
  `release_cap`; a same-file conflict is refused before a branch; dry run runs nothing.

### Fixed
- `record()` took the action id from `result["action"]`, which a batch result does not
  carry; it now takes it from the action itself.
- Commits are staged per step. `git add -A` before the VERSION commit swallowed every
  pending edit into it, leaving the per-change commits empty — one large commit, which
  is the opposite of what makes a batch reviewable change by change.

## [0.4.0] - 2026-10-05

### Added
- **Phase 2: `plan` and `act`.** The loop now acts on what it observes. `plan`
  decides deterministically from its own state and writes `plan.json` — actions with
  their evidence and exact edits, plus **every candidate it refused and the guard that
  refused it** (a plan with only actions proves nothing: you cannot tell "a guard
  stopped this" from "nobody looked"). `act` runs one allowed action end to end:
  guards → branch → edits → recompose defconfigs → VERSION → commit → gate →
  `<pkg>-dirclean` + `<pkg>-patch` per edition → `release.sh --publish --channel
  testing` → merge to `main` → record. Dry run unless `--apply` **and** `act_enabled`.
- **Stable is unreachable in code, not by policy.** `guards.CHANNEL` is the constant
  `testing` and `assert_locked()` re-checks every argv before it runs, so
  `update-promote` and `--channel stable` cannot be assembled. `main` moves only after
  a successful publish, so main means "what the fleet can actually get".
- **Evidence from NVD's own affected ranges**, read two ways because the two edit
  shapes need different proofs: `affected` (is the version we ship inside the range —
  what carrying a patch needs) and `covered` (does an available release close it — what
  a bump needs). `versionEndExcluding 2.15.4` exposes 2.15.3 and is closed by 2.15.4;
  `versionEndIncluding 2.15.3` exposes 2.15.3 and is closed only by something above it;
  a match pinned to `1.35` with no end field names exposure and no fix. A record whose
  references are vendor errata (tar's CVEs point at Red Hat advisories) has nothing to
  carry, so it is reported and never acted on.
- **Two edit shapes.** `recipe_bump` for packages we author (its own `.mk` + `.hash`),
  and `patch_dir` for stock packages: the upstream fix commits NVD names, as
  `buildroot-external/patches/<pkg>/<version>/*.patch` through `BR2_GLOBAL_PATCH_DIR`.
  The recipe's make prefix is read from the recipe (`gstreamer1` assigns
  `GSTREAMER_1_VERSION`; `gst1-plugins-good` assigns `GST1_PLUGINS_GOOD_VERSION`) —
  deriving it from the package name reassigns a variable nothing reads.
- **Guards, each named and each mutation-checked** (21 mutants, 0 survived):
  `channel_locked`, `major_never`, `stock_version_immovable`, `evidence_required`,
  `fix_commits_available`, `confirmed_only`, `not_host_only`, `denied_component`
  (linux / nvidia-open / buildroot / cosmic are report-only in this phase), `cooldown`,
  `cap`, `clean_tree`, `key_present`, `gate_first`.
- **`status` now reports the act lane**: whether it is enabled, how old `plan.json` is,
  how many actions are allowed/refused, which components have been acted on, and what
  is queued next — so "the loop is quiet" and "the loop has work it is not doing" are
  different, visible answers.
- Policy in `<state-dir>/nightly.toml` (machine-local): `act_enabled`, `push`, `lanes`,
  `priorities`, `max_actions`, `cooldown_days`, `allow_patch_shape`, `skip_cosmic`.
  Key and site remote come from `PENGYOS_RELEASE_KEY` / `PENGYOS_SITE_REMOTE` (env) or
  the key file / `~/Personal/.secrets`; missing either one makes `act` refuse rather
  than crash.
- Each published action is recorded in `actions.json` and appended to a
  `pengyos-attest` hash chain (`attest.jsonl`).
- **Resolution kinds, because a patch does not move a version.** `state.json` now
  distinguishes *open*, `resolved` (the match disappeared — the package moved past it)
  and `patched_by` (we carry the fix as a patch on the pinned version, with the date,
  the release and the patch files). `triage`, `report`, `status` and `plan` all honour
  it: a carried CVE is reported under "closed by patches we carry", is not re-triaged
  every night, and is never a candidate again. Without this the 7 libxml2 CVEs fixed
  tonight would appear as open in every report forever, and the loop would re-propose
  the same patch when its cooldown expired.

### Fixed
- **A stock package cannot be version-bumped from a br2-external tree, and the first
  attempt proved it.** `LIBXML2_VERSION = 2.15.4` in `external.mk` made Buildroot
  download 2.15.4 and check the hash supplied through `BR2_GLOBAL_PATCH_DIR`, but the
  pattern rule `$(BUILD_DIR)/%/.stamp_extracted` (`package/pkg-generic.mk:206`) had
  already fixed the stamp path and `$(@D)` when the stock recipe was parsed —
  `include package/Makefile.in` (Makefile:526) runs before
  `include $(BR2_EXTERNAL_FILE)` (Makefile:559) — so extraction died on a missing
  `build/libxml2-2.15.4` while make reported the 2.15.3 stamp. `libxml2-dirclean` did
  not help. The supported shape is carrying the fix commits as patches; repro and
  reasoning are in `edits.py`.
- **A new patch is invisible to an already-built tree.** Buildroot's
  `.stamp_extracted` / `.stamp_patched` say "done", so without `<pkg>-dirclean` the
  image would be rebuilt from the old, unpatched source and the fix would be silently
  absent. `act` now runs `<pkg>-dirclean` per affected edition.
- **A cherry-picked commit can apply to the source and fail on upstream's own tests.**
  podman's did: 3 hunks OK in `libpod/container_internal_common.go`, 2 FAILED in
  `test/e2e/run_working_dir_test.go` — caught 20 minutes into a build. `<pkg>-patch`
  (extract + patch only) now runs as a pre-flight, so it stops the run in seconds,
  before any build and before any publish.
- **A failed run spent the cooldown.** The first attempt recorded the component in
  `state.json` even though it published nothing, so libxml2 came back blocked for 7
  days by its own failure and the plan moved on to podman. Only a published action
  consumes the cooldown; the attempt is still recorded in `actions.json`.
- `external_tree` from `targets.toml` is resolved against the targets file (as
  `pengyos-build` does), not against the cwd — the first run handed make
  `tools/pengyos-nightly/buildroot-external`.
- `parse_pkgstats` now carries `pkg_path`, `mk_path`, `cpeid` and `patch_files`, so a
  sub-package under `package/gstreamer1/` is resolvable and the CPE product comes from
  pkg-stats rather than a guessed alias. `sources.nvd_raw` exposes the whole NVD record
  (`configurations` included); `nvd_record` keeps its summary shape.
- A published `act` re-plans, so `plan.json` and `status` describe the world after the
  action instead of advertising the action it just published.
- **A candidate whose patch was proved not to apply is refused by name**
  (`patch_does_not_apply`, remembered in `state.json` under `patch_failures`). Without
  it, podman would re-extract in four editions every night and spend the loop's one
  action slot on a candidate that cannot work, blocking every candidate behind it. The
  refusal names the file that failed, because the decision after it is a human's:
  adapt the patch, or choose a different fix.
- `act` reports a failure instead of raising one. A repo that is not a repo, a missing
  `VERSION`, or an attestation chain it cannot append to are logged conditions, not
  tracebacks in a cron log.
- **A quiet night exits 0.** `act --apply` with nothing allowed in the plan used to
  exit 1, which made a healthy loop indistinguishable from a dead one to a watchdog —
  the exact confusion `status` exists to remove. A real failure still exits 1.

### First real run (2026-10-05)
`security-libxml2-2.15.3`: 7 upstream fix commits (NVD named one per CVE) carried onto
the pinned 2.15.3, published as **26.10.8 to testing** for Desktop and Desktop NVIDIA
and pushed to the live feed. Verified independently: `dict.c` carries the patch's
`INT_MAX / 2` checks, no `.rej` files, `libxml2.so.16.1.3` rebuilt, both Desktop
installer sticks boot-checked under QEMU with zero failed units, and behoweather —
already on `channel = testing` — reports *"PengyOS 26.10.8 is available"*. Stable
untouched.

### Tests
- 87 new tests (129 total), offline: real NVD records copied from the live fkie-cad
  clone, real recipe text, a real upstream `.patch`, and a real git repo for the act
  lane (the claim under test is "main does not move when a run fails", which is worth
  nothing against a stubbed git). make, the gate and `release.sh` are fakes.
- Mutation-checked: 31 mutants of the guards, evidence rules, edit shapes, act ordering
  and the resolution kinds, all caught. Two of them found bugs in code I had just
  written — `act` exiting 1 on a quiet night, and `triage` re-triaging a CVE the loop
  had already carried a fix for.

## [0.3.0] - 2026-10-05

### Added
- **`pengyos-nightly status`** — answers "is the loop alive?" without an email. The
  loop is silent by design when nothing is new (that is the operator's chosen
  design), and silence is exactly what made two nights indistinguishable from a
  broken loop. `status` is the inspectable half: read-only, no email, and it reports
  the last observation and its age, package/finding counts, source errors, tracked
  CVE matches with the P0/P1 split, how many are triaged-but-never-reported, and
  when the last report and digest went out. Exit code 1 when the loop is stale
  (no observation in >30 h), a source failed, or the backlog is unreported — so a
  watchdog can rely on the exit code alone.

### Fixed
- **`--no-email` was consuming the report queue.** The send branch was
  `if sent or args.no_email:` — so a run that deliberately did *not* email still
  marked every new CVE and finding as reported. A development run on 2026-10-03
  marked 184 CVE matches and 7 findings that way; the 19:00 cron run that night,
  and every night after, correctly concluded "nothing new" and stayed silent. The
  operator saw no email for two nights and had no way to tell "quiet" from
  "broken". `--no-email` now renders the report to disk and leaves the queue
  intact; `--mark-reported` is the explicit opt-in for when you have already read
  the on-disk report.
- **A forced report could be empty.** `report --force-email` on a quiet,
  non-digest night produced a 420-byte email whose entire content was "nothing
  new" (I triggered this myself while testing). Whenever the loop sends, the
  standing state now travels with it: open CVE matches grouped by package
  worst-first, and the tracked upgrade list.
- **A digest titled "nothing new" while 67 P1 CVEs were open.** That subject
  teaches the reader to delete the one email that matters. Digests and forced
  reports now lead with the standing load, e.g.
  `PengyOS nightly 2026-10-05: 67 P0/P1 still open of 184 tracked, 5 upgrades tracked`.

### Tests
- 42 tests, up from 31. `test_no_email_does_not_consume_the_report_queue` (the
  regression, including that the *next* run still sends the same content),
  `test_mark_reported_clears_the_queue_without_sending`,
  `test_a_forced_report_is_never_blank`,
  `test_digest_subject_carries_the_standing_load_not_just_whats_new`, and seven for
  `status` (healthy, stale via a backdated file plus the CLI exit code, source
  errors, the triaged-but-never-reported backlog, never-run, read-only,
  last-report/digest). Each is mutation-checked: restoring `if sent or args.no_email`,
  dropping the standing state, removing the digest bits, and deleting the staleness
  and backlog checks each fail the suite.

### Observed (not a code change)
- The loop itself is healthy: cron fires at 19:00, observe reaches pkg-stats
  (410 packages across 5 editions, 0 source errors), the 4.0 GB NVD clone refreshes,
  and the local model endpoint answers. A fresh observe today dropped the two COSMIC
  findings (the epoch-1.9 move landed) and reported no PengyR finding (the pin is
  current) — 7 findings down to 5. The kernel finding is genuine: the tree is on
  7.2.5, confirmed by `uname -r` on behoweather, with 7.2.9 available.

## [0.2.0] - 2026-10-05

### Added
- **A `pengyr` observe source.** The agent every image ships is an authored
  Buildroot recipe (`buildroot-external/package/pengy`, `PENGY_VERSION` from
  github.com/patw/PengyR), so `pkg-stats` never sees it: it is not a stock
  Buildroot package and it is not in release-monitoring.org. The loop that exists
  to catch stale pins was blind to the pin for the assistant itself — PengyR 1.11.0
  and 1.11.1 shipped upstream while the images stayed on 1.10.0, and nothing
  reported it.
  `sources.pengyr_findings` reads the pin out of the recipe (the way the NVIDIA pin
  is read) and compares it against `git ls-remote --tags`. A patch bump inside the
  pinned minor is `routine`; a new minor is `major`, because it changes the agent
  every image carries. `available` is reported as the version you would write into
  `pengy.mk` (`1.11.1`), not the git tag (`v1.11.1`).
- Grounded on a real capture: `tests/fixtures/ls-remote-pengyr.txt` (52 tags,
  captured 2026-10-05). Early PengyR tags are bare (`1.0.0`), later ones are
  `v`-prefixed, so both spellings are handled and tested.

### Tests
- Five unit tests (the real minor bump from the real tags, a patch bump as
  routine, silence when current, non-version and pre-release tags ignored, bare
  tags) plus an assertion in the full observe→triage→report flow that the finding
  actually reaches the report. Mutation-checked: disabling the wiring in `cli.py`
  fails the flow test.

## [0.1.0] - 2026-10-03

### Added
- Phase 1 of `spec.md`: `observe` (Buildroot `pkg-stats` per edition for CVEs
  and upstream versions, Buildroot tags, kernel.org, COSMIC epoch tags, NVIDIA
  driver tags, CISA KEV, FIRST EPSS), `triage` (host-only and KEV rules, then
  the local model through Pengy's gateway as a plain completion, with a rules
  fallback), `report` (HTML email via the email skill, a copy under
  `reports/`, sent only when something is new, a source failed, or on Monday).
  P0/P1 CVEs are grouped by package, worst first: the decision is "move
  ffmpeg", not thirty separate CVEs.
