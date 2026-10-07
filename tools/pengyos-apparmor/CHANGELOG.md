# Changelog

All notable changes to **pengyos-apparmor** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/); versions
follow [SemVer](https://semver.org/).

## [Unreleased]

### Added
- The `find-model-server-port` scan's Kconfig (buildroot `Config.in`)
  value line now accepts the *conditional default* idiom — `default NNNN
  if <condition>` (any Kconfig condition after the `if`): the value stays
  a literal bare or quoted decimal, the condition is metadata about when
  it applies, not part of the value. A dangling `default NNNN if` with no
  condition is not a value (malformed Kconfig), and hex/arithmetic/symbol
  references as the value remain not candidates. A port defined upstream
  in a buildroot `Config.in` with a conditional default — a standard
  buildroot idiom — would previously have left the port-pin transition
  blocked with no machine signal. New tests in
  `tests/test_portdiscovery.py` cover the conditional form (bare and
  quoted values, condition expressions, trailing comments, multiple
  conditional defaults, help-block interplay), the dangling-`if` negative,
  a tree discovery, and the CLI end to end. The port-pin item is still
  blocked (re-check 32: upstream defines no port, the grant stays
  deliberately unbound).
- The `find-model-server-port` scan now decodes files with a *leading
  UTF-8 BOM stripped* (`utf-8-sig`): a BOM is a file-level encoding
  marker, and left in place it sat on line 1 before `^`, breaking every
  line-start-anchored shape — a `[model-server]` section opener or a
  `config …_MODEL_SERVER_PORT` Kconfig option on line 1 of a BOM-marked
  file left the definition on the following line invisible to the scan.
  The unanchored shapes (prose, listen, identifier, flag forms) were
  never affected; for a BOM-less file `utf-8-sig` decodes identically to
  `utf-8`, and only ONE leading BOM is stripped (a `U+FEFF` on a later
  line is line content, e.g. a concatenated file). Six new tests in
  `tests/test_portdiscovery.py` pin the BOM-marked TOML section and
  Kconfig option shapes, the unanchored-shape no-op, the one-BOM-only
  contract, the non-binary skip heuristic, and the CLI end to end.
  The port-pin item is still blocked (re-check 31: upstream defines no
  port, the grant stays deliberately unbound).
- The `port-pin-status` probe now reports the *commit-ability* verdict of
  a `pinned` repo — the machine signal for the runbook's final step (commit
  the pin and flip the checklist item). A `pinned` repo is commit-able only
  when its pinned port is defined upstream (present among the
  `find-model-server-port` candidates); `portstatus.PortPinStatus` gains a
  `commitable` property and a `commit_ability()` verdict, and the module
  adds the `COMMITABLE` / `NOT_YET_COMMITABLE` verdict constants plus the
  `port-pin-status` exit-code constants (`EXIT_OK` 0 / `EXIT_PARTIAL` 1 /
  `EXIT_BAD_INPUT` 2 / `EXIT_BLOCKED` 3 / `EXIT_NOT_YET_COMMITABLE` 4). The
  CLI prints a machine-greppable `commit-ability:
  commit-able|not-yet-commit-able` line for a pinned repo and now exits 4
  (previously 0) when the repo is internally consistent but the pinned port
  is not yet defined upstream — a pin without an upstream definition is not
  yet commit-able, so it must not be committed and the checklist must not be
  flipped yet; the commit-able (upstream-defined) case still exits 0. The
  runbook's commit step is now gated on the commit-able probe (a new step 5
  plus a "Pinned but not yet commit-able" failure mode, exit `4`), and
  spec.md's probe contract is refined to the same. `tests/test_portstatus.py`
  pins the property across blocked / ready / partial / pinned-with-upstream /
  pinned-without-upstream / pinned-with-a-different-upstream-port, the public
  API and exit-code constants, and the CLI's distinct exit 0 vs 4 (plus the
  verdict line). The port-pin item itself is still blocked (re-check 29:
  upstream defines no port, the grant stays deliberately unbound).
- The write side of the port-pin transition is now staged-atomic:
  `portpin.atomic_commit` (new public API) commits a sequence of edit
  records by first staging every file's new content into a temporary
  `*.pin-tmp` file in the target's own directory (fsynced), and only then
  swapping each staging file over its target with an atomic `os.replace`
  rename. `portpin.apply_transition` (the four transition files) and
  `portdocsync.sync_docs` (the four prose doc surfaces) both use it, so an
  interrupted apply or docs sync can never leave a *torn* file: a failure
  while staging changes nothing (all staging files are removed, including
  the one whose own write failed), and a failure between renames leaves a
  *mixed* — some files complete-new, the rest complete-old — state that
  the `port-pin-status` probe classifies `partial`, and an interrupted
  docs sync finishes with an idempotent re-run. Both CLI commands map an
  interrupted commit to a clean exit 2 with an actionable message (re-check
  with `port-pin-status`; re-run the docs sync). `tests/test_portpin_atomic.py`
  adds the 14-test failure-injection group (write-phase / commit-phase /
  torn-file-mode / CLI interruption + finishable re-run); the port-pin
  item itself is still blocked (25th re-check: upstream defines no port,
  the grant stays deliberately unbound).
- The `find-model-server-port` upstream re-check now discovers two more
  definition shapes so the still-blocked port-pin item cannot stay blocked
  after the port is actually defined upstream: the *listen shape*
  (`<address>:<port>` after a listen/bind/serve verb — "the model server
  listens on 127.0.0.1:NNNN", where the word *port* is absent; new public
  `portdiscovery.MODEL_SERVER_LISTEN_PATTERN`, which requires the verb and
  a concrete dotted-quad / bracketed-IPv6 / bare-hex / localhost address,
  so a URL mention without a verb or "binds loopback" prose is still a
  miss) and the *section-scoped config shape* (a `port = N` / `port: N` /
  `port is N` key line inside a model-server section — opener line
  `[model-server]` in TOML/INI, subsections included, or `model_server:`
  as a YAML key, list items and nested blocks tolerated; suffixed keys
  such as `port-default` and `listen_port` count; the section closes at a
  new section header, a shallower line, or a sibling mapping key, and the
  state is per file; array values and key lines without a number are not
  candidates). The number must still be a valid TCP port (1..65535) and a
  hit is still *evidence for a human to review* — the never-guess apply
  gate is unchanged. `tests/test_portdiscovery.py` gained 16 cases
  (positives/negatives for both shapes, the section state machine, tree
  fixture, CLI end to end); the live workspace anchor still reports
  blocked (exit 3 — upstream defines no port in any shape yet).
- The port-pin operator runbook: `docs/port-pin-transition.md` — the
  permanent, repo-checked home of the still-blocked "pin the model
  server's port" transition (the recipe previously lived only in the
  per-run AGENTS.md log and the CLI help). It documents the current
  blocked state and how to verify it, the trigger (the
  `find-model-server-port` scan exiting 0 with a candidate a human must
  confirm), the exact command sequence (`find-model-server-port` →
  `apply-port-pin <N>` → `sync-port-pin-docs <N>` → `pytest -q` +
  `validate --strict profiles/` → checklist flip → commit), the files
  each stage touches (`portpin.PORT_PIN_TRANSITION_FILES`,
  `portdocsync.PORT_PIN_DOC_FILES`), and every failure mode (never-guess
  gate, partial state, repo drift, one-way transition). New
  `tests/test_portpin_runbook.py` (10 test cases) pins its lockstep: the
  runbook names every transition command, every transition and doc
  file, every probe state, every red anchor test, and the ordered
  sequence — and carries NO port (only the `<N>` placeholder; the
  never-guess rule applies to the runbook itself). README and spec.md
  point at it. No production code changed. The blocked item stays
  open: tenth re-check, 2026-09-17 — the live workspace scan still
  exits 3 and `port-pin-status` reports `blocked`.
- End-to-end acceptance tests for the port-pin transition
  (`tests/test_transition_end_to_end.py`) — the first test group to
  assert the whole machine *composes*. On a fixture scan root that
  defines the port, one composed run of the real CLI
  (`find-model-server-port` → `apply-port-pin` → `sync-port-pin-docs`)
  drives a live-repo copy from the unbound `ready` state to the terminal
  state, with every invariant pinned at once: the `port-pin-status` probe
  reports `pinned`, `validate --strict profiles/` exits 0, all four
  transition files carry the port-pinned form (the grant plus its local
  `# sanctioned:` directive), and all four prose doc surfaces are synced
  (an idempotent re-run is a no-op); the transition is one-way (a
  re-apply is refused). A companion test pins the never-guess gate end to
  end: with no upstream definition the apply gate exits 3 and the docs
  sync is refused, leaving every transition AND doc file byte-identical.
  No production code changed; the port is always taken from the fixture
  scan, never invented.
- Post-apply docs sync for the port-pin transition:
  `pengyos-apparmor sync-port-pin-docs <port> [repo-root]
  [--scan-root ROOT] [--dry-run]` (module
  `pengyos_apparmor.portdocsync`) — the first
  `portpin.POST_APPLY_STEPS` step is no longer a manual prose edit.
  Once the repo is `pinned:<N>` (all four mechanical transition files
  consistent — the gate, checked via `portstatus.inspect_transition`),
  it updates the four prose doc surfaces from the unbound prose to the
  pinned prose: the `profiles/pengy-agent` network comment block, the
  README profile row, the spec.md pinned-grant paragraph, and a new
  first CHANGELOG entry under `## [Unreleased]` / `### Added`. Each
  surface is recognized by its exact pre-sync anchor and pinned marker:
  `editable` (rewritten), `synced` (untouched — a re-run is a no-op),
  or `drifted` (neither — the sync fails loudly and, being
  all-or-nothing, writes nothing). The port is never guessed: it must
  be the port the repo already pins (a different port or the
  unbound/partial state exits `1`); the upstream scan is reported as
  evidence only (a mismatch is noted, not acted on). The edited profile
  comment is re-validated for strict-clean before any write;
  `--dry-run` prints the docs diff without writing. Exit `0` synced or
  already in sync, `1` repo not pinned to the requested port, `2` on
  bad input or doc drift. New `tests/test_portdocsync.py` (30 test
  cases) pins the per-surface transforms (editable/synced/drifted,
  including doubled-anchor ambiguity), the gate (unbound/partial/
  different-port repos refuse with the docs byte-identical), purity and
  idempotence, the strict-clean re-check, `--dry-run`, and two live
  anchors (the committed docs are editable against the sync recipe; the
  live repo is refused with byte-identical docs). Suite: 338 passed,
  1 skipped. The blocked item stays open: eighth re-check,
  2026-09-17 — the live workspace scan still exits 3.
- Apply side of the port-pin transition:
  `pengyos-apparmor apply-port-pin <port> [repo-root]
  [--scan-root ROOT] [--dry-run]` (module functions
  `portpin.compute_transition` / `portpin.apply_transition`, shared core
  with the `plan-port-pin --patch` renderer) — the last un-automated
  piece of the transition. It writes the four transition files in place
  (the profile grant plus its local `# sanctioned:` line, the
  `SANCTIONED_NETWORK_RULES` lockstep entry, and the two test pins),
  gated so the requested port must carry *upstream evidence* — the
  `find-model-server-port` scan (default root: the PengyOS workspace)
  must have found that exact port as a candidate — otherwise nothing is
  written and the command exits `3` (the never-guess rule with no
  override flag). The transformed profile must be strict-clean before
  anything is written (exit `1` otherwise), the state re-probe must
  report `pinned:<N>` afterwards (else exit `1`), exit `2` on bad input
  or repo drift from the transition recipe, `0` on success (prints the
  remaining reviewed steps — docs, full suite, flipping the checklist
  item — via the new `portpin.POST_APPLY_STEPS`). The transition is
  one-way: a double application fails loudly (`PortPinTransitionError`,
  "already pins a port"); a mid-write failure leaves a `partial` state
  the `port-pin-status` probe detects. New
  `tests/test_portpin_apply.py` (26 test cases) pins the gate (no
  evidence → exit 3, different-port evidence → exit 3, multiple
  candidates → the requested one is pinned with a note), the purity of
  `compute_transition`, the drift/no-op fail-loud modes, the strict-clean
  pre-write guard, the one-way property, `--dry-run`, and two live
  anchor tests (the live workspace stays blocked with byte-identical
  transition files; a fixture scan root lets the *dry run* compute the
  full transition against the live repo). Suite: 308 passed, 1 skipped.
  The blocked item stays open: seventh re-check, 2026-09-16 — the live
  workspace scan still exits 3.
- Port-pin transition-state probe (`pengyos_apparmor.portstatus`) and
  the `pengyos-apparmor port-pin-status [repo-root] [--scan-root ROOT]`
  subcommand — the *read side* of the port-pin tooling (scan / plan /
  render were the write side). It classifies each of the four transition
  files (`portpin.PORT_PIN_TRANSITION_FILES`) as `unbound` /
  `pinned:<N>` / `drifted` by matching the exact pre- and
  post-transition anchor lines (the planner and patch only ever emit
  these, so anything else is drift: a missing or doubled anchor, a port
  range, an out-of-range port, conflicting ports across files, or a
  flipped `test_validator` anchor line whose companion assert was not
  flipped), and combines that with the upstream port scan into one state:
  `blocked` (all unbound, no upstream candidates — exit `3`, today),
  `ready` (all unbound, candidates found — exit `0`, prints the
  `plan-port-pin <N> --patch` follow-up), `pinned` (all files
  consistently pinned to one valid port — exit `0`; the upstream scan is
  printed as evidence, and a pin without an upstream definition is
  flagged as not-yet-commit-able), or `partial` (mixed or drifted repo —
  exit `1`, per-file detail). Read-only: nothing is modified and no port
  is ever guessed; exit `2` on a bad repo root or scan root (default scan
  root: `portdiscovery.DEFAULT_SCAN_ROOT`). New
  `tests/test_portstatus.py` (26 test cases) pins every state and drift
  mode on fixture repos, performs the *real rendered patch* on a copy of
  the committed repo and re-probes it (blocked → pinned), half-applies a
  transition and proves the probe catches it (partial), and carries an
  anchor test that the live repo + live workspace still report `blocked`
  (exit 3) — the first red signal when PengyOS defines the port.
  `portpin.PINNED_RULE_RE` (lifted from private) is the single source of
  truth for the pinned grant form shared by the planner and the probe.
  The blocked item stays open: sixth re-check, 2026-09-16 — the
  `find-model-server-port` scan of the live workspace still exits 3.

- Upstream model-server port discovery (`pengyos_apparmor.portdiscovery`)
  and the `pengyos-apparmor find-model-server-port [root]` subcommand
  (default root: the PengyOS workspace root, `DEFAULT_SCAN_ROOT`): the
  recurring re-check of the still-blocked "pin the model server's port"
  item — previously a hand-rolled grep re-derived from memory on every
  iteration — is now a deterministic, tested command. The scan is
  deliberately conservative (both the words "model server" and "port" must
  be on the line; the number must be a 2–5 digit TCP port in 1..65535), so
  a hit is *evidence for a human to review*, never an automatic pin; this
  tool's own directory is always excluded from the scan (its docs, tests
  and planner fixtures are full of deliberate port mentions), along with
  VCS/build noise directories, binary files (NUL in the first 8 KiB), and
  files over `MAX_SCAN_BYTES` (1 MiB). Exit code: `0` when candidates are
  found (each printed as `candidate: <path>:<line>: port <N>` plus the
  evidence line; a single distinct port also prints the exact
  `plan-port-pin <N> --patch` follow-up), `3` when none are found (the item
  stays blocked — the command never guesses a port), `2` on bad input.
  New `tests/test_portdiscovery.py` (33 test cases) pins the pattern
  contract (positive and negative line shapes), the tree-walk skips, the
  candidate record shape, and the CLI end to end — including an anchor
  test that the default workspace scan still reports "blocked" (exit 3),
  which is the visible, reviewable signal when PengyOS upstream defines
  the port. The blocked item stays open: fifth re-check, 2026-09-16 — no
  tool in the 35-tool workspace (including the new
  `pengy-cli`/`pengyos-cli`/`pengy-agent-package` doc stubs) defines a
  model-server port, so the port is still not guessed.

- Port-pin transition patch renderer (`portpin.render_transition_patch`)
  and the `plan-port-pin --patch` flag (with `--repo-root`, default
  `.`): the still-blocked "pin the model server's port" transition is
  now fully *mechanical* and reviewable as one artifact — a unified diff
  over the four repo files it touches (`profiles/pengy-agent`, the
  `SANCTIONED_NETWORK_RULES` lockstep entry in
  `pengyos_apparmor/validator.py`, the `test_network_grants` pin in
  `tests/test_profiles.py`, and the unbound-form anchor in
  `tests/test_validator.py`). Every anchor must match the current repo
  content verbatim (matched on the stripped line, indentation preserved),
  and a missing file, a missing/duplicated anchor, or an unchanged file
  fails loudly with `PortPinTransitionError` (CLI exit 2) — a patch that
  would not apply is worse than no patch. The rendered diff is pinned by
  the test suite to pass `git apply --check` (and to leave the exact
  post-transition state) against a copy of the repo it was rendered
  from; `REMAINING_MANUAL_STEPS` now lists applying the diff, updating
  the prose docs, and re-running the suite. New tests in
  `tests/test_portpin.py`: patch shape and exact old/new lines, the
  drift guard checked against the live repo, real `git apply` of the
  rendered patch (skipped when git is unavailable), 8 drift scenarios
  (missing/duplicated anchor in each of the four files), a missing
  transition file, bad ports, and the `--patch` CLI end to end
  (including the byte-identical dry-run guarantee). The blocked item
  stays open: PengyOS still defines no model-server port (fourth
  re-check, 2026-09-16 — none of the 35 workspace tools, including the
  new `pengyos-tiers`/`pengyos-upstream-watch`/`pengyos-update-triage`/
  `pengyos-secureboot-keys`/`pengyos-m2-defconfig`, defines one), so the
  port is still not guessed.

- Port-pin transition planner (`pengyos_apparmor.portpin`) and the
  `pengyos-apparmor plan-port-pin <port> [profile]` subcommand (default
  profile `profiles/pengy-agent`): a **dry run** of the still-blocked
  "pin the model server's port" transition. It renders the exact
  port-pinned rule and its local `# sanctioned:` line (the space inside
  `peer=(…)` included — the verbatim sanction match depends on it),
  transforms the profile text in memory (`apply_port_pin`, a pure text
  transformation that preserves indentation and replaces exactly the
  unbound grant line), and validates the result with the strict contract
  (`verify_port_pin` — the transformed profile is strict-clean). It
  fails loudly (`PortPinTransitionError`, CLI exit 2) when the profile
  does not carry exactly one unbound model-server grant, already pins a
  port, or is ambiguous; ports are canonicalized (`normalize_port`:
  1..65535, no leading zeros, no non-decimal literals). CLI exit: 0 when
  the transformed profile is strict-clean, 1 on validation issues, 2 on
  bad input. No files are modified and the port is never invented — it
  must be the one PengyOS defines upstream; the remaining reviewed edits
  (`SANCTIONED_NETWORK_RULES` lockstep entry, test pins, docs) are
  printed, not performed. The planner's old rule is pinned in lockstep
  to the committed `profiles/pengy-agent` grant and to
  `SANCTIONED_NETWORK_RULES`. New `tests/test_portpin.py` (52 test
  cases with parametrization) covers the module contract, port
  normalization, exact string shapes,
  the text transformation (including a repaired partially-done
  transition and the not-idempotent-on-pinned behaviour), strict-clean
  verification against the committed profile, and the CLI end to end.
  The blocked checklist item stays open: PengyOS still defines no
  model-server port anywhere, so the port is still not guessed.

- Profile-local network sanction directives: a `# sanctioned: <rule>`
  comment line in a profile sanctions that exact `network` rule for that
  profile, on top of the package-level `SANCTIONED_NETWORK_RULES` set
  (new public constant `SANCTION_DIRECTIVE` holds the prefix). A local
  directive's rule must be an exact `network` rule the profile itself
  carries — a mismatch, an empty directive, or a non-network rule is a
  hard *error*, so a dead/typo'd directive can never silently hide a
  grant — and it applies to allow rules only (a `deny network …` keeps
  its no-op warning). Plain `#` prose is inert. This is the intended
  vehicle for answering a grant in the profile's own file: the
  still-blocked model-server port pin's *validator-side* state becomes a
  single-file edit — `profiles/pengy-agent` gets `network inet tcp
  peer=(ip=127.0.0.1 port=N),` plus the matching local directive and
  passes `validate --strict` with zero warnings, pinned end to end by
  `test_port_pin_with_local_sanction_is_strict_clean_end_to_end` (the
  port itself remains undefined by PengyOS and is not guessed; the
  `SANCTIONED_NETWORK_RULES` lockstep entry and the test pins are still
  updated in the same change).
  `tests/test_validator.py` gains a 12-test group covering the directive
  grammar, exact-match semantics, deny inertness, prose immunity, and
  the strict-clean transition.

- Port-pin transition guard (tests only): `tests/test_validator.py` pins
  the shape of the still-blocked "pin the model server's port" follow-up —
  the sanctioned grant is the port-unbound form (anchor assertion to be
  updated deliberately when the port is defined), the port-pinned form
  (`peer=(ip=127.0.0.1 port=N)` / `port=N-M`) is grammar-valid with
  recognized loopback scope, and it is NOT sanctioned until
  `SANCTIONED_NETWORK_RULES` is updated in the same change (0 errors /
  2 warnings; `--strict` fails end to end). The item itself stays open:
  PengyOS has not defined the model server's default port anywhere, and
  the port is deliberately not guessed.

- The committed profiles are now **strict-clean**: `pengyos-apparmor
  validate --strict profiles/` passes (0 errors, 0 warnings). The three
  over-broad `/**` wildcard-tree write grants were replaced with
  explicit-path rules: `pengy-agent` writes only its workspace root,
  the flat `.git` metadata under it, the flat `/tmp/pengy-agent/*`
  scratch area (scratch files are temporary artifacts — the `k`
  keepcaps flag is gone), and `/var/log/pengy/agent.log`; `sshd` writes
  only `/run/sshd.pid` (the PidFile) under `/run`.
- New validator constant `SANCTIONED_NETWORK_RULES`: an exact-rule
  allowlist of network grants the project has answered deliberately —
  the network analogue of `WRITE_PREFIXES`. A well-formed rule that
  matches an entry verbatim carries no posture warnings (currently the
  pengy-agent's loopback grant
  `network inet tcp peer=(ip=127.0.0.1)`); any deviation — a port
  added, a different IP or type — is not sanctioned and is warned about
  (grammar mistakes are errors either way). `tests/test_validator.py`
  pins the sanctioned set against the grants actually committed.
- `tests/test_profiles.py` pins the tightened contract: no allow rule
  carries write/keepcaps/link flags on a `/**` tree, pengy-agent's
  write surface is the exact pinned path set, and sshd's only `/run`
  write is the PidFile.
- The `find-model-server-port` upstream re-check now discovers two more
  definition shapes so the still-blocked port-pin item cannot stay
  blocked after the port is actually defined upstream (re-check 20th:
  still blocked — `find-model-server-port` exits 3, `port-pin-status`
  reports `blocked`; the port was NOT guessed): the *inline-mapping*
  shape (the component name, a mapping colon, then the number directly
  — `model server: NNNN` / `model-server: NNNN` / `model_server: "NNNN"`,
  a one-line YAML/INI definition where neither the word *port* nor an
  address appears; new public
  `portdiscovery.MODEL_SERVER_INLINE_PATTERN`, which requires the bare
  number right after the colon, so "model server: it listens on …" is
  still a miss) and the *concatenated-identifier / environment-variable*
  shape (the word *port* fused to the component name with `-`/`_`:
  `MODEL_SERVER_PORT=NNNN`, buildroot defconfig style
  `BR2_..._MODEL_SERVER_PORT="NNNN"`, `model-server-port: NNNN`,
  make-style `?=`/`:=`; one optional middle identifier segment such as
  `model_server_default_port` tolerated; new public
  `portdiscovery.MODEL_SERVER_IDENTIFIER_PATTERN` — a plural fused key
  such as `MODEL_SERVER_PORTS = [N]` is not a candidate). The number
  must still be a valid TCP port (1..65535) and a hit is still
  *evidence for a human to review* — the never-guess apply gate is
  unchanged. `tests/test_portdiscovery.py` gained 24 cases (public API,
  positives/negatives for both shapes, prose/identifier dedup, tree
  fixture with a buildroot-style defconfig, CLI end to end); the live
  workspace anchor still reports blocked (exit 3 — upstream defines no
  port in any of the five shapes yet).
- The `find-model-server-port` upstream re-check now discovers the
  *Kconfig option* shape (re-check 21st: still blocked —
  `find-model-server-port` exits 3, `port-pin-status` reports `blocked`;
  the port was NOT guessed): the buildroot `Config.in` two-line form
  where the port sits on its own line — a `config <symbol>` option whose
  symbol is a fused model-server-port identifier (case-insensitive, one
  optional middle segment, the fused *port* at the END of the symbol — a
  plural symbol is not a default-port option) followed, in the indented
  option body, by a `default <bare number>` line. Only a bare decimal
  expression is a value (hex, arithmetic, and symbol references are not);
  blank lines, indented comments, and other body lines keep the option
  open; a `help` block's free text never contributes a value (the block
  ends at the first line at the `help` line's indent or shallower, blank
  lines included); the option is closed by a Kconfig directive
  (`config`/`menu`/`source`/`if`/…) or a dedent, and the state is per
  file. This closes the buildroot gap left by the defconfig single-line
  shape added in the previous re-check. The number must still be a valid
  TCP port (1..65535) and a hit is still *evidence for a human to
  review* — the runbook's trigger paragraph now tells the reviewer to
  confirm the enclosing `config` symbol for these candidates; the
  never-guess apply gate is unchanged. `tests/test_portdiscovery.py`
  gained 24 cases (positives/negatives, the option and help-block state
  machines, per-file state, machine independence, a tree fixture with a
  realistic `Config.in`, CLI end to end); the live workspace anchor
  still reports blocked (exit 3 — upstream defines no port in any of the
  six shapes yet).
- The `find-model-server-port` upstream re-check now discovers the
  *quoted-key section* shape (re-check 22nd: still blocked —
  `find-model-server-port` exits 3, `port-pin-status` reports `blocked`;
  the port was NOT guessed): the JSON/JSONC/quoted-YAML form where a
  quoted `"model-server":` / `'model-server':` key line (an optional
  flow-mapping brace `"model-server": {`, unquoted included, and a
  trailing `#`/`//` comment tolerated) opens a block carrying a `port`
  key line — quoted or unquoted key (`"port"`, `"listen_port"`,
  `"port-default"`), bare or quoted value (`11434`, `"8080"`), trailing
  comma tolerated. It reuses the section-scoped shape's close rules
  (a sibling quoted key at the opener's indent or a shallower line
  closes the block, so a sibling component's `"port"` line is never a
  candidate; per-file state), and the single-line JSON value
  `"model-server": 11434` is reported by the inline-mapping shape
  (whose key-quote tolerance it gained for the same one-liner). This
  closes the config-file gap left by the TOML/INI/YAML section shape:
  a port defined in a component `config.json` can no longer hide the
  transition. The number must still be a valid TCP port (1..65535) and a
  hit is still *evidence for a human to review* — the runbook's trigger
  paragraph now names the quoted-key section among the multi-line
  candidates to confirm enclosingly; the never-guess apply gate is
  unchanged. `tests/test_portdiscovery.py` gained 24 cases (positives /
  negatives, close semantics, dedup with the prose form, composition
  with the other shapes, a tree fixture with a component
  `model-server.json`, CLI end to end); the live workspace anchor still
  reports blocked (exit 3 — upstream defines no port in any of the seven
  shapes yet).
- The `find-model-server-port` upstream re-check now discovers the two
  *default-expression* value forms the identifier and Kconfig shapes left
  invisible: the *shell parameter-expansion default* (the service-script
  form — `MODEL_SERVER_PORT="${MODEL_SERVER_PORT:-NNNN}"` / `${VAR-NNNN}`:
  any inner variable name, whitespace after the `:` tolerated, a quote
  either outside the expansion — shell quoting — or inside it; a wrapper
  without a number is not a candidate, and a `PORT:-NNNN` occurrence
  inside ANOTHER variable's wrapper is not one either, because the bare
  `-` before the number reads as an identifier continuation) and the
  *Kconfig string-typed default* (a `default "NNNN"` / `default 'NNNN'`
  value line inside the `config …_MODEL_SERVER_PORT` option — the quoted
  decimal must be MATCHED; an unterminated or mismatched quote is not a
  value, and hex / arithmetic / symbol references stay non-values).
  Both are still *evidence for a human to review* — the never-guess apply
  gate is unchanged. `tests/test_portdiscovery.py` gained 43 cases
  (positives / negatives for both forms, the per-occurrence resume rule
  over the identifier pattern's new two-group alternation, composition
  with the other shapes, a tree fixture with a service script and a
  buildroot `Config.in` string option, CLI end to end); the live
  workspace anchor still reports blocked (exit 3 — upstream defines no
  port in any shape yet).
- The `find-model-server-port` upstream re-check now discovers the
  *section address-key* shape the section shapes left invisible: inside
  a model-server section (either opener form — `[model-server]` /
  `model_server:` or the quoted-key `"model-server":` block), the
  component's own `url` / `endpoint` / `address` / `listen` / `host`
  key line (exact stem, quotes and case tolerated — a suffixed or
  prefixed key such as `metrics_url` or `base-url` points at another
  component's endpoint and is never a candidate) whose value carries a
  concrete `<address>:<port>` (the address a dotted quad, a bracketed
  or bare IPv6 token, or `localhost`; a scheme and trailing path are
  tolerated, the value's quote optional) — the port embedded in a listen
  address or URL instead of a bare `port` key (`url =
  "http://127.0.0.1:NNNN/v1"`, `listen = 127.0.0.1:NNNN`). A URL without
  a port, a bare IP, a path-only value, a comment line, a
  non-concrete hostname, or a bare `key:` line (a YAML list item on the
  next line) is not a candidate; the section close rules are shared with
  the port-key shape, so the sibling components' URL lines are never
  candidates. Still *evidence for a human to review* — the never-guess
  apply gate is unchanged. `tests/test_portdiscovery.py` gained 28 cases
  (positives for all five keys and both opener forms, the negatives, the
  section-scope interaction, a tree fixture, CLI end to end); the live
  workspace anchor still reports blocked (exit 3 — re-check 24th:
  upstream defines no port in any of the eight shapes yet).
- The `find-model-server-port` upstream re-check now discovers the
  *fused-identifier address-key* shape the section shapes left
  invisible: a FLAT single line (no model-server section opener) with
  the component name fused to an address stem — `url` / `endpoint` /
  `address` / `listen` / `host` — whose value carries a concrete
  `<address>:<port>` instead of a bare number
  (`MODEL_SERVER_URL="http://127.0.0.1:NNNN/v1"` env/service-script,
  `model_server_endpoint = 127.0.0.1:NNNN` TOML/INI/make,
  `model-server.url = "…"` TOML dotted key, buildroot defconfig
  `BR2_..._MODEL_SERVER_HOST="127.0.0.1:NNNN"`; the value separator
  `=`/`:=`/`?=`/`:`/whitespace/`.`, the value's quote optional, a
  scheme and trailing path and a shell parameter-expansion wrapper
  around the value tolerated). One optional middle identifier segment
  is tolerated, which means a suffixed stem
  (`model_server_metrics_url`) is a candidate too — a definition claim
  a human reviews (the identifier shape has the same looseness);
  a plural fused key (`MODEL_SERVER_URLS`), a URL without a port, a
  bare IP, a path-only value, a non-concrete hostname, or the
  fully-spaced prose form is not a candidate. New public
  `portdiscovery.MODEL_SERVER_FUSED_ADDR_PATTERN`. Still *evidence for
  a human to review* — the never-guess apply gate is unchanged.
  `tests/test_portdiscovery.py` gained 28 cases (positives for all
  five stems and the dotted-key / defconfig / wrapper / middle-segment
  variants, the negatives, composition and de-dup with the other
  shapes, a tree fixture, CLI end to end); the live workspace anchor
  still reports blocked (exit 3 — re-check 25th: upstream defines no
  port in any of the nine shapes yet).
- The `find-model-server-port` upstream re-check now discovers the
  *short-flag* shape every earlier shape left invisible: the component
  name followed by the SHORT `-p` flag with a bare number value
  (`model-server -p NNNN`, `model-server -pNNNN`, `model-server
  -p=NNNN`, a systemd `ExecStart=/…/model-server -p NNNN` line, a
  service script, a spec sentence; the value optionally quoted, other
  flags in between tolerated, the flag must come AFTER the name). The
  flag token must be EXACTLY `-p` at a token boundary — `-profile` /
  `--print`-style long tokens are not the flag — and a flag with no
  number is not a claim. The long `--port` form is NOT this shape: a
  word boundary sits between the flag's `-` and `port`, so it is
  already reported by the prose shape (suffixed long flags such as
  `--http-port NNNN` alike). New public
  `portdiscovery.MODEL_SERVER_SHORT_FLAG_PATTERN`. Still *evidence for
  a human to review* — the never-guess apply gate is unchanged.
  `tests/test_portdiscovery.py` gained 34 cases (flag-separator /
  quoting / case / ExecStart / prose positives, the long-token and
  direction and out-of-window negatives, composition with the other
  shapes, a tree fixture with a systemd unit + service script, CLI end
  to end); the live workspace anchor still reports blocked (exit 3 —
  re-check 26th: upstream defines no port in any of the ten shapes
  yet).
- The `find-model-server-port` upstream re-check now discovers the
  *flag-default* shape the other flag forms left invisible: the same
  flags — the short `-p` flag, the single-dash `-port`, the long
  `--port` — whose value is wrapped in a shell parameter-expansion
  default, the service-script `ExecStart` form (`ExecStart=/…/
  model-server --port ${MODEL_SERVER_PORT:-NNNN}`, `model-server -p
  ${V-NNNN}`, `model-server -p=${V:-NNNN}`). The flag token must sit at
  a token boundary (line start or preceded by whitespace or a quote)
  and be exactly one of the three forms (long tokens such as
  `-profile`/`--print` are not flags; the word boundary after `port`
  keeps `--ports` out), and the flag must come AFTER the component name.
  The value is the expansion's *default* — the value the flag takes
  when the variable is unset, i.e. the port the service runs with by
  default (any inner variable name, the `:` optional, whitespace after
  the `:`/`-` separator tolerated, a quote either outside the expansion
  or inside it; a wrapper without a number is not a candidate, and the
  wrapper never reads into a nested expansion). A bare value is not this
  shape — the short-flag shape and the prose shape (a bare `--port NNNN`
  reads as the word *port*) already report those, and the new pattern is
  silent on both, so no double count. New public
  `portdiscovery.MODEL_SERVER_FLAG_DEFAULT_PATTERN`. Still *evidence for
  a human to review* — the never-guess apply gate is unchanged.
  `tests/test_portdiscovery.py` gained 38 cases (separator / quoting /
  inner-variable / case / ExecStart positives, the no-wrapper, long-token,
  direction, nested-expansion and out-of-window negatives, composition
  with the other shapes, de-dup with the identifier shape, a tree fixture
  with a systemd unit + service script, CLI end to end); the live
  workspace anchor still reports blocked (exit 3 — re-check 27th:
  upstream defines no port in any of the eleven shapes yet).

### Fixed

- The `find-agent-workspace-layout` scan now decodes files with a
  *leading UTF-8 BOM stripped* (`utf-8-sig`) — the file decode rule it
  already shared with the `find-model-server-port` scan but had silently
  diverged from. The layout pattern is not line-start-anchored, so a
  BOM-marked file was matched correctly either way (re-check 31
  verified that and left this scan alone) — but the *evidence line* a
  human reviews was printed with an invisible `U+FEFF` in front of it:
  a Windows-authored or tool-generated upstream layout spec would show
  up in the re-check output with an unstripped BOM while the port scan
  stripped the same marker. Now both scans apply the same rule: ONE
  leading BOM stripped (a BOM is a file-level encoding marker, not line
  content — a `U+FEFF` on a later line is line content and stays in
  place), and a BOM-less file decodes identically, so the change is a
  pure no-op for every BOM-less fixture. `tests/test_layoutdiscovery.py`
  gains the BOM group (line-1 evidence clean, one-BOM-only, later-line
  BOM kept, non-binary-skip, BOM-less no-op, CLI end to end). The
  port-pin item is still blocked (re-check 34: upstream defines no
  port, the grant stays deliberately unbound).
- The profile validator now applies its "a malformed rule reports its
  own error only" discipline to the per-field path-grammar checks, not
  just to the token-shape and comma-terminator errors. A `path` rule
  whose path is not absolute, whose flag token is missing, or whose
  flag token is invalid is malformed as a whole, so the *semantic*
  checks (the whole-filesystem allow-grant error, the over-broad
  wildcard-tree warning, and the write-confinement error) no longer
  additionally fire for it. Before the fix a relative-path write rule
  (`var/lib/other w,`) or an invalid-flag write rule (`/var/lib/other
  rwz,`) double-reported — its own grammar error *plus* a spurious
  write-confinement error — reasoning about the write surface of a rule
  real AppArmor rejects outright. A *well-formed* rule keeps every
  semantic check (`/** rw,` still reports both the whole-filesystem and
  the write-confinement error; a tree write outside the declared trees
  still pairs them with the over-broad warning). New tests in
  `tests/test_validator.py` pin the single-error behaviour (relative
  path, invalid flag token, both at once, no-slash path, missing flag
  token — each with no write-confinement error), the preserved
  well-formed co-firing, and the CLI end to end. The committed profiles
  stay strict-clean (`validate --strict profiles/` 0/0). The port-pin
  item is still blocked (re-check 33: upstream defines no port, the
  grant stays deliberately unbound).

- Automated the upstream re-check for the still-blocked "pin the
  pengy-agent's workspace file-write surface" item: the
  `pengyos-apparmor find-agent-workspace-layout [root]` subcommand
  (new module `pengyos_apparmor.layoutdiscovery`) scans a directory
  tree (default: the PengyOS workspace root, like
  `find-model-server-port`) for text lines that *define* the agent's
  working layout — a hit must name a concrete path token *below* the
  agent's workspace root (`layoutdiscovery.AGENT_WORKSPACE_ROOT`,
  lockstep-pinned to the committed profile's write grants); glob
  characters are allowed, a trailing `.`/`/` is treated as prose
  punctuation, and the root alone is not a candidate (the root grant
  already exists). The never-invent rule holds: candidates are printed
  as evidence (`candidate: <path>:<line>: <surfaces>` + the line) for a
  human to review — exit `0` with candidates, `3` without (the item
  stays blocked), `2` on a bad root. The skip rules are shared with the
  port scan (every checkout of this tool — including a tool checkout
  used as the scan root — plus VCS/build noise, binary and files over
  1 MiB); `portdiscovery`'s binary/checkout helpers are now public
  (`is_binary`, `is_tool_checkout`). New `tests/test_layoutdiscovery.py`
  (39 test cases) pins the pattern, the skip rules, the candidate
  record shape, the CLI end to end, and the live-workspace anchor that
  goes red the moment PengyOS upstream defines the layout. The blocked
  items stay open: 18th port-pin re-check (scan exits 3,
  `port-pin-status` reports `blocked`), and the live workspace still
  carries no agent layout definition.
- The validator now rule-validates **brace-bearing rule lines**: the rule
  body excludes its structural lines by line *identity*, not by substring
  — the header line and the counted brace lines (the code lines ending in
  `{` or `}`) are not rules, and *everything else* is rule-validated.
  The old substring filter dropped any line containing a brace, so a rule
  carrying braces — e.g. `network ip={127.0.0.1, 10.0.0.1} rw,` — was
  silently never checked (a write grant on a brace path could sit
  outside the declared write trees and validate clean). Address sets are
  a **documented grammar error**: multi-member `ip={a,b}` sets carry a
  comma and fail the comma-terminator rule, and the comma-free forms
  (`ip={a}`, `ip={}`, `port={N}`, local or peer) get a dedicated
  "unsupported address set" / "unsupported port set" error — one rule
  per address/port is the expressible form, which the per-exact-rule
  sanction model is built on. Braces *in paths* are literal glob
  characters (AppArmor path globs use `*`/`?`/`[..]`), so a well-formed
  brace path stays clean. Stray extra brace lines keep reporting the
  brace-count error only (no double rule validation). 16 new cases in
  `tests/test_validator.py`; committed profiles stay strict-clean
  (0/0).
- The profile validator now enforces the **comma terminator**: a rule
  line must end with exactly one comma and no comma may appear inside a
  rule. The earlier check only required the last character to be a
  comma, so `/etc/passwd, r,` validated clean as the path
  `/etc/passwd,` with flag `r`, `deny /etc/passwd, r,` as a read of a
  comma-laden path, and `network inet tcp rw,,` (a double terminator)
  passed as if the second comma were invisible — all of which real
  AppArmor rejects (it reads the comma as a rule terminator). Both
  shapes are now **grammar errors** ("commas terminate rules…" / "rule
  must end with exactly one ','"); a comma-laden rule reports its own
  error only (the per-field checks skip it, like the token-shape
  errors), and this means the validator's `ip=` conditional does not
  support address sets (`ip={a,b}`). Pinned by 18 new cases in
  `tests/test_validator.py` (comma-in-token rules across path/deny/
  capability/network shapes, double trailing commas, the one-error-only
  behaviour, well-formed controls — including the sanctioned unbound
  loopback grant that feeds the port-pin transition's anchors — and a
  CLI end-to-end check). Committed profiles stay strict-clean (0
  errors, 0 warnings). The blocked port-pin item was re-checked
  (16th, 2026-09-18): the live workspace scan still exits 3 and
  `port-pin-status` reports `blocked`.

- The profile validator now enforces the *token shape* of `path` and
  `capability` rules: a path rule is exactly `'<path> <flags>'` (one
  path token and one flag-set token, after an optional `deny`), and a
  capability rule names **exactly one** capability. Extra tokens were
  silently dropped — `/etc/passwd /etc/shadow r,` parsed as
  `/etc/passwd r,` (first path + last flags, middle ignored) and passed
  validation, while `capability setuid chown,` was only warned as a
  single "suspicious name". Both are now **grammar errors** ("path rule
  must be exactly '<path> <flags>'" / "capability rule must name
  exactly one capability"); a malformed rule reports its own error only
  (the per-field checks do not fire for the silently-taken tokens), and
  every well-formed two-token rule (incl. `deny …`) stays strict-clean.
  Pinned by 21 new cases in `tests/test_validator.py` (parametrized
  multi-token path and capability rules, deny-prefixed variants, the
  one-error-only behaviour, single-slot controls, and a CLI end-to-end
  check); the two `test_invalid_rule_prefix_flagged` fixtures became
  two-token variants so the prefix check is exercised on its own.
  Committed profiles stay strict-clean (0 errors, 0 warnings).

- The profile validator now enforces the apparmor.d(5) *multiplicity*
  invariant for `network` rules: a rule may carry at most **one**
  domain, at most **one** type/protocol, and at most **one** `peer=(…)`
  conditional. Each of those grammar slots is optional but appears only
  once, yet the per-token shape check accepted a second one —
  `network inet inet rw` (two families), `network tcp udp rw` (two
  protocols), and a rule with two `peer=(…)` blobs all passed as
  well-formed (posture warnings only). The duplicate slot is now a
  **grammar error** ("more than one domain / type-protocol /
  peer=(…)"), while every slot appearing exactly once (the committed
  `network inet tcp peer=(ip=127.0.0.1)` and the port-pinned form)
  stays strict-clean. Pinned by 13 new cases in
  `tests/test_validator.py`; the committed profiles remain 0 errors / 0
  warnings under `validate --strict`. Landed as the 14th re-check of the
  still-blocked port-pin item — the live workspace scan still exits 3.

- `find-model-server-port` no longer reports *itself* as upstream
  evidence. The scan only ever excluded its own directory (`TOOL_ROOT`),
  so a wider scan root (e.g. the workspace parent that also hosts
  sibling checkouts of this tool) surfaced the tool's own *deliberate*
  fixture ports — vendored copies of its test fixtures and doc examples
  ("model server port is 11434", …) — as candidates, and scanning a root
  that *is* a tool checkout flagged the tool's own docs. Now every
  checkout of this tool (any directory containing the
  `pengyos_apparmor` package — the marker, so a mere
  `pengyos_apparmor.txt` note or a directory without the package is not
  pruned) is skipped wherever it sits under the scan root, and a root
  that is itself a checkout reports no candidates at all (every line
  under it is a self-match). A repo that vendors the tool still has its
  non-vendored upstream definitions found. Pinned by
  `tests/test_portdiscovery.py` (five new cases, incl. an anchor that
  scans the tool's own live root and expects exit 3 "no model-server
  port definition found").

- The profile validator now *counts* braces instead of only checking their
  positions. The old check looked at the first line ending in `{` and the
  first line ending in `}`, so a profile with a stray extra `{` (two opens,
  one close) passed as balanced even though it is an unbalanced-braces parse
  error (real AppArmor rejects it). The validator now tracks every brace line
  and reports an error when more than one `{` or more than one `}` appears on
  a code line — a profile has exactly one top-level `{`/`}` pair. Braces
  inside `#` comment lines are prose and are not counted. All existing brace
  errors (missing close, close-before-open, header-not-opening,
  content-after-close) are unchanged; this adds the count invariant the
  positional checks miss. Pinned by `tests/test_validator.py`
  (`test_extra_open_brace_flagged`, `test_extra_close_brace_flagged`,
  `test_balanced_single_brace_pair_has_no_brace_error`,
  `test_braces_in_comments_are_not_counted`). The committed profiles stay
  strict-clean (0 errors, 0 warnings).
- The `port=` conditional in a `network` rule is now range-validated,
  not just shape-checked. The old shape regex (`\d{1,5}`, optional
  `-\d{1,5}`) let `port=99999`, `port=0`, `port=65536` and inverted
  ranges like `port=2000-1000` through as well-formed; they are not real
  TCP ports. A single port must now be `1..65535` and a range `N-M` must
  have both endpoints in window with `N <= M` (peer and local conditionals
  alike). This matches the `1..65535` window the port-pin planner's
  `normalize_port` already enforces, so a pin the planner emits can never
  be rejected by the profile it lands in. Pinned by
  `tests/test_validator.py` (`test_network_out_of_range_port_flagged`,
  `test_network_in_range_port_is_well_formed`, and
  `test_valid_port_window_matches_normalize_port`, which locks the
  validator's window to `normalize_port`'s). The committed profiles stay
  strict-clean.
- Inverted-boolean bug in the `rule_lines()` helper of
  `tests/test_profiles.py`: it returned `is_deny` under the name
  `is_allow`, so two checks (`test_no_wildcard_everything_allow_grants`,
  `test_sshd_never_allows_shadow_reads`) were silently examining deny
  rules instead of allow rules. The helper now returns the real
  `is_allow` and both checks examine allow rules as their names say
  (the committed profiles pass both, as intended).
- Loopback socket access for the pengy agent: `profiles/pengy-agent` now
  carries exactly one `network` rule —
  `network inet tcp peer=(ip=127.0.0.1),` — granting TCP client access to
  the local model server, scoped to the loopback interface via AppArmor's
  fine-grained inet mediation (apparmor.d(5), AppArmor >= 3.0). A `peer=`
  conditional cannot carry bind/listen permissions, so the agent stays a
  pure client; the port is deliberately unbound until the model server's
  default port is defined (follow-up in implementation.md).
  `tests/test_profiles.py` now pins the *exact* set of network grants
  per profile (the loopback rule for `pengy-agent`, none for any other
  profile) instead of forbidding network rules outright.
- Validator network-rule grammar: `network` rules are now checked against
  the apparmor.d(5) model `network [access] [domain] [type|protocol]
  [ip=… port=…] [peer=(…)]` — new public constants
  `NETWORK_ACCESS_KEYWORDS` (socket-operation access keywords),
  `NETWORK_DOMAINS`, `NETWORK_TYPE_PROTOCOLS`, and `LOOPBACK_IPS`.
  Grammar mistakes (unknown domains/types, malformed `ip=`/`port=`
  conditionals, unknown peer conditionals, bare `peer=` tokens, unknown
  access tokens) are *errors*; the grant itself, implied permissions
  (no explicit access list), and non-loopback `ip=` scope are
  *warnings*, as before. `peer=(…)` blobs are parsed with their inner
  spaces, as apparmor.d(5) shows them.
- `tests/test_github_ready.py` — the GitHub-ready contract from the spec:
  required files (`README.md`, `LICENSE`, `CHANGELOG.md`, `.gitignore`,
  `pyproject.toml`) exist and are non-empty; LICENSE is genuinely MIT with
  "Copyright (c) 2026 Pat Wendorf"; pyproject is consistent (MIT metadata,
  console script target resolves, readme field exists); CHANGELOG carries
  the current pyproject version (lockstep bump); README documents the CLI
  exit-code contract, the `--strict` flag, the `pytest -q` testing command
  and its layout paths; `.gitignore` patterns verified effective via
  `git check-ignore --no-index` (and required files are not ignored); the
  full suite is green, asserted by a nested `pytest --ignore=<this file>`
  subprocess.
- Over-broad wildcard-tree grant lint (warning): an *allow* rule whose
  path contains a `/**` wildcard tree and whose permission set uses any
  of the new `OVER_BROAD_TREE_PERMS` flags (`w` write, `k` keepcaps,
  `l` link) is warned about — even inside a declared write tree, a
  single `/**` rule like `/tmp/x/** rwkl,` is wider than the minimum
  the work needs. Read/execute/map tree grants (`r`, `x`, `m`) stay
  clean, `deny` rules are unaffected, and tree writes outside the
  declared trees remain hard errors (both findings fire together).
  The committed profiles now report 3 such warnings (0 errors), and
  `--strict` surfaces them as tightening candidates.
- Profile validator (`pengyos_apparmor.validator`), stdlib-only and
  root-free: header check (`<name> (alias) {` with name == file name),
  exactly one balanced brace pair, well-formed capability/path rules
  (valid rule prefixes — path, `deny`, `capability`, `network`; absolute
  paths; permission flags drawn from `rwxcplkmia`; trailing comma),
  strict-posture check (no `complain`/`unconfined`), no
  whole-filesystem allow grants, and write confinement to each
  profile's declared write prefixes. Findings carry `error`/`warning`
  severities; only errors fail.
- `WRITE_PREFIXES`, `WRITE_EXEMPT_PATHS`, `VALID_PERM_FLAGS` and
  `NON_PROFILE_FILES` moved into the validator package as the single
  source of truth; `tests/test_profiles.py` now imports them.
- `pengyos-apparmor validate <file-or-dir>... [--strict]` CLI
  subcommand: validates files or every profile in a directory
  (README.md excluded), prints `name: OK` per clean profile and a
  summary; exit 1 on any error, 2 on bad targets; `--strict` also fails
  on warnings.
- `tests/test_validator.py` — grammar/lint/CLI test group, plus a pin
  in `tests/test_profiles.py` that every committed profile validates
  with zero errors.
- Minimal, strict-by-default AppArmor profiles:
  - `profiles/pengy-agent` — headless pengy agent: read-only interpreter,
    stdlib and shared libraries, read-only install under `/opt/pengy/agent`,
    writes confined to its own workspace (`/opt/pengy/workspaces/pengy-agent`),
    `/tmp/pengy-agent`, and its log; explicit `deny` guardrails on
    `/etc/shadow`, `/etc/gshadow`, sudoers and `/root/**`; no `network`
    rules at all.
  - `profiles/sshd` — PengyOS OpenSSH server: key-only auth posture
    (deliberately **no** `/etc/shadow` read — shadow access is a
    config-drift signal), read-only config/keys, writes confined to
    `/run/sshd`, `/var/log/auth.log`, `/var/run/utmp` and `/dev/log`.
- `tests/test_profiles.py` — pins the profile authoring contract: header
  `name (alias) {` with name == file name, single balanced brace pair,
  well-formed rule lines (absolute paths, valid permission flags, trailing
  comma), strict posture (no `complain`/`unconfined`), no
  wildcard-everything allow grants, no network rules, writes confined to a
  per-profile allowlist, sshd never allows shadow reads, and an opt-in
  real-parse check via `aa-parser` behind `PENGYOS_IT=1`.

## [0.1.0] — 2026-09-16

### Added

- Repository scaffold: `pengyos_apparmor` stdlib-only package with
  `--version`/`--help` CLI (subcommands land with their subtasks),
  `pyproject.toml` with pytest config (`pythonpath=["."]`, so the suite runs
  without an editable install), and the `profiles/` directory.
- GitHub-ready files: `README.md`, `LICENSE` (MIT), `CHANGELOG.md`,
  `.gitignore`.
- `tests/test_scaffold.py` smoke tests (package import, version agreement
  with `pyproject.toml`, CLI `--version`/`--help` behaviour).
