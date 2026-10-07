# Port-pin transition — operator runbook

This is the permanent, repo-checked runbook for the still-blocked
TODO.md item *"Pin the local model server's port in the
pengy-agent network grant"*. Everything mechanical about that transition
is automated and tested (`plan-port-pin`, `find-model-server-port`,
`port-pin-status`, `apply-port-pin`, `sync-port-pin-docs`, and the
end-to-end acceptance group); this document is what a human or agent
follows when the transition finally fires.

The port is **never guessed**. Every command in this runbook enforces
that mechanically; the runbook itself therefore names no port — `<N>`
is always the port that PengyOS defines upstream.

## 1. Current state: blocked

Until PengyOS defines the model server's default port, the
`profiles/pengy-agent` grant is deliberately unbound:

```
network inet tcp peer=(ip=127.0.0.1),
```

Once the port exists, that line becomes the port-pinned form
(`<N>` always the upstream-defined port, never a guess):

```
network inet tcp peer=(ip=127.0.0.1 port=<N>),
```

Verify the state at any time (both are read-only):

```console
$ pengyos-apparmor find-model-server-port        # exit 3 while blocked
$ pengyos-apparmor port-pin-status               # state: blocked, exit 3
```

While the item is blocked both must exit `3`: the scan finds no upstream
definition, and the probe reports all four transition files `unbound`.
`port-pin-status` prints the per-file classification and the exact
action line to re-run later.

## 2. The trigger: upstream defines the port

The transition becomes executable when
`pengyos-apparmor find-model-server-port` (default root: the PengyOS
workspace) exits `0` with candidates. A candidate is **evidence for a
human to review**, not a decision: open the printed `path:line`, confirm
the line genuinely defines the model server's default port, and only
then continue. For the multi-line candidates — a `port` key line or an
address-key line (`url` / `endpoint` / `address` / `listen` / `host`
whose value carries a concrete `<address>:<port>`) inside a
`[model-server]` / `model_server:` / `"model-server":` (the quoted-key
JSON/JSONC form) section, or a `default` value line (bare, a quoted `default "NNNN"` — the
string-typed option form, or a conditional `default NNNN if
<condition>` — the value stays a literal number) inside
a `config …_MODEL_SERVER_PORT` buildroot `Config.in` option — confirm
the *enclosing* definition (the section header or the `config` symbol),
not just the printed line; for the single-line config shapes — a listen line naming an
`<address>:<port>`, a one-line mapping (`model server: NNNN`), a
concatenated identifier (`MODEL_SERVER_PORT=NNNN`, buildroot
defconfig style, or the service-script fallback
`MODEL_SERVER_PORT="${…:-NNNN}"`), or the fused-identifier
address-key form (`MODEL_SERVER_URL="<address>:<port>"` — the port
embedded in the value's listen address or URL instead of a bare
number), or the short-flag form (the component name followed by a
short `-p` flag with a bare number value — a systemd `ExecStart`
line, a service script, or a spec sentence; the long `--port` flag
with a bare value reads as the word *port* and is the prose shape),
or the flag-default form (the same flags — `-p` / `-port` / `--port` —
whose value is wrapped in a shell parameter-expansion default, a
systemd `ExecStart` line like `--port ${…:-<N>}`; the number is the
default the flag takes when the variable is unset) — confirm the
*surrounding*
definition (the sentence, the file: a defconfig or fallback line is a
port only if the outer variable is the model server's, not some other
component's; a flag-default line is a port only if the flag belongs to
the model server's own command). If several
distinct ports come back, confirm which one PengyOS intends before
running anything.

The same moment flips these anchor tests red — they are the visible
"now do the runbook" signal (each lives in the file named):

- `test_cli_default_root_is_the_pengyos_workspace_and_still_reports_blocked`
  (`tests/test_portdiscovery.py`)
- `test_cli_blocked_on_the_live_repo_and_workspace`
  (`tests/test_portstatus.py`)
- `test_cli_live_workspace_stays_blocked_and_writes_nothing`
  (`tests/test_portpin_apply.py`)
- `test_live_docs_carry_the_pre_sync_anchors`
  (`tests/test_portdocsync.py`)
- `test_committed_grant_is_the_unbound_port_form`
  (`tests/test_validator.py`)

## 3. The transition (exact sequence)

Run the steps in order, each from the tool's repo root, replacing `<N>`
with the confirmed upstream port:

1. **Confirm the candidate.**
   `pengyos-apparmor find-model-server-port` — review the evidence line.
   `pengyos-apparmor port-pin-status` should now report `ready`.
2. **Apply the mechanical transition.**
   `pengyos-apparmor apply-port-pin <N>` — writes the four transition
   files in place, gated on the upstream evidence (no override flag;
   without evidence it exits `3` and writes nothing). Reviewable
   alternative: `pengyos-apparmor plan-port-pin <N> --patch` renders the
   same edits as a unified diff for `git apply`.
3. **Sync the prose docs.**
   `pengyos-apparmor sync-port-pin-docs <N>` — rewrites the four doc
   surfaces (profile comment block, README profile row, the spec.md
   pinned-grant paragraph, a new CHANGELOG entry) from the unbound prose
   to the pinned prose; it is a no-op re-run and fails all-or-nothing on
   drift.
4. **Re-run the full contract.**
   `pytest -q` **and** `pengyos-apparmor validate --strict profiles/`
   — both must be green (the strict check exits 0).
5. **Confirm the pin is commit-able.**
   `pengyos-apparmor port-pin-status` must report `commit-ability:
   commit-able` (exit `0`) — the pinned port is defined upstream. If it
   reports `commit-ability: not-yet-commit-able` (exit `4`), the repo is
   internally consistent but the pin is not yet PengyOS's decision: do
   not commit or flip yet (re-run the probe once upstream defines the
   port).
6. **Remove the port-pin item** from the repo's `TODO.md` and
   **commit** the pin (gated on the commit-able probe above; the
   transition is not commit-able until the port is defined upstream and
   the suite is green).

### Files touched, per stage

`apply-port-pin` (the four files of
`portpin.PORT_PIN_TRANSITION_FILES`):

- `profiles/pengy-agent` — the grant becomes the port-pinned rule plus
  its local `# sanctioned:` line;
- `pengyos_apparmor/validator.py` — the
  `SANCTIONED_NETWORK_RULES` lockstep entry;
- `tests/test_profiles.py` — the `test_network_grants` pin;
- `tests/test_validator.py` — the unbound-form anchor
  (`test_committed_grant_is_the_unbound_port_form`).

`sync-port-pin-docs` (the four surfaces of
`portdocsync.PORT_PIN_DOC_FILES`):

- `profiles/pengy-agent` — the network comment block;
- `README.md` — the profile row;
- `spec.md` — the pinned-grant paragraph;
- `CHANGELOG.md` — a new first entry under
  `## [Unreleased]` / `### Added`.

(The profile is in both sets by design: the transition edits its grant
line, the docs sync edits its comment block.)

## 4. Failure modes

- **No upstream evidence** — `apply-port-pin` exits `3` and writes
  nothing (the never-guess gate, enforced mechanically; there is no
  override flag). `sync-port-pin-docs` on an unbound repo exits `1`
  and writes nothing.
- **Pinned but not yet commit-able** — the repo is internally
  consistent (`port-pin-status` reports `pinned`) but the pinned port is
  not yet defined upstream (e.g. the pin was applied from a fixture scan
  root, or upstream has not defined the port yet): the probe reports
  `commit-ability: not-yet-commit-able` and exits `4`. The transition is
  complete and the profile is strict-clean, but the pin must not be
  committed and the checklist item must not be flipped until upstream
  defines the port and the probe reports `commit-able` (exit `0`).
- **Half-applied transition** — an interrupted `apply-port-pin` /
  `sync-port-pin-docs` or a partial `git apply` leaves a mixed repo, never
  a torn file: both commands commit staged-atomically (each file is first
  written to a `*.pin-tmp` staging file next to its target and fsynced,
  then swapped into place with an atomic rename). A failure while staging
  changes nothing; a failure between renames leaves each file holding its
  complete old or its complete new content. `pengyos-apparmor
  port-pin-status` classifies the mixed state `partial` (exit `1`) with
  per-file detail before a full test run is needed; an interrupted docs
  sync is finished by simply re-running it (idempotent). An interrupted
  commit surfaces as exit `2` with the exact re-check command printed.
- **Repo drift from the recipe** — an anchor line edited by hand makes
  the transform fail loudly (exit `2`): a patch or write that would not
  apply is worse than none.
- **One-way transition** — once applied, re-applying (same or another
  port) is refused (`PortPinTransitionError`: "already pins a port");
  the transition cannot stack.

## 5. What stays genuinely human

Defining the port is a PengyOS-wide design decision owned *outside* this
tool (top-level SPEC / the future model-server component) — no command
here invents it. Confirming the evidence line, committing the pin (only
once `port-pin-status` reports `commit-ability: commit-able`, exit `0`),
and flipping the checklist item are the remaining human steps.
