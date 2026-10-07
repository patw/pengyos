# PengyOS qemu-verify — headless boot-test harness

## Summary
Boot a PengyOS image headless under QEMU/KVM and assert it reaches a login/console; emit JUnit XML + JSON.

## Purpose
Make 'it boots' an automated, machine-checkable fact (SPEC §7 verification).

## Deliverable
A Python CLI `pengyos-qemu-verify` that boots a disk image under QEMU (q35), waits for readiness markers over the serial console, runs a set of assertions, and emits JUnit XML + a JSON result.

## Interface
- pengyos-qemu-verify boot <image> [--kernel bzImage] [--uefi] [--tpm] [--ram 512] [--timeout 120] [--expect REGEX]... [--json OUT] [--junit OUT] [--no-snapshot] [--hostfwd SPEC]... [--ssh] [--ssh-command COMMAND]... [--ssh-key IDENTITY] [--ssh-port PORT] [--ssh-user USER]
- pengyos-qemu-verify probe --help-free: prints the resolved QEMU argv for inspection
- pengyos-qemu-verify smoke [--matrix PATH] [--image IMAGE] [--variant ID]... [--json OUT] [--junit OUT] [--dry-run]:
  boots every variant declared in a smoke matrix (default `smoke/core.toml`) and reports one
  verdict — the machine-checkable form of the Core acceptance gate's "passes the smoke matrix".

## Verification (tests are first-class)
- argv construction for KVM vs no-KVM, with/without --uefi and --tpm (golden strings).
- **artifact safety:** the guest disk is attached `snapshot=on` by default so a
  verification boot cannot mutate the image (goldens updated accordingly;
  `--no-snapshot` is the write-through escape hatch). Opt-in behavioural proof in
  `test_integration.py::TestArtifactSafety` hashes a real image around a real boot.
- **smoke matrix:** validation (a variant no check covers is a config error),
  runner verdicts, JSON + JUnit emission, and the shipped matrix grounded in
  real Core captures (UEFI and UEFI+TPM all-green; a bare `-kernel` boot that
  cannot find its root fails exactly the facts that did not happen).
- readiness detection against a captured sample serial log fixture (login appears / does not).
- assertion runner: passing and failing regex checks produce correct statuses + evidence.
- JUnit XML is well-formed (parse it back) and JSON schema matches.
- timeout path: a fake 'qemu' that sleeps forever yields FAIL with reason=timeout, not a crash.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- `pengyos-qemu-verify boot` never hangs past --timeout; always writes JSON.
- No real QEMU needed for the unit suite (uses a fake qemu script + fixtures).
- `pytest -q` green; test_github_ready green.

## Non-goals
- Full CI orchestration
- Real hardware testing

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.

## Implementation notes (discovered from the real PengyOS image — 2026-09-12)

These record the *real* behaviour of PengyOS images that the argv
construction, boot, and integration code must honour. They refine, and
do not change, the interface contract above.

- **The default PengyOS image is a UEFI/GRUB2 *disk image*, not a bare kernel.**
  Each edition ships `pengyos-<edition>-x86_64.img` = GPT + EFI System
  Partition (GRUB2 + bzImage) + ext4 root. The primary boot path is therefore **firmware disk boot**
  (`-drive` the `.img` under OVMF). `--kernel` is the *alternative* bare-metal
  path. When no `--kernel` is supplied, the harness should expect disk/firmware
  boot (for PengyOS, that means `--uefi` as well) rather than assuming `-kernel`.
- **Readiness is read from the serial console.** The kernel and getty run on
  `ttyS0` as well as `tty0` (its grub.cfg adds `console=ttyS0` when GRUB finds
  a real UART, as QEMU's is); readiness detection MUST read the `-serial` log
  file, never the display.
- **Readiness marker.** The guest ends at `pengyos login:` (getty on ttyS0 and
  tty1). The default `--expect login:` matches as a substring of that — keep it,
  but note the full marker is `pengyos login:`.
- **Reference KVM bare-metal argv** (known-good baseline):
  `qemu-system-x86_64 -M q35 -m 512 -smp 2 -enable-kvm -cpu host -kernel bzImage
  -drive file=rootfs.ext4,if=virtio,format=raw -append 'root=/dev/vda rw
  console=ttyS0' -display none -serial file:LOG -monitor none`.
  Note the reference uses `root=/dev/vda rw` (with `rw`); the exact `-append`
  string is a fixed contract, **finalised in item 2** (see below).
- **Reference image locations** for the `PENGYOS_IT=1` integration test: the
  Core build outputs at `~/build/pengyos/output/pengyos-core/images/`
  (`bzImage`, `rootfs.ext4`, `pengyos-core-x86_64.img`).
- **`-append` contract FINALISED (item 2, 2026-09-12):** the bare-metal
  kernel command line is exactly `root=/dev/vda rw console=ttyS0` — with `rw`,
  matching the known-good reference (the older sketch above omitted `rw`; a
  read-only rootfs would not yield a usable login prompt). Fixed golden
  contract.
- **argv order contract (golden, item 2):**
  `qemu-system-x86_64 -M q35 -m <RAM> -smp 2 <ACCEL> <DISK SECTION>
  [<TPM SECTION>] -display none -serial file:<LOG> -monitor none -no-reboot`, where
  `<ACCEL>` is `-enable-kvm -cpu host` when `/dev/kvm` is readable **and**
  writable, else `-cpu max`; `<DISK SECTION>` is
  `-kernel <BZ> -drive file=<IMAGE>,if=virtio,format=raw -append 'root=/dev/vda rw console=ttyS0'`
  when a kernel is given, else just
  `-drive file=<IMAGE>,if=virtio,format=raw` (firmware/disk boot). The UEFI and
  TPM columns of the argv matrix extend this baseline in items 3–4 without
  changing it.
- **UEFI/OVMF wiring contract FINALISED (item 3, 2026-09-12):** when `--uefi`
  is set, the firmware is attached *before* the boot disk. Two wirings are
  emitted, chosen by the located firmware's shape (`OvmfFirmware`):
  - **split** (a CODE file *and* a distinct VARS file): two pflash drives,
    `-drive if=pflash,format=raw,unit=0,readonly=on,file=<CODE>` then
    `-drive if=pflash,format=raw,unit=1,file=<VARS>`.
  - **combined** (a single CODE+VARS blob, e.g. the 4 MiB `OVMF.fd` shipped by
    Debian/Ubuntu): `-bios <OVMF.fd>`. A combined image is wired with `-bios`
    (never a lone pflash unit, which would hit the VARS-writability problem).
  - The VARS pflash must be a **writable** file at boot time (QEMU persists
    NVRAM there across reboots); the boot subtask (item 5) copies the located
    VARS to a temp path and passes it as `ovmf_vars`. `build_argv` stays pure
    and just renders whatever path it is given.
  - **Firmware location** (`locate_ovmf`): an explicit `--ovmf`/`ovmf` path
    wins, then the `PENGYOS_OVMF` / `OVMF` env vars, then the canonical
    candidates `/usr/share/ovmf/OVMF_CODE.fd`, `/usr/share/OVMF/OVMF_CODE.fd`,
    `/usr/share/ovmf/OVMF.fd`, `/usr/share/OVMF/OVMF.fd`,
    `/usr/share/OVMF/OVMF_CODE_4M.fd`, `/usr/share/edk2/x64/OVMF_CODE.fd`,
    `/usr/share/edk2/x64/OVMF.fd`, `/usr/share/qemu/OVMF.fd`. A split CODE file
    is auto-paired with its `_CODE`->`_VARS` sibling; a file with no sibling is
    treated as combined.
  - **Kernel/initrd handling under UEFI:** `--uefi` and `--kernel` are
    **mutually exclusive** (a `ValueError`). Under OVMF the kernel lives on the
    image's own ESP/GRUB2, so the bare-metal `-kernel`/`-append` path is never
    emitted in UEFI mode; the image is the virtio boot disk and that is all.
- **TPM2 / swtpm wiring contract FINALISED (item 4, 2026-09-12):** `--tpm`
  is an *optional* measured-boot add-on. It is a two-part concern split so each
  half is testable without a real `swtpm`:
  - **QEMU side** (`argv.build_argv(..., tpm_socket=)` / `argv.tpm_qemu_args`
    ): two tokens, emitted *after* the disk/firmware section and *before* the
    headless tail, composing on top of every boot mode (KVM/TCG, SeaBIOS/UEFI,
    bare-metal/firmware-disk):
    `-chardev socket,id=<CHR>,path=<SOCK>` then
    `-tpmdev emulator,id=<DEV>,chardev=<CHR>` (ids default `tpmchr` / `tpm0`,
    overridable). The `<SOCK>` is the *running* swtpm unix-socket datapath. When
    no `tpm_socket` is given the argv is byte-for-byte identical to the pre-TPM
    golden baseline, so the TPM column never perturbs it.
  - **swtpm side** (`tpm.Swtpm` + `tpm.build_swtpm_argv`): a context-managed
    `swtpm socket --tpm2` process (`--tpmstate dir=`, `--pid file=`). `start()`
    spawns it and blocks until the datapath socket exists (readiness) — raising
    `SwtpmStartError` if swtpm dies early or never opens the socket within the
    timeout; `stop()`/`__exit__` SIGTERMs (then SIGKILLs on a grace timeout) and
    removes the socket/ctrl/pid files. `swtpm_bin` is injectable, so the unit
    suite drives the real code path against a fake swtpm that just opens the
    requested socket. The boot subtask (item 5) owns the *integration*: create a
    workdir, `Swtpm(workdir).start()` → pass `tpm_socket=` into `build_argv` →
    launch QEMU → stop swtpm.
  - **Version-adaptive QEMU↔swtpm wiring (correction, 2026-09-13):** the
    meaning of the QEMU-facing `<SOCK>` is *not* constant across QEMU versions
    and wiring it wrong **deadlocks the guest at QEMU startup** (QEMU blocks on
    the TPM handshake before it ever opens the serial file → the harness times
    out with an empty log; this was the real cause of the pre-existing
    `PENGYOS_IT` `--tpm` boot failure, NOT TPM-state/measured-boot as first
    suspected). `tpm.resolve_tpm_protocol` (default `"auto"` probes the resolved
    `qemu_bin`'s `--version`) picks one of two wirings, both of which render the
    *same* QEMU `-chardev`/`-tpmdev` tokens (only the socket's meaning changes):
    - **`control` (QEMU ≥ 5.2; the default for any unknown/modern version):**
      the `<SOCK>` is the PTM *control* channel (QEMU `backends/tpm/tpm_emulator.c`
      protocol, `tpm_ioctl.h`). swtpm is started `--ctrl type=unixio,path=<SOCK>`
      with **no** `--server`; QEMU passes its data-channel fd to swtpm via
      `CMD_SET_DATAFD` (an `SCM_RIGHTS` ancillary message), then issues
      `CMD_GET_CAPABILITY`/`CMD_INIT`. A separate `ctrl_path` under this
      protocol is meaningless and rejected.
    - **`legacy` (QEMU < 5.2):** the `<SOCK>` is the raw TPM *data* channel —
      swtpm `--server type=unixio,path=<SOCK>` (optional separate `--ctrl`).
    `boot_image` records the resolved choice on `BootResult.tpm_protocol`
    (`control`/`legacy`, `""` when `--tpm` not used); the CLI's `tpmstate:` line
    shows it as `tpmstate: <dir> (<method>, <protocol>)`. An explicit
    `"control"`/`"legacy"` overrides the probe; any other value is a pre-flight
    `ValueError` (the "always writes JSON" path).
  - **Note:** `swtpm_setup` (TPM state provisioning) is *not* required for the
    datapath socket to come up (validated on the build host); provisioning
    (`--tpmstate` pre-seed / `swtpm_setup`) is a follow-up only if the guest
    actually exercises TPM2 commands.
- **TPM state provisioning contract FINALISED (2026-09-13):** with `--tpm`,
  `boot.py` seeds the `Swtpm` state dir (`<workdir>/tpmstate`, exactly the dir
  swtpm is given via `--tpmstate dir=`) *before* starting swtpm, via
  `tpm_state.provision_tpm_state` (first match wins):
  1. **existing** — the dir already holds a state file (a `tpm2-*` / `tpm-*`
     blob; a runtime `.lock` or `TMP*` temp never counts): keep it untouched
     (re-running `swtpm_setup` over a used state fails, and a reused state is
     what repeated boots of the same image want).
  2. **swtpm_setup** — a `swtpm_setup` binary is available (explicit
     `swtpm_setup_bin`, else auto-detected on `PATH`): run `swtpm_setup --tpm2
     --tpmstate dir=<dir>`; a failure or a run that writes no state falls
     through to the fixture instead of erroring.
  3. **fixture** — copy the committed state fixture's state files into the dir
     (the fixture itself is never mutated or locked). The committed fixture
     (`tests/fixtures/tpmstate/`, a real swtpm-generated `tpm2-00.permall`
     plus `GENERATE.py` for regeneration) is the default fallback, so `--tpm`
     works on a host with *no* `swtpm_setup` installed.
  No source yields a state → `tpm_state.TpmStateError`, raised *pre-flight*
  (before swtpm starts and before QEMU launches) — the CLI reports it through
  the "always writes JSON" pre-flight path (exit `1`), not a traceback.
  `boot_image` records the outcome on `BootResult.tpm_state` (dir) and
  `tpm_state_method` (`existing`/`swtpm_setup`/`fixture`), both in `to_dict()`.
- **Boot execution contract FINALISED (item 5, 2026-09-13):** `boot.py` owns
  everything `build_argv` deliberately did not (I/O, processes, clocks):
  - **Readiness semantics:** the boot is ready when **all** `--expect` regexes
    have matched anywhere in the accumulated serial log (default single
    marker `login:` — equivalent to the familiar "I see `login:`"). Each
    marker's evidence is the log line containing its first match (truncated
    to 512 chars). Patterns are plain `re` searches (no MULTILINE), so
    substring-style markers survive console CR/interleaving noise.
  - **Timeout is absolute:** the harness never blocks past `--timeout` (wall
    deadline, not "time after the first byte"). On the deadline QEMU is
    terminated (SIGTERM → SIGKILL after a grace timeout) and the result
    carries `timed_out=True` + the captured exit code — the spec's
    "fake qemu that sleeps forever yields FAIL with reason=timeout" case.
    On *readiness* QEMU is terminated the same way: the guest sits at a
    login prompt, which the harness does not drive (item 6 asserts over the
    captured log).
  - **The workdir is kept, not cleaned.** Default is a fresh `mkdtemp`
    (`pengyos-verify-*`); it holds the serial log, the writable OVMF VARS
    copy (split firmware only), and swtpm state — all of it is evidence for
    item 6's assertions and item 7's reports, and for humans. Only *managed*
    swtpm files (socket/ctrl/pid) are removed on teardown.
  - **Error policy:** pre-flight config errors *raise* (`ImageNotFound`,
    `ValueError` for bad inputs, `OvmfNotFound`, `SwtpmError`); problems
    observed *at launch* (QEMU binary missing / spawn OSError) are returned
    as `BootResult.error` (status `failed`), never raised — the emitters
    (item 7) must always have a result to write ("always writes JSON").
    `BootResult.status` ∈ `ready` | `timeout` | `failed`; `to_dict()` keys
    are the superset of the spec's JSON shape `{image, accel, duration,
    checks, result}` (markers carry name/status/evidence for item 6/7).
  - **Exit-code contract extended:** `boot` now returns `0` on readiness and
    a new `1` when it ran but did not reach readiness (timeout, QEMU
    failure, or a pre-flight error). `2` (usage) and `3` (not implemented —
    still `probe`) are unchanged.
- **Assertion-runner contract FINALISED (item 6, 2026-09-13):**
  `assertions.py` is a *pure* runner — it takes the captured serial-console log
  string and a list of checks and returns per-check results; no I/O, QEMU,
  network, root, or hardware.
  - **Check kinds:** each check is a plain-`re` pattern with a kind.
    *`present*` — the pattern must appear somewhere in the log (the "I see
    `login:`" assertion); PASS on a match. *`absent*` — the pattern must NOT
    appear (the "no `Kernel panic`" assertion); PASS when there is no match.
    A check's evidence is the *line* containing the first match (stripped,
    truncated to 512 chars); a check with no match to point at (a passing
    `absent`, or a failing `present`) carries `evidence = None`.
  - **SSH half dropped (resolves the 2026-09-13 flag):** the original task text
    said "optional SSH commands when `--ssh` given", but **no `--ssh` flag is
    part of this interface**. Rather than invent an undocumented flag (host/port/target) the
    interface does not declare, the runner operates **entirely over the
    captured serial log** — present/absent log-regex checks only. If a future
    image exposes a real, documented out-of-band channel, a `--ssh` (or
    `--command`) flag can be added in a later item *after* it is documented here
    and in the interface above; it is explicitly out of scope for item 6.
  - **Built-in set:** `default_assertions()` returns the small default checks the
    boot flow runs when the caller supplies none — at minimum a `present`
    "reached `login:`" check and an `absent` "no `Kernel panic`" check, i.e.
    "it booted to a login without panicking". `run_assertions(log, checks)`
    returns one `CheckResult` per check in input order; `all_passed(results)`
    is the roll-up (vacuously True for an empty set). The JSON/JUnit emitters
    (item 7) consume these results' `to_dict()` shape
    `{name, status, evidence}`.
- **Reports contract FINALISED (item 7, 2026-09-13):** `reports.py` is a pure
  emitter layer (plus thin file writers) that turns a `BootResult` (item 5) plus
  the optional list of `CheckResult`s (item 6) into the two report formats:
  - **JSON:** exactly the spec's `{image, accel, duration, checks, result}`
    shape *as a superset* (extra keys `returncode`, `ready`, `timed_out`,
    `markers`, `error`, `serial_log` — the same superset convention
    `BootResult.to_dict()` uses). `checks` is each `CheckResult.to_dict()`
    (`{name, status, evidence}`) in input order. `result` is the overall
    verdict: `"pass"` only when the boot reached readiness AND every check
    passed; a timeout, QEMU launch failure, or any failed check is `"fail"`
    (an empty check list is vacuously satisfied). Written with 2-space indent.
  - **JUnit XML:** a `<testsuites name="pengyos-qemu-verify">` wrapping one
    `<testsuite>` that carries the boot context as attributes (`image`,
    `accel`, `result`, `tests`, `failures`, `errors`, `skipped`, `time`).
    The first `<testcase>` is the synthetic `boot-reached-readiness` (the boot
    outcome as a check: no child when ready+errorless, else a `<failure>` with
    a short `message` and the error/timeout text); then one `<testcase name=…>`
    per assertion check, each failure carrying a `<failure>` whose text is the
    evidence line (or an explanatory placeholder for a failing check with no
    match line, e.g. a missed `present`). Built with `xml.etree.ElementTree`,
    so output is always well-formed and re-parseable (the spec's
    "parse it back" test).
  - **Always writes:** the `boot` subcommand writes `--json OUT` / `--junit
    OUT` whenever requested, including pre-flight failures — a missing image
    (`ImageNotFound`), missing UEFI firmware (`OvmfNotFound`), a swtpm start
    failure (`SwtpmError`), or an unprovisionable TPM state (`TpmStateError`)
    — each reported via a minimal error `BootResult`
    (`reports.make_error_result`) and exit `1`, honouring the "always writes
    JSON" acceptance criterion. These are the *only* boot-flow exceptions the
    CLI catches; a report that *cannot* be written (bad path) is an error on
    stderr but does not change the boot's exit code. Each written path is
    printed as `report:   <path>`.
  - **Checks wiring:** the emitters consume the checks *attached to the boot*
    (`checks=None` default → `BootResult.checks`, populated by the boot flow's
    wiring — see the wiring contract above) or an explicit caller-supplied
    iterable of `CheckResult`s / `{name, status, evidence}` dicts, which
    overrides.
  - Emitters are pure string builders (`result_to_dict` / `to_json` /
    `junit_xml`) + `write_json`/`write_junit` (parents created); no QEMU,
    network, display, or hardware.
- **Assertion-runner → boot-flow wiring contract FINALISED (wiring item,
  2026-09-13):** the runner is wired *inside* `boot_image()` (not after it
  returns), so every path that yields a `BootResult` yields a populated one:
  - **`boot_image(..., assertions=None)`** — `None` (the default) runs
    `assertions.default_assertions()`; a caller-supplied list replaces the
    default set (an empty list runs no checks at all). The set is
    **validated pre-flight** — a non-`Assertion` entry or a pattern that does
    not compile raises `ValueError` *before* the workdir is made and QEMU is
    launched (the CLI's "always writes JSON" pre-flight path, exit `1`).
  - **The checks run over whatever serial log was captured.** A ready boot
    gets the full log; a timeout gets the *partial* log captured up to the
    deadline; a QEMU launch failure gets the empty log (present checks fail,
    absent checks pass). The resulting `CheckResult`s are attached to
    `BootResult.checks` (serialised by `to_dict()`), and
    `BootResult.checks_passed` is the vacuously-true roll-up. `BootResult.ok`
    deliberately stays a *boot-level* property (ready, not timed-out,
    errorless) — the combined verdict is the emitters'
    `reports.overall_result`.
  - **The emitters fall back to the attached checks:** all of
    `overall_result` / `result_to_dict` / `to_json` / `junit_xml` /
    `write_json` / `write_junit` take `checks=None` meaning "use
    `boot.checks`"; an explicit iterable (even `()`) overrides, which is how
    the pre-wiring tests and report tests inject their own checks. Pre-flight
    error results (`reports.make_error_result`) carry an empty check list —
    nothing was booted to assert over.
  - **The `boot` CLI** prints one `check:    <name> PASS|FAIL — <evidence>`
    line per check in the summary and returns `0` only when the boot is ready
    **and** all checks pass (`reports.overall_result(result)`); a ready boot
    with a failed check (e.g. the log contains `Kernel panic`) is `1`. The
    default set is **orthogonal to `--expect`**: readiness markers decide
    when the boot is over, checks decide whether it was a *good* boot — a
    custom `--expect` boot that never prints `login:` is "ready" but still
    fails the default `reached-login` check (callers wanting a different
    verdict set use `boot_image(assertions=...)`; no CLI flag is invented).
  - **Validated:** the full `PENGYOS_IT=1` boot test asserts the attached
    checks equal a fresh `run_assertions()` over the same captured log (a
    real-boot oracle for the wiring) and feeds the emitters with
    `checks=None`.
- **`--dry-run` / `probe` contract FINALISED (item 8, 2026-09-13):** the
  interface's "prints the resolved QEMU argv for inspection" is delivered as a
  single pure render path shared by two entry points — the `probe` subcommand
  and a new `boot --dry-run` flag — so both print **byte-for-byte the same**
  argv (`cli.render_argv` → `argv.build_argv`):
  - **No side effects** (the defining requirement): no QEMU is launched, no
    per-boot workdir is created, no serial-log file is written, no OVMF VARS
    copy is made, and no swtpm process is started. The *only* I/O is the
    read-only OVMF location (an `os.path` probe), which is non-mutating — so a
    *pending* (not-yet-built) image or firmware still renders. This keeps
    `probe`/`--dry-run` usable for inspecting the argv of a build that has not
    landed yet (consistent with `build_argv` itself doing no existence checks).
  - **Output:** a shell-pasteable command — a leading `#` comment line, then
    the argv joined with `shlex.join` (space-containing args such as the
    `-append` string are quoted). Exit `0` on success; `1` when the argv cannot
    be resolved (e.g. `--uefi` + `--kernel`, which remain mutually exclusive).
  - **`--tpm` in a dry-run:** there is no running swtpm socket, so the chardev
    is rendered with the placeholder datapath `path=tpm.sock` (the real socket
    path is only known once `boot` starts swtpm).
  - **UEFI in a dry-run:** firmware is located with the real, existence-checking
    locator first (so a present *split* firmware faithfully renders two pflash
    drives); if no firmware is found (pending build / custom install) it falls
    back to an `exist=False` render so a representative argv is still printed
    rather than an error.
- **Integration-test contract FINALISED (item 9, 2026-09-13):**
  `tests/test_integration.py` is the only place a *real* QEMU/boot is needed,
  and it is strictly opt-in:
  - **Gate:** boot tests run only when the environment variable `PENGYOS_IT`
    is exactly `1` (anything else skips). The gate is a *class-level*
    `skipif`, never module-level, so the module's unit tests (discovery
    rules) run in every `pytest -q`.
  - **"A tiny prebuilt Alpine/hello image if present"** resolves to: whatever
    *prebuilt* image the build pipeline left on the host that boots to a
    `login:` prompt. There are no Alpine artefacts in this repo and tests may
    not use the network, so nothing is downloaded or built — the candidate set
    is purely discovery over documented, local locations.
  - **Candidate discovery** (priority order; a candidate is dropped when its
    files/firmware are absent; pure — `home`/`env`/`ovmf_present` injectable):
    1. `explicit` — `PENGYOS_IT_IMAGE` (always included when set; a missing
       file then fails the boot with a clear error rather than silently
       falling back), optional `PENGYOS_IT_KERNEL` (bare-metal `-kernel`)
       and `PENGYOS_IT_UEFI=1` (firmware/disk boot).
    2. `buildroot-bare-metal` — `~/build/pengyos/output/pengyos-core/images/`
       with both `bzImage` and `rootfs.ext4` present → bare-metal boot
       (`-kernel` + virtio rootfs).
    3. `core-uefi-disk` — `pengyos-core-x86_64.img` in that directory
       present *and* OVMF locatable on the host → UEFI firmware/disk boot.
  - **Per-candidate test:** real `boot_image()` (KVM when `/dev/kvm` is
    writable, else TCG) with a `PENGYOS_IT_TIMEOUT` seconds budget (default
    180; invalid/non-positive values fall back to the default) → asserts
    ready (not timed-out, no error) → runs `assertions.default_assertions()`
    over the captured serial log file (`BootResult.serial_log` is a path) →
    writes the JSON + JUnit reports and parses them back (`result == "pass"`,
    `checks` in order, `tests = 1 + len(checks)`, zero `<failure>` nodes).
  - **Never-empty parametrisation:** with no candidate present the boot test
    is collected once with a sentinel id (`no-candidate-image`) and skips with
    an actionable message (set `PENGYOS_IT_IMAGE` / build an image), so
    `PENGYOS_IT=1 pytest -q` is green on a bare host instead of collecting
    zero tests.
  - **Validated on the build host (behoserver, 2026-09-13):** both candidates
    boot under KVM and pass — the UEFI disk boot ≈ 11 s to `pengyos login:`,
    bare-metal bzImage+rootfs.ext4 ≈ 4 s — the full suite green with and
    without `PENGYOS_IT=1`.

### Artifact safety + smoke matrix (2026-09-14)

- **Found: verifying an image mutated it.** The harness attached the guest disk
  read-write, and the guest re-mounts the ext4 root `r/w` and writes
  (journal, syslog, RNG seed) — so every verification boot rewrote the
  artifact. Observed on the published image: `fa4423cb…` (matching its
  `.sha256`) became `29cf366f…` after our own `--uefi` / `--uefi --tpm` /
  `PENGYOS_IT=1` runs, breaking the documented `sha256sum -c`. Fixed by attaching the disk through a QEMU throwaway overlay
  (`snapshot=on`, the default); the guest still sees a writable disk, so
  readiness behaviour is unchanged. `--no-snapshot` restores write-through.
- **Defined: the smoke matrix.** SPEC §556's "passes the smoke matrix" was
  prose with no definition, so it could not be checked. It is now
  a TOML matrix — variants (`uefi`, `uefi-tpm`) × blocking checks — plus
  the `smoke` subcommand. Checks are regexes over the serial log, so the whole
  thing is headless and unprivileged; the TPM variant additionally proves the
  swtpm wiring does not wedge startup (Core has no TPM consumer yet).
- **Grounded, not invented:** every shipped check is satisfied by real
  captures, and a real failed capture fails exactly the facts it lacks —
  asserted by the unit suite, so a weakened (unfailable) check fails the tests.

### Core matrix (2026-10-02)

- The shipped matrix is `smoke/core.toml`, grounded in Core 26.10 serial logs
  (UEFI and UEFI+TPM) and a real failed boot (the Core kernel booted bare
  with `root=/dev/vda`, which panics). It replaced the original busybox-era
  matrix when that image was retired. Beyond boot it checks systemd as PID 1,
  networkd, resolved, `network.target`, chrony, sshd, `multi-user.target`, the
  `pengyos-core` login prompt, and that no unit failed.
- A matrix's relative `image` resolves against the build root
  (`$PENGYOS_BUILD_ROOT`, else `~/build/pengyos`), where `pengyos-build` writes.

### The SSH lane (2026-10-05) — the out-of-band channel is now documented

The 2026-09-13 note dropped the task text's "optional SSH commands" half because
the interface never declared a flag. That is resolved: the channel is real, it is
documented, and it is now part of the interface above.

- **What the channel is.** An image built with `PENGYOS_AUTHORIZED_KEYS_FILE`
  injects a key into root's `authorized_keys`
  (`buildroot-external/board/core/post-build.sh`); the image's sshd is key-only
  (`PermitRootLogin prohibit-password`); `--hostfwd tcp::2222-:22` reaches it
  from the host (`docs/qemu-testing.md` §4). Published images carry no key
  (`release.sh` refuses to publish while the variable is set), so the SSH lane is
  a **build-host verification tool**, not something a shipped image enables.
- **Why it exists.** The serial log proves what the guest *prints*. It cannot
  prove `systemctl --failed` is empty after the log stops being read, whether a
  service is active, or whether the Flatpak stack is present. Those are the facts
  this lane is for.
- **Contract.** `--ssh` runs the default checks (`ssh-no-failed-units`,
  `ssh-sshd-active`); `--ssh-command COMMAND` adds one that must exit 0. Checks
  run **while the guest is still up** — after readiness, before QEMU is
  terminated — and each produces the same `CheckResult` a log check does, so
  JSON, JUnit, `checks_passed` and the exit code treat them identically. A boot
  that timed out or never launched still gets a FAIL with a reason for every
  requested SSH check: the report shape never depends on how far the boot got.
- **A dead channel is never a pass.** An `absent` check whose command never
  reached the guest FAILS. "No match" from a refused connection is not evidence
  of absence (mutation-checked in `tests/test_sshchecks.py`).
- **The guest host key is throwaway and is recorded, not ignored.**
  `StrictHostKeyChecking=accept-new` with `UserKnownHostsFile` inside the boot's
  workdir. `StrictHostKeyChecking=no` (silently ignoring it) is rejected by test.
- **The port forward is added for you** when SSH checks are requested and no
  forward reaches port 22, so a missing `--hostfwd` cannot read as a broken
  image. `--dry-run` / `probe` render the same argv that will run. A boot with no
  SSH checks is untouched, so the pre-existing argv goldens still hold.
- **Not yet done:** the smoke matrix still declares log checks only. Adding
  `kind = "command"` checks plus a per-variant `ssh = true` is the next step; it
  changes matrix data that is currently grounded in real captures, so it lands
  with its own grounding rather than folded into this item.
