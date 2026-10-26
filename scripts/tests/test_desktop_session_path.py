"""Tests for the Desktop session PATH (`start-cosmic`).

greetd's session worker hands the session a minimal environment, so `PATH`
reached start-cosmic as `/usr/bin` alone -- not the `/usr/local/bin:/usr/bin` a
login shell gets. A session started by a system service never sources
profile.d, and the comfort profile deliberately returns early for
non-interactive shells (`case "$-" in *i*)`), so the per-user bin dirs it adds
existed for a terminal but not for a GUI app or for anything an app forks.

Found 2026-10-26 on a Desktop box: an agent harness launched from the panel ran
with `PATH=/usr/bin`, so `uv` (in `~/.local/bin`) was "command not found" and
every `uv run` skill invocation in its child shells failed -- while the same
command worked in a terminal on the same machine. `gh` was the visible symptom
and `uv` the expensive one.

The fix must (a) prepend the same dirs in the same order as the comfort
profile's `add_path`, so a terminal and a GUI app resolve the same binary;
(b) keep every existing entry; (c) be idempotent, because the private-bus
fallback re-execs this script; and (d) never produce an empty PATH component,
which means "the current directory".

The setup block is exercised for real: the script's own text is run with only
the tail that launches the session replaced, and the test fails loudly if either
anchor moves, so it can never quietly stop testing the file.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

import re
import shlex
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = (REPO / "buildroot-external/board/desktop/rootfs-overlay"
          / "usr/libexec/pengyos/start-cosmic")
PROFILE = REPO / "tools/pengyos-server-toolset/shell/profile"

# Everything from here down starts the session: ssh-agent, the autostart and
# target helpers, the keyring, then the compositor. Cut at it -- a test runner
# must not be able to start a real session or a portal on the machine it runs on.
CUT_AT = 'eval "$(ssh-agent -s)" >/dev/null\n'
EXEC_LINE = "exec /usr/bin/cosmic-session /usr/bin/cosmic-comp\n"


def session_path(tmp_path: Path, path_in: str) -> tuple[Path, str]:
    """Run start-cosmic's real setup block; return (fake HOME, exported PATH)."""
    src = SCRIPT.read_text()
    assert src.count(CUT_AT) == 1, (
        "the session-launch tail moved: update CUT_AT here, do not let this test "
        "silently cut in the wrong place")
    assert EXEC_LINE in src, "start-cosmic no longer execs the session here"
    dump = tmp_path / "path"
    body = (src.split(CUT_AT)[0]
            + "printf '%s' \"$PATH\" > " + shlex.quote(str(dump)) + "\n")
    script = tmp_path / "start-cosmic"
    script.write_text(body)
    script.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    # PATH is only what the test passes in: nothing below should reach for a real
    # binary on the host (the flatpak mkdir and xdg-user-dirs-update are allowed
    # to fail -- they do so harmlessly in the script itself).
    env = {"HOME": str(home), "PATH": path_in, "PENGYOS_PRIVATE_BUS": "1"}
    subprocess.run([shutil.which("sh"), str(script)], capture_output=True,
                   text=True, env=env, cwd=str(tmp_path), timeout=30)
    return home, dump.read_text()


def test_prepends_the_per_user_bin_dirs_ahead_of_everything(tmp_path):
    home, got = session_path(tmp_path, "/usr/bin")
    # ~/bin ends up first because both dirs are *prepended* in turn, which is
    # exactly what the comfort profile's add_path does.
    assert got == f"{home}/bin:{home}/.local/bin:/usr/bin"


def test_keeps_every_existing_path_entry(tmp_path):
    home, got = session_path(tmp_path, "/usr/bin:/usr/local/bin:/bin")
    assert got.startswith(f"{home}/bin:{home}/.local/bin:")
    assert got.endswith(":/usr/bin:/usr/local/bin:/bin")


def test_is_idempotent_so_the_private_bus_re_exec_cannot_duplicate(tmp_path):
    home = tmp_path / "home"
    given = f"{home}/bin:{home}/.local/bin:/usr/bin"
    _, got = session_path(tmp_path, given)
    assert got == given, "a re-exec that inherits the fixed PATH must be a no-op"
    assert got.count(f"{home}/.local/bin") == 1


def test_an_unset_path_never_yields_an_empty_component(tmp_path):
    # An empty component in PATH means "the current directory", so a chdir into a
    # writable dir would put attacker-controlled binaries on the session's path.
    for name, given in (("empty", ""), ("single", "/usr/bin")):
        sub = tmp_path / name
        sub.mkdir(parents=True)
        _, got = session_path(sub, given)
        assert "::" not in got, f"empty component in {got!r}"
        assert not got.startswith(":") and not got.endswith(":"), got
        assert "/usr/bin" in got


def test_the_session_and_the_comfort_profile_agree_on_the_dirs():
    """One definition of "per-user bin dirs".

    If these two drift, a terminal and a GUI app resolve different binaries --
    the class of bug this file exists to prevent -- so assert they agree rather
    than trusting two hand-maintained lists.
    """
    m = re.search(r'^add_path ((?:"\$HOME/[^"]+" ?)+)$', PROFILE.read_text(), re.M)
    assert m, "the comfort profile no longer calls add_path the way this test expects"
    profile_dirs = re.findall(r"\$HOME/[^\"\s]+", m.group(1))

    m = re.search(r"^for _d in (.*); do$", SCRIPT.read_text(), re.M)
    assert m, "start-cosmic no longer builds PATH with a loop over _d"
    session_dirs = re.findall(r"\$HOME/[^\"\s]+", m.group(1))

    assert session_dirs == profile_dirs, (
        f"start-cosmic prepends {session_dirs}, the comfort profile {profile_dirs}")
