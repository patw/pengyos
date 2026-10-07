//! The `doctor` subcommand: report host readiness.
//!
//! `doctor` answers a single question before anything else runs — *"is this
//! host ready to build PengyOS?"* — by evaluating a small, fixed matrix of
//! readiness checks and printing a human-readable report with a scriptable exit
//! code:
//!
//! * `0` — every **required** check passed (host is ready; optional warnings
//!   are fine).
//! * `1` — a required check failed (host is not ready).
//! * `2` — usage error (handled by the framework, never here).
//!
//! The checks are deliberately independent of any external process so the core
//! logic is unit-tested with a fixture [`Host`] and never touches the network,
//! root, or real hardware:
//!
//! * **tools** — the sibling PengyOS tools are on `PATH` (the build front
//!   door; see `pengyos-build`, `pengyos-verifier`, …).
//! * **kvm** — `/dev/kvm` is readable, so QEMU can use hardware acceleration
//!   instead of the slow TCG fallback (see `pengyos-qemu-verify`).
//! * **buildroot** — a well-formed Buildroot pin (`YYYY.MM[.N]`) is known, e.g.
//!   `2026.02.3` LTS (the pin the project builds against; see
//!   `pengyos-build/targets.toml`).
//!
//! Real system probing (scanning `PATH`, opening `/dev/kvm`) lives in
//! [`SystemProbe`]; [`diagnose`] takes any [`HostProbe`], so tests inject a
//! fixture.
//!
//! ## Output: text vs. JSON
//!
//! `doctor` renders two ways, driven by the `--json` flag:
//!
//! * text — the human-readable readiness report (`format_diagnosis`).
//! * `--json` — the stable meta-CLI schema (see `crate::dispatch::outcome_to_json`)
//!   with the readiness matrix embedded under a `checks` array, so callers can
//!   read `exit_code` / `ok` uniformly across subcommands.

use crate::config::Config;
use crate::dispatch::DispatchOutcome;
use crate::json::Value;
use crate::PROG;
use std::collections::BTreeSet;
use std::fmt;
use std::path::Path;

/// The canonical Buildroot pin the project builds against.
///
/// Mirrors `br_version = "2026.02.3"` in `pengyos-build/targets.toml`. Used as
/// the default when no `[build] buildroot` override is configured; a future
/// subtask may read `targets.toml` directly instead of hard-coding it.
pub const DEFAULT_BUILDROOT: &str = "2026.02.3";

/// The sibling tools the build front door depends on, by executable name.
///
/// Checked for presence on `PATH` by the `tools` readiness check. The names are
/// the actual executables under `tools/` (note `verify` → `pengyos-verifier`,
/// `profile` → `pengyos-hwprofile`, `triage` → `pengyos-update-triage`).
pub const REQUIRED_TOOLS: &[&str] = &[
    "pengyos-build",
    "pengyos-verifier",
    "pengyos-update-triage",
    "pengyos-hwprofile",
    "pengyos-sbom",
];

/// How a failing check affects the overall verdict.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Severity {
    /// A missing/failed check makes the host not ready (exit `1`).
    Required,
    /// A missing/failed check is a soft warning (host is still ready).
    Optional,
}

impl fmt::Display for Severity {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Severity::Required => write!(f, "required"),
            Severity::Optional => write!(f, "optional"),
        }
    }
}

/// The outcome of a single readiness check.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    /// The check passed.
    Pass,
    /// The check failed (makes the host not ready if [`Severity::Required`]).
    Fail,
    /// The check is in a degraded-but-acceptable state (a soft warning).
    Warn,
}

impl Status {
    /// A short, ASCII, machine-readable tag for the status (uppercase, for the
    /// human report).
    pub fn tag(self) -> &'static str {
        match self {
            Status::Pass => "PASS",
            Status::Fail => "FAIL",
            Status::Warn => "WARN",
        }
    }

    /// The lowercase JSON status used in the `--json` schema.
    pub fn json_status(self) -> &'static str {
        match self {
            Status::Pass => "pass",
            Status::Fail => "fail",
            Status::Warn => "warn",
        }
    }
}

/// One evaluated readiness check.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CheckResult {
    /// Stable identifier for the check (e.g. `buildroot`, `kvm`, `tools`).
    pub id: &'static str,
    /// A short human label shown in the report.
    pub name: &'static str,
    /// Whether a failure is fatal for the overall verdict.
    pub severity: Severity,
    /// The outcome of the check.
    pub status: Status,
    /// A human-readable detail (the pinned version, missing tools, …).
    pub detail: String,
}

impl CheckResult {
    fn new(
        id: &'static str,
        name: &'static str,
        severity: Severity,
        status: Status,
        detail: &str,
    ) -> CheckResult {
        CheckResult {
            id,
            name,
            severity,
            status,
            detail: detail.to_string(),
        }
    }
}

/// A matrix of readiness checks with the overall verdict.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Diagnosis {
    pub checks: Vec<CheckResult>,
}

impl Diagnosis {
    /// The number of checks that passed.
    pub fn passed(&self) -> usize {
        self.checks
            .iter()
            .filter(|c| c.status == Status::Pass)
            .count()
    }

    /// The number of required checks that failed.
    pub fn required_failures(&self) -> usize {
        self.checks
            .iter()
            .filter(|c| c.severity == Severity::Required && c.status == Status::Fail)
            .count()
    }

    /// The number of optional checks that warned.
    pub fn warnings(&self) -> usize {
        self.checks
            .iter()
            .filter(|c| c.status == Status::Warn)
            .count()
    }

    /// The scriptable exit code: `0` when ready, `1` when a required check
    /// failed.
    pub fn exit_code(&self) -> u8 {
        if self.required_failures() == 0 {
            0
        } else {
            1
        }
    }
}

/// A snapshot of the readiness-relevant state of a host.
///
/// This is the fixture a test builds to describe an arbitrary host: the set of
/// sibling tools found on `PATH`, whether `/dev/kvm` is readable, and the known
/// Buildroot pin (if any). A real host is collected into this shape by
/// [`collect_host`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Host {
    /// Names of sibling tools found on `PATH`.
    pub tools: BTreeSet<String>,
    /// Whether `/dev/kvm` is readable (hardware virtualization available).
    pub kvm: bool,
    /// The pinned Buildroot version (`YYYY.MM[.N]`), if known.
    pub buildroot: Option<String>,
}

impl Host {
    /// A host with nothing present — every required check fails.
    pub fn empty() -> Host {
        Host {
            tools: BTreeSet::new(),
            kvm: false,
            buildroot: None,
        }
    }
}

/// The seam over "probing the host": tool presence, `/dev/kvm`, and the
/// Buildroot pin. [`Host`] is the test fixture; [`SystemProbe`] is the real
/// implementation. [`diagnose`] is agnostic, so tests inject a fixture.
pub trait HostProbe {
    /// Whether a sibling tool executable is on `PATH`.
    fn tool_present(&self, tool: &str) -> bool;
    /// Whether `/dev/kvm` is readable (hardware virtualization available).
    fn kvm_available(&self) -> bool;
    /// The pinned Buildroot version, if known.
    fn buildroot_version(&self) -> Option<String>;
}

/// A fixture [`HostProbe`]: a plain [`Host`] you can build in tests.
impl HostProbe for Host {
    fn tool_present(&self, tool: &str) -> bool {
        self.tools.contains(tool)
    }

    fn kvm_available(&self) -> bool {
        self.kvm
    }

    fn buildroot_version(&self) -> Option<String> {
        self.buildroot.clone()
    }
}

/// The real [`HostProbe`], backed by `std`.
///
/// * `tool_present` scans `PATH` for an executable file with the execute bit
///   set (std-only `which`).
/// * `kvm_available` opens `/dev/kvm` read-only — a side-effect-free proxy for
///   the `os.access(R_OK|W_OK)` probe used by `pengyos-qemu-verify`; read-only
///   avoids acquiring the KVM device.
/// * `buildroot_version` comes from the resolved config's `[build] buildroot`
///   key, falling back to [`DEFAULT_BUILDROOT`].
pub struct SystemProbe {
    buildroot: Option<String>,
}

impl SystemProbe {
    /// Build a real probe. The Buildroot pin is read from config up front so the
    /// probe stays a plain data carrier.
    pub fn new(config: &Config) -> SystemProbe {
        let buildroot = config
            .str("build", "buildroot")
            .filter(|v| !v.is_empty())
            .or_else(|| {
                if DEFAULT_BUILDROOT.is_empty() {
                    None
                } else {
                    Some(DEFAULT_BUILDROOT.to_string())
                }
            });
        SystemProbe { buildroot }
    }
}

impl HostProbe for SystemProbe {
    fn tool_present(&self, tool: &str) -> bool {
        which(tool)
    }

    fn kvm_available(&self) -> bool {
        std::fs::OpenOptions::new()
            .read(true)
            .open("/dev/kvm")
            .is_ok()
    }

    fn buildroot_version(&self) -> Option<String> {
        self.buildroot.clone()
    }
}

/// Collect a real [`Host`] snapshot from the current system.
pub fn collect_host(config: &Config) -> Host {
    let probe = SystemProbe::new(config);
    let mut tools: BTreeSet<String> = BTreeSet::new();
    for tool in REQUIRED_TOOLS {
        if probe.tool_present(tool) {
            tools.insert(tool.to_string());
        }
    }
    Host {
        tools,
        kvm: probe.kvm_available(),
        buildroot: probe.buildroot_version(),
    }
}

/// Scan `PATH` for an executable file named `tool` (std-only `which`).
///
/// Returns `false` if `PATH` is unset or the executable is not found or lacks
/// the execute bit.
pub fn which(tool: &str) -> bool {
    let Some(paths) = std::env::var_os("PATH") else {
        return false;
    };
    for dir in std::env::split_paths(&paths) {
        if is_executable(&dir.join(tool)) {
            return true;
        }
    }
    false
}

/// Whether `path` is a regular file with any execute bit set (Unix) — a proxy
/// for "is it a runnable executable".
fn is_executable(path: &Path) -> bool {
    match std::fs::metadata(path) {
        Ok(meta) => {
            if !meta.file_type().is_file() {
                return false;
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                meta.permissions().mode() & 0o111 != 0
            }
            #[cfg(not(unix))]
            {
                true
            }
        }
        Err(_) => false,
    }
}

/// Check that a Buildroot pin is present and well-formed (`YYYY.MM[.N]`).
fn check_buildroot(probe: &dyn HostProbe) -> CheckResult {
    match probe.buildroot_version() {
        Some(v) if is_buildroot_version(&v) => CheckResult::new(
            "buildroot",
            "buildroot",
            Severity::Required,
            Status::Pass,
            &format!("pinned {v}"),
        ),
        Some(v) => CheckResult::new(
            "buildroot",
            "buildroot",
            Severity::Required,
            Status::Fail,
            &format!("invalid pin format `{v}` (expected YYYY.MM[.N])"),
        ),
        None => CheckResult::new(
            "buildroot",
            "buildroot",
            Severity::Required,
            Status::Fail,
            "no Buildroot pin declared",
        ),
    }
}

/// Check `/dev/kvm` (optional: a missing KVM is a soft warning, since TCG is a
/// correct but slow fallback).
fn check_kvm(probe: &dyn HostProbe) -> CheckResult {
    if probe.kvm_available() {
        CheckResult::new(
            "kvm",
            "kvm",
            Severity::Optional,
            Status::Pass,
            "/dev/kvm available",
        )
    } else {
        CheckResult::new(
            "kvm",
            "kvm",
            Severity::Optional,
            Status::Warn,
            "/dev/kvm not available (TCG fallback)",
        )
    }
}

/// Check that every required sibling tool is on `PATH`.
fn check_tools(probe: &dyn HostProbe) -> CheckResult {
    let missing: Vec<&str> = REQUIRED_TOOLS
        .iter()
        .copied()
        .filter(|t| !probe.tool_present(t))
        .collect();
    if missing.is_empty() {
        CheckResult::new(
            "tools",
            "tools",
            Severity::Required,
            Status::Pass,
            REQUIRED_TOOLS.join(", ").as_str(),
        )
    } else {
        CheckResult::new(
            "tools",
            "tools",
            Severity::Required,
            Status::Fail,
            &format!("missing: {}", missing.join(", ")),
        )
    }
}

/// Whether `v` is a well-formed Buildroot pin: `YYYY.MM` or `YYYY.MM.N` of
/// digits (Buildroot release tags are `YYYY.MM[.N]`).
pub fn is_buildroot_version(v: &str) -> bool {
    let mut segments = v.split('.');
    match (segments.next(), segments.next(), segments.next()) {
        (Some(year), Some(month), None) => {
            year.chars().all(|c| c.is_ascii_digit())
                && year.len() == 4
                && month.chars().all(|c| c.is_ascii_digit())
                && !month.is_empty()
        }
        (Some(year), Some(month), Some(patch)) => {
            year.len() == 4
                && year.chars().all(|c| c.is_ascii_digit())
                && month.chars().all(|c| c.is_ascii_digit())
                && !month.is_empty()
                && patch.chars().all(|c| c.is_ascii_digit())
                && !patch.is_empty()
        }
        _ => false,
    }
}

/// Evaluate the full readiness matrix against a [`HostProbe`].
pub fn diagnose(probe: &dyn HostProbe) -> Diagnosis {
    let checks = vec![
        check_buildroot(probe),
        check_kvm(probe),
        check_tools(probe),
    ];
    Diagnosis { checks }
}

/// Format a [`Diagnosis`] as a human-readable readiness report.
pub fn format_diagnosis(diag: &Diagnosis, verbose: bool) -> String {
    let mut lines = Vec::with_capacity(diag.checks.len() + 6);
    lines.push(format!("{PROG} doctor — report host readiness\n"));
    lines.push("READINESS:\n".to_string());
    for c in &diag.checks {
        lines.push(format!(
            "  [{}] {:<9} {}\n",
            c.status.tag(),
            c.id,
            c.detail
        ));
        // In verbose mode, note the severity of warnings/failures.
        if verbose && c.status != Status::Pass {
            lines.push(format!(
                "         ({})\n",
                if c.severity == Severity::Required {
                    "required"
                } else {
                    "optional"
                }
            ));
        }
    }

    let total = diag.checks.len();
    let passed = diag.passed();
    let warnings = diag.warnings();
    match diag.exit_code() {
        0 => {
            let note = if warnings > 0 {
                format!("{passed}/{total} checks passed, {warnings} warning")
            } else {
                format!("{passed}/{total} checks passed")
            };
            lines.push(format!("\n{note}. Host is ready."));
        }
        _ => {
            lines.push(format!(
                "\n{} required check(s) failed. Host is not ready.",
                diag.required_failures()
            ));
        }
    }
    lines.concat()
}

/// Render a [`Diagnosis`] as the `--json` document, embedding the readiness
/// matrix under `checks` while keeping the stable meta-CLI top-level shape
/// (`command`, `executable`, `args`, `exit_code`, `stdout`, `stderr`, `ok`).
fn diagnosis_to_json(diag: &Diagnosis) -> String {
    let checks: Vec<Value> = diag
        .checks
        .iter()
        .map(|c| {
            Value::Object(vec![
                ("id", Value::Str(c.id.to_string())),
                ("name", Value::Str(c.name.to_string())),
                ("severity", Value::Str(c.severity.to_string())),
                ("status", Value::Str(c.status.json_status().to_string())),
                ("detail", Value::Str(c.detail.clone())),
            ])
        })
        .collect();

    // Build the full object by hand so `checks` sits alongside the base fields,
    // in the same order as `outcome_to_json` (so `doctor` and the forwarded
    // subcommands share one schema).
    let pairs: Vec<(&'static str, Value)> = vec![
        ("command", Value::Str("doctor".to_string())),
        ("executable", Value::Str("pengyos-doctor".to_string())),
        ("args", Value::Array(vec![])),
        ("exit_code", Value::Number(diag.exit_code() as f64)),
        ("stdout", Value::Str(String::new())),
        ("stderr", Value::Str(String::new())),
        ("ok", Value::Bool(diag.exit_code() == 0)),
        ("checks", Value::Array(checks)),
    ];
    Value::Object(pairs).to_json()
}

/// The `doctor` handler: collect the real host, diagnose it, and render the
/// report. Unlike the other subcommands, `doctor` is a native Rust check — it
/// does not shell out to a sibling tool (there is no `pengyos-doctor`).
///
/// Renders the human report in text mode, or the stable `--json` schema (with
/// the readiness matrix embedded under `checks`) when `--json` was requested.
pub fn doctor_handler(
    command: &str,
    args: &crate::args::ParsedArgs,
    config: &Config,
    _runner: &mut dyn crate::dispatch::Runner,
    json: bool,
) -> DispatchOutcome {
    let host = collect_host(config);
    let diag = diagnose(&host);
    let verbose = args.flag("verbose");

    let exit_code = diag.exit_code() as i32;
    if json {
        DispatchOutcome {
            command: command.to_string(),
            executable: "pengyos-doctor".to_string(),
            args: Vec::new(),
            exit_code,
            stdout: diagnosis_to_json(&diag),
            stderr: String::new(),
            json: true,
        }
    } else {
        DispatchOutcome {
            command: command.to_string(),
            executable: "pengyos-doctor".to_string(),
            args: Vec::new(),
            exit_code,
            stdout: format_diagnosis(&diag, verbose),
            stderr: String::new(),
            json: false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A fully-ready host: all tools present, KVM on, Buildroot pinned.
    fn ready_host() -> Host {
        Host {
            tools: REQUIRED_TOOLS
                .iter()
                .map(|s| s.to_string())
                .collect(),
            kvm: true,
            buildroot: Some("2026.02.3".to_string()),
        }
    }

    #[test]
    fn is_buildroot_version_accepts_year_month() {
        assert!(is_buildroot_version("2026.02"));
        assert!(is_buildroot_version("2026.2")); // month is just non-empty digits
    }

    #[test]
    fn is_buildroot_version_accepts_year_month_patch() {
        assert!(is_buildroot_version("2026.02.3"));
        assert!(is_buildroot_version("2024.02.1"));
    }

    #[test]
    fn is_buildroot_version_rejects_garbage() {
        assert!(!is_buildroot_version("abc"));
        assert!(!is_buildroot_version("26.02.3")); // year must be 4 digits
        assert!(!is_buildroot_version("2026.02.x"));
        assert!(!is_buildroot_version("2026-02-3"));
        assert!(!is_buildroot_version(""));
    }

    #[test]
    fn ready_host_passes_everything() {
        let host = ready_host();
        let diag = diagnose(&host);
        assert_eq!(diag.checks.len(), 3);
        assert_eq!(diag.passed(), 3);
        assert_eq!(diag.required_failures(), 0);
        assert_eq!(diag.exit_code(), 0);
    }

    #[test]
    fn empty_host_fails_required_checks() {
        let host = Host::empty();
        let diag = diagnose(&host);
        // buildroot (required) and tools (required) fail; kvm warns (optional).
        assert_eq!(diag.required_failures(), 2);
        assert_eq!(diag.warnings(), 1);
        assert_eq!(diag.exit_code(), 1);
    }

    #[test]
    fn malformed_buildroot_is_a_required_failure() {
        let mut host = ready_host();
        host.buildroot = Some("not-a-version".to_string());
        let diag = diagnose(&host);
        let buildroot = diag.checks.iter().find(|c| c.id == "buildroot").unwrap();
        assert_eq!(buildroot.status, Status::Fail);
        assert_eq!(diag.exit_code(), 1);
    }

    #[test]
    fn missing_kvm_is_only_a_warning() {
        let mut host = ready_host();
        host.kvm = false;
        let diag = diagnose(&host);
        let kvm = diag.checks.iter().find(|c| c.id == "kvm").unwrap();
        assert_eq!(kvm.status, Status::Warn);
        assert_eq!(kvm.severity, Severity::Optional);
        // Still ready: the only required checks passed.
        assert_eq!(diag.exit_code(), 0);
    }

    #[test]
    fn missing_tools_reports_each_missing_name() {
        let host = Host {
            tools: ["pengyos-build".to_string()]
                .iter()
                .cloned()
                .collect(),
            kvm: true,
            buildroot: Some("2026.02.3".to_string()),
        };
        let diag = diagnose(&host);
        let tools = diag.checks.iter().find(|c| c.id == "tools").unwrap();
        assert_eq!(tools.status, Status::Fail);
        assert!(tools.detail.contains("pengyos-verifier"));
        assert!(tools.detail.contains("pengyos-sbom"));
        assert_eq!(diag.exit_code(), 1);
    }

    #[test]
    fn report_is_text_and_scriptable() {
        let diag = diagnose(&ready_host());
        let report = format_diagnosis(&diag, false);
        assert!(report.contains("READINESS:"));
        assert!(report.contains("[PASS] buildroot"));
        assert!(report.contains("Host is ready."));
        assert!(report.contains("3/3 checks passed"));
    }

    #[test]
    fn report_names_failures_and_exit_one() {
        let diag = diagnose(&Host::empty());
        let report = format_diagnosis(&diag, false);
        assert!(report.contains("[FAIL] buildroot"));
        assert!(report.contains("[FAIL] tools"));
        assert!(report.contains("[WARN] kvm"));
        assert!(report.contains("Host is not ready."));
    }

    #[test]
    fn verbose_report_adds_severity_notes() {
        let diag = diagnose(&Host::empty());
        let terse = format_diagnosis(&diag, false);
        let verbose = format_diagnosis(&diag, true);
        assert!(!terse.contains("(required)"));
        assert!(verbose.contains("(required)"));
        assert!(verbose.contains("(optional)"));
    }

    #[test]
    fn host_fixture_is_a_host_probe() {
        // The fixture Host implements HostProbe, so tests can pass &host as
        // &dyn HostProbe.
        let host = ready_host();
        let diag = diagnose(&host);
        assert_eq!(diag.exit_code(), 0);
    }

    #[test]
    fn which_respects_path_and_execute_bit() {
        // A real, executable file (this source file's directory has executables
        // after a build); at minimum, `sh` is on PATH on any Unix host and is
        // executable.
        assert!(which("sh"), "sh should be on PATH and executable");
        // A name that cannot exist is not found.
        assert!(!which("definitely-not-a-real-tool-xyz"));
    }

    // --- --json schema -------------------------------------------------------

    use crate::args::ParsedArgs;
    use crate::config::Config;
    use crate::dispatch::MockRunner;

    #[test]
    fn diagnosis_to_json_embeds_readiness_matrix() {
        // The `--json` document keeps the stable top-level shape (command,
        // executable, exit_code, ok, …) and embeds the readiness matrix under
        // `checks`, so callers can read `exit_code` / `ok` uniformly across
        // subcommands.
        let diag = diagnose(&ready_host());
        let json = diagnosis_to_json(&diag);
        assert!(json.contains(&format!("\"command\":\"doctor\"")));
        assert!(json.contains(&format!("\"executable\":\"pengyos-doctor\"")));
        assert!(json.contains(r#""args":[]"#));
        assert!(json.contains(r#""exit_code":0"#));
        assert!(json.contains(r#""ok":true"#));
        assert!(json.contains(r#""checks":["#));
        for id in ["buildroot", "kvm", "tools"] {
            assert!(
                json.contains(&format!("\"id\":\"{id}\"")),
                "missing check `{id}` in {json}"
            );
        }
        // Each check carries its fields, including the lowercase JSON status.
        assert!(json.contains(r#""severity":"required"#));
        assert!(json.contains(r#""status":"pass"#));
        assert!(json.contains(r#""detail":"#));
    }

    #[test]
    fn diagnosis_to_json_reports_required_failure() {
        // A required failure flips `ok` to false and `exit_code` to 1 in JSON.
        let diag = diagnose(&Host::empty());
        let json = diagnosis_to_json(&diag);
        assert!(json.contains(r#""exit_code":1"#));
        assert!(json.contains(r#""ok":false"#));
        assert!(json.contains(r#""status":"fail"#));
    }

    #[test]
    fn doctor_handler_json_vs_text() {
        let config = Config::default();
        let parsed = ParsedArgs::default();

        // Both modes agree on the exit code, whatever it is (readiness depends on
        // the host, which the test does not control).
        let mut mock = MockRunner::new();
        let text = doctor_handler("doctor", &parsed, &config, &mut mock, false);
        let mut mock = MockRunner::new();
        let json = doctor_handler("doctor", &parsed, &config, &mut mock, true);
        assert_eq!(text.exit_code, json.exit_code);

        // Text mode renders the human report and no JSON.
        assert!(text.stdout.contains("READINESS:"));
        assert!(!text.stdout.contains(&format!("\"command\":\"doctor\"")));

        // JSON mode renders the document and no human report.
        assert!(json.stdout.contains(&format!("\"command\":\"doctor\"")));
        assert!(!json.stdout.contains("READINESS:"));
    }
}
