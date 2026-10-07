# PengyOS apparmor — minimal profiles + profile validator

## Summary
Minimal AppArmor profiles (incl. for the pengy agent) and a validator.

## Purpose
SPEC §1: confinement for Core and the agent.

## Deliverable
A repo with `profiles/*` and a pytest validator (profile grammar basics: header, braces balanced, capability/path rules well-formed).

## Interface
- files: profiles/pengy-agent, profiles/sshd, …
- package: pengyos_apparmor.validator — pure, stdlib-only; the single
  source of truth for the authoring contract constants (write allowlist,
  exempt paths, valid perm flags, non-profile file exclusions, sanctioned
  network grants), which the test suite imports rather than re-declaring.
- CLI: `pengyos-apparmor validate <file-or-dir>... [--strict]` — exit 1
  on any error, 0 otherwise (--strict also fails on warnings).
  `pengyos-apparmor plan-port-pin <port> [profile]` — dry run of the
  model-server port-pin transition (default profile `profiles/pengy-agent`):
  renders the exact port-pinned rule and its local `# sanctioned:` line,
  transforms the profile text in memory, and validates the result with the
  strict contract — exit 0 when the transformed profile is strict-clean
  (0 errors and 0 warnings), 1 on validation issues, 2 on bad input
  (invalid port, missing file, profile already pinned or without the
  unbound grant, or `--patch` drift). `--patch` additionally prints the
  complete mechanical transition as a unified diff over the four repo
  files it touches (the profile, the `SANCTIONED_NETWORK_RULES` lockstep
  entry, and the two test pins), failing loudly (exit 2) when the repo
  has drifted from the transition recipe. No files are modified; the port
  must come from upstream (PengyOS), the command never invents one.
  `pengyos-apparmor find-model-server-port [root]` — scan a directory tree
  (default: the PengyOS workspace root, `portdiscovery.DEFAULT_SCAN_ROOT`,
  two levels above the tool) for text lines that *define* the model
  server's default port: the recurring re-check for the still-blocked
  port-pin item. The scan is deliberately conservative — a hit must carry
  the words *model server* plus one of eleven shapes and a 2–5 digit TCP
  port in 1..65535: (a) the word *port* on the same line (the prose forms,
  `portdiscovery.MODEL_SERVER_PORT_PATTERNS`); (b) the listen shape — an
  `<address>:<port>` (dotted quad, bracketed or bare IPv6 token,
  `localhost`) after a listen/bind/serve verb ("the model server listens
  on 127.0.0.1:NNNN", where the word *port* is absent;
  `portdiscovery.MODEL_SERVER_LISTEN_PATTERN` — a URL mention without the
  verb, or prose without an address, is still a miss); (c) the
  section-scoped config shape — a `port` key line (`port = N` /
  `port: N` / `port is N`; suffixed keys such as `port-default` and
  `listen_port` included) *inside* a model-server section: an opener line
  `[model-server]` (TOML/INI; subsections such as `[model-server.http]`
  included), `model_server:` (YAML key; list items and nested blocks
  tolerated), or the quoted-key form `"model-server":` / `'model-server':`
  (JSON/JSONC/quoted-YAML; an optional flow-mapping brace
  (`"model-server": {`, unquoted included) and a trailing `#`/`//`
  comment tolerated), closed by a new section header, a shallower line,
  or a sibling mapping key at the opener's indent or shallower (array
  values and key lines without a number are not candidates; quoted port
  keys (`"port": NNNN`) and quoted values (`"NNNN"`) are candidates, and
  a trailing comma is tolerated); the section state is per file; (d) the inline-mapping shape — the component name, a
  mapping colon, then the number directly (`model server: NNNN` /
  `model-server: NNNN` / `model_server: "NNNN"` / the quoted-key JSON
  one-liner `"model-server": NNNN`, a one-line YAML/INI/JSON definition
  where neither the word *port* nor an address appears;
  `portdiscovery.MODEL_SERVER_INLINE_PATTERN` — a value that is not a
  bare number is not a candidate); (e) the concatenated-identifier /
  environment-variable shape — the word *port* fused to the component
  name with `-`/`_` instead of whitespace (`MODEL_SERVER_PORT=NNNN`,
  buildroot defconfig style `BR2_..._MODEL_SERVER_PORT="NNNN"`,
  `model-server-port: NNNN`, make-style `?=`/`:=`, or wrapped in a shell
  parameter-expansion default in the service-script form —
  `MODEL_SERVER_PORT="${MODEL_SERVER_PORT:-NNNN}"` / `${VAR-NNNN}`:
  any inner variable name, whitespace after the `:` tolerated, a quote
  either outside the expansion (shell quoting) or inside it; a wrapper
  without a number is not a candidate, and a `PORT:-NNNN` inside ANOTHER
  variable's wrapper is not one either (the bare `-` before the number
  reads as an identifier continuation); one optional middle identifier
  segment such as `model_server_default_port` tolerated;
  `portdiscovery.MODEL_SERVER_IDENTIFIER_PATTERN` — a plural fused key
  such as `MODEL_SERVER_PORTS` is not a candidate); (f) the Kconfig
  option shape (a buildroot `Config.in`) — a `config <symbol>` line whose
  symbol is a fused model-server-port identifier (case-insensitive, one
  optional middle segment, the fused *port* at the end of the symbol — a
  plural symbol is not a default-port option), with an indented `default
  <number>` value line in the option body: a bare decimal or a MATCHED
  single-/double-quoted decimal (`default "NNNN"` — the string-typed
  buildroot option form; an unterminated or mismatched quote is not a
  value) is a value, and so is a Kconfig *conditional* default
  (`default NNNN if <condition>` — the standard buildroot idiom: the value
  stays a literal bare/quoted decimal, the condition after the `if` is
  metadata about when it applies, not part of the value; a dangling
  `default NNNN if` with no condition is not a value) (hex, arithmetic,
  or symbol references as the value are not);
  the option stays open across blank lines, indented comments, and other
  body lines, a `help` block ends at the first line at its own indent or
  shallower (its free text never contributes a value), and the option is
  closed by a directive (`config`/`menu`/`source`/`if`/…) or a dedent;
  the option state is per file; (g) the quoted-key section shape — the
  JSON/JSONC/quoted-YAML form of shape (c): a quoted (or flow-braced)
  `model-server` key line opening a block, with a `port` key line
  (quoted or unquoted key, bare or quoted number, trailing comma
  tolerated) inside it; it reuses shape (c)'s section close rules
  (a sibling quoted key at the opener's indent or a shallower line
  closes the block, so a sibling component's `"port"` line is never a
  candidate), and the single-line JSON value `"model-server": NNNN` is
  reported by shape (d) instead; (h) the section address-key shape —
  inside a model-server section (either section opener form, shapes (c)
  and (g)), the component's own `url` / `endpoint` / `address` /
  `listen` / `host` key line (exact stem — a suffixed or prefixed key
  such as `metrics_url` or `base-url` points at another component's
  endpoint and is not a candidate; quotes and case tolerated) whose
  value carries a concrete `<address>:<port>` (the address one of a
  dotted quad, a bracketed or bare IPv6 token, or `localhost`; a
  scheme and trailing path are tolerated, the value's quote optional) —
  the port embedded in a listen address or URL instead of a bare
  `port` key (`url = "http://127.0.0.1:NNNN/v1"`,
  `listen = 127.0.0.1:NNNN`); a URL without a port, a bare IP, a
  path-only value, a comment line, or a bare `key:` line (a YAML list
  item on the next line) is not a candidate; (i) the
  fused-identifier address-key shape — the flat single-line
  counterpart of shape (h) WITHOUT a model-server section: the component
  name fused to an address stem `url` / `endpoint` /
  `address` / `listen` / `host` (`MODEL_SERVER_URL="http://127.0.0.1:NNNN/v1"`,
  `model_server_endpoint = 127.0.0.1:NNNN`, the TOML dotted key
  `model-server.url = "…"`, buildroot defconfig
  `BR2_..._MODEL_SERVER_HOST="127.0.0.1:NNNN"`), the value carrying a
  concrete `<address>:<port>` instead of a bare number (the value
  separator `=`/`:=`/`?=`/`:`/whitespace/`.` (the TOML dotted-key
  separator), the value's quote optional, a scheme and a trailing path
  and a shell parameter-expansion wrapper around the value tolerated;
  one optional middle identifier segment such as
  `model_server_default_url` tolerated — which means a suffixed stem
  such as `model_server_metrics_url` is a candidate too, a definition
  claim a human reviews (the identifier shape has the same looseness;
  precision is the human-review step, the same as every other shape);
  a plural fused key such as `MODEL_SERVER_URLS` is not a candidate —
  the stem must be a token end); a URL without a port, a bare IP, a
  path-only value, or a non-concrete hostname is not a candidate, and
  the fully-spaced prose form ("the model server url is
  http://127.0.0.1:NNNN") is not this shape — the name and the stem
  must be fused; `portdiscovery.MODEL_SERVER_FUSED_ADDR_PATTERN`;
  (j) the short-flag shape — the component name, then the SHORT
  `-p` flag with a bare number value (`model-server -p NNNN`,
  `model-server -pNNNN`, `model-server -p=NNNN`, a systemd
  `ExecStart=/…/model-server -p NNNN` line, a service script, a spec
  sentence), the value optionally quoted; the flag token must be
  EXACTLY `-p` at a token boundary — `-profile`/`--print`-style long
  tokens are not the flag (the character after `-p` must be the value),
  and a flag with no number is not a claim — and the flag must come
  AFTER the name, with any other flags in between tolerated; the long
  `--port` form is NOT this shape: a `\b` sits between the flag's `-`
  and `port`, so it is already reported by the prose shape (a)
  (suffixed long flags such as `--http-port NNNN` alike); the name
  carries no lookbehind, as in every other shape (embedded in a larger
  token it still matches — a claim a human reviews); a plural name
  (`model servers -p NNNN`) is not the component; out-of-window and
  6-digit values are skipped by the shared `normalize_port` path;
  `portdiscovery.MODEL_SERVER_SHORT_FLAG_PATTERN` — the only shape
  that sees a definition whose flag carries no *port* letters at all);
  (k) the flag-default shape — the same flag forms (the short
  `-p` flag, the single-dash `-port`, the long `--port`, each at a
  token boundary, after the component name, any other flags in between)
  whose value is wrapped in a shell parameter-expansion default, the
  service-script `ExecStart` form — `model-server --port
  "${MODEL_SERVER_PORT:-NNNN}"` / `model-server -p ${V-NNNN}`: any inner
  variable name, the `:` optional, whitespace after the `:`/`-` separator
  tolerated, a quote either outside the expansion (shell quoting) or
  inside it; the number is the *default* — the value the flag takes when
  the variable is unset, i.e. the port the service runs with by default
  (a definition claim a human reviews, the same epistemic status as the
  identifier shape's wrapper form); a bare value is the short-flag shape's
  (the flag-default pattern is silent on it), and a wrapper without a
  number is not a candidate — the wrapper reads to the first separator,
  never into a nested expansion; out-of-window and 6-digit values are
  skipped by the shared `normalize_port` path; the long `--port` form
  with a BARE number is still the prose shape's (shape (a)) — only the
  wrapper value needs this shape; `portdiscovery.MODEL_SERVER_FLAG_DEFAULT_PATTERN`.
  A hit is evidence
  for a human to review, never an
  automatic pin; the scan is read-only; every checkout of this tool
  (any directory containing
  the `pengyos_apparmor` package — vendored copies in sibling workspaces
  included, so the scan can never report this tool's own fixture ports as
  upstream definitions), VCS/build noise dirs (`SKIP_DIRS`), binary files
  (NUL in first 8 KiB) and files over `MAX_SCAN_BYTES` (1 MiB) are never
  scanned; files are decoded as UTF-8 with a *leading BOM stripped*
  (`utf-8-sig`): a BOM is a file-level encoding marker, and unstripped it
  sits on line 1 before `^` and breaks the line-start-anchored shapes
  (the section opener, the Kconfig option opener) — a BOM on a *later*
  line is line content, not stripped; a scan whose *root* is a tool
  checkout reports no candidates
  (everything under it is self-match). Exit 0 with candidates
  (each printed as `candidate: <path>:<line>: port <N>` + the evidence
  line; a single distinct port also prints the exact
  `plan-port-pin <N> --patch` follow-up), 3 when none are found (the item
  stays blocked — the command never guesses a port), 2 on a bad root.
  `pengyos-apparmor find-agent-workspace-layout [root]` — scan a
  directory tree (default: the same PengyOS workspace root,
  `portdiscovery.DEFAULT_SCAN_ROOT`) for text lines that *define* the
  agent's working layout below its workspace root:
  `layoutdiscovery.AGENT_WORKSPACE_ROOT` (`/opt/pengy/workspaces/` +
  `pengy-agent`, locked in lockstep to the committed profile's write
  grants by `tests/test_layoutdiscovery.py`) — the recurring re-check
  for the still-blocked workspace file-write surface item. The scan is
  deliberately conservative (module `pengyos_apparmor.layoutdiscovery`):
  a hit must name a concrete path token *below* the workspace root
  (glob characters `*`/`?`/`[..]` allowed; a trailing `.`/`/` is prose
  punctuation, not path content; the root alone is not a candidate —
  the root grant already exists; a root that is a suffix of a longer
  path is rejected), so a hit is a definition claim for a human to
  review, never an automatic widening of the profile. The skip rules
  are shared with the port scan (every checkout of this tool, a tool
  checkout used as the scan root, `SKIP_DIRS` noise, binary files, files
  over `MAX_SCAN_BYTES`), and the file decode rule is shared too: files
  are decoded as UTF-8 with a *leading BOM stripped* (`utf-8-sig`, the
  port scan's decode rule) — a BOM is a file-level encoding marker, not
  line content, so it must not leak into the printed evidence line; a
  `U+FEFF` on a later line is line content and stays in place (the
  layout pattern is not line-start-anchored, so matching a BOM-marked
  file works either way — the decode rule is about the evidence line,
  not the match). Exit 0 with candidates (each printed as
  `candidate: <path>:<line>: <surface>, …` + the evidence line; a single
  candidate with a single distinct surface also prints the follow-up
  pointing at the TODO.md pin item), 3 when none are found
  (the item stays blocked — the command never invents a path set), 2 on
  a bad root.
  `pengyos-apparmor port-pin-status [repo-root] [--scan-root ROOT]` —
  transition-state probe (the read side of the port-pin tooling, module
  `pengyos_apparmor.portstatus`): classifies each of the four transition
  files (`portpin.PORT_PIN_TRANSITION_FILES`) as `unbound` / `pinned:<N>`
  / `drifted` by matching the exact pre- and post-transition anchor lines
  (the planner/patch only ever emits these, so anything else is drift —
  missing or doubled anchor, port range, out-of-range port, conflicting
  ports, or a flipped `test_validator` anchor line whose companion assert
  was not flipped), and combines that with the upstream scan into one
  state: `blocked` (all unbound, no upstream candidates — exit 3),
  `ready` (all unbound, candidates found — exit 0), `pinned` (all files
  consistently pinned to one valid port; the upstream scan is reported as
  evidence only and the **commit-ability** verdict is printed as a
  machine-greppable `commit-ability: commit-able|not-yet-commit-able` line
  and surfaced as a *distinct* exit code — `commit-able` (the pinned port
  is defined upstream, i.e. present among the scan's candidates): exit 0,
  the transition is complete and the pin may be committed and the
  checklist item flipped; `not-yet-commit-able` (the pinned port is not
  yet defined upstream — a pin without an upstream definition is a local,
  not-yet-ratified decision): exit 4, the pin must not be committed yet),
  or `partial` (mixed/drifted — exit 1). The `commitable` property and the
  `COMMITABLE` / `NOT_YET_COMMITABLE` verdicts live on
  `portstatus.PortPinStatus`; the exit codes are the single source of
  truth in `portstatus` (`EXIT_OK` 0 / `EXIT_PARTIAL` 1 / `EXIT_BAD_INPUT`
  2 / `EXIT_BLOCKED` 3 / `EXIT_NOT_YET_COMMITABLE` 4). Read-only, never
  guesses a port; exit 2 on a bad repo root or scan root (default scan
  root: `portdiscovery.DEFAULT_SCAN_ROOT`).
  `pengyos-apparmor apply-port-pin <port> [repo-root] [--scan-root ROOT]
  [--dry-run]` — the *apply side* of the transition (module functions
  `portpin.compute_transition` / `portpin.apply_transition`, the shared
  core of the `--patch` renderer): writes the four transition files in
  place through `portpin.atomic_commit` (staged-atomic: fsynced
  `*.pin-tmp` staging files swapped in with atomic renames — an
  interrupted commit leaves at worst a mixed pre/post state the probe
  reports `partial`, never a torn file), gated so the requested port must
  carry upstream evidence — the
  `find-model-server-port` scan (default root: the PengyOS workspace)
  must list that exact port among its candidates; there is no override
  flag, the port is never guessed (exit 3, nothing written). The
  transformed profile must be strict-clean before any write (exit 1
  otherwise); after the write, the `portstatus` re-probe must report
  `pinned:<N>` (else exit 1); exit 2 on bad input or repo drift from the
  transition recipe; exit 0 on success, printing the remaining reviewed
  steps (`portpin.POST_APPLY_STEPS`: docs, full suite, flipping the
  checklist item). `--dry-run` computes and prints without writing. The
  transition is one-way: re-applying (same or a different port) raises
  `PortPinTransitionError` ("already pins a port"); a mid-write failure
  leaves a `partial` state that `port-pin-status` detects.
  `pengyos-apparmor sync-port-pin-docs <port> [repo-root] [--scan-root
  ROOT] [--dry-run]` — the docs side of the first post-apply step
  (module `pengyos_apparmor.portdocsync`): once the repo is
  `pinned:<N>` (all four mechanical transition files consistent — the
  gate, checked via `portstatus.inspect_transition`), update the four
  prose doc surfaces named by `portpin.POST_APPLY_STEPS` from the
  unbound prose to the pinned prose: the `profiles/pengy-agent`
  network comment block, the README profile row, the spec.md
  pinned-grant paragraph, and a new first CHANGELOG entry under
  `## [Unreleased]` / `### Added`. Each surface is classified by its
  *exact* pre-sync anchor and *exact* pinned marker — `editable`
  (rewritten), `synced` (untouched; a re-run is a no-op), or `drifted`
  (neither form: the sync fails loudly and, being all-or-nothing,
  writes nothing). The port is never guessed: it must be the port the
  repo already pins (a different port, or the unbound/partial state,
  exits 1); the upstream scan is reported as evidence only (a mismatch
  is noted, not acted on). The edited profile comment is re-validated:
  strict-clean (0 errors, 0 warnings) or the sync refuses. `--dry-run`
  prints a unified diff of the editable surfaces without writing. Exit
  codes: 0 synced/already-in-sync, 1 repo not `pinned:<port>`, 2 bad
  input or doc drift.
- tests: pytest profile validator

## Validator semantics (discovered while implementing)
- Two severities: `error` (grammar violations + hard invariants: bad
  header, unbalanced braces, bad rule prefix / path / permission flags,
  missing trailing comma, `complain`/`unconfined`, whole-filesystem
  allow grants, writes outside the declared write prefixes) and
  `warning` (posture lints a future profile may answer intentionally:
  `network` rules, unknown/bare/suspicious capability grants, profiles
  with no declared write prefixes, and over-broad `/**` wildcard-tree
  grants). Only errors fail validation.
- Braces are *counted*, not just positionally checked: an AppArmor
  profile has exactly one top-level brace pair (the header's `{` and the
  profile's final `}`). Beyond the positional invariants (a header that
  opens the body, a present and last closing brace, no content after it),
  the validator counts the brace lines and reports an **error** when more
  than one `{` or more than one `}` appears on a code line — a stray extra
  brace is an unbalanced-braces parse error even when the first-open /
  first-close / ends-with-close checks all pass. Braces inside `#` comment
  lines are prose and are not counted.
- Rule token shape: a `path` rule is exactly `'<path> <flags>'` — one
  path token and one flag-set token, after an optional `deny` — and a
  `capability` rule names **exactly one** capability. Extra tokens are
  grammar **errors** ("path rule must be exactly '<path> <flags>'" /
  "capability rule must name exactly one capability"), not silently
  dropped: the earlier parse took the first path and the last flag set
  and ignored everything between (`/etc/passwd /etc/shadow r,` passed
  as `/etc/passwd r,`), and a multi-word capability name was only
  warned ("suspicious name"). A malformed rule reports its own error
  only — the per-field checks (absolute path, flag letters, write
  confinement) do not additionally fire for the silently-taken tokens;
  every well-formed two-token rule stays strict-clean. The *same*
  discipline applies once a per-field grammar check itself fails: a
  `path` rule whose path is not absolute, whose flag token is missing,
  or whose flag token is invalid (any letter outside the valid set) is
  malformed as a whole, so the **semantic** checks (the whole-filesystem
  allow-grant error, the over-broad wildcard-tree warning, and the
  write-confinement error) do not additionally fire — a rule with an
  invalid flag token or a non-absolute path is one real AppArmor
  rejects outright, so reasoning about its write surface would endorse a
  check on a rule that can never be active. (Both the non-absolute-path
  error and the invalid-flag error fire when both are true — they are
  independent grammar facts — but no semantic check fires.) A
  *well-formed* rule (absolute path, valid flag token) keeps every
  semantic check: `/** rw,` still reports both the whole-filesystem
  error and the write-confinement error, and a tree write outside the
  declared trees still pairs the write-confinement error with the
  over-broad warning.
- Comma terminator: a comma **terminates** a rule. A rule line must end
  with **exactly one** comma — a second trailing comma (`network inet tcp
  rw,,`) is a stray terminator and a grammar **error** — and no comma may
  appear *inside* the rule: the earlier check only required the last
  character to be a comma, so `/etc/passwd, r,` validated clean as the
  path `/etc/passwd,` with flag `r` and `deny /etc/passwd, r,` as a read
  of a comma-laden path, which real AppArmor rejects (it reads the comma
  as a rule terminator too). A comma-laden rule reports its own error
  only (the per-field checks skip it, like the token-shape errors). This
  means the validator's `ip=` conditional does not support address sets
  (`ip={a,b}`): a comma anywhere mid-rule is an error.
- Brace-bearing rule lines are rule-validated. The rule body excludes
  its structural lines by line **identity**, not by substring: the
  header line (the first non-comment line) and the counted brace lines
  (the code lines ending in `{` or `}` — the header's open, the closing
  brace, and any stray extra brace, which the count check already
  flags and which is NOT rule-validated a second time) are not rules;
  *everything else* is rule-validated, including a rule line that
  carries braces. The earlier substring filter (drop any line containing
  `{` or `}`) silently skipped such rules — a write grant on a
  brace-bearing path could sit outside the declared write trees and
  still validate clean; now it fails write confinement like any other
  rule. Decision recorded 2026-09-18: the `ip=`/`port=` conditionals do
  **not** support address sets — `ip={a,b}` is a documented grammar
  error (multi-member sets carry a comma and fail the comma-terminator
  rule; the comma-free forms `ip={a}`, `ip={}` and `port={N}`, peer or
  local, get a dedicated "unsupported address set" / "unsupported port
  set" error). One rule per address/port is the expressible form, which
  is what the per-exact-rule sanction model is built on. Braces *in
  paths* are literal glob characters (AppArmor path globs use
  `*`/`?`/`[..]`), so a well-formed brace path validates clean.
- `network` rules are *grammar-checked* against apparmor.d(5) — domain,
  type/protocol, socket-access keyword, and `ip=`/`port=`/`peer=(…)`
  conditionals must be well-formed (errors: unknown domains/types,
  malformed IPs, malformed ports, unknown conditionals, bare `peer=`
  tokens). A `port=N` / `port=N-M` conditional is malformed — not just
  the wrong shape — when it falls outside the TCP window: a single port
  must be `1..65535` and a range `N-M` must have both endpoints in
  window with `N <= M` (so `port=99999`, `port=0`, `port=65536` and
  inverted `port=2000-1000` are errors, matching the `1..65535` window
  the port-pin planner's `normalize_port` enforces). Each grammar slot also
  appears at most **once** per rule: a second domain (e.g. `network inet
  inet rw`), a second type/protocol (e.g. `network tcp udp rw`), or a second
  `peer=(…)` conditional is a **grammar error** even when every token on its
  own is valid — the per-token shape check alone accepts them, but
  apparmor.d(5) allows exactly one domain, one type/protocol, and one peer
  per rule. Warnings for any rule
  that is not sanctioned: the socket grant
  itself, implied permissions (no explicit access list), and non-loopback
  `ip=` scope (PengyOS grants should scope to the loopback interfaces,
  `127.0.0.1` / `::1` / `none`). A sanction is per *exact* rule — the
  network analogue of `WRITE_PREFIXES`: a well-formed rule that matches
  verbatim carries no warnings (its lints were answered deliberately);
  any deviation (a port added, a different IP or type) warns. Sanctions
  come from two places: the package-level `SANCTIONED_NETWORK_RULES`
  set, and *profile-local* sanction directives — comment lines of the
  form `# sanctioned: <rule>` (prefix constant `SANCTION_DIRECTIVE`). A
  local directive's rule must be an exact `network` rule the profile
  itself carries (a mismatch/typo is an *error*, so a dead directive can
  never hide), applies to allow rules only, and is matched verbatim
  like every other sanction (the trailing comma is optional, as it is in
  the body). Local directives are the intended vehicle for answering a
  grant in the profile's own file — the pending model-server port pin
  will ship as the port-pinned rule plus `# sanctioned: network inet
  tcp peer=(ip=127.0.0.1 port=N)` inside `profiles/pengy-agent`.
  Plain `#` prose that does not start with the exact directive prefix is
  inert.
- Network grants in the committed profiles are pinned *exactly* by
  `tests/test_profiles.py`: `pengy-agent` carries exactly one rule,
  `network inet tcp peer=(ip=127.0.0.1),` — loopback TCP to the local
  model server, pure client (a `peer=` conditional cannot carry
  bind/listen, so the agent cannot listen); the port is deliberately
  unbound until the model server's default port is defined (see
  TODO.md follow-up). Every other profile is socket-free.
  The port-pin transition's target shape is pinned end to end by
  `tests/test_validator.py` (`test_port_pin_with_local_sanction_is_strict_clean_end_to_end`):
  the profile carries `network inet tcp peer=(ip=127.0.0.1 port=N),`
  plus the matching local `# sanctioned:` directive and passes
  `validate --strict` with zero warnings; the transition still updates
  the `SANCTIONED_NETWORK_RULES` lockstep entry, the
  `test_network_grants` pin, and the transition-guard anchor assertion
  in the same change. The mechanical part of that transition is the
  port-pin planner (`pengyos_apparmor.portpin`, exposed as the
  `plan-port-pin` subcommand): `port_pinned_rule`/`sanction_directive`
  render the exact strings (the space inside `peer=(…)` is included —
  the verbatim sanction match depends on it), `apply_port_pin` performs
  the single-file profile edit as a pure text transformation (replace
  the unbound grant line, insert the directive above it, indentation
  preserved) and fails loudly with `PortPinTransitionError` when the
  profile does not carry exactly one unbound grant, already pins a
  port, or is ambiguous; `verify_port_pin` confirms the transformed
  profile is strict-clean. Ports are canonicalized by `normalize_port`
  (1..65535, no leading zeros, no non-decimal literals). The planner's
  old rule is locked in lockstep to the committed profile and to
  `SANCTIONED_NETWORK_RULES`; `render_transition_patch` (CLI:
  `plan-port-pin --patch`) renders the complete *mechanical* part of the
  transition — the profile grant + local directive, the
  `SANCTIONED_NETWORK_RULES` lockstep entry, and the two test pins — as a
  unified diff whose anchors must match the current repo files verbatim
  (missing/duplicated anchors or unchanged files fail loudly with
  `PortPinTransitionError`, and the diff is pinned by the test suite to
  pass `git apply --check` against the repo it was rendered from), while
  the docs remain reviewed prose. Nothing is ever performed.
- Over-broad grant lint (implemented): an *allow* rule whose path
  contains a `/**` wildcard tree and whose permissions include any of
  the `OVER_BROAD_TREE_PERMS` flags (`w`, `k` keepcaps, `l` link) is
  a *warning* — even inside a declared write tree. Tree grants limited
  to `r`/`x`/`m` stay clean (the intended minimal style), `deny` rules
  are exempt, and a tree write outside the declared trees remains a
  hard error.
- The committed profiles are **strict-clean**: `pengyos-apparmor
  validate --strict profiles/` exits 0 (0 errors, 0 warnings). No
  allow rule writes through a `/**` tree — the three former tree-write
  grants are narrowed to explicit paths (pengy-agent: workspace root +
  flat `.git` metadata + flat `/tmp/pengy-agent/*` scratch + its log;
  sshd: `/run/sshd.pid` under `/run`) — and the one network grant they
  carry is in `SANCTIONED_NETWORK_RULES`. The over-broad lint itself is
  pinned by `tests/test_validator.py` fixtures.

## Verification (tests are first-class)
- valid profile passes.
- unbalanced braces / bad rule prefix flagged.
- over-broad rule warned.
- the port-pin transition is tested piece by piece (scan, plan/patch,
  probe, apply, docs sync) AND composed end to end:
  `tests/test_transition_end_to_end.py` drives a live-repo copy from the
  unbound `ready` state through the real CLI
  (`find-model-server-port` → `apply-port-pin` → `sync-port-pin-docs`) to
  the terminal state, pinning at once that the probe reports `pinned`,
  `validate --strict` is 0/0, all four transition files carry the
  port-pinned form, and all four doc surfaces are synced (idempotent), plus
  a never-guess test proving the gate leaves every file byte-identical when
  no upstream definition exists (the port always comes from the fixture
  scan, never invented).
- The operator runbook for the transition is a permanent repo artifact:
  `docs/port-pin-transition.md` (current state, the trigger, the exact
  command sequence, the per-stage file sets, the failure modes, and the
  anchor tests that flip red when the port exists upstream). Its
  lockstep is pinned by `tests/test_portpin_runbook.py`: the runbook
  names every transition command, every file in
  `PORT_PIN_TRANSITION_FILES` and `PORT_PIN_DOC_FILES`, every probe
  state, every failure mode, and the ordered command sequence, and it
  carries NO port — only the `<N>` placeholder (the never-guess rule
  applies to the runbook itself).

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Catches common profile mistakes.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Enforcing at runtime

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
