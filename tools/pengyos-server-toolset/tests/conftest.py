"""Pytest bootstrap for the pengyos-server-toolset suite.

Adds the tool root to ``sys.path`` so a bare ``python3 -m pytest -q`` works
without ``pip install -e .`` (the verifier runs exactly that, cwd =
``tools/pengyos-server-toolset``). Future items add shared helpers here (the
tool table / fragment emitter / shell-syntax checker); they live under
``tests/`` so they can be imported by both the scaffold and the validator test
modules.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def sh_n(path: str | Path) -> bool:
    """Return True when ``sh -n <path>`` reports no syntax error.

    The shell-syntax gate. ``sh`` (dash on this
    box) is the POSIX-sh interpreter; ``-n`` makes it parse-only (no
    execution), so the check needs no runtime, no network, no root. External
    interpreter: mocked in the teeth test so the suite never depends on a real
    ``sh`` being present.
    """
    proc = subprocess.run(
        ["sh", "-n", str(path)],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


# A tmux option name: an optional ``*`` (server-option marker) followed by an
# identifier of letters/digits/underscore/dash, optionally indexed like
# ``status-format[0]``. This matches every option the comfort tmux.conf uses.
_TMUX_OPTION_RE = re.compile(r"^\*?[A-Za-z][A-Za-z0-9_-]*(\[\d+\])?$")
# A tmux key spec: one or more modifier-groups, each ``[A-Z]+-?<keyname>``
# (modifiers, optional dash, then a keyname). A keyname is a single character
# (``r``, ``1``, ``;``) or a *named key* (``F1``, ``Left``, ``Enter`` ...) —
# named keys are Capitalised in tmux, so a bare lowercase run like ``abc`` is
# not a key. This accepts ``C-a``, ``M-Left``, ``S-M-F2`` and bare ``r``; it
# rejects a bare alphanumeric run like ``123abc`` (neither a single char nor a
# Capitalised named key).
_TMUX_KEY_RE = re.compile(
    r"^(?:"
    r"(?:[A-Z]+-)?[A-Za-z0-9._/;-]"         # MOD-<char> or <char>
    r"|(?:[A-Z]+-)?[A-Z][A-Za-z0-9]*"       # MOD-<NamedKey> or <NamedKey>
    r")(?:-[A-Z]+-?(?:[A-Za-z0-9._/;-]|[A-Z][A-Za-z0-9]*))*"  # + -MOD<key>...
    r"$"
)
# The tmux verbs this small structural parser recognises (tmux man page
# "COMMANDS"). Anything else is a structural error, so a corrupt/typo'd
# directive is caught rather than silently accepted.
_TMUX_KNOWN_VERBS = frozenset(
    {
        "set", "setw", "set-option", "set-window-option",
        "bind-key", "bind",
        "run-shell", "new-session", "rename-session", "kill-session",
        "kill-pane", "respawn-pane", "send-keys", "select-pane",
        "split-window", "rename-window", "info", "attach-session",
    }
)


class TmuxDirective(NamedTuple):
    """A single structurally-parsed tmux.conf directive (see item 5)."""

    line: int
    """1-based source line number."""
    command: str
    """The verb: ``set`` / ``setw`` / ``bind-key`` / ``bind`` / known verb."""
    flags: list[str]
    """Flag tokens (``-g``, ``-s``, ``-ga``, ``-n``, ``-o``, ``-T`` ...)."""
    option: str | None = None
    """For ``set``/``setw``: the option name (quotes not part of it)."""
    value: str | None = None
    """For ``set``/``setw``: the value with surrounding quotes stripped."""
    key: str | None = None
    """For ``bind-key``/``bind``: the bound key spec."""
    keytable: str | None = None
    """For ``bind-key``: the ``-T`` key table, when given."""
    command_name: str | None = None
    """For ``bind-key``: the bound command (``send-prefix``, ``split-window`` ...)."""
    rest: list[str] = ()
    """Tokens remaining after the option/value or the bound command."""


def parse_tmux_conf(text: str) -> list[TmuxDirective]:
    """Parse ``text`` (a tmux.conf) into well-formed :class:`TmuxDirective` objects.

    The small structural check that stands in for a
    real ``tmux -f`` parse (no ``tmux`` binary is available at test time). It
    tolerates blank lines and full-line ``#`` comments, strips the leading
    option flags, and — crucially — splits the trailing value on the first
    top-level (unquoted) whitespace so quoted values such as
    ``"#[fg=#7aa2f7,bold] #S"`` are kept as a single ``value`` rather than
    splintering the parser. It raises :class:`ValueError` on the first
    structurally invalid directive (unknown verb, or a ``set``/``setw``/
    ``bind-key`` missing required pieces), so a drifted/corrupt tmux.conf is
    caught rather than silently accepted.
    """
    directives: list[TmuxDirective] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue  # blank or full-line comment

        tokens = shlex.split(line, comments=False, posix=True)
        if not tokens:
            continue  # shlex ate an all-comment line

        command = tokens[0]
        rest = tokens[1:]
        flags: list[str] = []

        # Strip leading option flags (and the value a -T flag consumes).
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok in ("-g", "-s", "-a", "-ga", "-da", "-n", "-o"):
                flags.append(tok)
                i += 1
            elif tok == "-T" and i + 1 < len(rest):
                flags.append(tok)
                flags.append(rest[i + 1])
                i += 2
            elif tok.startswith("-T") and len(tok) > 2:
                flags.append(tok)
                i += 1
            elif tok.startswith("-") and len(tok) > 1:
                # Unknown/unsupported flag: record it but keep parsing the rest.
                flags.append(tok)
                i += 1
            else:
                break

        rest = rest[i:]

        if command in ("set", "setw"):
            # set [-g] <option> <value...>  — option then a (possibly quoted) value.
            if len(rest) < 2:
                raise ValueError(
                    f"tmux.conf line {lineno}: {command!r} needs an option and a value"
                )
            option = rest[0]
            if not _TMUX_OPTION_RE.match(option):
                raise ValueError(
                    f"tmux.conf line {lineno}: {command!r} option {option!r} is not a "
                    "valid tmux option name"
                )
            # The value is everything after the option, re-quoted so quoted
            # values survive as a single token.
            value = " ".join(rest[1:])
            value = _strip_quotes(value)
            directives.append(
                TmuxDirective(lineno, command, flags, option=option, value=value)
            )
        elif command in ("bind-key", "bind"):
            # bind-key [-n|-o] [-T table] <key> <command> [args...]
            if len(rest) < 2:
                raise ValueError(
                    f"tmux.conf line {lineno}: {command!r} needs a key and a command"
                )
            key = rest[0]
            if not _TMUX_KEY_RE.match(key):
                raise ValueError(
                    f"tmux.conf line {lineno}: {command!r} key {key!r} is not a valid key"
                )
            command_name = rest[1]
            directives.append(
                TmuxDirective(
                    lineno,
                    command,
                    flags,
                    key=key,
                    keytable=_keytable_from_flags(flags),
                    command_name=command_name,
                    rest=rest[2:],
                )
            )
        elif command in _TMUX_KNOWN_VERBS:
            # A known verb with free-form args (run-shell, select-pane, ...).
            directives.append(TmuxDirective(lineno, command, flags))
        else:
            raise ValueError(
                f"tmux.conf line {lineno}: {command!r} is not a known tmux verb"
            )

    return directives


def _strip_quotes(value: str) -> str:
    """Strip a single layer of surrounding double quotes from ``value``."""
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _keytable_from_flags(flags: list[str]) -> str | None:
    """Return the ``-T`` key table value from a flag list, or ``None``.

    The flag list also holds the value a ``-T`` flag consumed, so we locate the
    ``-T`` token and return the token after it (correct even when other flags
    precede it, e.g. ``-n -T root``).
    """
    for idx, flag in enumerate(flags):
        if flag == "-T" and idx + 1 < len(flags):
            return flags[idx + 1]
    return None


def validate_tmux_conf(text: str) -> list[str]:
    """Return a list of structural problems in a tmux.conf (empty == well-formed).

    Thin wrapper over :func:`parse_tmux_conf` that turns a raised
    :class:`ValueError` into an error string, matching the
    ``validate() -> list[str]`` convention used by the validator module.
    """
    try:
        parse_tmux_conf(text)
    except ValueError as exc:
        return [str(exc)]
    return []


# --- no-telemetry scan ----------------------------
#
# The comfort configs (tmux.conf, the shell snippets) must never phone home: a
# fresh offline login must work, and the configs must not carry an
# analytics/telemetry endpoint or SDK. This scanner asserts that. It is a
# stand-in for a real "does this config reach out?" check — there is no network
# at test time, so we scan the *text* of the configs for the tell-tale tokens.
#
# The scan is deliberately conservative so it cannot produce false positives on
# the authored configs:
#   * it strips comments first (see :func:`strip_config_comments`) so a comment
#     that merely *mentions* "telemetry" does not trip the detector (that is also
#     a config key — starship's `telemetry` opt-out, set to `false`; starship.toml
#     was dropped at item 7, but the bare word is still an opt-out key, not a
#     phone-home signal);
#   * it only flags a *phone-home* token, not the bare word "telemetry" — the
#     desired `telemetry = false` state is asserted positively in
#     ``test_no_telemetry.py`` (``telemetry = false`` present, ``telemetry =
#     true`` absent), not by the bare word.

# A line is a full-line comment when its first non-space char is ``#``. This is
# true for every file type here (shell, tmux, TOML).
_FULLLINE_COMMENT_RE = re.compile(r"^\s*#")
# An inline shell comment: ``#`` preceded by whitespace (or at line start). We
# split on the first such ``#`` to drop the trailing comment. tmux.conf / TOML
# keep their inline content (tmux ``#[fg=...]`` colour codes are never at line
# start, so this never eats a colour code).
_INLINE_COMMENT_RE = re.compile(r"\s+#")


def strip_config_comments(text: str, is_shell: bool) -> str:
    """Return ``text`` with comments removed, so the scan checks *code*.

    Full-line comments (first non-space char is ``#``) are dropped for every
    file type. Shell snippets additionally drop inline ``#`` comments; tmux.conf
    and TOML keep their inline content (tmux ``#[fg=...]`` colour codes live
    inside quoted values, never at line start, so they survive).
    """
    out: list[str] = []
    for line in text.splitlines():
        if not line.strip() or _FULLLINE_COMMENT_RE.match(line):
            continue  # blank or full-line comment
        if is_shell:
            line = _INLINE_COMMENT_RE.split(line, maxsplit=1)[0]
        out.append(line)
    return "\n".join(out)


# Tokens that mean a config reaches out (analytics / telemetry / phone-home).
# A config whose *code* (comments stripped) contains any of these is phoning
# home. Kept conservative: no bare word "telemetry" (that is a config key such
# as starship's opt-out, set to `false`) — the scanner flags phone-home URLs,
# analytics SDKs and
# explicit telemetry *enablement* instead.
NO_TELEMETRY_TOKENS: tuple[str, ...] = (
    # network egress — anything that would reach out at runtime
    "http://",
    "https://",
    "//github.com",
    # analytics / telemetry SDKs and endpoints
    "analytics",
    "telemeter",
    "phone-home",
    "phonehome",
    "sentry",
    "segment.io",
    "mixpanel",
    "amplitude",
    "posthog",
    "matomo",
    "google-analytics",
    "googletagmanager",
    "gtm.js",
    "analytics.js",
    "doubleclick",
)

# Starship's opt-out key. The desired state is `telemetry = false`; only
# `telemetry = true` (enablement) is a problem. Checked *positively*, so this matches a `= true` enablement but never a `= false` opt-out.
_TELEMETRY_ENABLE_RE = re.compile(r"telemetry\s*=\s*true\b", re.IGNORECASE)


def scan_config_for_telemetry(text: str, is_shell: bool) -> list[str]:
    """Return the telemetry/phone-home problems in ``text`` (empty == clean).

    The no-telemetry assertion. Strips comments, then
    flags every line that contains a phone-home token (see
    :data:`NO_TELEMETRY_TOKENS`) or an explicit telemetry *enablement*
    (``telemetry = true``). Returns a list of ``"line<N>: <text>"`` problem
    strings (empty == the config does not phone home). This is the inverse-friendly
    shape of :func:`validate_tmux_conf` / :func:`toolset.validate`.

    Note: the bare word "telemetry" is *not* a problem (it is a config key such
    as starship's opt-out, `telemetry = false`); only an *enablement*
    (`telemetry = true`) is.
    """
    code = strip_config_comments(text, is_shell)
    problems: list[str] = []
    for lineno, line in enumerate(code.splitlines(), start=1):
        lowered = line.lower()
        for token in NO_TELEMETRY_TOKENS:
            if token.lower() in lowered:
                problems.append(f"{lineno}: {line.strip()}")
                break  # one problem per line is enough
        else:
            # no bare token matched — but an explicit telemetry enablement is
            # still a problem (and `telemetry = false` is not).
            if _TELEMETRY_ENABLE_RE.search(line):
                problems.append(f"{lineno}: {line.strip()}")
    return problems


def scan_config_path_for_telemetry(path: Path) -> list[str]:
    """Return the telemetry/phone-home problems in the config file at ``path``.

    Thin wrapper over :func:`scan_config_for_telemetry` that reads the file and
    decides whether it is a shell snippet (by extension) to pick the right
    comment-stripping rule.
    """
    text = Path(path).read_text(encoding="utf-8")
    is_shell = path.name == "profile" or path.suffix == ".sh"
    return scan_config_for_telemetry(text, is_shell)
