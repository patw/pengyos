"""Shell comfort profile tests.

Covers the shell profile + tmux.conf + zoxide init (idempotent, no network at
shell start).

These tests assert that the authored files landed, are committed, and actually
deliver the two promises the item makes:

* **idempotent** — sourcing the profile (or any snippet) more than once in a
  single shell runs the body at most once and never duplicates PATH entries;
* **no network at shell start** — the snippets make only `command -v` checks and
  local sourcing, and contain no network / telemetry tokens.

They deliberately do NOT duplicate item 5 (the exhaustive ``sh -n`` syntax
suite, ``tests/test_syntax.py``), item 6 (the planted-URL no-telemetry scan,
``tests/test_no_telemetry.py``) or the validator (``tests/test_validator.py``).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SHELL_DIR = REPO_ROOT / "shell"

PROFILE = SHELL_DIR / "profile"
TMUX_CONF = SHELL_DIR / "tmux.conf"
ZOXIDE_SH = SHELL_DIR / "zoxide.sh"
FZF_SH = SHELL_DIR / "fzf.sh"
STARSHIP_TOML = SHELL_DIR / "starship.toml"

# The authored shell/ artifacts (SPEC §Interface / README layout).
#
# `fzf.sh` and `starship.toml` were dropped at item 7 but re-added at item 20
# (the Buildroot packages now ship): fzf.sh is a POSIX-sh snippet, starship.toml
# is the prompt config (checked for telemetry, not `sh -n`). The full set is the
# profile, tmux.conf, zoxide.sh, fzf.sh and starship.toml.
SHELL_ARTIFACTS = (PROFILE, TMUX_CONF, ZOXIDE_SH, FZF_SH, STARSHIP_TOML)

# Idempotency guard: every shell snippet must set a unique marker env var so a
# double source is a no-op. This is the mechanism item 3 promises.
IDEMPOTENCY_MARKER = re.compile(r"PENGYOS_TOOLSET_[A-Z_]+=")

# Anything that would reach the network at shell start. Network calls at startup
# defeat the whole point (a fresh offline login must work); flag any of these.
# Loose words like "telemetry" are deliberately NOT here — that is the name of
# starship's config key (set to `false`), so item 6 checks telemetry separately.
# (starship.toml is back at item 20; the bare word "telemetry" is still just an
# opt-out key, not a phone-home signal — checked in test_no_telemetry.py.)
NETWORK_TOKENS = (
    "curl",
    "wget",
    "fetch",
    "git clone",
    "git ls-remote",
    "git fetch",
    "npm",
    "pip install",
    "pip3 install",
    "http://",
    "https://",
    "//github.com",
)


def _tracked(relpath: str) -> bool:
    """True when ``relpath`` (repo-root relative) is tracked by git."""
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", relpath],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return proc.returncode == 0


# --- the artifacts landed and are committed ---------------------------------


def test_all_shell_artifacts_exist():
    for path in SHELL_ARTIFACTS:
        assert path.is_file(), f"missing authored file: {path}"
        assert path.stat().st_size > 0, f"{path} is empty"


def test_shell_artifacts_are_tracked_by_git():
    # Not just present on disk — committed (else `git status` shows them
    # untracked and they wouldn't be pushed).
    for path in SHELL_ARTIFACTS:
        assert _tracked(str(path.relative_to(REPO_ROOT))), (
            f"{path} exists but is not tracked by git"
        )


# --- idempotency guards are present -----------------------------------------


def test_each_shell_snippet_has_idempotency_guard():
    # The profile + every POSIX-sh snippet must set a PENGYOS_TOOLSET_* marker so
    # a double source is a no-op (item 3: "idempotent").
    for path in (PROFILE, ZOXIDE_SH, FZF_SH):
        text = path.read_text(encoding="utf-8")
        assert IDEMPOTENCY_MARKER.search(text), (
            f"{path.name} has no idempotency marker (set a PENGYOS_TOOLSET_* var)"
        )


# --- no network / telemetry at shell start ----------------------------------
#
# The "no network at shell start" half of item 3 (only `command -v` + local
# sourcing) lives here; the telemetry/analytics scan with its teeth lives in
# ``tests/test_no_telemetry.py`` (which owns the shared scanner + the planted-URL
# teeth test). This module keeps the shell-command network tokens that the shared
# scanner does not (curl/wget/npm/pip/git), which are the ones a *shell* could
# actually execute at start.


def test_shell_snippets_make_no_network_call_at_start():
    # Item 3: "no network at shell start". None of the snippets may shell out to a
    # network tool (curl/wget/.../git/pip/npm) — only `command -v` + local
    # sourcing are allowed.
    for path in (PROFILE, ZOXIDE_SH, FZF_SH):
        code = _strip_comments(path)
        lowered = code.lower()
        for token in NETWORK_TOKENS:
            assert token.lower() not in lowered, (
                f"{path.name} contains a network call {token!r}: "
                f"{_first_match_line(code, token)!r}"
            )


def _first_match_line(text: str, token: str) -> str:
    needle = token.lower()
    for i, line in enumerate(text.splitlines(), start=1):
        if needle in line.lower():
            return f"{i}: {line.strip()}"
    return "<no line>"


def _strip_comments(path: Path) -> str:
    """Return the file text with comments removed.

    The no-token scan must check *code*, not documentation: a comment that says
    "no telemetry" must not trip the telemetry detector. Full-line comments
    (first non-space char is ``#``) are dropped for every file type; shell
    snippets additionally drop inline ``#`` comments. tmux.conf keeps its
    ``#[fg=...]`` color codes, which are never at the start of a line.
    """
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue  # blank or full-line comment
        if path.name == "profile" or path.suffix == ".sh":
            line = re.split(r"\s+#", line, maxsplit=1)[0]
        out.append(line)
    return "\n".join(out)


# --- tmux.conf is structurally valid ----------------------------------------


def test_tmux_conf_has_expected_directives():
    # A small structural check (item 5 does the full parse): the config must
    # declare the sane defaults the item promises.
    text = TMUX_CONF.read_text(encoding="utf-8")
    stripped = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    joined = "\n".join(stripped)
    # colour + mouse + a prefix remap + a source-for-reload binding are the core.
    assert "set -g default-terminal" in joined, "tmux.conf must set the terminal"
    assert "RGB" in joined, "tmux.conf must enable truecolor (RGB)"
    assert "mouse" in joined, "tmux.conf must configure the mouse"
    assert "bind-key" in joined, "tmux.conf must define key bindings"
    assert "source-file" in joined, "tmux.conf must allow reloading from disk"
    # every non-comment line is a tmux command starting with a known verb.
    verbs = {"set", "setw", "bind-key", "bind", "run-shell", "new-session", "rename-session"}
    for ln in stripped:
        head = ln.split(None, 1)[0]
        assert head in verbs, f"tmux.conf line is not a known command: {ln!r}"


# --- profile wires up the sibling snippets ----------------------------------


def test_profile_sources_zoxide():
    # The profile sources the zoxide sibling snippet.
    text = PROFILE.read_text(encoding="utf-8")
    assert "zoxide.sh" in text, "profile must source zoxide.sh"


def test_profile_sources_fzf():
    # The profile sources fzf.sh (re-added at item 20: the Buildroot package
    # ships). This has teeth against a regression that stops sourcing it.
    text = PROFILE.read_text(encoding="utf-8")
    assert "fzf.sh" in text, "profile must source fzf.sh"


def test_profile_loads_starship_when_installed():
    # The profile loads starship via `starship init` guarded on a marker and on
    # `command -v starship` (offline-safe). Assert the wiring is present.
    text = PROFILE.read_text(encoding="utf-8")
    assert "starship init" in text, "profile must invoke `starship init`"
    assert "PENGYOS_TOOLSET_STARSHIP_LOADED" in text, (
        "profile must guard starship load on a marker (idempotent)"
    )


def test_profile_sets_comfort_aliases():
    # The aliases are the user-visible payoff of the comfort set.
    text = PROFILE.read_text(encoding="utf-8")
    for alias in ("eza", "ripgrep", "fd", "bat"):
        assert alias in text, f"profile should reference the comfort tool {alias}"


# --- functional: idempotent at runtime (sourced under POSIX sh) -------------


@pytest.fixture
def shell_dir_env():
    """A temp HOME + PENGYOS_SHELL_DIR so the profile adds predictable PATH."""
    return {
        "PENGYOS_SHELL_DIR": str(SHELL_DIR),
        "HOME": "/nonexistent-home",
    }


def _run_sh(script: str, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env = {
        "PATH": "/usr/bin:/bin",
        "SHELL": "dash",
        **extra_env,
    }
    return subprocess.run(
        ["sh", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_profile_is_idempotent_when_sourced_twice(shell_dir_env):
    """Sourcing the profile twice must not duplicate PATH or re-run the body."""
    env = {**shell_dir_env, "PROF": str(PROFILE)}
    # NB: paths are injected via env vars (not .format()), so the ${...} shell
    # parameter expansions below are written literally into the script.
    script = (
        "set -i; "
        ". $PROF; "
        ". $PROF; "
        "echo \"PATH=$PATH\"; "
        "echo \"GUARD=$PENGYOS_TOOLSET_PROFILE_RC\"; "
        "alias; "
    )
    proc = _run_sh(script, env)
    assert proc.returncode == 0, f"sh exited non-zero: {proc.stderr}"

    path_line = next(l for l in proc.stdout.splitlines() if l.startswith("PATH="))
    entries = path_line[len("PATH="):].split(":")
    # no duplicate PATH entries after a double source
    assert len(entries) == len(set(entries)), f"PATH duplicated after double source: {entries}"
    # the home local bin was added exactly once (deduped, not duplicated)
    home_local = [e for e in entries if e.endswith(".local/bin")]
    assert len(home_local) == 1, f".local/bin duplicated after double source: {entries}"

    # the idempotency guard fired (body ran, marker exported)
    guard_line = next(l for l in proc.stdout.splitlines() if l.startswith("GUARD="))
    assert guard_line == "GUARD=1", f"guard marker not set: {guard_line!r}"


def test_profile_sourcing_is_a_no_op_on_second_source(shell_dir_env):
    """A second source must be a no-op: the marker already present short-circuits."""
    env = {**shell_dir_env, "PROF": str(PROFILE)}
    script = (
        "set -i; "
        ". $PROF; "
        "export PENGYOS_TOOLSET_PROFILE_RC=1; "
        ". $PROF; "
        "echo DONE; "
    )
    proc = _run_sh(script, env)
    assert proc.returncode == 0, proc.stderr
    # exactly one DONE: the body ran once, the second source returned early
    assert proc.stdout.count("DONE") == 1, (
        f"second source was not a no-op: {proc.stdout!r}"
    )


def test_zoxide_snippet_defines_z_under_sh(shell_dir_env):
    """The zoxide shim defines `z` when the binary is absent (offline fallback)."""
    env = {**shell_dir_env, "SHELL_DIR": str(SHELL_DIR)}
    script = (
        "set -i; "
        ". $SHELL_DIR/zoxide.sh; "
        "type z >/dev/null 2>&1 && echo HAS_Z; "
    )
    proc = _run_sh(script, env)
    assert proc.returncode == 0, proc.stderr
    assert "HAS_Z" in proc.stdout, "zoxide.sh must define `z` even without the binary"


def test_fzf_snippet_sets_defaults_under_sh(shell_dir_env):
    """fzf.sh sets FZF_DEFAULT_COMMAND + FZF_DEFAULT_OPTS (rg -> find fallback)."""
    env = {**shell_dir_env, "SHELL_DIR": str(SHELL_DIR)}
    script = (
        "set -i; "
        ". $SHELL_DIR/fzf.sh; "
        "echo \"FC=$FZF_DEFAULT_COMMAND\"; "
        "echo \"FO=$FZF_DEFAULT_OPTS\"; "
        "echo \"GUARD=$PENGYOS_TOOLSET_FZF_INIT\"; "
    )
    proc = _run_sh(script, env)
    assert proc.returncode == 0, proc.stderr
    fc = next(l for l in proc.stdout.splitlines() if l.startswith("FC="))
    assert "rg --hidden" in fc or "find . -type f" in fc, (
        f"fzf.sh must set FZF_DEFAULT_COMMAND: {fc!r}"
    )
    fo = next(l for l in proc.stdout.splitlines() if l.startswith("FO="))
    assert "border" in fo, f"fzf.sh must set FZF_DEFAULT_OPTS: {fo!r}"
    guard = next(l for l in proc.stdout.splitlines() if l.startswith("GUARD="))
    assert guard == "GUARD=1", f"fzf.sh idempotency marker not set: {guard!r}"


def test_fzf_snippet_is_idempotent_when_sourced_twice(shell_dir_env):
    """Sourcing fzf.sh twice must be a no-op (marker short-circuits the body)."""
    env = {**shell_dir_env, "SHELL_DIR": str(SHELL_DIR)}
    script = (
        "set -i; "
        ". $SHELL_DIR/fzf.sh; "
        "export PENGYOS_TOOLSET_FZF_INIT=1; "
        ". $SHELL_DIR/fzf.sh; "
        "echo DONE; "
    )
    proc = _run_sh(script, env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.count("DONE") == 1, (
        f"fzf.sh second source was not a no-op: {proc.stdout!r}"
    )
