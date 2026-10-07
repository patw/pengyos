# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-10-05

### Added
- **The SSH lane: `--ssh`, `--ssh-command`, `--ssh-key`, `--ssh-port`,
  `--ssh-user` on `boot`.** The serial log proves what a guest prints; it cannot
  prove `systemctl --failed` is empty after the log stops being read, whether a
  service is active, or whether the Flatpak stack is there. `pengyos-qemu-verify`
  now runs declared commands **inside the live guest** between readiness and
  teardown, over the channel the images already provide (a build with
  `PENGYOS_AUTHORIZED_KEYS_FILE`, sshd `PermitRootLogin prohibit-password`, a
  hostfwd into port 22). Each check yields the same `CheckResult` a log check
  does, so JSON, JUnit, `checks_passed` and the exit code treat them identically.
  `--ssh` runs the default pair (`ssh-no-failed-units`, `ssh-sshd-active`);
  `--ssh-command COMMAND` adds one that must exit 0.
- **A requested SSH check guarantees its own port forward.** Asking for in-guest
  checks without a forward reaching port 22 is a guaranteed FAIL that reads like
  a broken image, so the forward is added - in the run *and* in what
  `--dry-run`/`probe` print, so what you are shown is what runs. A boot with no
  SSH checks is untouched, so the argv goldens still hold.
- **A dead channel never passes an `absent` check**, and a boot that timed out or
  never launched still owes every requested SSH check a FAIL with a reason. The
  guest's throwaway host key is recorded in the boot's workdir
  (`StrictHostKeyChecking=accept-new` + a real `UserKnownHostsFile`) rather than
  silently ignored.
- **Version files caught up with the changelog**: `pyproject.toml` and
  `__version__` were still `0.3.1` while `0.4.0` (the `desktop` subcommand) had
  already been recorded.

### Tests
- `tests/test_sshchecks.py`: 34 tests - argv goldens, validation, every check
  kind's failing answer, retry-on-connect (and never retrying a real non-zero
  exit), and the boot wiring through the fake qemu with an injected runner.
  Mutation-checked: passing an `absent` check on a refused connection, dropping
  the SSH results from the report, and never adding the forward each fail the
  suite.
- Verified against a real image: 26.10.7 Server built with a key, booted under
  KVM, `--ssh --ssh-command 'systemctl is-enabled sshd' --ssh-command
  'pengyos-update status'` - 12.5 s, `ssh-no-failed-units`/`ssh-sshd-active`/
  `ssh:systemctl-is-enabled-sshd` pass, and `pengyos-update status` correctly
  fails because a raw disk image has no A/B layout yet.

## [0.4.0] - 2026-10-05

### Added
- **`desktop` subcommand: the COSMIC session check folded into this tool** from
  `scripts/qemu-desktop-check.py`, which is deleted. The Desktop gate is now one
  tool with one exit code and one report instead of a script `release.sh` has to
  know about. Same behaviour (installer stick under QEMU with a rendering GPU in
  Xvfb, a probe typed on the guest's serial console, screenshot + logs), but the
  argv, the environment and the report parsing are now pure functions with unit
  tests against real captures - including the two mutations that matter: a
  session with no compositor, and a unit the guest reports failed. `release.sh`
  calls `pengyos-qemu-verify desktop <installer> --out <prefix>`.
- **`autologin-shell` and `no-login-denied` in both shipped matrices.** A login
  *prompt* is not a login: 26.10.3 printed `root (automatic login)` and then
  looped `Permission denied`, and every check the matrix had at the time passed.
  The fact that proves autologin worked is the shell prompt after the login
  banner, so each matrix now requires it (anchored on the edition's own login
  line, so a Core boot cannot satisfy the Server matrix's copy of it) and
  forbids `Permission denied`. Proven against a fixture built from the real Core
  capture, cut at the login line with that loop appended.
- **`--settle SECONDS` on `boot` and `smoke`** (`boot.DEFAULT_SETTLE_SECONDS`,
  4 s). Readiness is the login prompt, and the harness stopped reading the serial
  log the instant it appeared, so the banner and the prompt the new checks need
  were never captured. The harness now keeps draining the log for the settle
  window before terminating QEMU; `--settle 0` restores the old behaviour.
  `boot_image(..., settle=...)` is the library knob and it is opt-in there, so a
  unit test that fakes QEMU does not pay for the window.

### Fixed
- **`network-target` no longer misses a console line that a kernel message
  split.** systemd writes a status line in pieces (prefix, colour sequence, unit
  name, terminator) and a kernel printk can land between them; that is what made
  this check flake once on 26.10.3 (Core, uefi-tpm). The pattern now allows one
  injected console line at each write boundary, with every gap bounded and
  written as `[\s\S]` (a bare `.` cannot cross a newline). Tests prove the three
  look-alikes that appear in a real Core boot - `Preparation for Network`, `Host
  and Network Name Lookups`, `Network is Online` - still do not satisfy it, and
  that removing the real line still fails the check.

## [0.3.1] - 2026-10-03

### Fixed
- `--tpm` gives the guest a TPM: the argv now carries
  `-device tpm-tis,tpmdev=tpm0`. Until now only the swtpm backend was attached
  (`-chardev` + `-tpmdev`), so the guest had no TPM device at all and the
  "uefi-tpm" smoke cases proved nothing about TPM wiring. Found 2026-10-03
  while reproducing a firmware-TPM boot stall.

## [0.3.0] - 2026-10-02

### Changed
- **The default smoke matrix is `smoke/core.toml`**, grounded in fresh Core
  26.10 serial logs (UEFI and UEFI+TPM) and proven against a real failed boot.
  It checks the GRUB handoff, systemd, networkd/resolved, `network.target`,
  chrony, sshd, `multi-user.target`, the `pengyos-core` login and no failed
  units. It replaces `smoke/core-m1.toml`, whose busybox image no longer exists.
- A matrix's relative `image` resolves against the build root
  (`$PENGYOS_BUILD_ROOT`, else `~/build/pengyos`) instead of the repo's old
  `dist/`. `smoke.repo_root()` is now `smoke.build_root()`.
- The opt-in integration tests find their images in
  `~/build/pengyos/output/pengyos-core/images/`.

### Added
- **`smoke/server.toml`**: the Server matrix (nftables, sshd, podman, backup,
  metrics and update timers, the Server target), grounded in real Server logs;
  a real Core boot fails exactly its Server-only checks. It replaces the
  `pengyos-server-smoke` / `pengyos-core-smoke-m2` / `pengyos-smoke-runner`
  tools.
- **`--hostfwd SPEC` (repeatable)** on `boot`/`probe`: forward host ports into
  the guest over QEMU user networking (`-nic user,hostfwd=…`). Lets a booted
  image that ships sshd be reached from the host (`--hostfwd tcp::2222-:22`,
  then `ssh -p 2222 root@127.0.0.1`) for live inspection, with no manual QEMU
  invocation. Omitted → byte-identical argv (QEMU's default NIC, no forwarding).

## [0.2.0] - 2026-09-14

### Added
- **`smoke` subcommand + declarative smoke matrix** (`pengyos_qemu_verify/smoke.py`,
  `smoke/core-m1.toml`). This is the machine-checkable form of the PengyOS Core
  acceptance gate's "passes the smoke matrix" criterion (SPEC §556): a TOML file
  declares the boot *variants* an image must survive plus the *checks* each must
  satisfy, and the runner boots each variant and evaluates its checks over the
  captured serial log. Reports: a combined JSON result and a JUnit XML with one
  `<testsuite>` per variant. Exit `0` only when every variant booted ready and
  every check passed.
  - Every check is **blocking** by design, a variant with no checks is a config
    error, and a boot that fails to return the declared checks fails its variant
    — so a matrix can never pass vacuously or be neutered into uselessness.
  - The shipped M1 matrix is **grounded in real captures**: every pattern is
    decided by two committed real M1 serial logs (a UEFI/GRUB2 boot of the dist
    image, all checks green; a bare-metal `-kernel` boot, which correctly fails
    exactly the GRUB2-handoff check). `tests/test_smoke.py` asserts that
    asymmetry, so weakening a check into a tautology fails the suite.
- **`--no-snapshot`** (a deliberate write-through escape hatch; see below).
- `BootResult.snapshot` and a `snapshot` key in the JSON report, so a result
  records whether the artifact was protected.

### Changed
- **Guest disks are now attached through a QEMU throwaway overlay
  (`snapshot=on`) by default** — a verification boot can no longer mutate the
  image it is verifying. This is a behaviour change to the rendered argv (the
  golden argv tests were updated deliberately): previously `-drive
  file=…,if=virtio,format=raw` let the guest re-mount the root r/w and write
  (journal, syslog, RNG seed), so **every verification run rewrote the
  artifact**: `dist/pengyos-m1-x86_64.img` went `fa4423cb…` → `29cf366f…` from
  our own boots, breaking the `sha256sum -c` that `dist/README.md` documents.
  The guest still sees a writable disk, so readiness/login behaviour is
  unchanged.

### Tests
- `tests/test_snapshot.py` — argv rendering for all three boot modes, the
  `boot_image` plumbing, and the CLI flag.
- `tests/test_integration.py::TestArtifactSafety` — opt-in (`PENGYOS_IT=1`)
  behavioural proof: hash a real image, boot a *copy* of it, hash again. The
  default boot must leave it byte-identical, and the `snapshot=False` control
  must change it (otherwise "unchanged" would prove nothing).
- `tests/test_smoke.py` — matrix validation, runner verdicts, report emission,
  CLI surface, and the real-log grounding/asymmetry checks.

## [0.1.11] - 2026-09-13

### Fixed
- **The pre-existing `PENGYOS_IT=1` `--tpm` boot timeout is diagnosed and
  confirmed fixed** (the last open item in `implementation.md`). Root cause:
  the QEMU↔swtpm *wiring protocol*, not TPM state or a slow guest. QEMU ≥ 5.2
  (here 10.2.1) treats the `-tpmdev emulator` chardev as the PTM *control*
  channel and blocks on the `CMD_SET_DATAFD` handshake at startup; the legacy
  `--server` (raw data channel) swtpm wiring never answers it, so QEMU
  deadlocks before it opens the serial file → empty log → timeout. The
  version-adaptive wiring (`tpm.resolve_tpm_protocol`, `"auto"` probes
  `qemu_bin --version` → QEMU ≥ 5.2 gets `--ctrl` / no `--server`) was already
  in-tree from the prior checkpoint; this closes the loop by re-verifying it
  against the real opt-in IT (`test_real_tpm_boot_provisions_and_reaches_login`
  now reaches `pengyos login:` in ~5 s under KVM) and repairing the unit guards
  that the same checkpoint had left stale (they were failing `pytest -q` at
  HEAD, breaking the green-suite invariant):
  - `test_tpm.py::test_build_swtpm_argv_control_golden` — pass `state_dir` by
    keyword (it was landing in the now-`ctrl_path` positional slot, which the
    control protocol rejects).
  - `test_boot.py::test_boot_tpm_provision_failure_is_preflight` — the
    sentinel fake qemu now answers `--version` (the read-only probe that TPM
    auto-protocol detection runs pre-flight) and only writes the sentinel on a
    real boot, so the sentinel truly means "a boot was attempted".
  - `test_boot.py::test_cli_boot_tpm_prints_tpmstate_line` — accept the
    protocol now carried by the `tpmstate:` line
    (`tpmstate: <dir> (<method>, <protocol>)`).
  - `test_boot.py::test_boot_tpm_bad_protocol_is_preflight_value_error` — fix
    the `FAKE_SWPTM_READY` → `FAKE_SWTPM_READY` typo (`NameError`).
- **spec.md** now documents the version-adaptive QEMU↔swtpm wiring contract
  (control vs legacy) instead of describing only the legacy `--server` shape.

### Verified
- Full unit suite: `297 passed, 5 skipped` (`pytest -q`). Full opt-in
  `PENGYOS_IT=1` integration suite: `19 passed` (QEMU 10.2.1, swtpm 0.10.1,
  no `swtpm_setup`, KVM on) — including the `--tpm` real-boot test
  (method `fixture`, protocol `control`) and the PTM control-channel
  handshake oracle.

## [0.1.10] - 2026-09-13

### Added
- The assertion runner is now **wired into the boot flow** (implementation.md
  wiring item). `boot_image(..., assertions=None)` runs
  `assertions.default_assertions()` (or a caller-supplied set — an empty list
  runs no checks) over the captured serial log once the boot is over, and
  attaches the `CheckResult`s to the new `BootResult.checks` field (mirrored
  in `to_dict()`), with `BootResult.checks_passed` as the vacuously-true
  roll-up. Checks run over *whatever was captured*: a ready boot gets the
  full log, a timeout the partial log, a QEMU launch failure the empty log.
  A malformed check or an uncompilable pattern is validated **pre-flight**
  (`ValueError` before the workdir is made and QEMU launches), so the CLI's
  "always writes JSON" path covers it with exit `1`.
- The `boot` CLI prints one `check:    <name> PASS|FAIL — <evidence>` line per
  check and now exits `0` only when the boot is ready **and** every check
  passed (`reports.overall_result(result)`); a ready boot whose log contains
  `Kernel panic` exits `1`.

### Changed
- The report emitters (`overall_result` / `result_to_dict` / `to_json` /
  `junit_xml` / `write_json` / `write_junit`) now take `checks=None` meaning
  "use the checks attached to the boot" (`BootResult.checks`); an explicit
  iterable (even an empty one) overrides, preserving the pre-wiring call
  style. Pre-flight error results carry an empty check list.

### Notes
- The default set is **orthogonal to `--expect`**: readiness markers decide
  when the boot is over, the checks decide whether it was a *good* boot — a
  custom `--expect` boot that never prints `login:` is "ready" but still
  fails the default `reached-login` check (callers wanting a different verdict
  set use `boot_image(assertions=...)`; no CLI flag is invented). The
  `PENGYOS_IT=1` boot test now asserts the attached checks equal a fresh
  `run_assertions()` over the same captured log (a real-boot oracle) and
  feeds the emitters with `checks=None`.

### Tests
- New wiring coverage in `tests/test_boot.py` (default set on a ready boot,
  failing checks on a panic log, caller-supplied / empty sets, pre-flight
  validation with a launch-sentinel proving QEMU never starts, checks over an
  empty log on launch failure) and `tests/test_reports.py` (`checks=None`
  fallback + explicit override at every emitter entry point; a new
  fake-QEMU "login + Kernel panic" case proving a ready-but-check-failed boot
  exits `1` with `result: fail` and a `<failure>` carrying the evidence
  line). Existing golden tests updated where the wired-in behavior changed
  the reports (`checks` is populated; timeout reports now carry the failed
  `reached-login` check; a custom `--expect` boot without `login:` exits `1`).

## [0.1.9] - 2026-09-13

### Added
- TPM state provisioning for `--tpm` boots (implementation.md follow-up).
  `tpm_state.provision_tpm_state` seeds the swtpm `--tpmstate` dir before the
  emulator starts, so a guest that exercises TPM2 commands never hits an
  uninitialised emulator. Policy (first match wins): keep **existing** state
  (a reused TPM is what repeated boots of one image want), else run
  **`swtpm_setup --tpm2 --tpmstate dir=<dir>`** when available, else seed from
  the committed state **fixture** (`tests/fixtures/tpmstate/`, a real
  swtpm-generated `tpm2-00.permall` + `GENERATE.py`), so `--tpm` works even on
  a host with no `swtpm_setup` installed. `boot_image` records the outcome on
  `BootResult.tpm_state` / `tpm_state_method`.
  - Tests: `tests/test_tpm_state.py` (pure `swtpm_setup` argv, discovery,
    policy, and the committed fixture itself) and boot-flow integration cases in
    `tests/test_boot.py` (fixture / swtpm_setup / existing / pre-flight failure).

### Fixed
- The `boot` CLI now catches `tpm_state.TpmStateError` (an unprovisionable TPM
  state) as a pre-flight failure, like `ImageNotFound`/`OvmfNotFound`/
  `SwtpmError`: it writes the requested `--json`/`--junit` reports (a minimal
  error result) and exits `1` instead of raising an unhandled traceback —
  preserving the "always writes JSON" acceptance criterion. Covered by
  `test_cli_boot_tpm_state_preflight_failure_writes_json`.

## [0.1.8] - 2026-09-13

### Added
- `test_github_ready` (implementation.md item 10) in
  `tests/test_github_ready.py` — asserts the *GitHub-ready contract* as a
  standing invariant: `README.md`, `LICENSE` (MIT, with the exact
  `Copyright (c) 2026 Pat Wendorf` line), `CHANGELOG.md` (Keep-a-Changelog
  structure) and `.gitignore` all exist at the repo root. The "full green
  suite" half of the contract is the run's own acceptance condition and is
  not self-asserted.

## [0.1.7] - 2026-09-13

### Added
- Opt-in integration tests (implementation.md item 9) in
  `tests/test_integration.py` — the one place a *real* QEMU boot is needed,
  strictly gated behind `PENGYOS_IT=1`:
  - **Candidate discovery** (pure; `home`/`env`/`ovmf_present` injectable) finds
    "a tiny prebuilt Alpine/hello image if present" — here: whatever prebuilt
    PengyOS image the build pipeline left on the host that boots to `login:`
    (no downloading or building at test time; tests stay network-free). In
    priority order: `PENGYOS_IT_IMAGE` (optional `PENGYOS_IT_KERNEL` /
    `PENGYOS_IT_UEFI=1`), the build outputs
    `~/build/pengyos/output/images/{bzImage,rootfs.ext4}` (bare-metal), and the
    dist image `~/Personal/pengyos/dist/pengyos-m1-x86_64.img` (UEFI, when OVMF
    is locatable).
  - **Per-candidate boot test:** real `boot_image()` with a `PENGYOS_IT_TIMEOUT`
    budget (default 180 s) → asserts readiness → runs
    `assertions.default_assertions()` over the captured serial log → writes the
    JSON + JUnit reports and parses them back (`result == "pass"`, ordered
    checks, `tests = 1 + len(checks)`, zero `<failure>` nodes).
  - **Never-empty parametrisation:** with no image present the test collects
    once under `no-candidate-image` and skips with an actionable message, so
    `PENGYOS_IT=1 pytest -q` is green on a bare host.
  - The gate is a *class-level* `skipif` so the module's discovery/timeout unit
    tests run in every `pytest -q`; 13 new unit tests cover the gate, timeout
    fallbacks, every discovery rule (priority, incomplete pairs, missing
    firmware), parametrisation, and the opt-in marker itself.
  - Validated on the build host: both candidates boot under KVM and pass (M1
    UEFI disk boot ≈ 11 s, bare-metal ≈ 4 s); the full suite is green with and
    without `PENGYOS_IT=1`.
  - Contract recorded in `spec.md` ("Integration-test contract FINALISED,
    item 9").

## [0.1.6] - 2026-09-13

### Added
- A `--dry-run` flag on `boot` (and the previously-scheduled `probe` subcommand
  is now real) — implementation.md item 8. Both share a single pure render
  path, `cli.render_argv`, so `pengyos-qemu-verify probe <image>` and
  `pengyos-qemu-verify boot <image> --dry-run` print **byte-for-byte the same**
  resolved `qemu-system-x86_64` argv.
  - The argv is printed as a shell-pasteable command: a leading `#` comment
    line, then the argv joined with `shlex.join` (space-containing args like
    the `-append` string are quoted so the line is copy-paste runnable).
  - **No side effects**: no QEMU is launched, no per-boot workdir is created,
    no serial-log file is written, no OVMF VARS copy is made, and no swtpm is
    started. The only I/O is the read-only OVMF location (an `os.path` probe),
    so a *pending* (not-yet-built) image/firmware renders too. A `--tpm` render
    uses the placeholder datapath `path=tpm.sock` (there is no running swtpm in
    a dry-run).
  - `--uefi` and `--kernel` together are still a clean `exit 1` (`could not
    resolve argv`) rather than a crash.
  - `tests/test_dry_run.py` (14 tests): the printed argv matches the golden
    `build_argv` (disk / bare-metal kernel boot), `probe` ≡ `boot --dry-run`,
    both UEFI firmware shapes render (split → two pflash drives via a temp
    CODE+VARS pair, combined → `-bios`), the missing-firmware fallback renders
    a representative path instead of erroring, `--tpm` renders the placeholder
    socket, the mutual-exclusion conflict fails cleanly, and — the crux — a
    sentinel-writing fake `qemu` on `PATH` is proven **not** to run (no
    sentinel, no `pengyos-serial.log`, no workdir). No real QEMU, network,
    root, display, or hardware.

## [0.1.5] - 2026-09-13

### Added
- JUnit XML + JSON result emitters (implementation.md item 7) in
  `pengyos_qemu_verify/reports.py`, a **pure** emitter layer over
  `BootResult` (item 5) + `CheckResult`s (item 6):
  - JSON: the spec's `{image, accel, duration, checks:[{name, status,
    evidence}], result}` shape as a superset (extra `returncode`, `ready`,
    `timed_out`, `markers`, `error`, `serial_log`). Overall `result` is
    `"pass"` only when the boot reached readiness **and** every check
    passed; timeout, launch failure, or any failed check is `"fail"`.
  - JUnit XML: `<testsuites>/<testsuite>` (suite carries `image`/`accel`/
    `result`/counts/`time`), a synthetic `boot-reached-readiness` testcase
    first, then one `<testcase>` per check; failures carry a `<failure>` with
    a short `message` and the evidence line as text (an explanatory
    placeholder when a failing check has no match line). Built with
    `xml.etree.ElementTree` — always well-formed and re-parseable.
  - `write_json` / `write_junit` create parent dirs; `make_error_result` is
    the minimal error `BootResult` for pre-flight failures.
- The `boot` CLI subcommand now honours `--json OUT` / `--junit OUT`
  (item 7): reports are written **even on failure** — including pre-flight
  errors such as a missing image ("always writes JSON") — and each written
  path is printed as `report:   <path>`. A report that cannot be written is
  reported on stderr without changing the boot's exit code. The exit code is
  `0` only when the boot is ready and all checks pass (with the empty check
  list until item 6 is wired in: readiness).
- `tests/test_reports.py` (34 tests): JSON spec-shape/round-trip, verdict
  matrix (ready+pass / failed check / timeout / launch error / vacuous), JUnit
  well-formedness (parsed back), synthetic-boot testcase, per-check failures
  with evidence text, XML escaping of hostile names/evidence, file writers,
  `make_error_result`, and CLI end-to-end against a fake `qemu-system-x86_64`
  on `PATH` (success → pass report, hanging guest → timeout report, missing
  image → both reports still written). No real QEMU, network, root, display,
  or hardware.
- Assertion runner (implementation.md item 6) in
  `pengyos_qemu_verify/assertions.py` — a **pure** module that turns the
  captured serial-console log into a set of PASS/FAIL checks with evidence:
  - `Assertion` (name, pattern, kind) with `.present()` / `.absent()`
    factories; kind is `present` (pattern must appear) or `absent` (pattern
    must NOT appear). Invalid name/pattern/kind raise `ValueError`.
  - `CheckResult` (name, status `passed`/`failed`, evidence) with a
    `.passed` property and `to_dict()` shaped for the item-7 emitters
    (`{name, status, evidence}`).
  - `run_assertions(log, checks)`: one `CheckResult` per check, in input order.
    Evidence is the *line* containing the first match (stripped, truncated to
    512 chars); a check with no match to point at (a passing `absent`, a
    failing `present`) carries `evidence = None`. `re.error` propagates for a
    non-compiling pattern.
  - `default_assertions()`: the built-in set — `present` "reached `login:`" +
    `absent` "no `Kernel panic`" ("it booted to a login without panicking");
    `all_passed(results)`: the roll-up (vacuously True for an empty set).
  - **The SSH half of the original task is dropped** (recorded in `spec.md`):
    there is no `--ssh` flag in the documented interface and the M1 console is
    serial-only, so the runner operates entirely over the captured serial log.
- `tests/test_assertions.py`: present/absent pass+fail, evidence capture and
  truncation, first-match-line selection, `Assertion` validation, `re.error`,
  empty-list/order/`all_passed`, and `default_assertions()` against the real M1
  login and kernel-panic fixtures. No QEMU, network, root, display, or hardware.
- Follow-up item added: wire `run_assertions` into the boot flow so the
  item-7 emitters can report the checks (contract recorded in `spec.md`).

## [0.1.4] - 2026-09-13

### Added
- Headless boot engine (implementation.md item 5) in
  `pengyos_qemu_verify/boot.py`:
  - `boot_image()`: validates the image exists (existence is a boot concern;
    `build_argv` stays pure), prepares a workdir (default a fresh `mkdtemp`,
    **kept** after the boot because it holds the serial-log evidence), copies
    a located split-layout OVMF VARS into a writable temp copy (the item-3
    contract the argv layer deferred here), optionally starts the item-4
    `Swtpm` and attaches its datapath socket, launches QEMU via
    `build_argv`, then polls the `-serial file:` log incrementally until
    readiness, guest exit, or the deadline.
  - Readiness: `detect_readiness(log, patterns)` — the `--expect` regex list
    (default `('login:',)`); the boot is ready when **all** markers have been
    seen; each marker carries its containing log line as evidence (truncated
    to 512 chars). `read_log_incremental()` streams the serial file (missing
    file is fine; UTF-8 decoded with replacement).
  - Timeout safety: the harness never hangs past `--timeout` — QEMU is
    terminated on the deadline (SIGTERM → SIGKILL on a grace timeout), with
    `timed_out=True` and the final exit code captured. On readiness QEMU is
    terminated the same way (the guest sits at a login prompt; item 6 will
    drive/assert on the captured log). swtpm is always torn down.
  - `BootResult` (argv, `kvm`/`accel`, `ready`, `timed_out`, `returncode`,
    `duration`, `serial_log`, per-marker evidence, `error`, `status` =
    `ready`/`timeout`/`failed`, `to_dict()` shaped for the item-7 emitters).
    Launch-phase problems (missing QEMU binary) are returned as
    `BootResult.error`, not raised, so a report can always be emitted.
- The `boot` CLI subcommand is now wired (was `not implemented yet`):
  runs `boot_image`, prints a readiness summary, and returns the new exit
  code `1` when readiness was not reached (0 ready, 2 usage, 3
  not-implemented unchanged). `probe` remains not-implemented (item 8).
- `tests/test_boot.py` (24 tests) + two captured serial-log fixtures
  (`tests/fixtures/serial_login_sample.log` ending at `pengyos login:`,
  `serial_kernel_panic_sample.log`). Drives the real boot path against a fake
  `qemu-system-x86_64` script that parses its own `-serial file:` argv and
  streams behaviour-specific lines (login / custom / panic / hang / die): the
  spec's "fake qemu that sleeps forever" case yields `timeout` without
  hanging; early death captures the exit code; missing QEMU is a result, not
  a crash; UEFI split VARS copy-out and swtpm attach/teardown are verified
  end-to-end. No real QEMU, network, root, or display.
- `tests/test_scaffold.py`: the "boot not implemented" scaffold test is
  replaced by a clean-failure test for a missing image (exit 1).

## [0.1.3] - 2026-09-12

### Added
- Optional swtpm TPM2 support (implementation.md item 4), split into a pure
  argv layer and a process-lifecycle layer so neither needs a real `swtpm`.
  - `argv.tpm_qemu_args()` + `build_argv(..., tpm_socket=, tpm_chardev_id=,
    tpm_dev_id=)`: when a running swtpm unix-socket datapath is supplied, the
    guest is handed an emulated TPM2 via `-chardev
    socket,id=<CHR>,path=<SOCK>` + `-tpmdev emulator,id=<DEV>,chardev=<CHR>`
    (ids default `tpmchr` / `tpm0`), emitted after the disk/firmware section and
    before the headless tail. Omitting `tpm_socket` leaves the argv
    byte-for-byte identical to the pre-TPM golden baseline.
  - New `pengyos_qemu_verify/tpm.py`: `build_swtpm_argv()` (pure `swtpm
    socket --tpm2 --server type=unixio,path=…` builder, with optional
    `--ctrl`/`--tpmstate`/`--pid`) and the `Swtpm` context manager — `start()`
    spawns the emulator and blocks until its datapath socket appears (raising
    `SwtpmStartError` on early death or timeout), `stop()`/`__exit__` terminate
    (SIGTERM → SIGKILL on grace timeout) and clean up the socket/ctrl/pid
    files. `swtpm_bin` is injectable; `find_swtpm_bin()` locates a real binary
    without raising.
  - Contract recorded in `spec.md` (QEMU-side tokens + placement, swtpm
    command line, readiness/teardown semantics).
- `tests/test_tpm.py`: golden strings for both argv builders, composition of
  the TPM column onto SeaBIOS/bare-metal/UEFI boots, a byte-identical
  no-TPM baseline regression guard, and the full `Swtpm` lifecycle (start/ready/
  stop/context manager/double-start/missing-binary/early-exit/timeout) driven
  against small fake swtpm executables that open the requested socket. No real
  `swtpm`, network, root, or display required.
- Follow-up item added: opt-in TPM *state provisioning* for guests that
  actually exercise TPM2 commands (the datapath socket comes up without it,
  which was validated on the build host).

## [0.1.2] - 2026-09-12

### Added
- UEFI/OVMF support in `pengyos_qemu_verify/argv.py` (implementation.md item 3).
  - `locate_ovmf()`: resolves the firmware from an explicit path, then the
    `PENGYOS_OVMF` / `OVMF` env vars, then the canonical on-disk candidates
    (`/usr/share/ovmf/…`, `/usr/share/OVMF/…`, `/usr/share/edk2/x64/…`,
    `/usr/share/qemu/OVMF.fd`). Raises `OvmfNotFound` (with the attempted
    paths) when nothing is found. Auto-pairs a split `OVMF_CODE*.fd` with its
    `_CODE`->`_VARS` sibling; a file with no sibling is treated as a combined
    blob.
  - `OvmfFirmware` value object (split `code`+`vars` vs combined single blob).
  - `build_argv(..., uefi=, ovmf=, ovmf_vars=)`: emits the firmware section
    *before* the boot disk — two pflash drives for split firmware
    (`unit=0,readonly=on` CODE, `unit=1` VARS) or `-bios` for a combined blob —
    and refuses to mix `--uefi` with `--kernel` (mutually exclusive: under OVMF
    the kernel lives on the image's ESP/GRUB2). The item-2 golden baseline is
    unchanged.
  - Contract recorded in `spec.md` (firmware locations, split vs combined
    wiring, writable-VARS requirement, kernel exclusion).
- `tests/test_ovmf.py`: locator resolution order, split/combined detection,
    golden UEFI argvs (KVM/no-KVM × split/combined), `ovmf_vars` override,
    kernel/UEFI exclusivity, and a regression guard on the item-2 baseline.
    No real firmware, QEMU, or network required (fixtures under `tmp_path`).

## [0.1.1] - 2026-09-12

### Added
- `pengyos_qemu_verify/argv.py`: pure QEMU argv construction (implementation.md
  item 2). `build_argv()` renders the golden q35 command line —
  `-enable-kvm -cpu host` when `/dev/kvm` is readable+writable else
  `-cpu max`, bare-metal `-kernel`/`-append` boot vs firmware/disk boot
  (no kernel), the virtio `-drive`, and the headless tail
  `-display none -serial file:LOG -monitor none -no-reboot`. `kvm_available()`
  probes the device; the accelerator can be pinned for deterministic tests/CI.
- Finalised the `-append` golden contract: `root=/dev/vda rw console=ttyS0`
  (includes `rw`, per the known-good reference). Recorded in `spec.md`.
- `tests/test_argv.py`: golden-string matrix (KVM/no-KVM × kernel/no-kernel),
  KVM-probe tests against temp files, and input-validation tests.

## [0.1.0] - 2026-09-12

### Added
- Project scaffold: `pyproject.toml` (PEP 621, MIT, `setuptools` build backend),
  the `pengyos_qemu_verify` package, and the `pengyos-qemu-verify` console script.
- CLI argument surface: `--version`, plus `boot` and `probe` subcommands with the
  documented flags (`--kernel`, `--uefi`, `--tpm`, `--ram`, `--timeout`,
  `--expect`, `--json`, `--junit`). The argument parsing is real and tested; the
  subcommand *logic* (argv construction, boot/readiness, assertions, emitters)
  is tracked as its own subtasks in `implementation.md` and currently returns a
  clear `not implemented yet` message with a distinct exit code (`3`).
- `python -m pengyos_qemu_verify` entry point alongside the console script.
- `README.md`, `CHANGELOG.md`, `LICENSE` (MIT, Copyright (c) 2026 Pat Wendorf),
  and `.gitignore`.
- Scaffold test suite (`tests/test_scaffold.py`) covering package metadata,
  entry-point behaviour, the CLI argument surface, and the declared entry point.
