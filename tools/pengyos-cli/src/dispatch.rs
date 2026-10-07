//! The subcommand dispatch framework (clap-style, standard-library only).
//!
//! The framework is the heart of the meta-CLI: it routes `argv` to a registered
//! [`Command`], parses its arguments against an [`ArgSpec`], and invokes a
//! [`Handler`]. Handlers never spawn processes directly — they call
//! [`Runner::run`] with a [`CommandSpec`], which makes dispatch fully testable
//! with a mock runner (see `MockRunner`).
//!
//! Each PengyOS subcommand shells out to a sibling Python tool; the mapping from
//! subcommand → tool is resolved here via [`Config::tool_for`] and refined as
//! tools are added.
//!
//! ## Output: text vs. JSON
//!
//! Every subcommand can emit a stable machine-readable document via `--json`
//! (see [`crate::json`]). The handler renders the same [`DispatchOutcome`] two
//! ways — a human report in text mode, or the `--json` schema when `--json` was
//! requested — and [`Registry::dispatch`] decides which by scanning `argv` for
//! the flag.

use crate::args::{ArgSpec, ParsedArgs};
use crate::config::Config;
use crate::doctor::doctor_handler;
use crate::json::Value;
use crate::PROG;

/// A description of an external command to run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandSpec {
    pub executable: String,
    pub args: Vec<String>,
}

impl CommandSpec {
    /// Build the spec for `executable` from parsed arguments.
    ///
    /// Positionals come first, then options as `--key=value`, then flags as
    /// `--key`. Iteration order is deterministic (BTreeMap), so forwarded
    /// arguments are stable across runs — important for golden tests.
    pub fn from_executable(executable: &str, args: &ParsedArgs) -> CommandSpec {
        let mut forwarded: Vec<String> = Vec::with_capacity(
            args.positionals.len() + args.options.len() + args.flags.len(),
        );
        forwarded.extend(args.positionals.iter().cloned());
        for (key, value) in &args.options {
            forwarded.push(format!("--{key}={value}"));
        }
        for (key, _) in &args.flags {
            forwarded.push(format!("--{key}"));
        }
        CommandSpec {
            executable: executable.to_string(),
            args: forwarded,
        }
    }
}

/// The captured result of running a [`CommandSpec`].
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct RunOutcome {
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
}

impl RunOutcome {
    /// A successful outcome carrying `stdout`.
    pub fn ok_stdout(stdout: &str) -> RunOutcome {
        RunOutcome {
            exit_code: 0,
            stdout: stdout.to_string(),
            stderr: String::new(),
        }
    }
}

/// Abstraction over "running an external command".
///
/// The production implementation is [`ProcessRunner`]; tests use [`MockRunner`].
pub trait Runner {
    fn run(&mut self, spec: &CommandSpec) -> RunOutcome;
}

/// The real runner: spawns the process via `std::process::Command`.
///
/// A missing executable yields a clean, scriptable error (exit `127`) rather
/// than an uncaught panic.
pub struct ProcessRunner;

impl Runner for ProcessRunner {
    fn run(&mut self, spec: &CommandSpec) -> RunOutcome {
        match std::process::Command::new(&spec.executable)
            .args(&spec.args)
            .output()
        {
            Ok(output) => RunOutcome {
                exit_code: output.status.code().unwrap_or(-1),
                stdout: String::from_utf8_lossy(&output.stdout).into(),
                stderr: String::from_utf8_lossy(&output.stderr).into(),
            },
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => RunOutcome {
                exit_code: 127,
                stdout: String::new(),
                stderr: format!("{PROG}: command not found: {}", spec.executable),
            },
            Err(err) => RunOutcome {
                exit_code: 1,
                stdout: String::new(),
                stderr: format!("{PROG}: failed to run {}: {}", spec.executable, err),
            },
        }
    }
}

/// A test double for [`Runner`]: records every [`CommandSpec`] it is asked to
/// run and returns a canned [`RunOutcome`].
#[derive(Debug, Default)]
pub struct MockRunner {
    /// Every spec handed to [`Runner::run`], in order.
    pub calls: Vec<CommandSpec>,
    /// The outcome returned for each run.
    pub outcome: RunOutcome,
}

impl MockRunner {
    /// A fresh mock that runs nothing and returns success.
    pub fn new() -> MockRunner {
        MockRunner::default()
    }

    /// A mock that returns `ok_stdout(stdout)` for every run.
    pub fn ok_stdout(stdout: &str) -> MockRunner {
        MockRunner {
            outcome: RunOutcome::ok_stdout(stdout),
            ..Default::default()
        }
    }

    /// Number of times a command was run.
    pub fn call_count(&self) -> usize {
        self.calls.len()
    }

    /// The last [`CommandSpec`] handed to [`Runner::run`], if any.
    pub fn last(&self) -> Option<&CommandSpec> {
        self.calls.last()
    }
}

impl Runner for MockRunner {
    fn run(&mut self, spec: &CommandSpec) -> RunOutcome {
        self.calls.push(spec.clone());
        self.outcome.clone()
    }
}

/// The result of a dispatch: what to exit with and what to print.
///
/// Beyond the exit code and captured output, it carries the identity of the run
/// (`command`, `executable`, `args`) so the same value can be rendered as a
/// human report *or* the stable `--json` schema (see [`DispatchOutcome::to_json`]).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DispatchOutcome {
    /// The canonical subcommand name (e.g. `build`).
    pub command: String,
    /// The resolved sibling tool (per `Config::tool_for`), e.g. `pengyos-build`.
    pub executable: String,
    /// The forwarded argv (positionals then options/flags), as the tool sees it.
    pub args: Vec<String>,
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
    /// Whether the caller requested machine-readable `--json` output. When set,
    /// handlers emit the stable `--json` schema instead of the human report.
    pub json: bool,
}

/// Serialize a [`DispatchOutcome`] to the stable `--json` schema.
///
/// The object carries the subcommand, the resolved tool, the forwarded argv,
/// the exit code, the captured stdout/stderr, and an `ok` flag (`exit_code ==
/// 0`). The `doctor` handler embeds its readiness matrix under an additional
/// `checks` array, but keeps this same top-level shape so callers can always
/// read `exit_code` / `ok` uniformly.
pub fn outcome_to_json(outcome: &DispatchOutcome) -> String {
    Value::Object(vec![
        ("command", Value::Str(outcome.command.clone())),
        ("executable", Value::Str(outcome.executable.clone())),
        ("args", Value::Array(
            outcome
                .args
                .iter()
                .map(|a| Value::Str(a.clone()))
                .collect(),
        )),
        ("exit_code", Value::Number(outcome.exit_code as f64)),
        ("stdout", Value::Str(outcome.stdout.clone())),
        ("stderr", Value::Str(outcome.stderr.clone())),
        ("ok", Value::Bool(outcome.exit_code == 0)),
    ])
    .to_json()
}

/// A subcommand handler.
///
/// Receives the canonical command name, the parsed arguments, the resolved
/// config, a mutable runner, and a `json` flag. Returns a [`DispatchOutcome`]
/// describing the exit code and output. The handler renders its result the way
/// the caller asked: the human report in text mode, or the stable `--json`
/// schema when `--json` was requested (the same [`DispatchOutcome`] rendered two
/// ways — see [`outcome_to_json`]).
pub type Handler =
    fn(&str, &ParsedArgs, &Config, &mut dyn Runner, bool) -> DispatchOutcome;

/// A registered subcommand: its surface plus its handler.
#[derive(Debug, Clone)]
pub struct Command {
    pub name: &'static str,
    pub aliases: &'static [&'static str],
    pub help: &'static str,
    pub spec: ArgSpec,
    pub handler: Handler,
}

/// The registry of all subcommands.
#[derive(Debug, Default)]
pub struct Registry {
    commands: Vec<Command>,
}

/// Build the [`DispatchOutcome`] for a forwarded command, surfacing a clear
/// error when the sibling tool exits non-zero.
///
/// The tool's own stdout/stderr pass through untouched. When it exits non-zero,
/// a one-line annotation is prepended to stderr naming the subcommand, the tool,
/// and the exit status — so a caller can see *why* the meta-CLI returned
/// non-zero even when the tool itself is quiet (or when the output is captured
/// somewhere other than the terminal). This mirrors the sibling tools' own
/// `<tool>: error: ...` convention, generalised to "any non-zero exit".
///
/// Only used in text mode; in `--json` mode the tool's raw stderr is captured
/// verbatim (no annotation), so [`forward_handler`] does not call this helper.
fn forward_outcome(
    command: &str,
    executable: &str,
    outcome: RunOutcome,
) -> DispatchOutcome {
    let mut stderr = outcome.stderr;
    if outcome.exit_code != 0 {
        let prefix = format!(
            "{prog} {command}: `{exe}` exited with status {code}\n",
            prog = PROG,
            command = command,
            exe = executable,
            code = outcome.exit_code,
        );
        stderr.insert_str(0, &prefix);
    }
    DispatchOutcome {
        command: command.to_string(),
        executable: executable.to_string(),
        args: Vec::new(),
        exit_code: outcome.exit_code,
        stdout: outcome.stdout,
        stderr,
        json: false,
    }
}

/// The generic handler used for every subcommand that shells out to a sibling
/// tool: forward the parsed arguments to `tool_for(command)`.
///
/// In text mode the tool's stdout passes through and a non-zero exit is
/// annotated on stderr (see [`forward_outcome`]). In `--json` mode the tool's
/// raw stdout/stderr are captured into the document and the visible stdout is
/// the serialized `--json` schema. The returned [`DispatchOutcome`] carries the
/// exit code (and `json`) for the caller.
pub fn forward_handler(
    command: &str,
    args: &ParsedArgs,
    config: &Config,
    runner: &mut dyn Runner,
    json: bool,
) -> DispatchOutcome {
    let executable = config.tool_for(command);
    let spec = CommandSpec::from_executable(&executable, args);
    let raw = runner.run(&spec);

    // The tool's raw output is captured into the document's stdout/stderr.
    let document = DispatchOutcome {
        command: command.to_string(),
        executable: executable.clone(),
        args: spec.args.clone(),
        exit_code: raw.exit_code,
        stdout: raw.stdout.clone(),
        stderr: raw.stderr.clone(),
        json: true,
    };

    if json {
        // Visible stdout is the JSON document; the tool's output lives inside
        // it (captured above). Nothing else is printed, so scripts get clean
        // JSON on stdout.
        DispatchOutcome {
            exit_code: document.exit_code,
            stdout: outcome_to_json(&document),
            stderr: String::new(),
            json: true,
            ..document
        }
    } else {
        // Text mode: surface a clear annotation on stderr for non-zero exits.
        let annotated = forward_outcome(command, &executable, raw);
        DispatchOutcome {
            command: command.to_string(),
            executable,
            args: spec.args,
            exit_code: annotated.exit_code,
            stdout: annotated.stdout,
            stderr: annotated.stderr,
            json: false,
        }
    }
}

impl Registry {
    /// Build the registry with the six documented subcommands (spec.md).
    pub fn new() -> Registry {
        let commands = vec![
            Command {
                name: "build",
                aliases: &[],
                help: "build a target image",
                spec: ArgSpec::builder()
                    .positional("target", "the target triple to build")
                    .flag("verbose", Some('v'), "verbose output")
                    .flag("quiet", Some('q'), "suppress non-error output")
                    .min_positionals(1)
                    .max_positionals(1)
                    .build(),
                handler: forward_handler,
            },
            Command {
                name: "verify",
                aliases: &[],
                help: "verify a built image",
                spec: ArgSpec::builder()
                    .positional("image", "path to the image to verify")
                    .flag("verbose", Some('v'), "verbose output")
                    .min_positionals(1)
                    .max_positionals(1)
                    .build(),
                handler: forward_handler,
            },
            Command {
                name: "triage",
                aliases: &[],
                help: "triage update candidates",
                spec: ArgSpec::builder()
                    .flag("verbose", Some('v'), "verbose output")
                    .build(),
                handler: forward_handler,
            },
            Command {
                name: "profile",
                aliases: &[],
                help: "profile the host",
                spec: ArgSpec::builder()
                    .flag("verbose", Some('v'), "verbose output")
                    .build(),
                handler: forward_handler,
            },
            Command {
                name: "sbom",
                aliases: &[],
                help: "emit a software bill of materials",
                spec: ArgSpec::builder()
                    .positional("artifact", "path to the artifact to SBOM")
                    .flag("verbose", Some('v'), "verbose output")
                    .min_positionals(1)
                    .max_positionals(1)
                    .build(),
                handler: forward_handler,
            },
            Command {
                name: "doctor",
                aliases: &[],
                help: "report host readiness",
                spec: ArgSpec::builder()
                    .flag("verbose", Some('v'), "verbose output")
                    .build(),
                // `doctor` is a native Rust readiness check (tool presence,
                // /dev/kvm, Buildroot pin), not a dispatch to a sibling tool —
                // there is no `pengyos-doctor` Python tool.
                handler: doctor_handler,
            },
        ];
        Registry { commands }
    }

    /// Look up a command by name or alias.
    pub fn find(&self, name: &str) -> Option<&Command> {
        self.commands.iter().find(|c| c.name == name || c.aliases.contains(&name))
    }

    /// Iterate over all registered commands (in registration order).
    pub fn commands(&self) -> impl Iterator<Item = &Command> {
        self.commands.iter()
    }

    /// Dispatch `argv` (everything after the program name).
    ///
    /// `argv[0]` is the subcommand. Missing/unknown commands and parse errors
    /// yield a usage error (exit `2`); otherwise the command's handler runs.
    ///
    /// A `--json` anywhere in `argv` selects the machine-readable `--json` schema
    /// for the run (see [`crate::json`]); otherwise the human report is printed.
    ///
    /// `--json` is a *global* flag: it is consumed by the meta-CLI and never
    /// forwarded to the sibling tool, so no subcommand spec has to declare it.
    /// It is stripped from `argv` before parsing and before the forwarded argv
    /// is built.
    pub fn dispatch(
        &self,
        argv: &[String],
        config: &Config,
        runner: &mut dyn Runner,
    ) -> DispatchOutcome {
        // `--json` is global: detect it, then strip it so the subcommand spec
        // never sees it (and it is not forwarded to the sibling tool). The
        // command name is `argv[0]`; the forwarded args are `argv[1..]` minus
        // any `--json`.
        let json = argv.iter().any(|a| a == "--json");
        // `argv[1..]` would panic on empty argv, so use `get(1..)`.
        let rest: Vec<String> = argv
            .get(1..)
            .unwrap_or(&[])
            .iter()
            .filter(|a| a.as_str() != "--json")
            .cloned()
            .collect();

        match argv.first().map(String::as_str) {
            None => DispatchOutcome {
                command: String::new(),
                executable: String::new(),
                args: Vec::new(),
                exit_code: 2,
                stdout: String::new(),
                stderr: format!("{PROG}: missing command\n{}", self.usage()),
                json,
            },
            Some(name) => match self.find(name) {
                Some(command) => match command.spec.parse(&rest[..]) {
                    Ok(parsed) => {
                        if parsed.help_requested {
                            return DispatchOutcome {
                                command: command.name.to_string(),
                                executable: String::new(),
                                args: Vec::new(),
                                exit_code: 0,
                                stdout: self.command_help(command),
                                stderr: String::new(),
                                json,
                            };
                        }
                        (command.handler)(command.name, &parsed, config, runner, json)
                    }
                    Err(err) => DispatchOutcome {
                        command: command.name.to_string(),
                        executable: String::new(),
                        args: Vec::new(),
                        exit_code: 2,
                        stdout: String::new(),
                        stderr: format!("{PROG}: {err}\n{}", self.command_help(command)),
                        json,
                    },
                },
                None => DispatchOutcome {
                    command: name.to_string(),
                    executable: String::new(),
                    args: Vec::new(),
                    exit_code: 2,
                    stdout: String::new(),
                    stderr: format!("{PROG}: unknown command `{name}`\n{}", self.usage()),
                    json,
                },
            },
        }
    }

    /// The top-level usage text (also shown on usage errors).
    pub fn usage(&self) -> String {
        let lines: Vec<String> = self
            .commands
            .iter()
            .map(|c| format!("    {:<10} {}", c.name, c.help))
            .collect();
        format!(
            "\
{prog} — the PengyOS meta-CLI

USAGE:
    {prog} <command> [args]

COMMANDS:
{cmds}

OPTIONS:
    -V, --version   Print version and exit
    -h, --help      Print this help and exit

Run `{prog} <command> --help` for command-specific help.
",
            prog = PROG,
            cmds = lines.join("\n"),
        )
    }

    /// Per-command help text.
    pub fn command_help(&self, command: &Command) -> String {
        format!(
            "\
{prog} {name} — {help}

USAGE:
    {prog} {name} [args]

ARGS:
{args}

OPTIONS:
    -h, --help      Print this help and exit
",
            prog = PROG,
            name = command.name,
            help = command.help,
            args = command.spec.format_items(),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::args::ArgSpec;

    fn argv(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn forwards_positionals_and_flags_golden() {
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner::new();
        let outcome = registry.dispatch(
            &argv(&["build", "x86_64", "--verbose"]),
            &config,
            &mut mock,
        );
        assert_eq!(outcome.exit_code, 0);
        assert_eq!(
            mock.last(),
            Some(&CommandSpec {
                executable: "pengyos-build".to_string(),
                args: vec!["x86_64".to_string(), "--verbose".to_string()],
            })
        );
    }

    #[test]
    fn forwards_options_as_equals_form() {
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner::new();
        let outcome = registry.dispatch(
            &argv(&["sbom", "/path/to/rootfs", "--verbose"]),
            &config,
            &mut mock,
        );
        assert_eq!(outcome.exit_code, 0);
        assert_eq!(
            mock.last().unwrap().args,
            vec!["/path/to/rootfs".to_string(), "--verbose".to_string()]
        );
    }

    #[test]
    fn resolves_tool_from_config() {
        let src = "[tools]\nbuild = \"custom-build\"\n";
        let config = Config::from_toml(src).unwrap();
        let registry = Registry::new();
        let mut mock = MockRunner::new();
        registry.dispatch(&argv(&["build", "x86_64"]), &config, &mut mock);
        assert_eq!(mock.last().unwrap().executable, "custom-build");
    }

    #[test]
    fn propagates_runner_stdout() {
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner::ok_stdout("built x86_64\n");
        let outcome = registry.dispatch(&argv(&["build", "x86_64"]), &config, &mut mock);
        assert_eq!(outcome.stdout, "built x86_64\n");
        assert_eq!(outcome.exit_code, 0);
    }

    #[test]
    fn missing_positional_is_usage_error() {
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner::new();
        // `build` with no target: handler never runs.
        let outcome = registry.dispatch(&argv(&["build"]), &config, &mut mock);
        assert_eq!(outcome.exit_code, 2);
        assert_eq!(mock.call_count(), 0);
        assert!(outcome.stderr.contains("missing 1 positional argument"));
    }

    #[test]
    fn unknown_command_is_usage_error() {
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner::new();
        let outcome = registry.dispatch(&argv(&["frobnicate"]), &config, &mut mock);
        assert_eq!(outcome.exit_code, 2);
        assert!(outcome.stderr.contains("unknown command `frobnicate`"));
    }

    #[test]
    fn command_help_short_circuits() {
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner::new();
        let outcome = registry.dispatch(&argv(&["build", "--help"]), &config, &mut mock);
        assert_eq!(outcome.exit_code, 0);
        assert!(outcome.stdout.contains("build a target image"));
        assert_eq!(mock.call_count(), 0);
    }

    #[test]
    fn framework_is_generic_with_fake_command() {
        // The framework must work for any registered command, not just the six.
        let spec = ArgSpec::builder()
            .positional("thing", "a thing")
            .min_positionals(1)
            .build();
        let registry = Registry {
            commands: vec![Command {
                name: "echothing",
                aliases: &["et"],
                help: "echo a thing (test only)",
                spec,
                handler: forward_handler,
            }],
        };
        let config = Config::default();
        let mut mock = MockRunner::new();
        // Dispatch via the alias.
        let outcome = registry.dispatch(&argv(&["et", "hello"]), &config, &mut mock);
        assert_eq!(outcome.exit_code, 0);
        assert_eq!(
            mock.last().unwrap(),
            &CommandSpec {
                executable: "pengyos-echothing".to_string(),
                args: vec!["hello".to_string()],
            }
        );
    }

    // --- argv/dispatch golden per subcommand --------------------------------

    // Each documented subcommand forwards its positionals/flags to the sibling
    // tool with the exact argv the tool expects. These are the golden dispatch
    // assertions the spec (spec.md) calls for.
    #[test]
    fn verify_forwards_positional_and_flag_golden() {
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner::new();
        let outcome = registry.dispatch(
            &argv(&["verify", "/images/rootfs.img", "--verbose"]),
            &config,
            &mut mock,
        );
        assert_eq!(outcome.exit_code, 0);
        assert_eq!(
            mock.last(),
            Some(&CommandSpec {
                executable: "pengyos-verify".to_string(),
                args: vec!["/images/rootfs.img".to_string(), "--verbose".to_string()],
            })
        );
    }

    #[test]
    fn triage_forwards_only_flag_golden() {
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner::new();
        // `triage` takes no positional, only flags.
        let outcome = registry.dispatch(
            &argv(&["triage", "--verbose"]),
            &config,
            &mut mock,
        );
        assert_eq!(outcome.exit_code, 0);
        assert_eq!(
            mock.last(),
            Some(&CommandSpec {
                executable: "pengyos-triage".to_string(),
                args: vec!["--verbose".to_string()],
            })
        );
    }

    #[test]
    fn profile_forwards_only_flag_golden() {
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner::new();
        let outcome = registry.dispatch(
            &argv(&["profile", "--verbose"]),
            &config,
            &mut mock,
        );
        assert_eq!(outcome.exit_code, 0);
        assert_eq!(
            mock.last(),
            Some(&CommandSpec {
                executable: "pengyos-profile".to_string(),
                args: vec!["--verbose".to_string()],
            })
        );
    }

    #[test]
    fn tool_mapping_in_config_is_respected_per_command() {
        // The real sibling tools are named differently from the subcommands
        // (`verify` -> `pengyos-verifier`, `profile` -> `pengyos-hwprofile`,
        // `triage` -> `pengyos-update-triage`); the `[tools]` table resolves
        // each independently.
        let src = "\
[tools]
verify = \"pengyos-verifier\"
profile = \"pengyos-hwprofile\"
triage = \"pengyos-update-triage\"
";
        let config = Config::from_toml(src).unwrap();
        let registry = Registry::new();

        let mut mock = MockRunner::new();
        registry.dispatch(&argv(&["verify", "img"]), &config, &mut mock);
        assert_eq!(mock.last().unwrap().executable, "pengyos-verifier");

        registry.dispatch(&argv(&["profile"]), &config, &mut mock);
        assert_eq!(mock.last().unwrap().executable, "pengyos-hwprofile");

        registry.dispatch(&argv(&["triage"]), &config, &mut mock);
        assert_eq!(mock.last().unwrap().executable, "pengyos-update-triage");
    }

    // --- clear error surfacing ----------------------------------------------

    #[test]
    fn non_zero_exit_annotates_stderr() {
        // A tool that exits non-zero must get a clear, one-line annotation on
        // stderr naming the subcommand, tool, and exit status.
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner {
            outcome: RunOutcome {
                exit_code: 1,
                stdout: String::new(),
                stderr: "boom\n".to_string(),
            },
            ..Default::default()
        };
        let outcome = registry.dispatch(&argv(&["build", "x86_64"]), &config, &mut mock);
        assert_eq!(outcome.exit_code, 1);
        assert!(
            outcome.stderr.contains("pengyos build: `pengyos-build` exited with status 1"),
            "missing error annotation, stderr: {}",
            outcome.stderr
        );
        // The tool's own stderr is preserved, after the annotation.
        assert!(outcome.stderr.ends_with("boom\n"));
    }

    #[test]
    fn success_exit_leaves_stderr_untouched() {
        // A successful tool must not get any annotation — its stderr passes
        // through verbatim.
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner {
            outcome: RunOutcome {
                exit_code: 0,
                stdout: String::new(),
                stderr: "a warning\n".to_string(),
            },
            ..Default::default()
        };
        let outcome = registry.dispatch(&argv(&["build", "x86_64"]), &config, &mut mock);
        assert_eq!(outcome.exit_code, 0);
        assert_eq!(outcome.stderr, "a warning\n");
        assert!(!outcome.stderr.contains("exited with status"));
    }

    #[test]
    fn annotation_is_prepended_before_tool_stderr() {
        // The annotation must come first so it is the first thing a caller sees.
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner {
            outcome: RunOutcome {
                exit_code: 3,
                stdout: String::new(),
                stderr: "tool detail line\n".to_string(),
            },
            ..Default::default()
        };
        let outcome = registry.dispatch(&argv(&["verify", "img"]), &config, &mut mock);
        let expected_first = "pengyos verify: `pengyos-verify` exited with status 3\n";
        assert!(
            outcome.stderr.starts_with(expected_first),
            "stderr should start with the annotation, got: {}",
            outcome.stderr
        );
        assert!(outcome.stderr.contains("tool detail line"));
    }

    #[test]
    fn annotation_names_the_configured_tool() {
        // When the tool is resolved from config, the annotation must name that
        // tool, not the subcommand.
        let src = "[tools]\nbuild = \"custom-build\"\n";
        let config = Config::from_toml(src).unwrap();
        let registry = Registry::new();
        let mut mock = MockRunner {
            outcome: RunOutcome {
                exit_code: 2,
                stdout: String::new(),
                stderr: String::new(),
            },
            ..Default::default()
        };
        let outcome = registry.dispatch(&argv(&["build", "x86_64"]), &config, &mut mock);
        assert_eq!(outcome.exit_code, 2);
        assert!(
            outcome.stderr.contains("pengyos build: `custom-build` exited with status 2"),
            "annotation should name the configured tool, got: {}",
            outcome.stderr
        );
    }

    #[test]
    fn quiet_tool_failure_is_still_surfaced() {
        // A tool that exits non-zero with NO stderr must still surface a clear
        // annotation, otherwise the failure is invisible.
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner {
            outcome: RunOutcome {
                exit_code: 1,
                stdout: String::new(),
                stderr: String::new(),
            },
            ..Default::default()
        };
        let outcome = registry.dispatch(&argv(&["sbom", "rootfs"]), &config, &mut mock);
        assert_eq!(outcome.exit_code, 1);
        assert!(
            outcome.stderr.contains("pengyos sbom: `pengyos-sbom` exited with status 1"),
            "a silent tool failure must still be surfaced, got: {}",
            outcome.stderr
        );
    }

    // --- --json schema -------------------------------------------------------

    // The `--json` schema is stable across subcommands: every subcommand emits the
    // same top-level object (see crate::json). These tests pin that shape.
    #[test]
    fn outcome_to_json_schema_is_stable() {
        let outcome = DispatchOutcome {
            command: "build".to_string(),
            executable: "pengyos-build".to_string(),
            args: vec!["x86_64".to_string(), "--verbose".to_string()],
            exit_code: 0,
            stdout: "built x86_64\n".to_string(),
            stderr: String::new(),
            json: true,
        };
        let json = outcome_to_json(&outcome);
        assert_eq!(
            json,
            r#"{"command":"build","executable":"pengyos-build","args":["x86_64","--verbose"],"exit_code":0,"stdout":"built x86_64\n","stderr":"","ok":true}"#
        );
    }

    #[test]
    fn json_flag_is_stripped_from_forwarded_args() {
        // `--json` is a global flag: it selects the schema but is never forwarded
        // to the sibling tool.
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner::ok_stdout("built\n");
        registry.dispatch(&argv(&["build", "x86_64", "--verbose", "--json"]), &config, &mut mock);
        assert_eq!(
            mock.last().unwrap().args,
            vec!["x86_64".to_string(), "--verbose".to_string()],
            "`--json` must not be forwarded to the tool"
        );
    }

    #[test]
    fn json_mode_emits_document_and_captures_stdout() {
        // In JSON mode the visible stdout is the serialized document; the tool's
        // raw stdout is captured *inside* it, and stderr stays empty.
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner::ok_stdout("built x86_64\n");
        let outcome = registry.dispatch(&argv(&["build", "x86_64", "--json"]), &config, &mut mock);
        assert_eq!(outcome.exit_code, 0);
        assert_eq!(outcome.stderr, "");
        assert_eq!(
            outcome.stdout,
            r#"{"command":"build","executable":"pengyos-build","args":["x86_64"],"exit_code":0,"stdout":"built x86_64\n","stderr":"","ok":true}"#
        );
    }

    #[test]
    fn json_mode_captures_raw_stderr_not_annotated() {
        // In JSON mode the tool's raw stderr is captured verbatim — no text-mode
        // annotation is prepended (the exit status lives in `exit_code`/`ok`).
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner {
            outcome: RunOutcome {
                exit_code: 1,
                stdout: String::new(),
                stderr: "boom\n".to_string(),
            },
            ..Default::default()
        };
        let outcome = registry.dispatch(&argv(&["build", "x86_64", "--json"]), &config, &mut mock);
        assert_eq!(outcome.exit_code, 1);
        assert_eq!(outcome.stderr, "");
        assert!(
            outcome
                .stdout
                .contains(&format!("\"stderr\":\"boom\\n\"")),
            "raw stderr should be captured verbatim, got: {}",
            outcome.stdout
        );
        assert!(
            !outcome.stdout.contains("exited with status"),
            "JSON mode must not add a text annotation, got: {}",
            outcome.stdout
        );
        assert!(outcome.stdout.contains(r#""ok":false"#));
    }

    #[test]
    fn json_mode_reports_missing_tool_via_exit_code() {
        // A missing tool (exit 127) is reported through the document's
        // `exit_code`/`stderr`, not a text annotation, so scripts can read it.
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner {
            outcome: RunOutcome {
                exit_code: 127,
                stdout: String::new(),
                stderr: format!("{PROG}: command not found: pengyos-build"),
            },
            ..Default::default()
        };
        let outcome = registry.dispatch(&argv(&["build", "x86_64", "--json"]), &config, &mut mock);
        assert_eq!(outcome.exit_code, 127);
        assert_eq!(outcome.stderr, "");
        assert!(outcome.stdout.contains(r#""exit_code":127"#));
        assert!(outcome.stdout.contains(r#""ok":false"#));
        assert!(
            outcome.stdout.contains("command not found: pengyos-build"),
            "the tool's stderr should be captured in the document, got: {}",
            outcome.stdout
        );
    }

    #[test]
    fn help_short_circuits_even_with_json_flag() {
        // `--help` wins over `--json`: the human help is printed, not JSON.
        let registry = Registry::new();
        let config = Config::default();
        let mut mock = MockRunner::new();
        let outcome = registry.dispatch(&argv(&["build", "--json", "--help"]), &config, &mut mock);
        assert_eq!(outcome.exit_code, 0);
        assert!(outcome.stdout.contains("build a target image"));
        assert!(!outcome.stdout.contains(r#""command":"#));
        assert_eq!(mock.call_count(), 0);
    }
}
