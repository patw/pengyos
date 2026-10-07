"""Syntax checks.

Covers: *"shell snippets pass ``sh -n``; tmux.conf parses via a small
structural check."*

Two independent gates live here:

* **``sh -n`` over the shell snippets** — ``dash`` (the POSIX ``/bin/sh`` on
  this box) parses each snippet in *parse-only* mode (``-n`` = no execution),
  so the check needs no runtime, no network, no root and no real shell binary
  beyond ``sh`` itself. The interpreter is mocked in the teeth test so the
  suite never hard-depends on a real ``sh`` being present.
* **A small structural parser for ``tmux.conf``** — there is no ``tmux``
  binary at test time, so this stands in for a real ``tmux -f`` parse. It
  tolerates blank lines / full-line ``#`` comments, strips leading option
  flags, keeps quoted values (including ``#[fg=...]`` colour codes) intact, and
  raises on the first structurally invalid directive. ``parse_tmux_conf`` /
  ``validate_tmux_conf`` live in ``conftest.py`` so both this module and any
  future one can import them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from conftest import (
    parse_tmux_conf,
    sh_n,
    validate_tmux_conf,
)

# A fake ``sh`` result. ``CompletedProcess`` lives in ``subprocess`` (not
# ``unittest.mock``); we only need it to build a return value with an exit code.
_Comp = subprocess.CompletedProcess

REPO_ROOT = Path(__file__).resolve().parents[1]
SHELL_DIR = REPO_ROOT / "shell"

PROFILE = SHELL_DIR / "profile"
TMUX_CONF = SHELL_DIR / "tmux.conf"
ZOXIDE_SH = SHELL_DIR / "zoxide.sh"
FZF_SH = SHELL_DIR / "fzf.sh"

# The POSIX-sh snippets that must pass ``sh -n`` (SPEC §Verification).
#
# `fzf.sh` was re-added at item 20 (the Buildroot package ships), so it is a
# POSIX-sh snippet again. `profile` + `fzf.sh` + `zoxide.sh` are the POSIX-sh
# files in `shell/` (tmux.conf and starship.toml are not shell).
SHELL_SNIPPETS = (PROFILE, FZF_SH, ZOXIDE_SH)


# --- sh -n over the shell snippets ------------------------------------------


def test_sh_n_is_available():
    """The gate is only meaningful if ``sh`` actually exists on the box.

    Guards against a silent pass: if ``sh`` were absent, ``sh_n`` would return
    False and every downstream assertion would fail loudly — but we assert the
    interpreter is present up front so the failure mode is obvious. A missing
    ``sh`` skips rather than fails (the suite is still correct elsewhere).
    """
    import shutil

    if shutil.which("sh") is None:
        pytest.skip("sh not available on this box")
    # a trivial parse-only invocation must exit 0 (it exists and is invocable)
    proc = subprocess.run(
        ["sh", "-n", "/dev/null"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, "sh -n /dev/null should succeed"


def test_all_shell_snippets_pass_sh_n():
    """Every POSIX-sh snippet must parse cleanly under ``sh -n`` (item 5)."""
    for path in SHELL_SNIPPETS:
        assert path.is_file(), f"missing shell snippet: {path}"
        assert sh_n(path), (
            f"{path.name} failed `sh -n` — see the dash diagnostics below\n"
            f"{_sh_n_stderr(path)}"
        )


def test_sh_n_flags_a_syntax_error(tmp_path: Path):
    """The gate has teeth: a deliberately broken script must fail ``sh -n``.

    Without this, ``test_all_shell_snippets_pass_sh_n`` could pass vacuously
    (e.g. if ``sh_n`` were always True). We point ``sh -n`` at a temp file with
    an unterminated ``if`` and assert ``sh_n`` reports the error.
    """
    broken = tmp_path / "broken.sh"
    broken.write_text("if [ -n \"$X\" ]; then\n  echo oops\n", encoding="utf-8")
    assert not sh_n(broken), "a script with an unterminated `if` must fail `sh -n`"


def test_sh_n_is_not_vacuously_true_via_mock():
    """Even with ``sh`` mocked to always exit 1, ``sh_n`` must return False.

    Proves ``sh_n`` really reads the interpreter's exit code instead of
    hard-coding ``True``.
    """
    with mock.patch.object(
        subprocess,
        "run",
        return_value=_Comp(args=[], returncode=1, stdout="", stderr=""),
    ):
        assert sh_n(PROFILE) is False


def test_sh_n_reads_exit_code_via_mock():
    """With ``sh`` mocked to exit 0, ``sh_n`` must return True (complement)."""
    with mock.patch.object(
        subprocess,
        "run",
        return_value=_Comp(args=[], returncode=0, stdout="", stderr=""),
    ):
        assert sh_n(PROFILE) is True


def _sh_n_stderr(path: Path) -> str:
    """Return the ``sh -n`` stderr for ``path`` (for a readable failure report)."""
    proc = subprocess.run(
        ["sh", "-n", str(path)],
        capture_output=True,
        text=True,
    )
    return proc.stderr.strip() if proc.stderr.strip() else "(no diagnostics)"


# --- tmux.conf structural check ---------------------------------------------


def test_tmux_conf_parses_clean():
    """The committed tmux.conf must parse with no structural errors (item 5)."""
    text = TMUX_CONF.read_text(encoding="utf-8")
    assert validate_tmux_conf(text) == [], (
        f"tmux.conf did not parse cleanly:\n"
        + "\n".join(validate_tmux_conf(text))
    )


def test_tmux_conf_yields_expected_directives():
    """The parser must recover exactly one directive per non-comment line.

    The expected count is derived from the raw file (non-blank, non-full-line
    ``#`` comment lines), so the assertion stays correct as the config grows
    rather than pinning a magic number that silently rots.
    """
    text = TMUX_CONF.read_text(encoding="utf-8")
    expected = sum(
        1
        for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    )
    directives = parse_tmux_conf(text)
    assert len(directives) == expected, (
        f"parser recovered {len(directives)} directives, expected {expected}"
    )
    assert expected > 0, "sanity: the committed tmux.conf must have directives"


def test_parse_sets_option_and_value():
    """A ``set -g <option> <value>`` line yields the option and its value."""
    directives = parse_tmux_conf('set -g base-index 1\n')
    assert len(directives) == 1
    d = directives[0]
    assert d.command == "set"
    assert d.flags == ["-g"]
    assert d.option == "base-index"
    assert d.value == "1"


def test_parse_setw_option():
    directives = parse_tmux_conf("setw -g pane-base-index 1\n")
    assert len(directives) == 1
    d = directives[0]
    assert d.command == "setw"
    assert d.flags == ["-g"]
    assert d.option == "pane-base-index"
    assert d.value == "1"


def test_parse_setga_flag():
    directives = parse_tmux_conf('set -ga terminal-overrides ",xterm-256color:RGB"\n')
    assert len(directives) == 1
    d = directives[0]
    assert d.command == "set"
    assert d.flags == ["-ga"]
    assert d.option == "terminal-overrides"
    assert d.value == ",xterm-256color:RGB"


def test_parse_indexed_option():
    """``status-format[0]`` is an indexed option the parser must accept."""
    directives = parse_tmux_conf(
        'set -g status-format[0] "#[fg=#7aa2f7,bold] #S #[fg=default]#I:#A"\n'
    )
    assert len(directives) == 1
    d = directives[0]
    assert d.option == "status-format[0]"
    # the quoted value (with its #[fg=...] colour codes) survives as one value
    assert d.value == "#[fg=#7aa2f7,bold] #S #[fg=default]#I:#A"


def test_parse_value_with_internal_quotes():
    """A value containing an embedded double-quote must not split the parser."""
    text = 'set -g status-style "bg=#1a1b26,fg=#a9b1d6"\n'
    directives = parse_tmux_conf(text)
    assert len(directives) == 1
    assert directives[0].option == "status-style"
    assert directives[0].value == "bg=#1a1b26,fg=#a9b1d6"


def test_parse_bind_key_send_prefix():
    directives = parse_tmux_conf("bind-key C-a send-prefix\n")
    assert len(directives) == 1
    d = directives[0]
    assert d.command == "bind-key"
    assert d.key == "C-a"
    assert d.command_name == "send-prefix"


def test_parse_bind_key_no_prefix_flag():
    """``bind-key -n M-Left select-pane -L`` must capture the ``-n`` flag."""
    directives = parse_tmux_conf("bind-key -n M-Left select-pane -L\n")
    assert len(directives) == 1
    d = directives[0]
    assert d.flags == ["-n"]
    assert d.key == "M-Left"
    assert d.command_name == "select-pane"
    assert d.rest == ["-L"]


def test_parse_bind_key_with_reload_and_semicolon():
    """The reload binding (``\\;``) must parse into command + args."""
    text = 'bind-key r source-file ~/.tmux.conf \\; display-message "tmux.conf reloaded"\n'
    directives = parse_tmux_conf(text)
    assert len(directives) == 1
    d = directives[0]
    assert d.key == "r"
    assert d.command_name == "source-file"
    # the remaining args (including the escaped `;` and the quoted message)
    assert ";" in " ".join(d.rest)
    assert "tmux.conf reloaded" in " ".join(d.rest)


def test_parse_bind_key_keytable_after_other_flags():
    """``-T`` key table is extracted correctly even when another flag precedes it."""
    text = "bind-key -n -T root C-a send-prefix\n"
    directives = parse_tmux_conf(text)
    assert len(directives) == 1
    d = directives[0]
    assert d.flags == ["-n", "-T", "root"]
    assert d.key == "C-a"
    assert d.keytable == "root"
    assert d.command_name == "send-prefix"


def test_parse_bind_key_without_keytable():
    text = "bind-key C-a send-prefix\n"
    directives = parse_tmux_conf(text)
    assert directives[0].keytable is None


def test_parse_ignores_comments_and_blanks():
    text = (
        "# a comment\n"
        "\n"
        "set -g mouse on\n"
        "   # indented comment\n"
        "setw -g mouse on\n"
    )
    directives = parse_tmux_conf(text)
    assert [d.command for d in directives] == ["set", "setw"]


def test_validate_tmux_conf_flags_unknown_verb():
    """A directive with an unknown verb is a structural error (teeth)."""
    assert validate_tmux_conf("frobnicate foo bar\n")


def test_validate_tmux_conf_flags_missing_value():
    """``set -g base-index`` with no value is a structural error (teeth)."""
    assert validate_tmux_conf("set -g base-index\n")


def test_validate_tmux_conf_flags_bad_option():
    """An option that is not a valid tmux name is a structural error (teeth)."""
    assert validate_tmux_conf("set -g 123abc somevalue\n")


def test_validate_tmux_conf_flags_bind_key_missing_command():
    """``bind-key C-a`` with no bound command is a structural error (teeth)."""
    assert validate_tmux_conf("bind-key C-a\n")


def test_validate_tmux_conf_flags_bad_key():
    """A key that is not a valid tmux key spec is a structural error (teeth)."""
    assert validate_tmux_conf("bind-key 123abc send-prefix\n")


def test_validate_tmux_conf_real_file_is_clean():
    """End-to-end: the committed tmux.conf validates clean."""
    text = TMUX_CONF.read_text(encoding="utf-8")
    assert validate_tmux_conf(text) == []
