# pengyos-apparmor

Minimal, **strict-by-default** AppArmor profiles for PengyOS core components
(including the `pengy` agent) plus a validator/lint that catches common
profile mistakes.

Part of [PengyOS](../../README.md). Implements SPEC §1: confinement for Core and the
agent.

## Layout

| Path | Purpose |
|------|---------|
| `profiles/` | AppArmor profile files, one per confined process (`pengy-agent`, …) |
| `pengyos_apparmor/` | Python package: CLI, validator, lint (stdlib-only) |
| `tests/` | pytest suite — no network, no root, no real AppArmor required |

## Profiles

| Profile | Confinement |
|---------|-------------|
| `pengy-agent` | Headless pengy agent: read-only interpreter/stdlib/shared libs and install; writes pinned to explicit paths — its workspace root, the flat `.git` metadata under it, the flat `/tmp/pengy-agent/*` scratch area, and its log; explicit `deny` guardrails on credentials and `/root/**`; the only socket access is a single loopback-scoped `network` grant (TCP, `peer=(ip=127.0.0.1)`, pure client, sanctioned by `SANCTIONED_NETWORK_RULES`) to the local model server. |
| `sshd` | PengyOS OpenSSH (key-only auth): no `/etc/shadow` read — shadow access is a config-drift signal; writes pinned to `/run/sshd.pid`, `auth.log`, `utmp`, `/dev/log`. |

Both are **strict-by-default**: no `complain` mode, no wildcard-everything
allow grants, no wildcard-tree write grants (writes are pinned to explicit
paths), and no network rules except the agent's single sanctioned
loopback-scoped grant. Together that means
`pengyos-apparmor validate --strict profiles/` passes: 0 errors, 0 warnings.
The invariants (including the exact write surface and the exact set of
network grants) are pinned by `tests/test_profiles.py`, so a profile that
drifts wide breaks the suite.
A real parse with `aa-parser` runs when AppArmor tooling is present and
`PENGYOS_IT=1` is set.

## Usage

```console
$ pengyos-apparmor --version
$ pengyos-apparmor validate profiles/
pengy-agent: OK
sshd: OK
2 profile(s): 0 error(s), 0 warning(s)
$ pengyos-apparmor validate --strict profiles/
pengy-agent: OK
sshd: OK
2 profile(s): 0 error(s), 0 warning(s)   # exit 0 — the committed profiles are strict-clean
```

`pengyos-apparmor validate <file-or-dir>...` checks profile grammar
basics — header (`name (alias) {`, name == file name), exactly one
balanced brace pair, and well-formed capability/path rules (valid rule
prefixes, absolute paths, permission flags drawn from `rwxcplkmia`,
trailing comma) — plus the PengyOS invariants: strict posture (no
`complain`/`unconfined`), no whole-filesystem allow grants, and writes
confined to each profile's declared write prefixes. `network` rules are
grammar-checked against apparmor.d(5): domains, types/protocols, socket
access keywords, and `ip=`/`port=`/`peer=(…)` conditionals must be
well-formed (errors). Exit code is `1` on any error; `--strict` also
fails on warnings. Warnings (any `network` rule that is not sanctioned —
the pengy-agent's documented loopback grant is sanctioned and carries no
warnings — plus a non-loopback `ip=` scope or implied permissions on a
non-sanctioned network rule, an over-broad `/**` wildcard-tree grant that
carries write/keepcaps/link flags even inside a declared write tree, or a
new, unregistered profile name) do not fail validation on their own —
they mark places a future profile tightening should answer intentionally.
`r`/`x`/`m` grants on a scoped tree stay clean; that is the intended
minimal style. A sanction is per *exact* rule, like a write prefix is per
declared path: any deviation (a port added, a different IP or type) is
not sanctioned and warns. Sanctions come from the package-level
`SANCTIONED_NETWORK_RULES` set *and* from profile-local directives: a
`# sanctioned: <rule>` comment line in a profile sanctions that exact
`network` rule for that profile (the rule must be one the profile
actually carries — a mismatch is a hard error — and it applies to allow
rules only). That is how a profile answers for a grant in its own file;
the pending model-server port pin will ship as the port-pinned rule plus
its local directive.

```console
$ pengyos-apparmor plan-port-pin 11434 profiles/pengy-agent
port-pin transition plan for pengy-agent (port 11434)
  old rule: network inet tcp peer=(ip=127.0.0.1),
  new rule: network inet tcp peer=(ip=127.0.0.1 port=11434),
  new line: # sanctioned: network inet tcp peer=(ip=127.0.0.1 port=11434)
  (dry run — no files are modified)
  remaining reviewed steps:
    - apply the rendered unified diff (git apply) — it carries the
      mechanical edits: the profile grant plus its local '# sanctioned:'
      line, the validator.SANCTIONED_NETWORK_RULES lockstep entry, and
      the test pins (test_network_grants in tests/test_profiles.py and
      test_committed_grant_is_the_unbound_port_form in
      tests/test_validator.py)
    - update the docs: the profiles/pengy-agent comment block, the README
      profile row, the spec.md pinned-grant paragraph, and CHANGELOG.md
    - re-run the full suite: pytest -q and
      pengyos-apparmor validate --strict profiles/
pengy-agent: OK (strict-clean)
```

`pengyos-apparmor plan-port-pin <port> [profile]` is a **dry run** of
the model-server port-pin transition (the profile defaults to
`profiles/pengy-agent`): it renders the exact port-pinned rule and its
local `# sanctioned:` line, transforms the profile text *in memory*, and
validates the result with the strict contract — exit code `0` when the
transformed profile is strict-clean (0 errors **and** 0 warnings), `1`
when validation finds issues, `2` on bad input (invalid port, missing
file, profile already pinned or without the unbound grant). No files are
modified; the port must be the one PengyOS has defined upstream — the
command plans, it never invents one.

With `--patch` the command also prints the **complete mechanical
transition as a unified diff** over the four repo files it touches
(`profiles/pengy-agent`, the `SANCTIONED_NETWORK_RULES` lockstep entry
in `pengyos_apparmor/validator.py`, and the two test pins in
`tests/test_profiles.py` / `tests/test_validator.py`): every anchor line
must match the current repo content verbatim, so any drift fails loudly
(exit `2`) instead of emitting a patch that would not apply. The diff is
pinned by the test suite to pass `git apply --check` against the repo it
was rendered from.

```console
$ pengyos-apparmor find-model-server-port
no model-server port definition found under ~/src/pengyos
the port-pin item stays blocked — do not guess a port; the port must come from PengyOS upstream
$ pengyos-apparmor find-model-server-port /path/to/workspace
candidate: SPEC.md:3: port 11434
  The model server's default port is 11434.
one distinct port candidate: 11434 — review the line above, then run: pengyos-apparmor plan-port-pin 11434 --patch
```

`pengyos-apparmor find-model-server-port [root]` scans a directory tree
(default: the PengyOS workspace root — where the upstream specs and all
sibling tools live) for text lines that *define* the model server's
default port: the recurring re-check for the still-blocked port-pin item.
It is deliberately conservative — the line must carry the words
“model server” in one of eleven definition shapes (prose with the word
“port” — which also reads a long `--port` flag with a bare number value,
the listen form
`<address>:<port>`, the section-scoped `port` key, the quoted-key
section form, the inline mapping `model server: NNNN`, the fused
identifier `MODEL_SERVER_PORT=NNNN`, the Kconfig `default` option,
the section address key, the fused identifier address key
`MODEL_SERVER_URL="<address>:<port>"`, the short `-p` flag
`model-server -p NNNN`, or the flag-default form — the same flags whose
value is wrapped in a shell parameter-expansion default, the
service-script `ExecStart` form
`model-server --port ${MODEL_SERVER_PORT:-NNNN}`) and a 2–5 digit TCP port —
so a hit is evidence for a human to review: the command never
decides or guesses the port. Exit code: `0` with candidates, `3` without (the
item stays blocked), `2` on a bad root. This tool's own directory,
VCS/build noise, binary files, and files over 1 MiB are never scanned.
When a candidate is confirmed upstream, the mechanical transition is
`pengyos-apparmor plan-port-pin <port> --patch` (below).

```console
$ pengyos-apparmor find-agent-workspace-layout
no agent workspace layout definition found under ~/src/pengyos
the workspace file-write surface item stays blocked — do not invent a path set; the layout must come from PengyOS upstream
$ pengyos-apparmor find-agent-workspace-layout /path/to/workspace
candidate: SPEC.md:3: build/**
  The agent writes build output to /opt/pengy/workspaces/pengy-agent/build/**.
one layout candidate (surface: build/**) — review the line above, then pin the workspace file-write surface
```

`pengyos-apparmor find-agent-workspace-layout [root]` scans a directory
tree (default: the PengyOS workspace root, like
`find-model-server-port`) for text lines that *define* the agent's
working layout below its workspace root
(`/opt/pengy/workspaces/pengy-agent`): the recurring re-check for the
still-blocked workspace file-write surface item. It is deliberately
conservative (a hit must name a concrete path *below* the workspace
root — the root grant already exists, so the root alone is not a
candidate — and the path set is never invented), so a hit is evidence
for a human to review. Exit code: `0` with candidates, `3` without (the
item stays blocked), `2` on a bad root. This tool's own directory,
VCS/build noise, binary files, and files over 1 MiB are never scanned
(shared skip rules with the port scan).

```console
$ pengyos-apparmor plan-port-pin 11434 --patch
port-pin transition plan for pengy-agent (port 11434)
  old rule: network inet tcp peer=(ip=127.0.0.1),
  new rule: network inet tcp peer=(ip=127.0.0.1 port=11434),
  new line: # sanctioned: network inet tcp peer=(ip=127.0.0.1 port=11434)
  (dry run — no files are modified)
  mechanical transition diff (review it, then git apply):
--- a/profiles/pengy-agent
+++ b/profiles/pengy-agent
@@ -74,5 +74,6 @@
   # A rule with a peer= condition cannot carry bind/listen permissions
   # (apparmor.d(5)), so the agent stays a pure client: no listening, no
   # UDP, no other family.
-  network inet tcp peer=(ip=127.0.0.1),
+  # sanctioned: network inet tcp peer=(ip=127.0.0.1 port=11434)
+  network inet tcp peer=(ip=127.0.0.1 port=11434),
 }
# … the diff continues over pengyos_apparmor/validator.py,
# tests/test_profiles.py, and tests/test_validator.py
pengy-agent: OK (strict-clean)
```

`pengyos-apparmor port-pin-status [repo-root] [--scan-root ROOT]`
reports *where the port-pin transition is right now* (the read side of
the write-side commands above): it classifies each of the four
transition files as `unbound` (still pre-transition), `pinned` (carries
the post-transition form for a port), or `drifted` (anything else — a
missing or doubled anchor, a port range, an out-of-range port, a
conflicting port, or a flipped line whose companion assert was not
flipped), and combines that with the upstream port scan into one state:

* `blocked` (exit `3`) — all four files unbound and no upstream
  definition found (the item stays blocked); the default today;
* `ready` (exit `0`) — all unbound and upstream candidate(s) found;
* `pinned` (exit `0`) — all four files consistently pinned to one
  port; the upstream scan is printed as *evidence* (a pin without an
  upstream definition is flagged as not-yet-commit-able);
* `partial` (exit `1`) — the transition is mixed or drifted across
  files (a half-applied patch is caught here before a full test run
  would be).

Read-only: nothing is modified and no port is ever guessed; exit `2` on
a bad repo root or scan root.

```console
$ pengyos-apparmor port-pin-status
port-pin transition state: BLOCKED (port not yet defined upstream)
  profiles/pengy-agent: unbound — the model-server grant line: unbound anchor present
  pengyos_apparmor/validator.py: unbound — the SANCTIONED_NETWORK_RULES entry: unbound anchor present
  tests/test_profiles.py: unbound — the test_network_grants pin: unbound anchor present
  tests/test_validator.py: unbound — the unbound-form anchor: unbound anchor present
  upstream scan (~/src/pengyos):
    no model-server port definition found
  action: stays blocked — do not guess a port; re-run once PengyOS defines the model server's default port
$ echo $?
3
```

`pengyos-apparmor apply-port-pin <port> [repo-root] [--scan-root ROOT]
[--dry-run]` is the *apply side*: it writes the four transition files in
place, staged-atomically (each file is committed through a `*.pin-tmp`
staging file plus an atomic rename — an interrupted apply can leave a
mixed pre/post state for `port-pin-status` to report `partial`, but never
a torn file) — the profile grant plus its local `# sanctioned:` line, the
`SANCTIONED_NETWORK_RULES` entry, and the two test pins) — the same
mechanical edits the `--patch` diff renders, computed by the same code
(`portpin.compute_transition`). The never-guess gate: the requested
port must appear among the upstream scan's candidates (default scan
root: the PengyOS workspace) — otherwise nothing is written and the
command exits `3`. On success the transformed profile must be
strict-clean (before any write), the `port-pin-status` re-probe must
report `pinned:<N>`, and the remaining reviewed steps (docs, suite,
checklist item) are printed. Exit code: `0` applied and consistent, `1`
the post-apply re-probe is not pinned (or the transformed profile is
not strict-clean), `2` on bad input or repo drift, `3` when the port
has no upstream evidence. The transition is one-way: once applied,
applying it again fails loudly.

```console
$ pengyos-apparmor apply-port-pin 8080
port 8080 has no upstream evidence — the scan under ~/src/pengyos found no model-server port definition
refusing to pin: the port must come from PengyOS upstream (never guessed). Re-run once the scan finds port 8080, or review with: pengyos-apparmor port-pin-status . --scan-root ~/src/pengyos
$ echo $?
3
$ pengyos-apparmor apply-port-pin 11434 /path/to/tool --scan-root /path/to/workspace
upstream evidence: SPEC.md:1: port 11434
  The model server's default port is 11434.
  profiles/pengy-agent: 2 line(s) added, anchor line(s) replaced
  pengyos_apparmor/validator.py: 1 line(s) added, anchor line(s) replaced
  tests/test_profiles.py: 1 line(s) added, anchor line(s) replaced
  tests/test_validator.py: 2 line(s) added, anchor line(s) replaced
port-pin transition applied: port 11434 pinned in 4 files
state re-probe: pinned:11434 (all transition files consistent)
remaining reviewed steps:
    - update the docs: the profiles/pengy-agent comment block, the README profile row, the spec.md pinned-grant paragraph, and CHANGELOG.md
    - re-run the full suite: pytest -q and pengyos-apparmor validate --strict profiles/
    - remove the port-pin item from TODO.md once the suite is green and the port is defined upstream
```

`pengyos-apparmor sync-port-pin-docs <port> [repo-root]
[--scan-root ROOT] [--dry-run]` is the *docs side* of the first
post-apply step: once the repo is `pinned:<N>` (all four mechanical
transition files consistent), it updates the four prose doc surfaces
from the unbound prose to the pinned prose — the
`profiles/pengy-agent` network comment block, the README profile row,
the spec.md pinned-grant paragraph, and a new first CHANGELOG entry
under `## [Unreleased]` / `### Added` (module
`pengyos_apparmor.portdocsync`). Each surface is recognized by its
exact pre-sync anchor and its pinned marker: **editable** (rewritten),
**synced** (untouched — a re-run is a no-op), or **drifted** (neither —
the command fails loudly and, being all-or-nothing, writes *nothing*).
The gate: the repo must already be `pinned:<port>` (the port is never
guessed — it must be the one the repo pins); the upstream scan is
reported as evidence only. The edited profile comment is re-checked for
strict-clean before any write. Exit code: `0` synced or already in
sync, `1` when the repo is not pinned to the requested port (apply the
transition first), `2` on bad input or doc drift; `--dry-run` prints
the docs diff without writing.

```console
$ pengyos-apparmor sync-port-pin-docs 8080
pengyos-apparmor: repo is not pinned to port 8080: state blocked (profiles/pengy-agent: unbound, pengyos_apparmor/validator.py: unbound, tests/test_profiles.py: unbound, tests/test_validator.py: unbound) — apply the mechanical transition first (pengyos-apparmor apply-port-pin 8080)
$ echo $?
1
$ pengyos-apparmor sync-port-pin-docs 11434 /path/to/tool --scan-root /path/to/workspace
docs sync for port 11434 (repo pinned: all four transition files consistent)
upstream evidence: SPEC.md:1: port 11434
  The model server's default port is 11434.
  profiles/pengy-agent: editable — unbound prose replaced with the pinned form
  README.md: editable — unbound prose replaced with the pinned form
  spec.md: editable — unbound prose replaced with the pinned form
  CHANGELOG.md: editable — unbound prose replaced with the pinned form
docs synced for port 11434: 4 file(s) updated
remaining reviewed steps:
    - re-run the full suite: pytest -q and pengyos-apparmor validate --strict profiles/
    - remove the port-pin item from TODO.md once the suite is green and the port is defined upstream
```

The whole transition — current state, the trigger, the exact command
sequence, the files each stage touches, the failure modes, and the
anchor tests that go red the moment the port exists upstream — is
documented in the operator runbook
[`docs/port-pin-transition.md`](docs/port-pin-transition.md) (its
lockstep is pinned by `tests/test_portpin_runbook.py`).

## Testing

```console
$ python3 -m pytest -q
```

The suite runs headless as a normal user: no network, no root, no display,
no running kernel AppArmor. Anything that would touch the real system is
opt-in behind `PENGYOS_IT=1` and is skipped otherwise.

## Status

See `CHANGELOG.md`.
Profiles and validator are being built one small subtask per iteration.

## License

MIT — see [LICENSE](LICENSE).
