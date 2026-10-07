"""Upstream model-server port discovery for PengyOS.

The follow-up "pin the local model server's port in the
pengy-agent network grant" is blocked until PengyOS defines the model
server's default port *upstream* — and the rule in force since iteration 6
is that the port must never be guessed. Every re-check of that item (so far
five of them) has been a hand-rolled grep over the PengyOS workspace, and
each one re-derived the search pattern from memory. This module turns that
re-check into a deterministic, tested command:

* :func:`find_model_server_port` scans a directory tree (the PengyOS
  workspace) for text lines that *define* the model server's port. It is
  deliberately conservative — a hit must carry the words *model server*
  plus one of eleven shapes and a 2–5 digit TCP port: the word *port* on
  the same line (the two classic prose forms), an ``<address>:<port>``
  after a listen/bind/serve verb ("the model server listens on
  127.0.0.1:11434"), a ``port`` key line inside a *model-server section*
  (a ``[model-server]`` / ``model_server:`` opener with ``port = N`` /
  ``port: N`` inside it, for config-shaped definitions where the two
  words land on two lines), an *address key* line inside a
  model-server section (the component's own ``url`` / ``endpoint`` /
  ``address`` / ``listen`` / ``host`` key whose value carries a concrete
  ``<address>:<port>`` — the port embedded in a listen address or URL,
  ``url = "http://127.0.0.1:11434/v1"``; a suffixed key such as
  ``metrics_url`` is not a candidate), the *inline mapping* form
  (``model server:`` / ``model-server:`` / ``model_server:`` followed
  directly by the number — a one-line YAML/INI definition, no *port*
  word needed), or the
  *concatenated-identifier / environment variable* form (the word *port*
  fused to the component name with ``-``/``_``: ``MODEL_SERVER_PORT=``
  ``11434``, buildroot-style ``BR2_..._MODEL_SERVER_PORT="8080"``,
  ``model-server-port: 9000``, make-style ``?=`` — or wrapped in a
  shell parameter-expansion default, ``MODEL_SERVER_PORT="${MODEL_SERVER_PORT:-NNNN}"``,
  the service-script form), or the *Kconfig option*
  shape (a ``config ..._MODEL_SERVER_PORT`` symbol line in a buildroot
  ``Config.in``, with an indented ``default NNNN`` value line in the
  option body before the next option/directive — a matched single- or
  double-quoted decimal, ``default "NNNN"``, is the string-typed buildroot
  form, and the Kconfig *conditional* default, ``default NNNN if
  <condition>``, is a value line too (the value stays a literal decimal;
  the ``if`` condition is metadata, not part of the value — a dangling
  ``default NNNN if`` with no condition is not a value)), or the
  *quoted-key
  section* shape (a JSON/JSONC/quoted-YAML component key line —
  ``"model-server": {`` / ``'model-server':`` / an unquoted
  ``model-server: {`` flow-mapping line — with a quoted or unquoted
  ``port`` key line carrying a bare (optionally quoted) number inside
  the block; the section stays open until a section header, a shallower
  line, or a sibling key at the opener's indent, so a sibling component's
  ``"port"`` line is never a candidate), or the *fused-identifier
  address-key* shape (the component name fused to an address stem —
  ``url`` / ``endpoint`` / ``address`` / ``listen`` / ``host`` — on ONE
  line, ``MODEL_SERVER_URL="http://127.0.0.1:11434/v1"``,
  ``model_server_endpoint = 127.0.0.1:11434``, a TOML dotted key
  ``model-server.url = "…"``, make/defconfig style alike, the value
  carrying a concrete ``<address>:<port>`` instead of a bare number —
  the flat single-line counterpart of the section address-key shape, which
  needs an opener), or the *short-flag*
  form (the component name, then the short ``-p`` flag with a bare number
  value — ``model-server -p 11434``, ``model-server -p11434``, a systemd
  ``ExecStart`` line, a service script, a spec sentence; the long
  ``--port`` form is already the prose patterns' — a ``\b`` sits between
  the flag's ``-`` and ``port`` — so this shape exists only for the
  short flag, which carries no *port* letters at all: the flag token must
  be exactly ``-p`` at a token boundary, the value a bare optionally
  quoted number, and the flag must come AFTER the name), or the
  *flag-default* form (the same flag forms — ``-p``/``-port``/``--port``
  at a token boundary, after the component name — whose value is wrapped
  in a shell parameter-expansion default, the service-script
  ``ExecStart`` form: ``model-server --port ${MODEL_SERVER_PORT:-NNNN}``;
  the number is the default the flag takes when the variable is unset,
  a definition claim a human reviews; a bare value is the short-flag
  shape's, and a wrapper without a number is not a candidate) — and the
  number must be in the 1..65535 window. A hit is evidence for a human to review, never
  an automatic pin;
* :func:`scan_text` is the pure per-text half, unit-testable without any
  filesystem at all;
* every checkout of this tool is always skipped — not just this one:
  any directory containing the ``pengyos_apparmor`` package (a vendored
  copy in a sibling workspace, an editable install's source tree, this
  repo itself) is pruned, and a scan whose *root* is a tool checkout
  reports no candidates (the tool's docs, tests and planner fixtures
  mention model-server ports constantly — scanning them would be
  self-matches, not upstream definitions);
* binary files, files over :data:`MAX_SCAN_BYTES`, and build/VCS noise
  directories (:data:`SKIP_DIRS`) are skipped; and files are decoded as
  UTF-8 with a *leading BOM stripped* (``utf-8-sig``) — the BOM is a
  file-level encoding marker, and left in place it would sit on line 1
  before ``^`` and break the line-start-anchored shapes (the section
  opener, the Kconfig option opener).

Nothing here modifies files and nothing ever decides the port: the
command's job ends at *reporting candidates*. The mechanical transition is
``pengyos-apparmor plan-port-pin <port> --patch`` (see
:mod:`pengyos_apparmor.portpin`), which a human runs only after reviewing
what this command found.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence, Tuple, Union

from .portpin import normalize_port

__all__ = [
    "TOOL_ROOT",
    "DEFAULT_SCAN_ROOT",
    "SKIP_DIRS",
    "MAX_SCAN_BYTES",
    "MODEL_SERVER_PORT_PATTERNS",
    "MODEL_SERVER_LISTEN_PATTERN",
    "MODEL_SERVER_INLINE_PATTERN",
    "MODEL_SERVER_IDENTIFIER_PATTERN",
    "MODEL_SERVER_FUSED_ADDR_PATTERN",
    "MODEL_SERVER_SHORT_FLAG_PATTERN",
    "MODEL_SERVER_FLAG_DEFAULT_PATTERN",
    "PortDiscoveryError",
    "PortCandidate",
    "scan_text",
    "find_model_server_port",
    "is_binary",
    "is_tool_checkout",
]

#: The root of this tool (``<workspace>/tools/pengyos-apparmor``). Scans
#: always skip this directory and everything under it — its own docs,
#: tests and planner fixtures are full of deliberate model-server port
#: mentions.
TOOL_ROOT = Path(__file__).resolve().parent.parent

#: The PengyOS workspace root (``<workspace>``, two levels above the
#: tool: ``<workspace>/tools/pengyos-apparmor``). This is what the
#: ``find-model-server-port`` subcommand scans by default — the upstream
#: specs (SPEC.md, USE-CASES.md) and every sibling tool
#: live under it, and the port, when defined, would be defined here.
DEFAULT_SCAN_ROOT = TOOL_ROOT.parent.parent

#: Directory names that are never scanned (VCS, build, dependency noise).
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".venv",
        "venv",
        "node_modules",
        "build",
        "dist",
        "target",
    }
)

#: Files larger than this are skipped — a port definition lives in a small
#: spec/doc file, and a scan should stay cheap even on a big workspace.
MAX_SCAN_BYTES = 1_000_000

#: A port token: 2–5 digits, not part of a longer number, in the
#: 1..65535 range (checked with ``normalize_port`` after extraction).
#: Single-digit ports are deliberately out of scope — no model server is
#: going to run on one.
_PORT = r"(?<!\d)(?P<port>\d{2,5})(?!\d)"

#: The two conservative line patterns. Both require the words *model
#: server* (hyphen/space separated) AND *port* on the same line, with the
#: port number between or after them — a hit is a *definition* claim, not a
#: bare port mention. A closing quote after the word *port* and quotes
#: around the number are the compact single-line JSON form (a hit on a
#: one-line object like `"model-server": {"port": 11434}`; the
#: multi-line JSON block is the quoted-key section shape).
MODEL_SERVER_PORT_PATTERNS: Tuple[re.Pattern, ...] = (
    # "the model server's default port is 11434" / "model-server port: 8080"
    # / the JSON key form "port": 11434 (quote-tolerant)
    re.compile(
        rf"model[- _]?server\b[^.\n]*?\bport['\"]?\s*(?:is|=|:)?\s*['\"]?{_PORT}",
        re.I,
    ),
    # "port 9000 is the model server port"
    re.compile(
        rf"\bport['\"]?\s*(?:is|=|:)?\s*['\"]?{_PORT}[^.\n]*?model[- _]?server",
        re.I,
    ),
)

#: Listen-form line: "the model server listens on 127.0.0.1:11434" — the
#: word *port* is absent, so neither of the line patterns above can see it.
#: The verb (listen/bind/serve) plus a concrete ``<address>:<port>`` keeps
#: this conservative: a URL mention without a verb or a prose line with no
#: address ("binds loopback only") is still a miss by design.
MODEL_SERVER_LISTEN_PATTERN: re.Pattern = re.compile(
    rf"model[- _]?server\b[^.\n]*?\b(?:listen|bind|serve)s?\b[^.\n]*?"
    rf"(?:\d{{1,3}}(?:\.\d{{1,3}}){{3}}|\[[0-9A-Fa-f:]+\]"
    rf"|[0-9A-Fa-f:]{{2,}}|localhost|127\.0\.0\.1)"
    rf"\s*:\s*{_PORT}",
    re.I,
)

#: The inline-mapping line: "model server: 11434" / "model-server: 8080"
#: / "model_server: '9000'" / the quoted-key JSON form
#: "\"model-server\": 11434" — the component name, a mapping colon, then
#: the port directly. A one-line YAML/INI/JSON definition: neither the
#: word *port* nor an address appears, so none of the patterns above can
#: see it. Key quotes (JSON/JSONC quoted keys) and a value quote are
#: tolerated; a value that is not a bare number (a word, a comment) is
#: deliberately not a candidate.
MODEL_SERVER_INLINE_PATTERN: re.Pattern = re.compile(
    rf"model[- _]?server['\"]?\s*:\s*[\"']?{_PORT}",
    re.I,
)

#: The shell parameter-expansion default wrapper, ``${VAR:-N}`` / the
#: ``${VAR-N}`` form: any inner variable name (no nested braces), the
#: ``:`` optional, optional whitespace after it. A plain (non-f) string —
#: the literal braces would collide with f-string escaping. The
#: lookbehind is what keeps the match from starting INSIDE the wrapper
#: (the wrapper's inner variable name is usually the same identifier —
#: ``${MODEL_SERVER_PORT:-8080}`` — and an unguarded match there would
#: report the inner name as the definition line's subject).
_SHELL_DEFAULT_WRAPPER = r"(?<!\$\{)(?:\$\{[^{}]*?[:-]\s*)?"

#: The concatenated-identifier / environment-variable line: the word
#: *port* is fused to the component name with ``-``/``_`` instead of
#: whitespace — ``MODEL_SERVER_PORT=11434``, buildroot defconfig style
#: ``BR2_PENGY_MODEL_SERVER_PORT="8080"``, ``model-server-port: 9000``,
#: make-style ``MODEL_SERVER_PORT ?= 8080``. One optional middle
#: identifier segment is tolerated (``model_server_default_port``); the
#: value separator is ``=``, ``:=``, ``?=``, ``:`` or plain whitespace,
#: with an optional value quote. The value may also be wrapped in a
#: *shell parameter-expansion default* — the service-script form
#: ``MODEL_SERVER_PORT="${MODEL_SERVER_PORT:-11434}"`` (any inner
#: variable name, the ``:`` optional, whitespace after the ``:``
#: tolerated; a quote may sit either outside the expansion — shell
#: quoting — or inside it). The fused *port* is not a candidate when
#: more identifier characters follow it (``..._PORTS = [N]``).
#
#: The pattern is a two-branch alternation carrying the port number in
#: two SEPARATELY named groups — a bare value (``port``) or one wrapped
#: in the expansion (``port_expanded``); Python's ``re`` forbids the same
#: group name in two alternation branches, and :func:`scan_text` merges
#: the two. The bare branch comes first so a plain ``VAR=NNNN`` never
#: pays for the wrapper attempt.
MODEL_SERVER_IDENTIFIER_PATTERN: re.Pattern = re.compile(
    rf"model[- _]?server(?:[- _]\w+)?[- _]?port"
    rf"\s*(?:\?=|:=|=|:)?\s*"
    + r"(?:[\"']?" + _PORT
    + r"|[\"']?"
    + _SHELL_DEFAULT_WRAPPER
    + r"[\"']?(?<!\d)(?P<port_expanded>\d{2,5})(?!\d))",
    re.I,
)

#: The fused-identifier / environment-variable *address-key* line: the
#: component name fused to an address stem (``url`` / ``endpoint`` /
#: ``address`` / ``listen`` / ``host``) on ONE line, with the port
#: embedded in the value's concrete ``<address>:<port>`` instead of a
#: bare number — ``MODEL_SERVER_URL="http://127.0.0.1:11434/v1"``
#: (env/service-script), ``model_server_endpoint = 127.0.0.1:11434``
#: (TOML/INI/make), ``model-server.url = "…"`` (a TOML dotted key),
#: buildroot defconfig style
#: ``BR2_..._MODEL_SERVER_HOST="127.0.0.1:11434"``. The flat
#: single-line counterpart of the section address-key shape (which needs
#: a model-server section *opener* — a flat definition line has no
#: opener, so neither of the section patterns can see it). The value
#: separator is ``=``, ``:=``, ``?=``, ``:`` or plain whitespace (a
#: ``.`` is the TOML
#: dotted-key separator), with an optional value quote; a scheme and a
#: trailing path around the address are tolerated, and a shell
#: parameter-expansion wrapper around the value
#: (``${MODEL_SERVER_URL:-http://…}``) is transparent to the lazy value
#: prefix. The stem must be a TOKEN END — a plural fused key such as
#: ``MODEL_SERVER_URLS`` is not a candidate (exactly as with the fused
#: *port* in the identifier shape). The address must be concrete (dotted
#: quad, bracketed or bare IPv6 token, ``localhost``): a URL without a
#: port, a bare IP, a path-only value, or a non-concrete hostname
#: (``https://example.com:NNNN``) is not a candidate; out-of-window and
#: 6-digit numbers are dropped by the shared ``normalize_port`` path.
#: One middle identifier segment is tolerated
#: (``model_server_default_url``), which means a suffixed stem
#: (``model_server_metrics_url``) is ALSO a candidate — the fused
#: ``metrics_url`` key reads as the model server's metrics endpoint, a
#: definition claim a human reviews and rejects if it is not the model
#: server's own port. The identifier shape has exactly the same
#: looseness (``model_server_metrics_port=NNNN`` is a candidate there
#: too); precision is the human-review step, the same as every other
#: shape.
MODEL_SERVER_FUSED_ADDR_PATTERN: re.Pattern = re.compile(
    rf"model[- _]?server(?:[- _]\w+)?[._-]?(?:url|endpoint|address|listen|host)"
    rf"\s*(?:\?=|:=|=|:)?\s*['\"]?"
    rf"[^'\"]*?"
    rf"(?:\d{{1,3}}(?:\.\d{{1,3}}){{3}}|\[[0-9A-Fa-f:]+\]"
    rf"|[0-9A-Fa-f:]{{2,}}|localhost)"
    rf"\s*:\s*{_PORT}",
    re.I,
)

#: A model-server *section opener*: a TOML/INI section header
#: ``[model-server]`` (any subsection such as ``[model-server.http]``
#: included) or a YAML/INI/JSON mapping key ``model_server:`` /
#: ``"model-server":`` (key quotes are the JSON/JSONC/quoted-YAML form);
#: an optional YAML list dash, an optional flow-mapping brace
#: (``"model-server": {``), and a trailing ``#``/``//`` comment are
#: tolerated. The section is what lets the port sit on a second line
#: while the component name sits on the opener line.
_MODEL_SERVER_SECTION_OPENER: re.Pattern = re.compile(
    r"^\s*(?:-{1,3}\s+)?"
    r"(?:\[\s*model[- _]?server\b[^\]]*\]"
    r"|['\"]?model[- _]?server['\"]?\s*:)"
    r"\s*(?:\{)?\s*(?:(?://|#).*)?$",
    re.I,
)

#: Inside an active model-server section, a ``port`` key line
#: (``port``, ``port-default``, ``listen_port`` …): the separator is
#: ``=`` (TOML/INI), ``:`` (YAML) or ``is`` (prose). Key quotes
#: (``"port":``) and value quotes (``"11434"``) are the JSON/JSONC form;
#: a trailing comma is tolerated (no end anchor). Array values
#: (``ports = [N]``) and key lines without a number are deliberately not
#: candidates.
_MODEL_SERVER_SECTION_PORT_KEY: re.Pattern = re.compile(
    rf"^\s*(?:-{1,3}\s+)?['\"]?(?:listen[- _]?)?port(?:[- _]\w+)*['\"]?"
    rf"\s*(?:is|=|:)\s*['\"]?{_PORT}",
    re.I,
)

#: Inside an active model-server section, an *address key* line: the
#: component's own ``url`` / ``endpoint`` / ``address`` / ``listen`` /
#: ``host`` key (exact stem, quoted or unquoted, case-insensitive) whose
#: value carries a concrete ``<address>:<port>`` — the port embedded in a
#: listen address or URL instead of a bare ``port`` key (``url =
#: "http://127.0.0.1:11434/v1"``, ``listen = 127.0.0.1:11434``,
#: ``endpoint: "http://[::1]:8080"``). The address must be one of the
#: concrete address forms (dotted quad, bracketed or bare IPv6 token,
#: ``localhost``) — a URL without a port, a bare IP, or a path-only value
#: is not a candidate. The key must be EXACTLY one of the stems: a
#: suffixed or prefixed key (``metrics_url``, ``base-url``,
#: ``server_host``) points at some other component's endpoint and is
#: never a model-server-port definition. The value's quote is optional
#: and a scheme/path around the address is tolerated; a bare
#: ``key:`` line (a YAML list item on the next line) is not a candidate,
#: exactly as with the port-key shape.
_MODEL_SERVER_SECTION_ADDR_KEY: re.Pattern = re.compile(
    rf"^\s*(?:-{1,3}\s+)?['\"]?(?:url|endpoint|address|listen|host)['\"]?"
    rf"\s*(?:is|=|:)\s*['\"]?"
    rf"[^'\"]*?"
    rf"(?:\d{{1,3}}(?:\.\d{{1,3}}){{3}}|\[[0-9A-Fa-f:]+\]"
    rf"|[0-9A-Fa-f:]{{2,}}|localhost)"
    rf"\s*:\s*{_PORT}",
    re.I,
)

#: The short-flag form: the component name, then the SHORT `-p` flag
#: with a bare number value — `model-server -p 11434`, `model-server
#: -p11434`, `ExecStart=/usr/bin/model-server -p 11434`,
#: `model_server -p "11434"`. This is the gap the other shapes leave:
#: the long `--port` form is already caught by the prose patterns (a
#: `\b` sits between the flag's `-` and `port`, so "... --port N" reads
#: as the word *port* after the component name), but the short flag
#: carries no *port* letters at all, so no other shape can see it.
#: The flag token must sit at a token boundary (line start or preceded
#: by whitespace or a quote) and be EXACTLY `-p`: `-profile`,
#: `--print`-style long tokens are not flags here (the character after
#: `-p` must be the value, not more letters). The name carries no
#: lookbehind, exactly like every other shape: embedded in a larger
#: token (`a-model-server`, a binary path) it still matches — a
#: definition claim a human reviews. The value separator is whitespace or
#: `=`, with an optional value quote (same tolerance as the
#: identifier shape's value). The gap between the component name and
#: the flag is any run of non-dot, non-newline characters (other
#: flags in between: `model-server --verbose -p 11434`), the same
#: bounded gap the listen shape uses. A flag that comes BEFORE the
#: component name ("-p 11434 for the model server") is not a
#: candidate — the name must introduce the line's subject, the same
#: directionality as every other shape.
MODEL_SERVER_SHORT_FLAG_PATTERN: re.Pattern = re.compile(
    rf"model[- _]?server\b[^.\n]*?(?:^|[\s\'\"])-p\s*=?\s*[\"']?{_PORT}",
    re.I,
)

#: The flag form whose VALUE is wrapped in a shell parameter-expansion
#: default — the service-script `ExecStart` form: `ExecStart=
#: /opt/pengy/bin/model-server --port ${MODEL_SERVER_PORT:-11434}`,
#: `model-server -p ${MODEL_SERVER_PORT:-11434}`, `model-server
#: -p=${MODEL_SERVER_PORT:-11434}`. This is the gap the other flag forms
#: leave: the short-flag shape and the long `--port` prose shape both
#: read the flag's value as a bare (optionally quoted) number, and a
#: `${VAR:-NNNN}` wrapper in the value position breaks both — neither
#: can see a definition whose flag value is an expansion, not a number.
#: The component name, a bounded gap (any other flags in between), then
#: the flag token must sit at a token boundary (line start or preceded
#: by whitespace or a quote) and be EXACTLY `--port`, `-port` or `-p`
#: (the character after `-p` must be the value, not more letters, so
#: `-profile`/`-print`-style long tokens are not flags; the word boundary
#: after `port` keeps `--ports`/`-portx` out), then an optional `=` and
#: quote, and the value must be wrapped in the shell parameter-expansion
#: default (any inner variable name, the `:` optional, whitespace after
#: the `:`/`-` separator tolerated, a quote either outside the expansion
#: — shell quoting — or inside it). A bare value is not a candidate HERE
#: (the short-flag shape's), and a wrapper without a number is not one
#: either. The number is the *default* — the value the flag takes when
#: the variable is unset, i.e. the port the service runs with by default:
#: a definition claim a human reviews (the same epistemic status as the
#: identifier shape's wrapper form). Direction pinned as with every other
#: shape: the flag comes AFTER the name. The name carries no lookbehind
#: (embedded in a binary path, as in the `ExecStart` form, it still
#: matches — a claim a human reviews).
MODEL_SERVER_FLAG_DEFAULT_PATTERN: re.Pattern = re.compile(
    rf"model[- _]?server\b[^.\n]*?"
    rf"(?:^|[\s\'\"])(?:--port\b|-port\b|-p)\s*=?\s*[\"']?"
    + r"(?<!\$\{)\$\{[^{}]*?[:-]\s*"
    + r"[\"']?"
    + _PORT,
    re.I,
)

#: Any TOML/INI-style section header — the line kind that ends a section
#: in a flat (non-YAML) file.
_ANY_SECTION_HEADER: re.Pattern = re.compile(r"^\s*\[\s*\w")

#: A YAML/INI mapping-key line (``key:``) — at the opener's indent or
#: shallower it ends the section (a sibling key); deeper it is nesting
#: *inside* the model-server block and the section stays open.
_MAPPING_KEY_LINE: re.Pattern = re.compile(r"^\s*\S[^:#\s\[\]]*\s*:")

#: A Kconfig option opener: ``config <symbol>`` (a buildroot ``Config.in``
#: option definition). The line carries only the symbol — the value lives
#: on a later ``default`` line in the indented option body.
_KCONFIG_OPTION_OPENER: re.Pattern = re.compile(
    r"^\s*config\s+(\w+)\s*(?:#.*)?$"
)

#: The symbol half of the Kconfig opener: a fused *model-server-port*
#: identifier (buildroot style ``BR2_..._MODEL_SERVER_PORT``, case
#: insensitive, one optional middle segment), with the fused *port* at the
#: END of the symbol — a plural symbol (``..._MODEL_SERVER_PORTS``) is not
#: a default-port option.
_KCONFIG_MODEL_SERVER_PORT_SYMBOL: re.Pattern = re.compile(
    r"\w*model[-_]?server(?:[-_]\w+)?[-_]?port",
    re.I,
)

#: Inside an active Kconfig model-server-port option: the indented
#: ``default <number>`` value line. A bare decimal expression, a MATCHED
#: single- or double-quoted decimal (``default "11434"`` — the
#: string-typed buildroot form), or a Kconfig *conditional* default
#: (``default 11434 if BR2_…`` — the standard buildroot idiom, the value
#: staying a literal bare/quoted decimal while the ``if`` condition is
#: metadata about WHEN it applies, not part of the value) is a candidate;
#: an unterminated or mismatched quote is NOT: ``(?(q)(?P=q))`` (a
#: conditional group — a bare ``(?P=q)`` backreference to a
#: non-participating group does not match empty in Python's ``re``)
#: closes the quote only when one opened. A DANGLING ``default NNNN if``
#: (no condition after the ``if``) is not a value (malformed Kconfig),
#: and hex, arithmetic, or symbol references are not candidates; a
#: trailing ``# comment`` is tolerated.
_KCONFIG_DEFAULT_LINE: re.Pattern = re.compile(
    rf"^\s+default\s+(?P<q>[\"'])?{_PORT}(?(q)(?P=q))"
    r"\s*(?:if\s+\S.*|#.*)?$"
)

#: A ``help`` keyword line (option body) — the free-text help block that
#: follows it cannot contribute a value line until it ends.
_KCONFIG_HELP_OPENER: re.Pattern = re.compile(r"^\s+help\s*(?:#.*)?$")

#: A Kconfig directive word — the line that ends the current option's
#: value-matching scope (a new option, a menu, an if, a source, ...).
_KCONFIG_DIRECTIVE: re.Pattern = re.compile(
    r"^\s*(?:config|menu|endmenu|mainmenu|choice|endchoice|source|"
    r"if|endif|comment)\b"
)


class PortDiscoveryError(Exception):
    """The scan target is not a directory (or another bad-input state)."""


@dataclass(frozen=True)
class PortCandidate:
    """One line that claims to define the model server's port.

    ``path`` is the file path relative to the scanned root, ``lineno`` is
    1-based, ``port`` the extracted (validated) port, ``line`` the
    stripped source line — the evidence a human reviews before pinning.
    """

    path: str
    lineno: int
    port: int
    line: str


def _port_group(match: re.Match) -> str:
    """The group name carrying the port token in a line-pattern match.

    Every per-line pattern names it ``port`` except the identifier
    pattern's expansion branch, which uses ``port_expanded`` (Python's
    ``re`` forbids the same group name in two alternation branches).
    Returns the name of whichever port group participated.
    """
    for name in ("port", "port_expanded"):
        if name in match.re.groupindex and match.group(name) is not None:
            return name
    raise PortDiscoveryError("line-pattern match carries no port token")


def scan_text(text: str) -> list[tuple[int, int]]:
    """Pure per-text half of the scan.

    Returns ``(lineno, port)`` pairs (1-based, in line order, one entry
    per matching port occurrence on a line). Lines whose extracted number
    is not a valid TCP port (out of range, part of a longer number —
    impossible by construction of the token, or otherwise rejected by
    ``normalize_port``) are dropped.

    Beyond the per-line patterns (prose forms, the listen form, the
    inline-mapping form ``model server: N`` (quoted key tolerated: the
    JSON one-liner ``"model-server": 11434``), the
    concatenated-identifier form ``MODEL_SERVER_PORT=N``, and the
    fused-identifier address-key form
    ``MODEL_SERVER_URL="http://127.0.0.1:NNNN/v1"`` — the port embedded
    in the value's ``<address>:<port>`` instead of a bare number), a
    *model-server section* — an opener line (``[model-server]`` /
    ``model_server:`` / the quoted-key forms ``"model-server":`` and
    ``"model-server": {``) with a ``port`` key line inside it (quoted
    keys and values tolerated: ``"port": 11434``) or an *address key*
    line inside it (``url`` / ``endpoint`` / ``address`` / ``listen`` /
    ``host`` whose value carries a concrete ``<address>:<port>`` —
    ``url = "http://127.0.0.1:11434/v1"``) — is a candidate too:
    that is the config-shaped definition where the two words land on two
    lines. A section stays open until a section header, a shallower line,
    or a same/shallower-indent mapping key ends it. The
    concatenated-identifier form also accepts the value wrapped in a
    shell parameter-expansion default (``MODEL_SERVER_PORT=${MODEL_SERVER_PORT:-NNNN}``);
    a wrapper without a number is not a candidate. The *short-flag*
    form (``model-server -p NNNN``, ``-pNNNN``, ``-p=NNNN``, the value
    optionally quoted; the flag exactly ``-p`` at a token boundary,
    AFTER the component name, with any other flags in between) is a
    candidate too — the only shape that sees a definition whose flag
    carries no *port* letters at all. The *flag-default* form — the same
    flag forms (``-p``/``-port``/``--port`` at a token boundary, after
    the component name) whose value is wrapped in a shell
    parameter-expansion default (``model-server --port
    ${MODEL_SERVER_PORT:-NNNN}``, the service-script ``ExecStart`` form;
    any inner variable name, the ``:`` optional, a quote outside or
    inside the wrapper; a bare value is the short-flag shape's, and a
    wrapper without a number is not a candidate) is a candidate too.

    A second, independent two-line shape is the *Kconfig option* (a
    buildroot ``Config.in``): a ``config <symbol>`` line whose symbol is
    a fused model-server-port identifier, with an indented ``default
    <number>`` line in the option body before any directive, dedent,
    or ``help`` free-text block — the number may be bare or a matched
    single- or double-quoted decimal (the string-typed option form), and
    the value may carry a Kconfig *conditional* default clause
    (``default NNNN if <condition>`` — the value stays a literal decimal,
    the condition is not part of the value; a dangling ``default NNNN if``
    with no condition is not a value).
    Blank lines and indented comments keep
    the option open.
    """
    hits: list[tuple[int, int]] = []
    in_section = False
    opener_indent = 0
    in_kconfig_option = False
    in_kconfig_help = False
    help_indent = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        # Kconfig state machine (the buildroot Config.in form): a
        # ``config ..._MODEL_SERVER_PORT`` line arms the option; inside
        # it, an indented ``default <number>`` line is a candidate;
        # blank lines and indented comments keep it open; a directive,
        # a dedent, or a help block boundary closes/steps it.
        m = _KCONFIG_OPTION_OPENER.match(line)
        if m is not None:
            in_kconfig_help = False
            in_kconfig_option = _KCONFIG_MODEL_SERVER_PORT_SYMBOL.fullmatch(
                m.group(1)
            ) is not None
        elif in_kconfig_option and line.strip() and not line.lstrip().startswith(
            "#"
        ):
            # Blank / comment lines are inert: they neither close the
            # option nor end the help block — they are skipped entirely.
            if in_kconfig_help:
                if (len(line) - len(line.lstrip())) <= help_indent:
                    in_kconfig_help = False
                    # fall through: the line is option body again
                # else: still free-text help — never a value line
            if in_kconfig_option and not in_kconfig_help:
                indent = len(line) - len(line.lstrip())
                if (
                    _KCONFIG_DIRECTIVE.match(line) is not None
                    or indent == 0
                ):
                    in_kconfig_option = False
                else:
                    h = _KCONFIG_HELP_OPENER.match(line)
                    if h is not None:
                        in_kconfig_help = True
                        help_indent = indent
                    else:
                        v = _KCONFIG_DEFAULT_LINE.match(line)
                        if v is not None:
                            try:
                                port = normalize_port(v.group("port"))
                            except Exception:  # noqa: BLE001 — out-of-range ports are skipped, not errors
                                port = None
                            if port is not None:
                                hits.append((lineno, port))
        # Section state machine (the config form): an opener activates a
        # section; inside it, a port key line is a candidate; a header,
        # a shallower line, or a sibling mapping key closes it.
        if _MODEL_SERVER_SECTION_OPENER.match(line):
            in_section = True
            opener_indent = len(line) - len(line.lstrip())
        elif in_section:
            indent = len(line) - len(line.lstrip())
            if (
                _ANY_SECTION_HEADER.match(line) is not None
                or indent < opener_indent
                or (
                    indent <= opener_indent
                    and _MAPPING_KEY_LINE.match(line) is not None
                )
            ):
                in_section = False
            else:
                m = _MODEL_SERVER_SECTION_PORT_KEY.match(line)
                if m is None:
                    # The address-key shape: the port embedded in an
                    # <address>:<port> value (url/endpoint/address/
                    # listen/host) instead of a bare port key.
                    m = _MODEL_SERVER_SECTION_ADDR_KEY.match(line)
                if m is not None:
                    try:
                        port = normalize_port(m.group("port"))
                    except Exception:  # noqa: BLE001 — out-of-range ports are skipped, not errors
                        port = None
                    if port is not None:
                        hits.append((lineno, port))
        for pattern in (
            *MODEL_SERVER_PORT_PATTERNS,
            MODEL_SERVER_LISTEN_PATTERN,
            MODEL_SERVER_INLINE_PATTERN,
            MODEL_SERVER_IDENTIFIER_PATTERN,
            MODEL_SERVER_FUSED_ADDR_PATTERN,
            MODEL_SERVER_SHORT_FLAG_PATTERN,
            MODEL_SERVER_FLAG_DEFAULT_PATTERN,
        ):
            # Resume from just past each port token, not from the end of
            # the whole match: a pattern span can extend past a *second*
            # definition on the same line ("... port 8080 ... port 8081
            # for the model server") and finditer would swallow it.
            pos = 0
            while True:
                m = pattern.search(line, pos)
                if m is None:
                    break
                group = _port_group(m)
                pos = m.end(group)
                try:
                    port = normalize_port(m.group(group))
                except Exception:  # noqa: BLE001 — out-of-range ports are skipped, not errors
                    continue
                hits.append((lineno, port))
    # A line can satisfy several patterns only with duplicated port
    # tokens; de-duplicate exact (lineno, port) pairs and keep line order.
    seen: set[tuple[int, int]] = set()
    unique: list[tuple[int, int]] = []
    for hit in hits:
        if hit not in seen:
            seen.add(hit)
            unique.append(hit)
    return unique


def is_binary(data: bytes) -> bool:
    """Cheap binary heuristic: a NUL byte in the first 8 KiB."""
    return b"\x00" in data[:8192]


def is_tool_checkout(path: Path) -> bool:
    """True if *path* is a checkout of this tool's source tree.

    The marker is the package itself: a ``pengyos_apparmor`` subdirectory
    that is a Python package (carries an ``__init__.py``). Any such
    checkout — this repo, a vendored copy in a sibling workspace, an
    editable-install source tree — is full of *deliberate* model-server
    port mentions (docs, test fixtures, planner strings); a hit from
    under one is a self-match, never an upstream definition. Merely
    mentioning the name (a ``pengyos_apparmor.txt`` note, a directory
    without the package) is not a checkout.
    """
    return (path / "pengyos_apparmor" / "__init__.py").is_file()


def find_model_server_port(
    root: Union[str, Path],
    extra_skip: Iterable[Union[str, Path]] = (),
) -> list[PortCandidate]:
    """Scan the tree under *root* for model-server port definitions.

    Returns :class:`PortCandidate` records sorted by ``(path, lineno)``.
    Skips: non-directories raise :class:`PortDiscoveryError`;
    :data:`SKIP_DIRS` subtrees; every checkout of this tool (any directory
    containing the ``pengyos_apparmor`` package — this repo, vendored
    copies in sibling workspaces, editable-install source trees);
    anything in *extra_skip*; binary files; files larger than
    :data:`MAX_SCAN_BYTES`.

    Files are decoded as UTF-8 with a *leading BOM stripped*
    (``utf-8-sig``): a BOM is a file-level encoding marker, not line
    content — unstripped it sits on line 1 before ``^`` and breaks
    every line-start-anchored shape (the section opener, the Kconfig
    option opener), so a definition on line 1 of a BOM-marked file
    would be invisible to the scan.

    A *root* that is itself a tool checkout yields no candidates at all:
    every line under it is part of this tool, so every hit would be a
    self-match (the tool's docs/tests mention model-server ports
    constantly), and "no upstream definition found" is the honest answer.

    The scan is read-only and never decides which port is "the" one —
    multiple distinct candidates are reported for a human to resolve.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        raise PortDiscoveryError(f"not a directory: {root}")
    if is_tool_checkout(root_path):
        return []

    skip_paths = {TOOL_ROOT}
    for extra in extra_skip:
        skip_paths.add(Path(extra).resolve() if Path(extra).exists() else Path(extra))
    root_resolved = root_path.resolve()

    candidates: list[PortCandidate] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        # Prune noise and skipped subtrees in place so os.walk never descends.
        kept = []
        for name in dirnames:
            child = Path(dirpath) / name
            if name in SKIP_DIRS:
                continue
            if is_tool_checkout(child):
                # A checkout of this tool (any of them): its docs/tests
                # fixtures are self-matches, never upstream definitions.
                continue
            try:
                child_resolved = child.resolve()
            except OSError:  # broken symlink etc. — do not descend
                continue
            if any(
                child_resolved == skip or skip in child_resolved.parents
                for skip in skip_paths
            ):
                continue
            kept.append(name)
        dirnames[:] = kept

        for name in filenames:
            fpath = Path(dirpath) / name
            try:
                if fpath.stat().st_size > MAX_SCAN_BYTES:
                    continue
                data = fpath.read_bytes()
            except OSError:
                continue
            if is_binary(data):
                continue
            # utf-8-sig: strip ONE leading BOM if present. A BOM is a
            # file-level encoding marker, not line content: left in
            # place it sits on line 1 before '^' and breaks the
            # line-start-anchored shapes (the section opener, the
            # Kconfig option opener) — a definition on line 1 of a
            # BOM-marked file would be invisible to the scan. For a
            # BOM-less file utf-8-sig decodes identically to utf-8.
            text = data.decode("utf-8-sig", errors="replace")
            for lineno, port in scan_text(text):
                rel = fpath.relative_to(root_path).as_posix()
                line = text.splitlines()[lineno - 1].strip()
                candidates.append(PortCandidate(rel, lineno, port, line))

    candidates.sort(key=lambda c: (c.path, c.lineno))
    return candidates
