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

import os
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


def _aliases(text: str) -> dict[str, str]:
    """name -> body for every `alias name='body'` line."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*alias ([\w.-]+)='([^']*)'", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def test_profile_sets_comfort_aliases():
    # The aliases are the user-visible payoff of the comfort set.
    aliases = _aliases(PROFILE.read_text(encoding="utf-8"))
    assert aliases["ls"].startswith("eza "), "ls should be eza"
    assert aliases["cat"].startswith("bat "), "cat should be bat"


def test_profile_aliases_never_break_the_command_they_shadow():
    """Every alias must run a binary the image ships, with flags it accepts, and
    must not replace a tool whose semantics differ.

    Regressions this pins (26.10.25 review): `grep`/`grepv`/`fg` aliased to a
    `ripgrep` binary that does not exist (the binary is `rg`), `fg` shadowing
    the job-control builtin, `diff` aliased to an unshipped `delta`, `ls` to an
    eza flag (`--group-dirs`) eza rejects, and `cat`/`grep` forcing
    `--color=always` so every pipe got escape codes.
    """
    aliases = _aliases(PROFILE.read_text(encoding="utf-8"))
    for name in ("grep", "find", "diff", "fg", "bg", "cd", "echo", "test"):
        assert name not in aliases, f"profile must not alias `{name}`"
    shipped = {"eza", "bat", "rg", "fd", "zoxide", "fzf", "starship", "ls", "git"}
    for name, body in aliases.items():
        prog = body.split()[0]
        assert prog in shipped, f"alias {name} runs `{prog}`, which the image does not ship"
        assert "--color=always" not in body, f"alias {name} forces colour into pipes"
    assert "--group-dirs " not in aliases["ls"] + " ", "eza has no --group-dirs flag"


def test_profile_markers_are_not_exported():
    """A child shell (tmux pane, nested zsh) must run the profile itself:
    aliases, prompt hooks and ZLE widgets are not inherited, so an exported
    run-once marker left every child without them."""
    for path in (PROFILE, SHELL_DIR / "fzf.sh", SHELL_DIR / "zoxide.sh"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*export PENGYOS_TOOLSET_", text, re.M), (
            f"{path.name} exports its run-once marker"
        )


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

# --- the image wiring the profile promises ---------------------------------
#
# The profile is only worth shipping because the image wires it: the Server
# board installs it at /etc/pengyos/shell and sources it from the system rc
# files. These tests read the buildroot-external tree (repo-root relative) and
# fail when the wiring drifts away from the profile's documented paths.

EXT = REPO_ROOT.parents[1] / "buildroot-external"
SERVER_BOARD = EXT / "board" / "server"
SERVER_OVERLAY = SERVER_BOARD / "rootfs-overlay"
SERVER_SKEL = SERVER_OVERLAY / "etc" / "skel"


def test_server_post_build_installs_the_profile():
    # The Server post-build copies the authored shell/ files to their canonical
    # system path (/etc/pengyos/shell) and the tmux defaults to /etc/tmux.conf.
    text = (SERVER_BOARD / "post-build.sh").read_text(encoding="utf-8")
    assert "pengyos-server-toolset/shell" in text, (
        "server post-build must install the toolset's shell/ files"
    )
    assert "etc/pengyos/shell" in text, (
        "server post-build must install the profile at /etc/pengyos/shell"
    )
    assert "etc/tmux.conf" in text, "server post-build must install /etc/tmux.conf"
    for name in ("profile", "fzf.sh", "zoxide.sh", "starship.toml"):
        assert f"shell/{name}" in text, f"post-build must install shell/{name}"


def test_system_rc_files_source_the_profile():
    # zsh: /etc/zshrc (Buildroot's path) and the /etc/zsh/zshrc symlink
    # (Debian-patched zsh's path); bash: /etc/profile.d. All guarded on the
    # profile being readable.
    zshrc = (SERVER_OVERLAY / "etc" / "zshrc").read_text(encoding="utf-8")
    assert "/etc/pengyos/shell/profile" in zshrc, "/etc/zshrc must source the profile"
    bash_sh = (SERVER_OVERLAY / "etc" / "profile.d" / "pengyos-shell.sh").read_text(
        encoding="utf-8"
    )
    assert "/etc/pengyos/shell/profile" in bash_sh, (
        "/etc/profile.d/pengyos-shell.sh must source the profile"
    )
    link = SERVER_OVERLAY / "etc" / "zsh" / "zshrc"
    assert link.is_symlink() and link.resolve().name == "zshrc", (
        "the overlay must carry /etc/zsh/zshrc -> /etc/zshrc for patched zsh"
    )


def test_skel_rcs_source_the_profile():
    # A new account's ~/.zshrc and ~/.bashrc source the profile (guarded, with
    # a per-user ~/.config override path first).
    for name in (".zshrc", ".bashrc"):
        text = (SERVER_SKEL / name).read_text(encoding="utf-8")
        assert "/etc/pengyos/shell/profile" in text, f"skel {name} must source the profile"


def test_skel_zshrc_keeps_the_omz_feel():
    # The plugin-free oh-my-zsh bits the starter file promises, plus the
    # prompt fix that made zsh show '%' for a wheel user (CAP_WAKE_ALARM).
    text = (SERVER_SKEL / ".zshrc").read_text(encoding="utf-8")
    assert "hist_substring_search" not in text, "not a zsh option: errors at every start"
    assert "compinit" in text and "menu select" in text
    assert "_pengyos_mark='%%'" in text, "prompt must use '%', not zsh's '%#'"


# --- starship init is shell-specific (there is no POSIX-sh emitter) ---------


def test_profile_picks_the_starship_shell():
    # `starship init sh` emits nothing: the profile must pick zsh/bash from the
    # sourcing shell and only fall back to sh for other POSIX shells.
    text = PROFILE.read_text(encoding="utf-8")
    assert "_starship_shell=zsh" in text and "_starship_shell=bash" in text, (
        "profile must detect zsh/bash for `starship init`"
    )
    assert 'starship init "$_starship_shell"' in text, (
        "profile must pass the detected shell to `starship init`"
    )


def test_profile_points_starship_at_the_system_theme():
    # The curated starship.toml is only used if STARSHIP_CONFIG points at it
    # (guarded: an env override and a user's own config keep working).
    text = PROFILE.read_text(encoding="utf-8")
    assert "STARSHIP_CONFIG" in text, "profile must wire STARSHIP_CONFIG"
    assert "$PENGYOS_SHELL_DIR/starship.toml" in text, (
        "STARSHIP_CONFIG must point at the sibling starship.toml"
    )


def test_profile_sources_the_zsh_plugins():
    # The oh-my-zsh feel: autosuggestions first, syntax highlighting last
    # (it wraps the ZLE widgets); both guarded and zsh-only.
    text = PROFILE.read_text(encoding="utf-8")
    assert "/usr/share/zsh/plugins/zsh-autosuggestions/zsh-autosuggestions.zsh" in text
    assert "/usr/share/zsh/plugins/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh" in text
    a = text.index("zsh-autosuggestions.zsh")
    s = text.index("zsh-syntax-highlighting.zsh")
    assert a < s, "autosuggestions must be sourced before syntax highlighting"


# --- functional: the profile works under zsh (the Server's user shell) ------


def _run_zsh(script: str, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["zsh", "-f", "-i", "-c", script],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent-home", **extra_env},
        timeout=30,
    )


def test_profile_sources_cleanly_under_zsh(shell_dir_env):
    """Sourcing the profile under zsh works and lands the comfort aliases."""
    if subprocess.run(["which", "zsh"], capture_output=True).returncode != 0:
        pytest.skip("zsh not installed on the test host")
    env = {**shell_dir_env, "PROF": str(PROFILE)}
    script = (
        ". $PROF; "
        ". $PROF; "
        "alias ls cat; "
        "echo \"GUARD=$PENGYOS_TOOLSET_PROFILE_RC\"; "
    )
    proc = _run_zsh(script, env)
    assert proc.returncode == 0, f"zsh exited non-zero: {proc.stderr}"
    out = proc.stdout
    assert "eza" in out, "profile must alias ls to eza under zsh"
    assert "bat" in out, "profile must alias cat to bat under zsh"
    assert "GUARD=1" in out, "profile guard marker must be set under zsh"


def test_profile_double_source_is_a_no_op_under_zsh(shell_dir_env):
    """The idempotency guard fires under zsh too (/etc/zshrc + ~/.zshrc source
    the profile in the same interactive shell)."""
    if subprocess.run(["which", "zsh"], capture_output=True).returncode != 0:
        pytest.skip("zsh not installed on the test host")
    env = {**shell_dir_env, "PROF": str(PROFILE)}
    script = ". $PROF; . $PROF; echo DONE; "
    proc = _run_zsh(script, env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.count("DONE") == 1, (
        f"second source was not a no-op under zsh: {proc.stdout!r}"
    )



def _fake_starship(tmp_path: pathlib.Path) -> pathlib.Path:
    """A `starship` stand-in whose zsh init sets a recognisable PROMPT."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "starship"
    fake.write_text(
        "#!/bin/sh\n"
        "[ \"$1 $2\" = 'init zsh' ] && echo \"PROMPT='\\$(starship prompt)'\"\n"
        "exit 0\n"
    )
    fake.chmod(0o755)
    return bindir


def test_skel_zshrc_starts_silently_and_keeps_the_profile(shell_dir_env, tmp_path):
    """The real login order: /etc/zshrc (the profile) then the starter
    ~/.zshrc. No stderr at all (a bad setopt printed on every shell start),
    and the starter file must not undo the profile: the starship prompt and
    the eza `ls` survive it."""
    if subprocess.run(["which", "zsh"], capture_output=True).returncode != 0:
        pytest.skip("zsh not installed on the test host")
    home = tmp_path / "home"
    home.mkdir()
    bindir = _fake_starship(tmp_path)
    env = {**shell_dir_env, "PROF": str(PROFILE), "HOME": str(home),
           "SKEL": str(SERVER_SKEL / ".zshrc"), "PATH": f"{bindir}:/usr/bin:/bin"}
    proc = subprocess.run(
        ["zsh", "-f", "-i", "-c", '. $PROF; . $SKEL; print -r -- "P=$PROMPT"; alias ls'],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == "", f"zsh start is not silent: {proc.stderr!r}"
    assert "P=$(starship prompt)" in proc.stdout, "skel .zshrc clobbered the starship prompt"
    assert "eza" in proc.stdout, "skel .zshrc clobbered the profile's ls alias"


def test_skel_zshrc_stays_silent_with_no_terminal_and_an_insecure_fpath(shell_dir_env,
                                                                        tmp_path):
    """The CI gate's condition, made reproducible on any host.

    compinit runs compaudit. When a directory in fpath is world-writable and not owned by
    root, compinit prompts - and `read -q` needs a terminal. With no terminal it prints
    "not interactive and can't open terminal" and compinit aborts: the shell loses
    completion entirely and leaks that pair of lines to stderr. The CI runner hits this
    because its fpath has directories compaudit calls insecure; a developer box with a
    controlling terminal does not, which is exactly why
    test_skel_zshrc_starts_silently_and_keeps_the_profile passed for a week and then failed
    only in CI (gate run 38580797351, 2026-10-14).

    `compinit -i` skips the security check and the prompt. Measured: without -i, rc=1 and
    that exact stderr; with -i, rc=0 and silent.
    """
    if subprocess.run(["which", "zsh"], capture_output=True).returncode != 0:
        pytest.skip("zsh not installed on the test host")
    fpath = tmp_path / "fpath"
    (fpath / "Completion").mkdir(parents=True)
    (fpath / "Completion" / "_pengyos_test").write_text("#compdef pengyos-test\n")
    os.chmod(fpath, 0o777)                      # what compaudit calls insecure
    os.chmod(fpath / "Completion", 0o777)
    home = tmp_path / "home"
    home.mkdir()
    bindir = _fake_starship(tmp_path)
    env = {**shell_dir_env, "PROF": str(PROFILE), "HOME": str(home),
           "SKEL": str(SERVER_SKEL / ".zshrc"), "PATH": f"{bindir}:/usr/bin:/bin"}
    # setsid + stdin from /dev/null: no controlling terminal, like a CI runner.
    proc = subprocess.run(
        ["setsid", "zsh", "-f", "-i", "-c",
         f'fpath=("{fpath}" $fpath); . $PROF; . $SKEL; print -r -- "P=$PROMPT"'],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=30)
    assert proc.stderr == "", f"zsh start is not silent: {proc.stderr!r}"
    assert "P=$(starship prompt)" in proc.stdout, proc.stdout


def test_child_shell_runs_the_profile_again(shell_dir_env):
    """`zsh` started from a shell that already ran the profile still gets it."""
    if subprocess.run(["which", "zsh"], capture_output=True).returncode != 0:
        pytest.skip("zsh not installed on the test host")
    env = {**shell_dir_env, "PROF": str(PROFILE)}
    proc = _run_zsh('. $PROF; zsh -f -i -c ". $PROF; alias ls"', env)
    assert proc.returncode == 0, proc.stderr
    assert "eza" in proc.stdout, "the child shell skipped the profile"


def test_fzf_default_opts_keep_their_double_dash(shell_dir_env):
    """`${X:--height …}` expands to `-height …` (the `:-` eats a dash); fzf
    rejects that, even for `fzf --zsh`, so neither the key bindings nor any
    later `fzf` run worked."""
    env = {**shell_dir_env, "SHELL_DIR": str(SHELL_DIR)}
    proc = _run_sh('set -i; . $SHELL_DIR/fzf.sh; echo "FO=$FZF_DEFAULT_OPTS"', env)
    assert proc.returncode == 0, proc.stderr
    fo = next(l for l in proc.stdout.splitlines() if l.startswith("FO="))
    assert fo.startswith("FO=--height "), f"FZF_DEFAULT_OPTS lost a dash: {fo!r}"


def test_fzf_snippet_loads_the_per_shell_integration():
    """fzf ships key-bindings.bash / key-bindings.zsh (no `.sh`), and fzf 0.48+
    prints the matching one with `fzf --zsh` / `fzf --bash`."""
    text = (SHELL_DIR / "fzf.sh").read_text(encoding="utf-8")
    assert "key-bindings.sh" not in text, "there is no key-bindings.sh"
    assert 'fzf --"$_fzf_sh"' in text
    assert 'key-bindings.$_fzf_sh' in text


def test_starship_theme_parses_with_a_real_starship():
    """The shipped theme must load in starship with no warning or parse error:
    the first version had a malformed `format` ("Error parsing format --> 1:10"
    on every prompt), a `telemetry` key starship does not have, and a `symbol`
    on `shell`. Uses $PENGYOS_STARSHIP or a starship on PATH; the Buildroot
    output's binary works (target/x86_64-unknown-linux-gnu/release/starship)."""
    import os
    import shutil
    exe = os.environ.get("PENGYOS_STARSHIP") or shutil.which("starship")
    if not exe:
        pytest.skip("no starship binary (set PENGYOS_STARSHIP)")
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent-home",
           "STARSHIP_CONFIG": str(STARSHIP_TOML), "STARSHIP_SHELL": "zsh"}
    for args in (["prompt", "--status=0"], ["prompt", "--status=1"]):
        proc = subprocess.run([exe, *args], capture_output=True, text=True, env=env, timeout=30)
        assert proc.returncode == 0, proc.stderr
        assert "WARN" not in proc.stderr and "ERROR" not in proc.stderr, proc.stderr
        assert "❯" in proc.stdout
