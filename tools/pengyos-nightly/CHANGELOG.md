# Changelog

All notable changes to `pengyos-nightly` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Fixed
- **A golang/cargo bump now hashes the archive Buildroot actually builds.** `recipe_bump`
  named the tarball `<name>-<version>.tar.gz` — Buildroot's *default* — but golang and cargo
  infra append their own suffix (`pkg-utils.mk:51` `pkg_source_ext`, `pkg-download.mk:12-15`
  `-go2`/`-cargo6`, `pkg-golang.mk:101`, `pkg-cargo.mk:190`), so the `.hash` line named a file
  the build never produces. `check-hash` exits 3, `dl-wrapper` treats that as fatal, the
  download step dies, and the component is recorded as known-bad. Measured on 2026-10-12:
  `routine-fzf-0.74.5` failed exactly this way and fzf stayed refused.
  The digest is also *unobtainable* by download-and-hash: Buildroot clones the repo and
  re-tars it, so hashing the SITE URL yields a true digest of the wrong file (fzf 0.74.5:
  generated archive `45fa3fb8…`, GitHub's own tarball `5529d088…`). The plan now carries the
  `.mk` edit alone plus an honest `hash_generated` marker naming the file Buildroot will
  produce, and the act lane runs `make <pkg>-source` and hashes the archive it finds —
  verified against the live tree: for fzf 0.74.4 it computes `de72d134…`, byte-identical to
  the hand-authored line in `fzf.hash`. A recipe with an explicit `*_SOURCE` (cosmic-portal)
  is unaffected: Buildroot downloads that file, so a download-and-hash stays correct.
  No golang/cargo recipe fixture existed before this, which is why the defect survived.
- **A finding about an authored recipe must be keyed by the package name.** The PengyR
  source emitted `component: "pengyr"`; `obs["packages"]` is keyed `pengy` (pkg-stats
  names our authored recipe by its Buildroot package). The planner's join found nothing,
  built the candidate from the empty fallback, and the guards refused it for reasons that
  were not true: `not_host_only: the package is in no configured edition` (it is in all
  five) and `nothing_newer: upstream has no newer release than the pin` (current was `""`).
  Measured on the live plan of 2026-10-11, where the loop had correctly observed
  `pengyr 1.11.2 -> 1.11.5` and could not act on it. The component is now `pengy`, so the
  candidate carries the real pin, the real editions and the real bump kind.
- **`pengy` is report-only, and now says why.** The image ships the cargo-VENDORED tarball
  Buildroot generates at download time (`pengy-1.11.5-cargo6.tar.gz`, `BR_FMT_VERSION_cargo`),
  not the SITE archive. A `recipe_bump` hashes what it can download — the GitHub tarball
  under the SOURCE name — so its `.hash` would name a file the build never produces and the
  build would fail on it. `pengy` joins `DENIED` with that reason and the manual recipe
  (`make pengy-source`, record the hash it reports). Without the first fix the refusal
  would have been attributed to invented guards.
  *(Superseded on this point by 0.12.1 above: the lane can now read the generated archive's
  hash itself, so this reason no longer describes a limit of the tool. Keeping `pengy` in
  `DENIED` is now an operator policy call, not a technical necessity — the hand-bump recipe
  still works, it is just no longer the only way.)*
- **`test_status_reads_a_recovered_batch_as_recovered` was date-sensitive.** It logged a
  batch dated 2026-10-08 and asserted `"1 published today"` while letting `do_status` read
  the wall clock, so it passed only on 2026-10-08 and failed every day since. `today` is
  now pinned to the fixture's date.

## [0.12.0] - 2026-10-08

### Changed
- **A known-bad fix drops its CVE, not the component.**
  `guards.patch_does_not_apply` refused a whole action when the candidate named *any*
  upstream fix a pre-flight had already proved does not apply — while the pre-flight's
  own rule is "the unit of failure is a patch, not a package" (`act.triage_package`).
  Measured on the live state 2026-10-08: ffmpeg had 19 candidate fixes, 8 known-bad and
  **11 that had passed pre-flight**, and the plan refused the component outright;
  gst1-plugins-good lost 6 carried patches to 1 bad fix. The 26.10.14 batch shipped
  1 change / 1 CVE instead of the 4 changes / 25 CVEs it was queued with. Same bug shape
  as the `fix_commits_available` veto corrected in 0.6.0 (that one was holding 28
  fetchable patches behind 17 CVEs with nothing to fetch), different input.
- **The filter is inside `edits.carried_patches(cves, known_bad=...)`**, not beside it.
  One function decides what is carriable, and the claim (`carried_cves`), the two guards
  and `patch_dir` all read the same filtered set — so a known-bad patch is never fetched
  (the network cost is real) and never claimed, and a guard cannot allow what the edit
  builder would then refuse. `patch_does_not_apply` keeps its name and refuses only when
  nothing carriable is left; `fix_commits_available` asks the same filtered question and
  keeps its own reason ("NVD named no commit" vs "every commit NVD named is already
  proved bad"), so a candidate is refused once, by the guard that owns it.
- **A known-bad verdict is about a pinned version.** `patch_failures[component]` now
  records `version`, and `guards.patch_failure()` — the single reader of the record —
  stops binding it once the pin moves. "Does not apply to ffmpeg 6.1.5" was a permanent
  block wearing a temporary one's clothes. Records with no version, and records from
  before per-fix tracking, keep blocking with the same wording: clearing a known-bad
  verdict stays a human act, and nothing clears it automatically.
- **Filtering is not forgetting.** Every dropped CVE stays in `still_open`, is recorded on
  the action as `skipped_known_bad` (`cve`, `fix`, `recorded`, `failed_on`), appears in
  `plan.json`, in the release notes with its own reason, and on the plan's action line
  (`closes 11 CVE match(es), 8 fix(es) skipped as known-bad`).

### Verified
- `plan` on the live state: **0 allowed / 10 refused → 2 allowed / 8 refused**.
  `security-ffmpeg-6.1.5` carries 11 patches closing 11 CVE matches (8 fixes skipped as
  known-bad); `security-gst1-plugins-good-1.24.13` carries 6 patches closing 2 (2
  skipped). `security-libsndfile-1.2.2` (nothing carriable) and `security-podman-v5.7.0`
  (legacy record, no fix list) are still refused, by name, with the old wording.
- 255 tests (239 + 16 new). Every new test was mutation-checked against seven mutants:
  veto restored, filter removed at its source, recorded version ignored,
  `skipped_known_bad` bookkeeping dropped, `version` dropped from the record,
  last-resort refusal removed, notes stopped naming the skips — each mutant failed at
  least one test.

## [0.11.1] - 2026-10-08

### Fixed
- **The mailer is exec'd, not fed to `python3`.** 0.11.0 made the mailer
  machine-local but `send()` still built `argv = ["python3", <mailer>, ...]`, the
  leftover from when the mailer *was* a .py file. On behoserver the mailer is
  `~/.local/bin/pengyos-email`, a bash wrapper that `exec`s the email skill, so bash
  syntax became a `SyntaxError`, `send()` raised, and the 19:00 `run` died at the
  report step - no email, and `plan` never ran, so the 21:00 act lane worked off a
  stale plan. `mailer_argv()` now honours the mailer's own shebang (exec it when it
  is executable, read the shebang when it is not, `python3` only as the fallback).
- **`send()` returns `True` on a delivered report.** It fell off the end of the
  function and returned `None`, so even a successful send left the queue unreported:
  every following night would re-report the same CVEs and findings, and `last_report`
  would never move. Only the no-mailer path was tested, which is why it survived 0.11.0.
- **A failed delivery is contained, not fatal.** `do_report` catches
  `CalledProcessError`, names the mailer's own error line in the log, records
  `last_delivery_error` in state, and leaves the queue intact; the run still reaches
  `plan` and exits 1 so cron/log readers see a failed night.
- **`scripts/release.sh` is guarded before anything is spent.** Commit 2d894fba
  (2026-10-07) flipped it from git mode 100755 to 100644 while editing it. The act lane
  calls it directly, so the 2026-10-08 batch discovered that at the last step - after the
  branch, the edits, four pre-flights, three commits and a green gate - as a
  PermissionError that escaped `run_batch` entirely: no recorded failure, main's working
  tree never restored, a nightly branch left checked out. New
  `guards.release_script_executable` runs with the other guards, and both release
  invocations now catch `OSError` and bail like any other step failure.
- **A dropped package no longer poisons the build.** A pre-flight that dies mid-patch
  leaves `build/<pkg>-<version>` extracted, partly patched, with `.applied_patches_list`
  populated and `.stamp_patched` missing. The full build then re-runs the patch step,
  `apply-patches.sh` sees basenames it already applied and dies with `Error: duplicate
  filename` on Buildroot's own patch. That is what cost the 2026-10-08 batch: libsndfile's
  only carried fix is one Buildroot 2026.08 already ships, the package was dropped
  correctly, and the build then failed on libsndfile's own 0001-...patch. `triage_package`
  now dircleans a package it is about to drop (`act.dirclean_package`).
- **A bail takes every declaration back.** Each `cve_declaration_edit` carries `old` =
  the whole file as it stood when that action wrote it, so replaying the declarations in
  order after a bail writes the earlier blocks back. The 2026-10-08 release failure left
  `FFMPEG_IGNORE_CVES` and `GST1_PLUGINS_BAD_IGNORE_CVES` on main for patches no longer
  in the tree - an ignore that reports a CVE closed by a fix we do not carry. A declared
  file tracked in main is now restored from main instead of replayed.
- **A bail wipes the build trees its pre-flight patched.** Buildroot's legal-info step
  copies every patch named in `build/<pkg>/.applied_patches_list`, and a pre-flight puts
  the batch's own patch files in that list. After a bail those files are gone while the
  build dir still names them, so the *next* build dies with `cp: cannot stat <patch>` - on
  2026-10-08 that was ffmpeg's legal-info, in a batch that no longer contained ffmpeg.
  `run_batch` now dircleans every package whose pre-flight ran.
- **A leftover nightly branch no longer blocks the retry.** `checkout -b nightly/<date>-batch`
  refused a same-night retry after a failed attempt (`a branch named ... already exists`).
  The branch is the loop's scratch space for today's batch, so it is `checkout -B`.
- **`status` reads a recovered batch as recovered.** `_blocked_batches` returned every
  unpublished batch in the log and `status` named the last one, so three failed attempts
  followed by a successful 26.10.14 still read "last batch published nothing" all morning.
  It now reports only the unpublished batches *since the last published one*.

### Added
- `status` flags `last_delivery_error` and exits non-zero: "no email" stays ambiguous
  unless the loop can say whether it had something to send and failed to send it.

### Tests
- `test_send_execs_the_mailer_it_is_given` (a real shell mailer, real subprocess, argv
  checked end to end), `test_the_mailer_decides_how_it_is_run` (executable / shebang /
  no-shebang), `test_a_broken_mailer_fails_the_run_but_does_not_cost_the_night`,
  `test_a_report_that_failed_to_deliver_is_not_a_quiet_night`. Mutation-checked: dropping
  `return True`, restoring the `python3` prefix, and removing the `try` each fail at
  least one of them. On the act lane:
  `test_a_release_script_that_cannot_be_run_is_refused_before_a_branch`,
  `test_a_dropped_package_leaves_no_half_patched_tree_behind` and
  `test_a_failed_batch_takes_every_declaration_back` - the fixture now tracks
  `cve-declarations.mk` on main the way the real tree does, because whether a file is in
  main is what decides how a bail can restore it.


## [0.11.0] - 2026-10-07

### Changed
- **The mailer is machine-local.** `EMAIL_SCRIPT` pointed into one person's home
  (`~/Personal/skills/email/send_email.py`). It is now `$PENGYOS_EMAIL_SCRIPT`,
  defaulting to `~/.local/bin/pengyos-email`, and `email_script()` returns `None`
  when the machine has no mailer configured. `send()` returns a bool: with no
  mailer it puts nothing on the wire and says `False`, so the queue is not marked
  reported by a report nobody received - the rule `--no-email` already enforced.
- `triage.pengy_settings` honours `$PENGY_SETTINGS`; `rehearse.py` honours
  `$PENGYOS_BUILD_ROOT` instead of a hard-coded absolute home path.
- README and spec: the cron example and the "needs" list no longer name a
  specific build host or a personal skill path.

### Tests
- `test_pengyos_email_script_names_the_mailer` and
  `test_no_mailer_means_report_only_and_not_reported`. An autouse `conftest`
  fixture pins `PENGYOS_EMAIL_SCRIPT` to a path that does not exist: on a host
  that *does* have a mailer, a test which forgot to inject a fake `send` would
  otherwise email out of the test suite. Injected fakes now return `True`,
  because "delivered" is the contract `do_report` acts on.

## [0.10.0] - 2026-10-07

### Added
- **The act lane declares the fixes it carries.** `edits.cve_declaration_edit` writes
  `<PKG>_IGNORE_CVES` to `buildroot-external/patches/cve-declarations.mk` (included from
  `external.mk`), one line per CVE, each preceded by a comment naming the patch file —
  Buildroot's own convention. `pkg-stats` cannot see a patch delivered through
  `BR2_GLOBAL_PATCH_DIR` (`set_patch_count()` walks only the package's own directory), so
  without this the tree keeps reporting a CVE open after the fix shipped: libxml2 did exactly
  that for all seven CVEs published in 26.10.8, and `ffmpeg 6.1.5` reports 37 open CVEs with
  no ignores at all.
- **The declaration is written after the pre-flight, from what survived.** Not from what the
  plan promised: a fix Buildroot rejected gets no declaration. An ignore that outlives its
  patch would report a CVE closed while the image is still exposed, which is a worse claim
  than the one this fixes. Each package's block is regenerated whole, so a package left with
  nothing to carry loses its block, and a CVE a human declared in `external.mk` or in a
  recipe we author is left where it is rather than duplicated.
- **A declaration that cannot be written stops the batch** (`cve-declaration`): the loop may
  not publish a fix it cannot account for.

### Fixed
- **A batch that bailed left its untracked patch files in the working tree.** `git checkout
  -f main` does not touch files main has never seen, and the patches a batch wrote are
  exactly that. Left behind they leave the tree dirty — and a dirty tree is what makes the
  *next* night's run refuse to do anything at all (`clean_tree`). A bail now takes every
  action's edits back the way a dropped package's are taken back, and takes the declarations
  back too. Found by mutation, not by reading the code.

### Tests
11 new tests (7 in the tool's edit layer, 4 in the batch lane), 226 passed. 16 mutations on
the new behaviour: 15 caught, 1 equivalent (`carried` and `action["edits"]` are the same set
after the pre-flight, because triage removes rejected patches from `action["edits"]` in
place — no test can distinguish them, and the behaviour is correct either way). Two
mutations found real defects before commit: the dirty-tree bail above, and a test that
passed on a substring match (`"cve-declaration" in error` matched the *filename*
`cve-declarations.mk`) instead of the guard that was supposed to fire.

## [0.9.0] - 2026-10-07

### Added
- **The Buildroot release-bump lane, decision half (spec phase 4).** A new Buildroot release
  series is a *finding*; moving every edition onto it is a scheduled, operator-approved event.
  `bump_buildroot = ""` in `nightly.toml` is the default and means the lane is **inert** — a
  newer series produces no candidate at all. Naming a target is the operator scheduling it.
  The shape is `tree_move`, not `recipe_bump` or `patch_dir`: it does not move a package pin
  and it does not cherry-pick onto one, it moves the tree every edition builds from.
- **`edits.tree_move(repo, target)`** rewrites `br_version` for every edition in
  `targets.toml` — the tree choice is declared in that file, not implied by a checkout.
  **`edits.carried_patch_files(repo)`** lists every patch under `buildroot-external/patches/`,
  which is the loop's own security maintenance and the thing a tree move must account for: the
  plan records it as `retires` with a `retire_check` naming what has to be re-checked against
  the new line. Keeping them all silently is how a loop re-fixes what upstream fixed.
- **Four guards, each named and tested.** `bump_event_approved` refuses unless
  `bump_buildroot` names exactly the target. `lane_allowed` gives the bump its **own** lane,
  enabled only by the schedule — measured by test, scheduling a Buildroot bump does not enable
  the kernel or the NVIDIA driver, which share the "new release series" label.
  `denied_component(component, cfg)` lifts `buildroot`'s report-only default for that event
  only; `linux`, `nvidia-open` and COSMIC stay denied whatever is scheduled. `major_never`
  treats a `tree_move` as the event itself rather than a package bump.
- **`tree_move_not_executable` refuses the action by name.** The act lane has no full-rebuild
  path, so a plan that *allowed* a tree move would hand it to a lane that cannot run it, and
  the honest failure would surface at 21:00 as a build error instead of as a guard that says
  what is not built.
- **A refused tree move still shows its edit.** The planner builds it before the verdicts (one
  local file read, no network, no build) and keeps it when the action is refused, so
  `plan.json` records what would have changed and which guard stopped it. A refusal that hides
  the change is not reviewable.
- **`status` shows the lane**, which it did not before:
  `bump lane: inert | Buildroot 2026.08 | no newer Buildroot release found`,
  `… | finding: 2026.11 (new release series)`, or
  `bump lane: scheduled -> 2026.11 (not executable yet)`. A `!` line appears when a newer
  series is a finding with nothing scheduled, when the scheduled target is not what the
  observer found, and when a schedule has no finding behind it. These do not flip `ok` or the
  exit code: an open human decision is attention, not a broken loop — the same rule already
  applied to a queued batch with `act_enabled = false`.

### Fixed
- **The User-Agent we present to upstream named a version that did not exist.** `edits.py`
  said `pengyos-nightly/0.4` and `sources.py` said `pengyos-nightly/0.1` while the tool was
  0.8.0. Both now derive from `__version__`.

### Known limit, measured not assumed
Phase 4 is half built and the spec says which half. Built: the schedule, the candidate, the
edit, the retirement *accounting*, the guards, and the report. Not built: fetching and
verifying the new line, recomposing `buildroot-external` against its package set, re-resolving
the COSMIC / NVIDIA / kernel pins, the full rebuild of every edition, and the *actual* patch
retirement. Acceptance criteria 19 is met, 21 is partly met, 20 and 22–23 are not.

### Tests
19 new tests (12 guards/planner/edits, 7 status), 214 passed. 11 mutations on the status
behaviour, all caught — one of them found a real defect in the first version: it validated a
scheduled bump only against *major* findings, so a scheduled point release (routine lane) was
misreported as "no finding behind it". The phase-4 batch had 8/8 mutations caught.

### Changed
- **A named upstream commit is checked before it is carried.** `edits.classify_patch`
  reads the patch itself and refuses three things that are not a fix for this package: one
  that **adds new files only** (GStreamer publishes its security advisory as its own commit
  and NVD links that as the "fix" — measured 2026-10-07, 7 of gstreamer1's 10 named fixes
  were `security-advisories/sa-2026-*.md`; carrying them adds markdown to the source tree
  and would have let the notes claim 8 CVEs closed), one that **belongs to a sibling
  component** (NVD's CPE product is `gstreamer`, so a gst-plugins-ugly fix is matched
  against our `gstreamer1`, whose tarball has no such files), and one **spanning several
  components**. Refusals are recorded per CVE with a reason and the commit subject
  (`action["not_carried"]`); a package with nothing carriable is refused by its own guard,
  `no_fix_for_this_package`, not by the generic `recipe_unreadable`.
- **Monorepo patches are re-rooted.** Buildroot downloads one standalone tarball per
  gstreamer component (`GST1_PLUGINS_GOOD_SITE = .../src/gst-plugins-good`), so
  `subprojects/gst-plugins-good/gst/...` has to become `gst/...`. Buildroot cannot do it:
  its series format documents a `-pN` field, but `apply-patches.sh` cuts it away and
  hardcodes `-p1` (line 119; its own comment says "We assume this field to always be
  -p1") — verified against a real gstreamer patch on 2026-10-07. `reroot_patch` rewrites
  the path lines only; the hunks are upstream's, unchanged, and the action records the
  re-rooting as a risk.
- **The claim is recomputed after the fetch.** The planner's first `closes` is what NVD's
  metadata promised; once the patches are fetched and classified, `closes` / `still_open`
  are recomputed from the edits actually written. A CVE whose named commit turned out to be
  a document goes back to `still_open` with its reason and is not claimed.
- `patch_dir` returns `(edits, refused)`; `NoCarriableFix` carries the refusals.

### Known limit, measured not assumed
Re-rooting makes a monorepo fix *addressable*; it does not make it apply. The four qtdemux
commits from gstreamer MR 11243 are authored against a newer tree and fail on 1.24.13 with
`-F0` (hunk offsets of -3109 lines). The failure is now legible — "does not apply to the
pinned version" instead of "No file to patch" — and the triage drops them.

## [0.7.0] - 2026-10-07

### Changed
- **The unit of pre-flight failure is a patch, not a package.** `act.triage_package`
  reads the patch Buildroot rejected (`apply-patches.sh`: `Patch failed!  Please fix
  <patch>!`), takes that file out of the tree, re-extracts, and retries. `shrink_action`
  then recomputes `closes` / `still_open` / `patches` from what survived, so the claim
  follows the tree. Previously one uncarriable cherry-pick condemned the whole package:
  measured 2026-10-06, ffmpeg had 11 of 19 patches that apply and the loop would have
  shipped none of them, then blacklisted ffmpeg.
- **`patch_does_not_apply` is scoped to the fix, not the component.** The record is now
  `patch_failures[<pkg>]["fixes"]` — the upstream fix URLs proved not to apply — and a
  candidate is refused only when it wants one of them. A commit authored against ffmpeg
  8.x failing on 6.1.5 no longer blocks a fix written for 6.1.15. The known-bad set
  accumulates across nights (merge, never overwrite). A record with no fix list predates
  per-fix tracking and is honoured as a component-wide block until a human clears it.
- **`Edit` carries `cves` and `fix_url`.** The act lane can only re-compute its claim
  after pre-flight if each patch file says which CVEs it closes and which upstream fix
  it came from; `plan.json` is now that mapping.
- **Rejection reasons are named in the notes**: `already present in the pinned version`
  (patch reported `Reversed (or previously applied)`) versus `does not apply to the
  pinned version`. The first is a fact about the CVE; the second is a fact about the
  cherry-pick.
- **A review request is split into the patches it contains.** A GitLab MR / GitHub PR
  `.patch` is the whole series concatenated (measured: gstreamer MR 11243 = 4 patches in
  one file, MR 12xxx = 6). Buildroot pipes one file to one `patch` invocation, so the
  concatenated response made patch read the next email's headers as garbage and those
  packages could never carry a review-request fix at all. `split_patches` separates them
  on the `From <sha> Mon Sep 17 00:00:00 2001` head; `patch_dir` writes one numbered file
  per commit. `fetch_patches` raises on a response that is not a format-patch, so an HTML
  page cannot become a `.patch` in the tree.
- **`PatchFailure` carries the whole command output.** One ffmpeg cherry-pick produced 17
  failed hunks, pushing the `Applying <patch>` line hundreds of lines from the end of the
  log, so the triage could not name the culprit from a summarised message. The message is
  summarised for humans; `.output` is not truncated for the triage. `_failed_patch` names
  the culprit from the last `Applying` line, because `apply-patches.sh` runs `set -e` and
  never reaches its own `Patch failed!  Please fix <patch>!` echo (measured on a real
  `make libsndfile-patch` failure).

## [0.6.0] - 2026-10-06

### Changed
- **`fix_commits_available` is per CVE, not per component.** One CVE whose NVD record
  named no upstream commit used to refuse the whole package. Measured on the live plan
  2026-10-06: that veto held **28 fetchable patches behind 17 CVEs with nothing to
  fetch** — ffmpeg 18 of 21 CVEs actionable, refused; gstreamer1 10 of 24, refused.
  A component is now actionable when at least one affected CVE names a fetchable fix.
- **The claim is computed from the patch set.** `edits.carried_patches` decides which
  patches are written and `edits.carried_cves` is the union of CVEs they close; the
  planner takes `closes` from the same function, so the notes, the commit message and
  the `patched_by` record can never claim more than the tree carries. Affected CVEs
  left out are recorded as `still_open` and named in the release notes as still open.
  Two CVEs fixed by one commit are still **one patch and two closed** — deduplicating
  the patch does not shrink the claim.
- **A pull/merge request counts as a named fix.** FFmpeg and GStreamer publish fixes as
  review requests and NVD links the request, not the commit. `evidence.fix_commit` now
  recognises `pull/N`, `pulls/N` and `-/merge_requests/N` (a named commit still wins),
  and `edits.patch_sha` reads the commit sha back out of the fetched format-patch so
  the notes still cite a commit. Verified live: `code.ffmpeg.org/.../pulls/22348.patch`
  and `gitlab.freedesktop.org/.../merge_requests/11243.patch` both serve 200 + patch text.
- **`code.ffmpeg.org` is a known patch host.** 16 of the ffmpeg fix references live
  there and the old host list could not fetch any of them — the guard would have allowed
  the action and `fetch_patch` would have raised on it. `carried_patches` now checks the
  patch form exists before counting a fix, so the guard cannot allow what the fetcher
  cannot fetch. `patch_url` also passes an already-`.patch` reference through instead of
  doubling it.

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
