"""Tests for upstream model-server port discovery
(``pengyos_apparmor.portdiscovery``).

The follow-up "pin the local model server's port" is blocked
until PengyOS defines the model server's default port upstream — the port
must never be guessed. Until now that re-check was a hand-rolled grep,
re-derived from memory on every iteration. This file pins the automated
version: the conservative line patterns, the skip rules (every checkout
of this tool, VCS/build noise, binary and oversized files), the candidate
record shape, and the ``find-model-server-port`` CLI end to end. All
filesystem tests run on ``tmp_path`` fixture trees; no network, no root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_apparmor import portdiscovery
from pengyos_apparmor.cli import main


# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------


def test_portdiscovery_public_api():
    for attr in (
        "TOOL_ROOT",
        "DEFAULT_SCAN_ROOT",
        "SKIP_DIRS",
        "MAX_SCAN_BYTES",
        "MODEL_SERVER_PORT_PATTERNS",
        "PortDiscoveryError",
        "PortCandidate",
        "scan_text",
        "find_model_server_port",
    ):
        assert hasattr(portdiscovery, attr), f"portdiscovery.{attr} missing"


def test_portdiscovery_public_api_includes_listen_pattern():
    # The listen-form pattern ("listens on <addr>:<port>", no "port" word)
    # is part of the public contract: a port PengyOS defines in that form
    # must be discoverable, otherwise the port-pin item stays blocked.
    assert hasattr(portdiscovery, "MODEL_SERVER_LISTEN_PATTERN")
    assert "MODEL_SERVER_LISTEN_PATTERN" in portdiscovery.__all__


def test_portdiscovery_public_api_includes_inline_and_identifier_patterns():
    # The inline-mapping form ("model server: N") and the
    # concatenated-identifier form ("MODEL_SERVER_PORT=N") are part of
    # the public contract too: PengyOS is a buildroot-based distro, so
    # the port may well be defined in a defconfig env-var line or a
    # one-line YAML mapping — forms the prose/listen/section patterns
    # cannot see. Undiscoverable = the port-pin item stays blocked
    # forever with the port already defined upstream.
    for attr in ("MODEL_SERVER_INLINE_PATTERN", "MODEL_SERVER_IDENTIFIER_PATTERN"):
        assert hasattr(portdiscovery, attr), f"portdiscovery.{attr} missing"
        assert attr in portdiscovery.__all__


# ---------------------------------------------------------------------------
# scan_text — listen-form line and model-server section (config form)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("The model server listens on 127.0.0.1:11434.\n", [(1, 11434)]),
        ("the model server binds 0.0.0.0:9000 for all interfaces\n", [(1, 9000)]),
        ("model server serves on http://localhost:6006\n", [(1, 6006)]),
        ("the model-server listens on [::1]:8080 (IPv6 loopback)\n", [(1, 8080)]),
        # The config form: the component name on the opener line, the port
        # on the next line — the shape the single-line patterns cannot see.
        ("[model-server]\nport = 11434\n", [(2, 11434)]),
        ("[model-server.http]\nport = 8080\n", [(2, 8080)]),
        ("[model server]\nport is 9000\n", [(2, 9000)]),
        ("model_server:\n  port: 11434\n", [(2, 11434)]),
        ("service:\n  model_server:\n    port: 7777\n", [(3, 7777)]),
        ("services:\n  - model_server:\n    port: 7000\n", [(3, 7000)]),
        # Port keys with suffixes and the listen_ prefix are candidates too.
        ("[model-server]\nport-default = 1234\n", [(2, 1234)]),
        ("[model-server]\nlisten_port = 5555\n", [(2, 5555)]),
    ],
)
def test_scan_text_finds_listen_form_and_sections(text, expected):
    assert portdiscovery.scan_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # The listen form still requires a concrete <address>:<port>;
        # prose without one is a miss by design.
        "the model server listens on a Unix socket\n",
        "the model server binds loopback only\n",
        "the model server was listening before 2020\n",
        # Out-of-window numbers are skipped in the new forms too.
        "model server listens on 127.0.0.1:99999\n",
        "model server listens on 127.0.0.1:0\n",
        "[model-server]\nport = 99999\n",
        "[model-server]\nport = 65536\n",
        # Section shape without a usable port key.
        "[model-server]\nversion = 2\n",
        "[model-server]\nports = [11434]\n",  # array value
        "[model-server]\nport =\n",  # no number
        # A port key in some OTHER section is never a candidate.
        "[sshd]\nport = 2222\n",
        # The words on two lines WITHOUT a section opener is not a
        # definition (the section form needs the opener line).
        "the model server\nport = 11434\n",

        # The opener must be the line's shape, not a mid-sentence mention.
        "the [model server] block\nport = 11434\n",
    ],
)
def test_scan_text_rejects_listen_form_and_section_non_definitions(text):
    assert portdiscovery.scan_text(text) == []


@pytest.mark.parametrize(
    "text, expected",
    [
        # The inline-mapping form: the name, a mapping colon, then the
        # number directly — no "port" word, no address.
        ("model server: 9000\n", [(1, 9000)]),
        ("model-server: 8080\n", [(1, 8080)]),
        ("model_server: 11434\n", [(1, 11434)]),
        ("  model_server: '7000'\n", [(1, 7000)]),  # indented, quoted
        ("# the model server: 6006\n", [(1, 6006)]),  # a comment is a definition
        # The concatenated-identifier / env-var form: port fused to the
        # component name with -/_.
        ("MODEL_SERVER_PORT=11434\n", [(1, 11434)]),
        ('BR2_PENGY_MODEL_SERVER_PORT="8080"\n', [(1, 8080)]),  # buildroot
        ("model-server-port: 9000\n", [(1, 9000)]),
        ("export MODEL_SERVER_PORT ?= 8080\n", [(1, 8080)]),  # make-style
        ("MODEL-SERVER-PORT := 5000\n", [(1, 5000)]),
        ("model_server_default_port = 6006\n", [(1, 6006)]),  # middle segment
    ],
)
def test_scan_text_finds_inline_mapping_and_identifier_forms(text, expected):
    assert portdiscovery.scan_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # Plural fused keys are not a default-port definition.
        "MODEL_SERVER_PORTS = [8080]\n",
        # A number fused into the identifier itself is not a value.
        "model-server-port-8080\n",
        # The section opener line has no value — still just an opener.
        "model_server:\n",
        # A word after the colon, not a number: not a candidate from the
        # inline form (and no "port" word for the prose form either).
        "model server: it listens on 8080\n",
        "model server: see docs for the port\n",
        # A different component that merely contains the words.
        "model-serving: 8080\n",
        # Out-of-window numbers are skipped in the new forms too.
        "MODEL_SERVER_PORT=99999\n",
        "MODEL_SERVER_PORT=65536\n",
        "model server: 0\n",
    ],
)
def test_scan_text_rejects_inline_mapping_and_identifier_non_definitions(text):
    assert portdiscovery.scan_text(text) == []


def test_scan_text_identifier_form_dedups_with_prose_pattern():
    # "model server port is 8080" matches BOTH the prose pattern and the
    # identifier form (whitespace counts as a separator there); the
    # same (lineno, port) must be reported once.
    line = "the model server port is 8080\n"
    assert portdiscovery.scan_text(line) == [(1, 8080)]


def test_scan_text_section_close_stops_reporting_but_keeps_in_section_hits():
    # The in-section candidate is still reported; ports AFTER the close
    # (a new [header] or a sibling YAML key) are not.
    assert portdiscovery.scan_text(
        "[model-server]\nport = 11434\n[other]\nport = 8080\n"
    ) == [(2, 11434)]
    assert portdiscovery.scan_text(
        "[model-server]\nport = 11434\nother_service:\n  port: 8080\n"
    ) == [(2, 11434)]


def test_scan_text_section_scope_is_per_file_not_shared_across_calls():
    # The section state machine is local to one scan_text call: a
    # trailing opener that never gets a port key must not leak into a
    # later scan.
    assert portdiscovery.scan_text("[model-server]\n") == []
    assert portdiscovery.scan_text("port = 11434\n") == []


def test_scan_text_section_closes_at_sibling_yaml_key_and_reopens():
    text = (
        "model_server:\n"
        "  port: 11434\n"
        "other_service:\n"
        "  port: 8080\n"
        "model-server:\n"
        "  port: 6006\n"
    )
    assert portdiscovery.scan_text(text) == [(2, 11434), (6, 6006)]


def test_scan_text_listen_and_prose_forms_dedup_the_same_port():
    # A line that satisfies both the prose pattern and the listen form
    # must not double-report the same (lineno, port).
    line = (
        "the model server listens on 127.0.0.1:8080 and its port is 8080\n"
    )
    assert portdiscovery.scan_text(line) == [(1, 8080)]


# ---------------------------------------------------------------------------
# scan_text — the Kconfig (buildroot Config.in) two-line option form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        # The classic buildroot Config.in option: the fused symbol on the
        # ``config`` line, the value on the indented ``default`` line.
        (
            'config BR2_PENGY_MODEL_SERVER_PORT\n'
            '    int "Default port for the local model server"\n'
            '    default 11434\n',
            [(3, 11434)],
        ),
        # Case-insensitive symbols; two-space indents (Kconfig style).
        (
            "config pengy_model_server_port\n"
            "  default 9000\n",
            [(2, 9000)],
        ),
        # One optional middle identifier segment.
        (
            "config BR2_PENGY_MODEL_SERVER_DEFAULT_PORT\n"
            "    default 6006\n",
            [(2, 6006)],
        ),
        # A trailing comment on the value line is tolerated.
        (
            "config BR2_PENGY_MODEL_SERVER_PORT\n"
            "    default 11434 # the upstream default\n",
            [(2, 11434)],
        ),
        # Blank lines and indented comments keep the option open; other
        # body lines (range/depends on) do not close it.
        (
            "config BR2_PENGY_MODEL_SERVER_PORT\n"
            "\n"
            "    # the default below\n"
            "    range 1 65535\n"
            "    default 11434\n",
            [(5, 11434)],
        ),
        # The value may follow a help block — but only AFTER the help
        # text ends (a dedent back to the body indent).
        (
            "config BR2_PENGY_MODEL_SERVER_PORT\n"
            "    help\n"
            "      prose about the port\n"
            "    default 11434\n",
            [(4, 11434)],
        ),
        # Options inside a (indented) menu work too.
        (
            'menu "Pengy"\n'
            "    config BR2_PENGY_MODEL_SERVER_PORT\n"
            "        default 11434\n"
            "    endmenu\n",
            [(3, 11434)],
        ),
        # A comment on the config line itself.
        (
            "config BR2_PENGY_MODEL_SERVER_PORT # the model server\n"
            "    default 11434\n",
            [(2, 11434)],
        ),
    ],
)
def test_scan_text_finds_kconfig_option_form(text, expected):
    assert portdiscovery.scan_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # A plural symbol is not a default-port option.
        "config BR2_PENGY_MODEL_SERVER_PORTS\n    default 11434\n",
        # A different component's option: its default is not ours.
        "config BR2_PENGY_OTHER_SERVICE_PORT\n    default 11434\n",
        # A word after the fused ``port`` in the symbol is not the port.
        "config BR2_PENGY_MODEL_SERVER_PORTABLE\n    default 11434\n",
        # The value must be indented — a column-0 line is not option body.
        "config BR2_PENGY_MODEL_SERVER_PORT\ndefault 11434\n",
        # Only a BARE decimal expression is a value: hex...
        "config BR2_PENGY_MODEL_SERVER_PORT\n    default 0x2CF6\n",
        # ...arithmetic...
        "config BR2_PENGY_MODEL_SERVER_PORT\n    default 4000 + 1\n",
        # ...or a symbol reference is not.
        "config BR2_PENGY_MODEL_SERVER_PORT\n    default BR2_SOMETHING_ELSE\n",
        # Out-of-window numbers are skipped in this form too.
        "config BR2_PENGY_MODEL_SERVER_PORT\n    default 70000\n",
        "config BR2_PENGY_MODEL_SERVER_PORT\n    default 65536\n",
        # A config line with trailing junk is not an option opener.
        "config BR2_PENGY_MODEL_SERVER_PORT and friends\n    default 11434\n",
    ],
)
def test_scan_text_rejects_kconfig_non_definitions(text):
    assert portdiscovery.scan_text(text) == []


def test_scan_text_kconfig_next_option_and_directives_close_the_scope():
    # A new option, a source line, a menu/conditional directive, or a
    # dedent ends the value-matching scope; a later ``default`` line then
    # belongs to whatever follows (or to nothing).
    assert portdiscovery.scan_text(
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        "    default 11434\n"
        "config BR2_PENGY_MODEL_SERVER_TIMEOUT\n"
        "    default 9000\n"
    ) == [(2, 11434)]
    assert portdiscovery.scan_text(
        'config BR2_PENGY_MODEL_SERVER_PORT\n'
        '    default 11434\n'
        'source "other/Config.in"\n'
        "\n"
        "    default 8080\n"
    ) == [(2, 11434)]
    assert portdiscovery.scan_text(
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        "    default 11434\n"
        "if BR2_LINUX\n"
        "    default 8080\n"
    ) == [(2, 11434)]
    # The scope reopens for a second model-server-port option; a symbol
    # that does NOT end in the fused port (BR2_..._PORT_V2) does not arm.
    assert portdiscovery.scan_text(
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        "    default 11434\n"
        "config BR2_OTHER\n    default 1\n"
        "config BR2_V2_MODEL_SERVER_PORT\n"
        "    default 8080\n"
        "config BR2_PENGY_MODEL_SERVER_PORT_V2\n"
        "    default 6006\n"
    ) == [(2, 11434), (6, 8080)]


def test_scan_text_kconfig_help_block_is_free_text_never_a_value():
    # A ``default``-looking line INSIDE the help text is prose, not a
    # value: only the body line after the help block ends counts.
    assert portdiscovery.scan_text(
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        "    default 11434\n"
        "    help\n"
        "      the default 9999 is not right\n"
        "      11434 on loopback.\n"
    ) == [(2, 11434)]
    # Blank lines inside the help block do not end it.
    assert portdiscovery.scan_text(
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        "    help\n"
        "      prose\n"
        "\n"
        "      default 9999 is not right\n"
        "    default 11434\n"
    ) == [(6, 11434)]


def test_scan_text_kconfig_option_state_is_per_file_not_shared_across_calls():
    # The option state machine is local to one scan_text call: a trailing
    # opener that never gets a value must not leak into a later scan.
    assert portdiscovery.scan_text("config BR2_PENGY_MODEL_SERVER_PORT\n") == []
    assert portdiscovery.scan_text("    default 11434\n") == []


def test_scan_text_kconfig_and_section_machines_are_independent():
    # A file mixing the two two-line shapes (and a single-line form) must
    # report each exactly once, in line order.
    assert portdiscovery.scan_text(
        "[model-server]\n"
        "port = 11434\n"
        "[other]\n"
        "\n"
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        "    default 8080\n"
        "BR2_PENGY_MODEL_SERVER_PORT_ALT=6006\n"
    ) == [(2, 11434), (6, 8080)]
# ---------------------------------------------------------------------------
# scan_text — the quoted-key section form (JSON / JSONC / quoted YAML)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        # The classic multi-line JSON object: the quoted component key
        # opens the block, the quoted ``port`` key line carries the value.
        (
            '{\n  "model-server": {\n    "port": 11434\n  }\n}\n',
            [(3, 11434)],
        ),
        # Deeper nesting inside a components map — the section is scoped
        # to the model-server block, not the whole file.
        (
            '{\n  "components": {\n    "model-server": {\n'
            '      "port": 11434\n    },\n    "web": {\n'
            '      "port": 1234\n    }\n  }\n}\n',
            [(4, 11434)],
        ),
        # A trailing comma after the value (normal JSON) is tolerated.
        (
            '"model-server": {\n  "port": 11434,\n  "threads": 4\n}\n',
            [(2, 11434)],
        ),
        # A quoted numeric value (JSONC/relaxed JSON style) is a value.
        ('"model-server": {\n  "port": "8080"\n}\n', [(2, 8080)]),
        # Single-quoted keys (JSONC) with // comments on both lines.
        (
            "'model-server': { // listen\n"
            "  'port': 9000, // default\n"
            '  "timeout": 30\n}\n',
            [(2, 9000)],
        ),
        # A quoted YAML key with an unquoted (YAML-style) port line.
        ('"model-server":\n  port: 11434\n', [(2, 11434)]),
        # The unquoted flow-mapping opener (``model-server: {``) too.
        ('model-server: {\n  port: 8080\n}\n', [(2, 8080)]),
        # Suffix/prefixed port keys work in the quoted form as well.
        ('"model-server": {\n  "listen_port": 8080\n}\n', [(2, 8080)]),
        ('"model-server": {\n  "port-default": 1234\n}\n', [(2, 1234)]),
        # The quoted one-line inline-mapping value: the component key,
        # the colon, the number — no object brace, no "port" word.
        ('{"model-server": 11434}\n', [(1, 11434)]),
        ('  "model-server": 6006,\n', [(1, 6006)]),
    ],
)
def test_scan_text_finds_quoted_key_section_form(text, expected):
    assert portdiscovery.scan_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # A different component's JSON block: its port is not ours.
        '{\n  "database": {\n    "port": 5432\n  }\n}\n',
        # A plural/other quoted key is not a port key.
        '"model-server": {\n  "ports": [11434, 11435]\n}\n',
        # A string value (not a bare/quoted number) is not a value.
        '"model-server": {\n  "port": "eleven thousand"\n}\n',
        # Out-of-window numbers are skipped in this form too.
        '"model-server": {\n  "port": 99999\n}\n',
        '"model-server": {\n  "port": 65536\n}\n',
        # A quoted key without a mapping colon is not an opener.
        '"model-server"\n  port: 11434\n',
        # A prose-ish value line inside the block is not a port key line
        # (and the opener line itself carries no number).
        '"model-server": {\n  "help": "the default port is 11434"\n}\n',
        # The words on two lines WITHOUT the quoted opener is not a
        # definition (the section form needs the opener line).
        'the model server\n  "port": 11434\n',
    ],
)
def test_scan_text_rejects_quoted_key_section_non_definitions(text):
    assert portdiscovery.scan_text(text) == []


def test_scan_text_quoted_key_section_close_semantics_match_the_yaml_section():
    # A sibling quoted key at the opener's indent closes the section —
    # the sibling's deeper port line is never a candidate...
    assert portdiscovery.scan_text(
        '{\n  "model-server": {\n    "port": 11434\n  },\n'
        '  "web": {\n    "port": 1234\n  }\n}\n'
    ) == [(3, 11434)]
    # ...a model-server block that is the LAST element in its parent
    # closes at the parent's closing brace (shallower line)...
    assert portdiscovery.scan_text(
        '{\n  "a": {},\n  "model-server": {\n    "port": 11434\n  }\n}\n'
    ) == [(4, 11434)]
    # ...and the machine reopens for a second quoted model-server key
    # (a differently-suffixed quoted key is not an opener, but it is a
    # sibling mapping key that closes the first block).
    assert portdiscovery.scan_text(
        '"model-server": {\n  "port": 11434\n},\n'
        '"web": {},\n'
        '"model-server-fallback": {\n}\n'
        '"model-server": {\n  "port": 6006\n}\n'
    ) == [(2, 11434), (8, 6006)]


def test_scan_text_quoted_key_forms_dedup_the_same_port():
    # The single-line nested JSON object matches the prose pattern
    # (model-server ... port: N on one line); the section machine sees
    # no opener (the line does not end at the colon/brace). The same
    # (lineno, port) must still be reported exactly once.
    assert portdiscovery.scan_text('"model-server": {"port": 11434}\n') == [
        (1, 11434)
    ]
    # A port mentioned in a comment on the OPENER line: the prose
    # pattern sees it, the section machine does not (no port key line);
    # one report, not two.
    assert portdiscovery.scan_text(
        '"model-server": {  # model server port: 11434\n'
        '  "threads": 4\n}\n'
    ) == [(1, 11434)]
    # The compact one-liner with a QUOTED value (JSONC style) reports
    # once too (prose pattern 1, quote-tolerant; no other shape fires).
    assert portdiscovery.scan_text(
        '"model-server": {"port": "8080"}\n'
    ) == [(1, 8080)]
    # The reversed prose order is quote-tolerant as well: "port": N ... model server.
    assert portdiscovery.scan_text(
        '"port": "9000" is the "model server" setting\n'
    ) == [(1, 9000)]


def test_scan_text_quoted_key_form_composes_with_the_other_shapes():
    # A file mixing the quoted-key section, the Kconfig option, the
    # section form, and a prose line must report each exactly once,
    # in line order.
    assert portdiscovery.scan_text(
        '{\n  "model-server": {\n    "port": 11434\n  }\n}\n'
        "\n"
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        "    default 8080\n"
        "\n"
        "model server port is 6006\n"
    ) == [(3, 11434), (8, 8080), (10, 6006)]


def test_find_discovers_quoted_key_section_shape_in_a_tree(tmp_path):
    # The quoted-key shape as it would look upstream: a component
    # config.json defining the port in a JSON object, next to a JSON
    # config of another component that must stay silent.
    _write(
        tmp_path / "usr" / "share" / "pengy" / "model-server.json",
        '{\n'
        '  "listen-address": "127.0.0.1",\n'
        '  "model-server": {\n'
        '    "port": 11434,\n'
        '    "threads": 4\n'
        '  },\n'
        '  "panel": {\n'
        '    "port": 8443\n'
        '  }\n'
        '}\n',
    )
    _write(
        tmp_path / "usr" / "share" / "pengy" / "negatives.json",
        '{\n  "database": {\n    "port": 5432\n  },\n'
        '  "model-server": {\n    "ports": [11434]\n  }\n}\n',
    )
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [(c.path, c.lineno, c.port) for c in candidates] == [
        ("usr/share/pengy/model-server.json", 4, 11434)
    ]
    assert candidates[0].line == '"port": 11434,'


def test_cli_finds_quoted_key_section_candidates(tmp_path, capsys):
    # The quoted-key section shape is a first-class candidate end to
    # end: the CLI must surface the port key line as evidence (path:line
    # + port) and stop at the human-review next step, exactly like the
    # other forms.
    _write(
        tmp_path / "usr" / "share" / "pengy" / "model-server.json",
        '{\n  "model-server": {\n    "port": 11434\n  }\n}\n',
    )
    assert main(["find-model-server-port", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "candidate: usr/share/pengy/model-server.json:3: port 11434" in out
    assert '"port": 11434' in out  # the port key line is the evidence
    assert "one distinct port candidate: 11434" in out
    assert "plan-port-pin 11434 --patch" in out

# ---------------------------------------------------------------------------
# scan_text — the section address-key form (port embedded in a
# <address>:<port> value: url / endpoint / address / listen / host)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        # TOML: the port embedded in a listen-URL value, not a bare
        # ``port`` key.
        ('[model-server]\nurl = "http://127.0.0.1:11434/v1"\n', [(2, 11434)]),
        # A bare (unquoted) listen address, no scheme at all.
        ('[model-server]\nlisten = 127.0.0.1:11434\n', [(2, 11434)]),
        # YAML nesting: the quoted endpoint value inside the model-server
        # block.
        (
            'components:\n  model-server:\n    endpoint: "http://127.0.0.1:8080"\n',
            [(3, 8080)],
        ),
        # INI-style opener + address key.
        ('[model_server]\naddress = 127.0.0.1:9000\n', [(2, 9000)]),
        # The JSON quoted-key section carries the address key too.
        (
            '{\n  "model-server": {\n    "url": "http://127.0.0.1:1234"\n  }\n}\n',
            [(3, 1234)],
        ),
        # Bracketed IPv6 loopback inside the value.
        ('[model-server]\nendpoint = "http://[::1]:6006"\n', [(2, 6006)]),
        # The localhost address form.
        ('[model-server]\nhost = "localhost:7000"\n', [(2, 7000)]),
        # Unquoted scheme + trailing path after the port.
        ('[model-server]\nurl = http://0.0.0.0:4321/v1/chat\n', [(2, 4321)]),
        # Keys and the value are case-insensitive.
        ('[MODEL-SERVER]\nURL = "HTTP://127.0.0.1:2222"\n', [(2, 2222)]),
        # Both section shapes in one block: each line reported once, in
        # line order.
        ('[model-server]\nport = 1111\nurl = "http://127.0.0.1:2222"\n',
         [(2, 1111), (3, 2222)]),
    ],
)
def test_scan_text_finds_section_address_key_form(text, expected):
    assert portdiscovery.scan_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # A suffixed or prefixed key points at some OTHER component's
        # endpoint — never a model-server-port definition.
        '[model-server]\nmetrics-url = http://127.0.0.1:5000\n',
        '[model-server]\nbase-url = http://127.0.0.1:5000\n',
        '[model-server]\nserver_host = 127.0.0.1:5000\n',
        # A URL without a port / a bare IP / a path-only value: no
        # concrete <address>:<port>, not a candidate.
        '[model-server]\nurl = "http://localhost/noport"\n',
        '[model-server]\naddress = 127.0.0.1\n',
        '[model-server]\nendpoint = /opt/model-server\n',
        '[model-server]\nurl = "https://example.com:8080"\n',  # no concrete address form
        # Out-of-window numbers are skipped in this form too.
        '[model-server]\nurl = "http://127.0.0.1:99999"\n',
        '[model-server]\nurl = "http://127.0.0.1:65536"\n',
        '[model-server]\nurl = "http://127.0.0.1:114345"\n',  # 6 digits
        # A comment line is prose, not a key line (the pattern is
        # line-anchored past the key, and '#' is neither quote nor key).
        '[model-server]\n# url = http://127.0.0.1:11434\n',
        # A bare ``key:`` line (a YAML list item on the next line) is
        # not a value, exactly as with the port-key shape.
        'model_server:\n  url:\n    - "http://127.0.0.1:11434"\n',
        # The address key in some OTHER section (or with no opener at
        # all) is never a candidate.
        "[sshd]\nurl = \"http://127.0.0.1:11434\"\n",
        '[x]\nurl = "http://127.0.0.1:11434"\n',
        # A sibling component's URL in a sibling section.
        'components:\n  other-service:\n    url: "http://127.0.0.1:1234"\n',
    ],
)
def test_scan_text_rejects_section_address_key_non_definitions(text):
    assert portdiscovery.scan_text(text) == []


def test_scan_text_section_address_key_scopes_with_the_section_machine():
    # The address key is a candidate only while the model-server section
    # is active: a sibling component's URL line is silent, a later
    # model-server block is not.
    assert portdiscovery.scan_text(
        "components:\n"
        "  other-service:\n"
        '    url: "http://127.0.0.1:1234"\n'
        "  model-server:\n"
        '    url: "http://127.0.0.1:11434"\n'
        "  web:\n"
        '    url: "http://127.0.0.1:8443"\n'
    ) == [(5, 11434)]
    # After a section header closes the block, the same line shape is
    # prose again.
    assert portdiscovery.scan_text(
        '[model-server]\n'
        'url = "http://127.0.0.1:11434"\n'
        "[other]\n"
        'url = "http://127.0.0.1:8080"\n'
    ) == [(2, 11434)]


def test_find_discovers_section_address_key_shape_in_a_tree(tmp_path):
    # The address-key shape as it would look upstream: a component
    # config.toml defining the port in a listen-URL value, next to a
    # config of another component that must stay silent.
    _write(
        tmp_path / "usr" / "share" / "pengy" / "model-server.toml",
        "[model-server]\n"
        'url = "http://127.0.0.1:11434/v1"\n'
        "[panel]\n"
        'url = "http://127.0.0.1:8443"\n',
    )
    _write(
        tmp_path / "usr" / "share" / "pengy" / "negatives.toml",
        "[model-server]\n"
        'metrics-url = "http://127.0.0.1:5000"\n'
        "[other]\n"
        'url = "http://127.0.0.1:6006"\n',
    )
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [(c.path, c.lineno, c.port) for c in candidates] == [
        ("usr/share/pengy/model-server.toml", 2, 11434)
    ]
    assert candidates[0].line == 'url = "http://127.0.0.1:11434/v1"'


def test_cli_finds_section_address_key_candidates(tmp_path, capsys):
    # The address-key shape is a first-class candidate end to end: the
    # CLI surfaces the value line as evidence and stops at the
    # human-review next step, like the other forms.
    _write(
        tmp_path / "etc" / "pengy" / "model-server.toml",
        "[model-server]\n"
        "listen = 127.0.0.1:11434\n",
    )
    assert main(["find-model-server-port", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "candidate: etc/pengy/model-server.toml:2: port 11434" in out
    assert "listen = 127.0.0.1:11434" in out  # the value line is the evidence
    assert "one distinct port candidate: 11434" in out
    assert "plan-port-pin 11434 --patch" in out



def test_default_scan_root_is_the_workspace_containing_this_tool():
    # The CLI default scans the PengyOS workspace root — the directory
    # whose tools/ subdirectory contains this tool (where SPEC.md & co
    # live). Pinning the relative shape keeps the default correct even
    # if the workspace moves.
    ws = portdiscovery.DEFAULT_SCAN_ROOT
    assert (ws / "tools" / "pengyos-apparmor").resolve() == portdiscovery.TOOL_ROOT


def test_tool_root_is_this_repo():
    # The scan always skips the tool's own tree: its docs/tests/fixtures
    # are full of deliberate model-server port mentions (self-matches).
    root = portdiscovery.TOOL_ROOT
    assert root.is_dir()
    assert (root / "pengyos_apparmor" / "portdiscovery.py").is_file()
    assert (root / "profiles" / "pengy-agent").is_file()


# ---------------------------------------------------------------------------
# scan_text — the pure per-text half
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, expected",
    [
        ("The model server's default port is 11434.", [(1, 11434)]),
        ("model-server default port: 8080", [(1, 8080)]),
        ("model server port is 6006", [(1, 6006)]),
        ("The model server port = 3000 on loopback.", [(1, 3000)]),
        ("port 9000 is the model server port", [(1, 9000)]),
        ("PORT 54321 (MODEL SERVER) binds 127.0.0.1", [(1, 54321)]),
        ("model server port 8080 and the fallback is port 8081 for the model server",
         [(1, 8080), (1, 8081)]),
    ],
)
def test_scan_text_finds_definitions(line, expected):
    assert portdiscovery.scan_text(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        # No number on the line.
        "the model server listens on a Unix socket",
        "model server port TBD",
        # Out of range / part of a longer number / single digit.
        "model server port 99999",
        "model server port 1111434",
        "model server port 9",
        # The two words are not on the same line — patterns are
        # deliberately conservative (a hit must be a definition claim).
        "the model server binds loopback",
        "port 11434 is used by the sshd",
        # A URL-style mention without the word "port".
        "see http://127.0.0.1:11434/model-server for details",
        # 65536 is not a valid TCP port.
        "model server port 65536",
    ],
)
def test_scan_text_rejects_non_definitions(line):
    assert portdiscovery.scan_text(line) == []


def test_scan_text_is_per_line_and_1_based():
    text = (
        "no mention here\n"
        "the model server's default port is 11434\n"
        "and nothing else\n"
    )
    assert portdiscovery.scan_text(text) == [(2, 11434)]


def test_scan_text_dedupes_the_same_port_from_both_patterns():
    # A line that satisfies both pattern orderings must not double-report
    # the same (lineno, port).
    line = "model server port is 8080 (see the model server doc)"
    assert portdiscovery.scan_text(line) == [(1, 8080)]


# ---------------------------------------------------------------------------
# find_model_server_port — the tree walk
# ---------------------------------------------------------------------------


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture_tree(root: Path) -> None:
    # Positive candidates in three shapes, nested at different depths.
    _write(
        root / "SPEC.md",
        "PengyOS model server\n\n"
        "The model server's default port is 11434.\n"
        "It binds loopback only.\n",
    )
    _write(root / "tools" / "model-srv" / "spec.md", "model-server default port: 8080\n")
    _write(root / "notes.txt", "port 9000 is the model server port\n")
    # The config form (section-scoped) as it would look upstream.
    _write(
        root / "config" / "model-server.toml",
        "[model-server]\nport = 11434\nbind = \"127.0.0.1\"\n",
    )
    # Negatives: no candidate may come from these.
    _write(
        root / "negatives.md",
        "the model server listens on a Unix socket\n"
        "model server port TBD\n"
        "model server port 99999\n"
        "model server port 9\n",
    )
    # Noise that must be skipped entirely.
    _write(root / ".git" / "config", "model server port is 6666\n")
    _write(root / "__pycache__" / "x.py", "model server port is 6666\n")
    (root / "binfile.bin").parent.mkdir(parents=True, exist_ok=True)
    (root / "binfile.bin").write_bytes(b"\x00\x01model server port is 5555\x00")
    big = "model server port is 7777\n" + ("padding line\n" * 90000)
    _write(root / "big.txt", big)
    assert (root / "big.txt").stat().st_size > portdiscovery.MAX_SCAN_BYTES


def test_find_returns_sorted_candidates_with_relpaths_and_linenumbers(tmp_path):
    _fixture_tree(tmp_path)
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [
        (c.path, c.lineno, c.port) for c in candidates
    ] == [
        ("SPEC.md", 3, 11434),
        ("config/model-server.toml", 2, 11434),
        ("notes.txt", 1, 9000),
        ("tools/model-srv/spec.md", 1, 8080),
    ]
    # The evidence line is the stripped source line.
    assert candidates[0].line == "The model server's default port is 11434."
    # No noise file ever surfaces.
    paths = {c.path for c in candidates}
    assert not (
        paths
        & {
            ".git/config",
            "__pycache__/x.py",
            "binfile.bin",
            "big.txt",
            "negatives.md",
        }
    )


def test_find_on_empty_tree_finds_nothing(tmp_path):
    (tmp_path / "README.md").write_text("nothing to see\n", encoding="utf-8")
    assert portdiscovery.find_model_server_port(tmp_path) == []


def test_find_raises_when_root_is_not_a_directory(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(portdiscovery.PortDiscoveryError):
        portdiscovery.find_model_server_port(missing)
    # A file (not a directory) is also a bad root.
    f = tmp_path / "file.txt"
    f.write_text("model server port is 11434\n", encoding="utf-8")
    with pytest.raises(portdiscovery.PortDiscoveryError):
        portdiscovery.find_model_server_port(f)


def test_find_skips_an_extra_skip_directory(tmp_path):
    _write(tmp_path / "a" / "spec.md", "model server port is 11434\n")
    _write(tmp_path / "b" / "spec.md", "model server port is 11434\n")
    candidates = portdiscovery.find_model_server_port(
        tmp_path, extra_skip=[tmp_path / "a"]
    )
    assert [c.path for c in candidates] == ["b/spec.md"]


def test_find_skips_oversized_files(tmp_path):
    big = "model server port is 7777\n" + ("x\n" * 600000)
    (tmp_path / "big.txt").write_text(big, encoding="utf-8")
    assert (tmp_path / "big.txt").stat().st_size > portdiscovery.MAX_SCAN_BYTES
    assert portdiscovery.find_model_server_port(tmp_path) == []


def test_find_does_not_descend_into_symlink_loops_or_broken_symlinks(tmp_path):
    (tmp_path / "ok.txt").write_text("model server port is 11434\n", encoding="utf-8")
    (tmp_path / "self").symlink_to(tmp_path)  # loop
    (tmp_path / "broken").symlink_to(tmp_path / "gone")
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [c.path for c in candidates] == ["ok.txt"]


# ---------------------------------------------------------------------------
# find_model_server_port — pruning checkouts of this tool itself
# ---------------------------------------------------------------------------


def _tool_checkout(root: Path) -> None:
    # A minimal checkout of this tool: the package marker plus the kind
    # of files a real one carries (docs and test fixtures with
    # DELIBERATE model-server port mentions).
    _write(root / "pengyos_apparmor" / "__init__.py", '"""pkg"""\n')
    _write(root / "README.md", "The model server's default port is 11434.\n")
    _write(root / "spec.md", "model-server default port: 8080\n")
    _write(root / "tests" / "t.py", "model server port is 6006\n")


def test_sibling_checkouts_of_this_tool_are_pruned(tmp_path):
    # A vendored copy of this tool in a sibling tree must not surface its
    # fixture ports as upstream candidates; a real definition elsewhere
    # still does.
    _write(tmp_path / "SPEC.md", "The model server's default port is 11434.\n")
    _tool_checkout(tmp_path / "other-ws" / "tools" / "pengyos-apparmor")
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [(c.path, c.port) for c in candidates] == [("SPEC.md", 11434)]


def test_scan_root_that_is_a_tool_checkout_reports_no_candidates(tmp_path):
    # The root itself is a checkout: every line under it is this tool, so
    # every hit would be a self-match — "no upstream definition found" is
    # the honest answer.
    _tool_checkout(tmp_path)
    assert portdiscovery.find_model_server_port(tmp_path) == []


def test_vendored_checkout_does_not_hide_upstream_definitions_around_it(tmp_path):
    # A PengyOS repo that vendors this tool in a subdirectory: the
    # upstream definition at the repo root and in a sibling tool must
    # still be found; only the vendored checkout is pruned.
    _write(tmp_path / "SPEC.md", "The model server's default port is 11434.\n")
    _write(tmp_path / "tools" / "model-srv" / "spec.md", "model-server port: 8080\n")
    _tool_checkout(tmp_path / "tools" / "pengyos-apparmor")
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [(c.path, c.port) for c in candidates] == [
        ("SPEC.md", 11434),
        ("tools/model-srv/spec.md", 8080),
    ]


def test_merely_mentioning_the_package_name_is_not_a_checkout(tmp_path):
    # The checkout marker is the package itself (a pengyos_apparmor
    # subdirectory carrying __init__.py); a note file with the name, or a
    # directory without the marker, is ordinary content and gets scanned.
    _write(tmp_path / "pengyos_apparmor.txt", "model server port is 6006\n")
    _write(tmp_path / "pengyos_apparmor" / "notes.md", "model server port is 9000\n")
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert sorted(c.port for c in candidates) == [6006, 9000]


def test_cli_scan_of_this_tools_own_root_reports_blocked(capsys):
    # Anchor against self-matches: the tool's own tree is full of
    # deliberate model-server port mentions (this module's docstring,
    # the planner strings, the test fixtures). Scanning it must exit 3
    # with "no model-server port definition found", never list its own
    # fixtures as candidates.
    assert main(["find-model-server-port", str(portdiscovery.TOOL_ROOT)]) == 3
    assert "no model-server port definition found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# CLI — find-model-server-port
# ---------------------------------------------------------------------------


def test_cli_finds_candidates_and_prints_next_step(tmp_path, capsys):
    _write(tmp_path / "SPEC.md", "The model server's default port is 11434.\n")
    assert main(["find-model-server-port", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "candidate: SPEC.md:1: port 11434" in out
    assert "The model server's default port is 11434." in out
    assert "plan-port-pin 11434 --patch" in out


def test_find_discovers_inline_and_identifier_shapes_in_a_tree(tmp_path):
    # The two new shapes as they would look upstream: a buildroot-style
    # defconfig line and a one-line YAML mapping, plus a negative file
    # that must stay silent.
    _write(
        tmp_path / "configs" / "pengyos_core_m1_defconfig",
        '# PengyOS M1\nBR2_PENGY_MODEL_SERVER_PORT="11434"\n'
        "BR2_PENGY_SSHD_PORT=\"2222\"\n",
    )
    _write(tmp_path / "services.yaml", "services:\n  model_server: 8080\n")
    _write(
        tmp_path / "negatives.md",
        "MODEL_SERVER_PORTS = [8080]\nmodel-server-port-8080\n"
        "model server: it listens on 8080\n",
    )
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [(c.path, c.lineno, c.port) for c in candidates] == [
        ("configs/pengyos_core_m1_defconfig", 2, 11434),
        ("services.yaml", 2, 8080),
    ]
    assert candidates[0].line == 'BR2_PENGY_MODEL_SERVER_PORT="11434"'


def test_cli_finds_listen_form_and_section_candidates(tmp_path, capsys):
    # The two new shapes are first-class candidates end to end: the CLI
    # must surface them (path:line + port) and stop at the human-review
    # next step, exactly like the prose forms.
    _write(tmp_path / "SPEC.md", "the model server listens on 127.0.0.1:11434\n")
    _write(tmp_path / "config.toml", "[model-server]\nport = 11434\n")
    assert main(["find-model-server-port", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "candidate: SPEC.md:1: port 11434" in out
    assert "candidate: config.toml:2: port 11434" in out
    assert "port = 11434" in out  # the section key line is the evidence
    assert "one distinct port candidate: 11434" in out


def test_cli_finds_inline_and_identifier_candidates(tmp_path, capsys):
    # The two newest shapes are first-class candidates end to end too:
    # a buildroot defconfig line and a one-line YAML mapping must be
    # surfaced (path:line + port + evidence) and stop at the human-review
    # next step, exactly like the other forms.
    _write(
        tmp_path / "configs" / "defconfig",
        'BR2_PENGY_MODEL_SERVER_PORT="11434"\n',
    )
    _write(tmp_path / "services.yaml", "model_server: 11434\n")
    assert main(["find-model-server-port", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "candidate: configs/defconfig:1: port 11434" in out
    assert "candidate: services.yaml:1: port 11434" in out
    assert 'BR2_PENGY_MODEL_SERVER_PORT="11434"' in out
    assert "one distinct port candidate: 11434" in out
    assert "plan-port-pin 11434 --patch" in out


def test_find_discovers_kconfig_shape_in_a_tree(tmp_path):
    # The Kconfig two-line shape as it would look upstream: a buildroot
    # Config.in with a model-server-port option (plus a help block and an
    # unrelated option that must stay silent), alongside the negative
    # Config.in shapes that must not fire.
    _write(
        tmp_path / "package" / "pengy" / "Config.in",
        'menu "PengyOS"\n'
        "\n"
        "config BR2_PENGY\n"
        "    bool\n"
        "    default y\n"
        "\n"
        "if BR2_PENGY\n"
        "\n"
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        '    int "Default port for the local model server"\n'
        "    range 1 65535\n"
        "    help\n"
        "      The port the local model server listens on.\n"
        "    default 11434\n"
        "\n"
        "config BR2_PENGY_SSHD_PORT\n"
        "    int\n"
        "    default 2222\n"
        "\n"
        "endif\n"
        "endmenu\n",
    )
    _write(
        tmp_path / "negatives" / "Config.in",
        "config BR2_PENGY_MODEL_SERVER_PORTS\n"
        "    string\n"
        "    default 11434\n"
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        "    default 0x2CF6\n"
        "    default 70000\n",
    )
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [(c.path, c.lineno, c.port) for c in candidates] == [
        ("package/pengy/Config.in", 14, 11434)
    ]
    assert candidates[0].line.strip() == "default 11434"


def test_cli_finds_kconfig_candidates(tmp_path, capsys):
    # The Kconfig two-line shape is a first-class candidate end to end:
    # the CLI must surface the VALUE line as evidence (path:line + port)
    # and stop at the human-review next step, exactly like the other
    # forms.
    _write(
        tmp_path / "package" / "pengy" / "Config.in",
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        '    int "Default port for the local model server"\n'
        "    default 11434\n",
    )
    assert main(["find-model-server-port", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "candidate: package/pengy/Config.in:3: port 11434" in out
    assert "default 11434" in out  # the value line is the evidence
    assert "one distinct port candidate: 11434" in out
    assert "plan-port-pin 11434 --patch" in out


def test_cli_reports_all_distinct_candidates(tmp_path, capsys):
    _write(tmp_path / "a.md", "model server port is 11434\n")
    _write(tmp_path / "b.md", "model-server default port: 8080\n")
    assert main(["find-model-server-port", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "port 11434" in out
    assert "port 8080" in out
    assert "2 distinct port candidates" in out
    assert "plan-port-pin" not in out  # no auto-suggested command while ambiguous


def test_cli_no_candidates_exits_three(tmp_path, capsys):
    _write(tmp_path / "SPEC.md", "the model server listens on a socket\n")
    assert main(["find-model-server-port", str(tmp_path)]) == 3
    out = capsys.readouterr().out
    assert "no model-server port definition found" in out
    assert "do not guess a port" in out


def test_cli_missing_root_exits_two(tmp_path, capsys):
    assert main(["find-model-server-port", str(tmp_path / "nope")]) == 2
    assert "no such directory" in capsys.readouterr().err


def test_cli_default_root_is_the_pengyos_workspace_and_still_reports_blocked(capsys):
    # Anchor test: the default scan target is the PengyOS workspace root
    # (the parent of this tool), and — as of this commit — it contains no
    # model-server port definition, so the command reports "blocked"
    # (exit 3). When PengyOS upstream defines the port, THIS test is the
    # visible, reviewable signal that the blocked item has unblocked.
    assert main(["find-model-server-port"]) == 3
    out = capsys.readouterr().out
    assert "no model-server port definition found" in out


def test_cli_help_advertises_find_model_server_port(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "find-model-server-port" in out


# ---------------------------------------------------------------------------
# scan_text — the shell parameter-expansion default form (service scripts)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        # The service-script form: the value wrapped in a shell
        # parameter-expansion default, quoted or not.
        ('MODEL_SERVER_PORT="${MODEL_SERVER_PORT:-11434}"', [(1, 11434)]),
        ("MODEL_SERVER_PORT=${MODEL_SERVER_PORT:-11434}\n", [(1, 11434)]),
        ('export MODEL_SERVER_PORT="${MODEL_SERVER_PORT:-8080}"', [(1, 8080)]),
        # The ``${VAR-N}`` (no colon) variant is the same default claim.
        ("MODEL_SERVER_PORT=${MODEL_SERVER_PORT-11434}\n", [(1, 11434)]),
        # The value may be quoted INSIDE the expansion, and a quote may
        # sit OUTSIDE it (shell quoting) — either or both.
        ('MODEL_SERVER_PORT="${MODEL_SERVER_PORT:-"11434"}"\n', [(1, 11434)]),
        # Whitespace after the ``:`` is valid bash and stays a candidate.
        ("MODEL_SERVER_PORT=${MODEL_SERVER_PORT:- 9000}\n", [(1, 9000)]),
        # The inner variable name may differ from the outer one.
        ("MODEL_SERVER_PORT=${MS_PORT:-11434}\n", [(1, 11434)]),
        # Make-style assignment carrying the shell default.
        ("MODEL_SERVER_PORT ?= ${MODEL_SERVER_PORT:-8080}\n", [(1, 8080)]),
        # The bare forms stay candidates alongside the expansion form.
        ("MODEL_SERVER_PORT=11434\n", [(1, 11434)]),
        ('MODEL_SERVER_PORT="11434"\n', [(1, 11434)]),
        ('BR2_PENGY_MODEL_SERVER_PORT="8080"\n', [(1, 8080)]),
        # A concatenated value reports the FIRST expansion's default —
        # the line does carry a genuine "default 8080" claim.
        ('MODEL_SERVER_PORT="${A:-8080}${B:-8081}"\n', [(1, 8080)]),
    ],
)
def test_scan_text_finds_shell_expansion_default_form(text, expected):
    assert portdiscovery.scan_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # A wrapper without a number is not a default claim.
        "MODEL_SERVER_PORT=${MODEL_SERVER_PORT:-}\n",
        'MODEL_SERVER_PORT="${MODEL_SERVER_PORT:-$OTHER}"\n',
        "MODEL_SERVER_PORT=${A}${B}\n",
        # A leading space inside the quotes is not a bare number.
        'MODEL_SERVER_PORT=" 11434"\n',
        # Plural fused keys are not a default-port definition.
        "MODEL_SERVER_PORTS = [8080]\n",
        # A number fused into the identifier itself is not a value.
        "model-server-port-8080\n",
        # A wrapper on another component's variable is not ours.
        'FOO=${BAR:-11434}\n',
        # The ``-`` in ``PORT:-`` belongs to the OTHER variable's
        # expansion wrapper, not to the separator: a bare ``-`` before
        # the number reads as an identifier continuation (the
        # fused-number negative) and stays a miss.
        'FOO=${MODEL_SERVER_PORT:-11434}\n',
        # Out-of-window and too-long numbers are skipped in this form too.
        "MODEL_SERVER_PORT=${MODEL_SERVER_PORT:-99999}\n",
        "MODEL_SERVER_PORT=${MODEL_SERVER_PORT:-65536}\n",
        "MODEL_SERVER_PORT=${MODEL_SERVER_PORT:-114345}\n",
    ],
)
def test_scan_text_rejects_shell_expansion_non_definitions(text):
    assert portdiscovery.scan_text(text) == []


def test_scan_text_expansion_form_is_resumed_per_occurrence_like_the_others():
    # The per-occurrence resume rule applies to the identifier pattern's
    # two-branch alternation too: the match resumes just past the port
    # TOKEN (the bare or the expanded group — the one that participated),
    # so a second definition later on the same line is not swallowed.
    assert portdiscovery.scan_text(
        "model server port is 8080 and MODEL_SERVER_PORT=${MS_PORT:-8081}\n"
    ) == [(1, 8080), (1, 8081)]


# ---------------------------------------------------------------------------
# scan_text — the Kconfig string-typed (quoted default) form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        # A string-typed buildroot option: the value is a MATCHED quoted
        # decimal.
        (
            "config BR2_PENGY_MODEL_SERVER_PORT\n"
            "    type string\n"
            '    default "11434"\n',
            [(3, 11434)],
        ),
        # Single quotes are matched the same way.
        ("config BR2_PENGY_MODEL_SERVER_PORT\n    default '11434'\n", [(2, 11434)]),
        # A trailing comment after the quoted value is tolerated.
        (
            "config BR2_PENGY_MODEL_SERVER_PORT\n"
            '    default "11434" # the upstream default\n',
            [(2, 11434)],
        ),
        # The quoted value may follow a help block, like the bare one.
        (
            "config BR2_PENGY_MODEL_SERVER_PORT\n"
            "    help\n"
            "      prose about the port\n"
            '    default "11434"\n',
            [(4, 11434)],
        ),
        # Inside a (indented) menu too.
        (
            'menu "Pengy"\n'
            "    config BR2_PENGY_MODEL_SERVER_PORT\n"
            '        default "11434"\n'
            "    endmenu\n",
            [(3, 11434)],
        ),
        # The bare form stays a candidate next to the quoted one.
        ("config BR2_PENGY_MODEL_SERVER_PORT\n    default 11434\n", [(2, 11434)]),
    ],
)
def test_scan_text_finds_kconfig_quoted_default_form(text, expected):
    assert portdiscovery.scan_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # An unterminated quote is not a value (the conditional
        # close-group is what rejects it).
        'config BR2_PENGY_MODEL_SERVER_PORT\n    default "11434\n',
        # A dangling closing quote is not a value either.
        'config BR2_PENGY_MODEL_SERVER_PORT\n    default 11434"\n',
        # Mismatched quotes are not a value.
        'config BR2_PENGY_MODEL_SERVER_PORT\n    default "11434\'\n',
        # Hex, arithmetic, and symbol references stay non-values, quoted
        # or not (a quoted WORD is not a quoted decimal).
        "config BR2_PENGY_MODEL_SERVER_PORT\n    default 0x2CF6\n",
        'config BR2_PENGY_MODEL_SERVER_PORT\n    default "11434 + 1"\n',
        "config BR2_PENGY_MODEL_SERVER_PORT\n    default BR2_SOMETHING_ELSE\n",
        # Out-of-window numbers are skipped in the quoted form too.
        'config BR2_PENGY_MODEL_SERVER_PORT\n    default "70000"\n',
        # A too-long number (six digits) is not a port.
        'config BR2_PENGY_MODEL_SERVER_PORT\n    default "114345"\n',
        # Trailing junk after the quoted value is not a value line.
        'config BR2_PENGY_MODEL_SERVER_PORT\n    default "11434" extra\n',
        # A plural symbol is not a default-port option, quoted or not.
        'config BR2_PENGY_MODEL_SERVER_PORTS\n    default "11434"\n',
    ],
)
def test_scan_text_rejects_kconfig_quoted_default_non_definitions(text):
    assert portdiscovery.scan_text(text) == []


def test_scan_text_expansion_and_kconfig_forms_compose_with_the_others():
    # One file per dialect: the expansion form composes with the
    # single-line shapes in line order, and the Kconfig machine keeps
    # reporting the quoted value exactly once per option.
    assert portdiscovery.scan_text(
        "The model server listens on 127.0.0.1:6006\n"
        "MODEL_SERVER_PORT=${MODEL_SERVER_PORT:-8080}\n"
        "model server: 9000\n"
    ) == [(1, 6006), (2, 8080), (3, 9000)]
    assert portdiscovery.scan_text(
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        '    default "11434"\n'
        "config BR2_PENGY_MODEL_SERVER_TIMEOUT\n"
        '    default "5"\n'
    ) == [(2, 11434)]


# ---------------------------------------------------------------------------
# find-model-server-port — the new forms in a tree, and the CLI
# ---------------------------------------------------------------------------


def test_find_discovers_expansion_and_quoted_kconfig_shapes_in_a_tree(tmp_path):
    # The two new shapes as they would look upstream: a service script
    # with the shell fallback default, a buildroot Config.in with a
    # string-typed port option — and a negatives file carrying the
    # close-call shapes that must stay silent.
    _write(
        tmp_path / "usr" / "bin" / "pengy-model-server.sh",
        "#!/bin/sh\n"
        "# Local model server launcher.\n"
        'MODEL_SERVER_PORT="${MODEL_SERVER_PORT:-11434}"\n'
        'echo "model server listening on 127.0.0.1:${MODEL_SERVER_PORT}"\n',
    )
    _write(
        tmp_path / "buildroot-external" / "package" / "pengy" / "Config.in",
        "config BR2_PENGY\n"
        "    bool\n"
        "    default y\n"
        "\n"
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        '    string "Default port for the local model server"\n'
        "    range 1 65535\n"
        '    default "11434"\n'
        "\n"
        "config BR2_PENGY_OTHER_SERVICE_PORT\n"
        '    default "9999"\n',
    )
    _write(
        tmp_path / "negatives" / "launcher.sh",
        'OTHER_PORT="${OTHER_PORT:-6666}"\n'
        "MODEL_SERVER_PORT=${MODEL_SERVER_PORT:-}\n"
        'MODEL_SERVER_PORT="${MODEL_SERVER_PORT:-$ELSEWHERE}"\n',
    )
    _write(
        tmp_path / "negatives" / "Config.in",
        'config BR2_PENGY_MODEL_SERVER_PORTS\n    default "11434"\n'
        'config BR2_PENGY_MODEL_SERVER_PORT\n    default "11434\n'
        "config BR2_PENGY_MODEL_SERVER_PORT\n    default 0x2CF6\n",
    )
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [(c.path, c.lineno, c.port) for c in candidates] == [
        ("buildroot-external/package/pengy/Config.in", 8, 11434),
        ("usr/bin/pengy-model-server.sh", 3, 11434),
    ]
    # The evidence lines are the lines a human reviews.
    assert candidates[0].line.strip() == 'default "11434"'
    assert candidates[1].line.strip() == 'MODEL_SERVER_PORT="${MODEL_SERVER_PORT:-11434}"'


def test_cli_finds_expansion_and_quoted_kconfig_candidates(tmp_path, capsys):
    # End to end: both new shapes are first-class candidates with the
    # value line as evidence and the human-review next step intact.
    _write(
        tmp_path / "scripts" / "model-server.sh",
        'MODEL_SERVER_PORT="${MODEL_SERVER_PORT:-11434}"\n',
    )
    _write(
        tmp_path / "package" / "pengy" / "Config.in",
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        '    default "11434"\n',
    )
    assert main(["find-model-server-port", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "candidate: package/pengy/Config.in:2: port 11434" in out
    assert "candidate: scripts/model-server.sh:1: port 11434" in out
    assert 'default "11434"' in out
    assert 'MODEL_SERVER_PORT="${MODEL_SERVER_PORT:-11434}"' in out
    # One distinct port -> the exact follow-up, exactly like the other
    # forms (the port is evidence, never a guess).
    assert "one distinct port candidate: 11434" in out
    assert "plan-port-pin 11434 --patch" in out


# ---------------------------------------------------------------------------
# scan_text — the fused-identifier address-key form (the component name
# fused to a url/endpoint/address/listen/host stem on ONE line, the port
# embedded in the value's <address>:<port> instead of a bare number —
# the flat single-line counterpart of the section address-key shape)
# ---------------------------------------------------------------------------


def test_portdiscovery_public_api_includes_fused_addr_pattern():
    # The fused-identifier address-key pattern is part of the public
    # contract: a port PengyOS defines as MODEL_SERVER_URL="…" (or the
    # defconfig / TOML / YAML variants) must be discoverable, otherwise
    # the port-pin item stays blocked forever with the port already
    # defined upstream.
    assert hasattr(portdiscovery, "MODEL_SERVER_FUSED_ADDR_PATTERN")
    assert "MODEL_SERVER_FUSED_ADDR_PATTERN" in portdiscovery.__all__


@pytest.mark.parametrize(
    "text, expected",
    [
        # env/service-script style: the port embedded in the URL value.
        ('MODEL_SERVER_URL="http://127.0.0.1:8080"', [(1, 8080)]),
        # export form with a trailing path after the port.
        ('export MODEL_SERVER_ENDPOINT=http://127.0.0.1:8080/v1', [(1, 8080)]),
        # TOML: a quoted bare listen address, no scheme at all.
        ('model_server_address = "127.0.0.1:8080"', [(1, 8080)]),
        # YAML: bracketed IPv6 loopback in the value.
        ('model-server-listen: "http://[::1]:9000"', [(1, 9000)]),
        # buildroot defconfig style.
        ('BR2_PENGY_MODEL_SERVER_HOST="127.0.0.1:8080"', [(1, 8080)]),
        # make-style ?= separator, unquoted value.
        ('model-server-url ?= http://127.0.0.1:8080', [(1, 8080)]),
        # One optional middle identifier segment (the identifier shape's
        # tolerance, applied to the address stems).
        ('model_server_default_url = "http://127.0.0.1:11434/v1"', [(1, 11434)]),
        # The middle-segment tolerance means a suffixed stem is a
        # candidate TOO — a definition claim a human reviews and
        # rejects if it is not the model server's own port (the
        # identifier shape matches model_server_metrics_port alike;
        # precision is the human-review step, the same as every shape).
        ('model_server_metrics_url = "http://127.0.0.1:9091"', [(1, 9091)]),
        # YAML flat, indented, unquoted value.
        ('  model_server_url: http://127.0.0.1:8080', [(1, 8080)]),
        # TOML dotted key: the component name and the stem on one key.
        ('model-server.endpoint = "http://127.0.0.1:8080"', [(1, 8080)]),
        # Shell parameter-expansion wrapper around the value: the lazy
        # value prefix is transparent to ${VAR:-…}.
        ('MODEL_SERVER_URL="${MODEL_SERVER_URL:-http://127.0.0.1:8080}"',
         [(1, 8080)]),
        # A bare address with no scheme at all.
        ('MODEL_SERVER_LISTEN=127.0.0.1:8080', [(1, 8080)]),
        # The localhost address form.
        ('model_server_host = "localhost:7000"', [(1, 7000)]),
        # A commented definition line is still a claim (like the other
        # per-line shapes).
        ('# MODEL_SERVER_URL="http://127.0.0.1:8080"', [(1, 8080)]),
        # Keys and the value are case-insensitive.
        ('Model_Server_Url = "HTTP://127.0.0.1:2222"', [(1, 2222)]),
    ],
)
def test_scan_text_finds_fused_addr_key_form(text, expected):
    assert portdiscovery.scan_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # No model-server name in the identifier: another component.
        'METRICS_URL="http://127.0.0.1:9091"',
        # A URL without a port / no concrete <address>:<port>: not a
        # candidate (the address must be followed by :port).
        'MODEL_SERVER_URL="http://127.0.0.1"',
        'MODEL_SERVER_URL_PATH=/opt/model-server',
        # A non-concrete hostname is not a concrete address form
        # (consistent with the listen and section address-key shapes).
        'MODEL_SERVER_URL="http://example.com:8080"',
        # A plural fused key is not a candidate: the stem must be a
        # token end (exactly as with the fused *port* in the
        # identifier shape).
        'MODEL_SERVER_URLS="http://127.0.0.1:8080"',
        'MODEL_SERVER_URLS = [8080]',
        # A fused key that is NOT an address stem: a docs endpoint on
        # some server is not the model server's own listen definition.
        'MODEL_SERVER_DOCS = "http://127.0.0.1:8080/docs"',
        # Out-of-window numbers are skipped in this form too (the
        # shared normalize_port path).
        'MODEL_SERVER_URL = "https://127.0.0.1:99999"',
        'MODEL_SERVER_URL = "http://127.0.0.1:65536"',
        'MODEL_SERVER_URL = "http://127.0.0.1:114345"',  # 6 digits
        # The name and the stem must be FUSED (separator characters
        # only): the fully-spaced prose form "the model server url is
        # http://…" is not this shape — a prose URL without the word
        # *port* and without a listen verb stays a miss by design
        # (the same conservatism as the listen shape's "binds
        # loopback only").
        'the model server url is http://127.0.0.1:8080',
        # No address stem at all.
        'MODEL_SERVER = "http://127.0.0.1:8080"',
        # A bare ``key:`` line (a YAML list item on the next line) is
        # not a value, exactly as with the section address-key shape.
        'model-server.url:\n  - "http://127.0.0.1:8080"\n',
    ],
)
def test_scan_text_rejects_fused_addr_key_non_definitions(text):
    assert portdiscovery.scan_text(text) == []


def test_scan_text_fused_addr_key_composes_with_the_other_shapes():
    # A line carrying both the identifier form and the fused address
    # form reports the shared port ONCE (the exact (lineno, port)
    # de-dup covers the overlap).
    assert portdiscovery.scan_text(
        'MODEL_SERVER_PORT=8080 # see MODEL_SERVER_URL="http://127.0.0.1:8080"\n'
    ) == [(1, 8080)]
    # A file mixing the fused shape with the section and Kconfig shapes
    # reports every definition, once each, in line order.
    assert portdiscovery.scan_text(
        'MODEL_SERVER_URL="http://127.0.0.1:8080"\n'
        "[model-server]\n"
        "listen = 127.0.0.1:9000\n"
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        "    default 11434\n"
    ) == [(1, 8080), (3, 9000), (5, 11434)]
    # The section address-key shape is untouched: inside a model-server
    # section the (h) line is still reported by the section machine
    # (and the fused pattern sees no fused name there — no duplicate).
    assert portdiscovery.scan_text(
        '[model-server]\nurl = "http://127.0.0.1:11434/v1"\n'
    ) == [(2, 11434)]


def test_find_discovers_fused_addr_key_shape_in_a_tree(tmp_path):
    # The fused address-key shape as it would look upstream: a service
    # env file and a buildroot defconfig line, next to a file of
    # negatives that must stay silent.
    _write(
        tmp_path / "usr" / "share" / "pengy" / "model-server.env",
        'MODEL_SERVER_URL="http://127.0.0.1:11434/v1"\n',
    )
    _write(
        tmp_path / "configs" / "pengyos_core_m1_defconfig",
        'BR2_PENGY_MODEL_SERVER_HOST="127.0.0.1:11434"\n'
        "BR2_PENGY_PANEL_PORT=\"8443\"\n",
    )
    _write(
        tmp_path / "negatives.env",
        'METRICS_URL="http://127.0.0.1:9091"\n'
        'MODEL_SERVER_URL="http://example.com:8080"\n'
        "MODEL_SERVER_URLS=[8080]\n",
    )
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [(c.path, c.lineno, c.port) for c in candidates] == [
        ("configs/pengyos_core_m1_defconfig", 1, 11434),
        ("usr/share/pengy/model-server.env", 1, 11434),
    ]
    # The evidence lines are the lines a human reviews.
    assert candidates[0].line == 'BR2_PENGY_MODEL_SERVER_HOST="127.0.0.1:11434"'
    assert candidates[1].line == 'MODEL_SERVER_URL="http://127.0.0.1:11434/v1"'


def test_cli_finds_fused_addr_key_candidates(tmp_path, capsys):
    # The fused address-key shape is a first-class candidate end to
    # end: the CLI surfaces the definition line as evidence and stops
    # at the human-review next step, like the other forms.
    _write(
        tmp_path / "etc" / "pengy" / "model-server.env",
        'MODEL_SERVER_URL="http://127.0.0.1:11434/v1"\n',
    )
    assert main(["find-model-server-port", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "candidate: etc/pengy/model-server.env:1: port 11434" in out
    assert 'MODEL_SERVER_URL="http://127.0.0.1:11434/v1"' in out  # the value line is the evidence
    assert "one distinct port candidate: 11434" in out
    assert "plan-port-pin 11434 --patch" in out


# ---------------------------------------------------------------------------
# The short-flag shape (j): the component name, then the SHORT `-p`
# flag with a bare number value (`model-server -p 11434`). The long
# `--port` form is already caught by the prose patterns (a word
# boundary sits between the flag's `-` and `port`), so this shape
# exists only for the short flag, which carries no *port* letters at
# all and was invisible to every earlier shape.
# ---------------------------------------------------------------------------


def test_portdiscovery_public_api_includes_short_flag_pattern():
    # The short-flag pattern is part of the public contract: a port
    # PengyOS defines as `model-server -p NNNN` (a systemd ExecStart
    # line, a service script, or a spec sentence) must be discoverable,
    # otherwise the port-pin item stays blocked forever with the port
    # already defined upstream.
    assert hasattr(portdiscovery, "MODEL_SERVER_SHORT_FLAG_PATTERN")
    assert "MODEL_SERVER_SHORT_FLAG_PATTERN" in portdiscovery.__all__


@pytest.mark.parametrize(
    "text, expected",
    [
        # The plain short-flag form.
        ("model-server -p 11434\n", [(1, 11434)]),
        # The docker-style glued value.
        ("model-server -p11434\n", [(1, 11434)]),
        # An `=` between the flag and the value.
        ("model-server -p=11434\n", [(1, 11434)]),
        # Quoted values, both quote kinds.
        ('model-server -p "11434"\n', [(1, 11434)]),
        ("model-server -p '11434'\n", [(1, 11434)]),
        # The underscore and fully-spaced component-name separators.
        ("model_server -p 11434\n", [(1, 11434)]),
        ("model server -p 11434\n", [(1, 11434)]),
        # The flag is case-insensitive like every other shape.
        ("MODEL-SERVER -P 11434\n", [(1, 11434)]),
        # A systemd ExecStart line: the name inside a binary path, the
        # flag as the first real argument.
        ("ExecStart=/usr/bin/model-server -p 11434\n", [(1, 11434)]),
        # Other flags in between: the bounded gap carries them.
        ("model-server --verbose -p 11434\n", [(1, 11434)]),
        # A prose sentence about how the server is started.
        ("run the model server with -p 8080\n", [(1, 8080)]),
        # A quoted command line (a spec's code span).
        ('"model-server -p 9000"\n', [(1, 9000)]),
        # A commented definition line is still a claim (like the other
        # per-line shapes).
        ("# start the model server: -p 7000\n", [(1, 7000)]),
        # A flag trailing after the value is part of the line, not the
        # value: the number token is the claim.
        ("model-server -p 11434 -v\n", [(1, 11434)]),
        # The name fused inside a larger binary name (a path-like
        # token, as in the ExecStart form) — the no-lookbehind
        # looseness every other shape has: a definition claim a
        # human reviews.
        ("a-model-server -p 11434\n", [(1, 11434)]),
        ("model-server-x -p 11434\n", [(1, 11434)]),
    ],
)
def test_scan_text_finds_short_flag_form(text, expected):
    assert portdiscovery.scan_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # A suffixed long flag is not the short `-p` flag (the hit,
        # if any, is the prose shape's, pinned separately below).
        "model-server --ports 11434\n",
        # Long tokens that START with `-p` are not the flag: the
        # character after `-p` must be the value, not more letters.
        "model-server -profile 11434\n",
        "model-server -print 11434\n",
        # The flag with no number is not a definition.
        "model-server -P\n",
        "model-server -p\n",
        # Direction: the flag must come AFTER the component name.
        # Before it, the line's subject is whatever owns the flag.
        "-p 11434 for the model server\n",
        # No model-server name on the line: another component's flag.
        "daemon -p 11434\n",
        # Out-of-window numbers are skipped (the shared normalize_port
        # path), 6 digits included.
        "model-server -p 99999\n",
        "model-server -p 65536\n",
        "model-server -p 114345\n",
        "model-server -p 1234567\n",
        # A value that is not a bare number is not a claim.
        "model server -p http://11434\n",
        # A plural name is not the component (no word boundary after
        # the singular stem).
        "model servers -p 11434\n",
        # A 6-digit glued value: the number token cannot extend past
        # five digits, and it cannot start mid-number.
        "model-server -p114345\n",
    ],
)
def test_scan_text_rejects_short_flag_non_definitions(text):
    assert portdiscovery.scan_text(text) == []


def test_scan_text_short_flag_composes_with_the_other_shapes():
    # A suffixed long flag still reaches the PROSE patterns (the
    # boundary between the flag's `-` and `port` reads as the word
    # *port*): a single prose hit, nothing added by the short-flag
    # shape itself (the `-p` token is not present in these lines).
    assert portdiscovery.scan_text("model-server --http-port 11434\n") == [
        (1, 11434)
    ]
    assert portdiscovery.scan_text("model-server -port 11434 -x\n") == [
        (1, 11434)
    ]
    # The long `--port` form is the prose shape's, exactly as before
    # this shape existed.
    assert portdiscovery.scan_text("model-server --port 11434\n") == [
        (1, 11434)
    ]
    # A file mixing the short-flag shape with the fused address-key
    # and section shapes reports every definition, once each, in line
    # order.
    assert portdiscovery.scan_text(
        "model-server -p 8080\n"
        'MODEL_SERVER_URL="http://127.0.0.1:9000"\n'
        "[model-server]\n"
        "port = 11434\n"
    ) == [(1, 8080), (2, 9000), (4, 11434)]
    # The listen shape still owns the `--listen <address>:<port>` flag
    # line (no short `-p` token there — no duplicate).
    assert portdiscovery.scan_text(
        "model-server --listen 127.0.0.1:11434\n"
    ) == [(1, 11434)]


def test_find_discovers_short_flag_shape_in_a_tree(tmp_path):
    # The short-flag shape as it would look upstream: a systemd unit
    # file and a service script, next to a file of negatives that must
    # stay silent.
    _write(
        tmp_path / "etc" / "systemd" / "system" / "pengy-model-server.service",
        "[Service]\n"
        "ExecStart=/usr/bin/model-server -p 11434\n",
    )
    _write(
        tmp_path / "usr" / "lib" / "model-server.sh",
        "#!/bin/sh\n"
        "exec model-server --verbose -p 11434\n",
    )
    _write(
        tmp_path / "negatives.sh",
        "model-server -profile 11434\n"
        "-p 11434 for the model server\n"
        "daemon -p 11434\n"
        "model-server -p 99999\n",
    )
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [(c.path, c.lineno, c.port) for c in candidates] == [
        ("etc/systemd/system/pengy-model-server.service", 2, 11434),
        ("usr/lib/model-server.sh", 2, 11434),
    ]
    # The evidence lines are the lines a human reviews.
    assert candidates[0].line == "ExecStart=/usr/bin/model-server -p 11434"
    assert candidates[1].line == "exec model-server --verbose -p 11434"


def test_cli_finds_short_flag_candidates(tmp_path, capsys):
    # The short-flag shape is a first-class candidate end to end: the
    # CLI surfaces the definition line as evidence and stops at the
    # human-review next step, like the other forms.
    _write(
        tmp_path / "etc" / "systemd" / "system" / "pengy-model-server.service",
        "[Service]\n"
        "ExecStart=/usr/bin/model-server -p 11434\n",
    )
    assert main(["find-model-server-port", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "candidate: etc/systemd/system/pengy-model-server.service:2: port 11434" in out
    assert "ExecStart=/usr/bin/model-server -p 11434" in out  # the flag line is the evidence
    assert "one distinct port candidate: 11434" in out
    assert "plan-port-pin 11434 --patch" in out


# ---------------------------------------------------------------------------
# The flag-default shape: the flag's value wrapped in a shell
# parameter-expansion default (the service-script ExecStart form)
# ---------------------------------------------------------------------------


def test_portdiscovery_public_api_includes_flag_default_pattern():
    # The flag-default pattern is part of the public contract: a port
    # PengyOS defines as `model-server --port ${MODEL_SERVER_PORT:-NNNN}`
    # (a systemd ExecStart line, a service script, or a spec sentence)
    # must be discoverable, otherwise the port-pin item stays blocked
    # forever with the port already defined upstream.
    assert hasattr(portdiscovery, "MODEL_SERVER_FLAG_DEFAULT_PATTERN")
    assert "MODEL_SERVER_FLAG_DEFAULT_PATTERN" in portdiscovery.__all__


@pytest.mark.parametrize(
    "text, expected",
    [
        # The plain short-flag form with the wrapper value.
        ("model-server -p ${MODEL_SERVER_PORT:-11434}\n", [(1, 11434)]),
        # The docker-style glued wrapper.
        ("model-server -p${MODEL_SERVER_PORT:-11434}\n", [(1, 11434)]),
        # An `=` between the flag and the value.
        ("model-server -p=${MODEL_SERVER_PORT:-11434}\n", [(1, 11434)]),
        # The inner variable name is any name — the flag's position, not
        # the variable, scopes the line to the model server.
        ("model-server -p ${PORT:-11434}\n", [(1, 11434)]),
        # The bare `-` separator form of the parameter expansion.
        ("model-server -p ${PORT-11434}\n", [(1, 11434)]),
        # Whitespace after the `:-` separator is tolerated.
        ("model-server -p ${VAR:- 11434}\n", [(1, 11434)]),
        # Shell quoting outside the expansion (both quote kinds).
        ('model-server -p "${MODEL_SERVER_PORT:-11434}"\n', [(1, 11434)]),
        ("model-server -p '${MODEL_SERVER_PORT:-11434}'\n", [(1, 11434)]),
        # The quote inside the expansion (the JSON-ish default form).
        ('model-server -p ${VAR:-"11434"}\n', [(1, 11434)]),
        # The long `--port` flag (which the prose shapes only see with a
        # BARE number — the wrapper breaks their value position).
        ("model-server --port ${MODEL_SERVER_PORT:-11434}\n", [(1, 11434)]),
        # `=` between the long flag and the value.
        ("model-server --port=${MODEL_SERVER_PORT:-11434}\n", [(1, 11434)]),
        # The single-dash `-port` flag form.
        ("model-server -port ${VAR:-11434}\n", [(1, 11434)]),
        # The full systemd ExecStart line: the name inside a binary path,
        # the flag as the first real argument.
        (
            "ExecStart=/opt/pengy/bin/model-server"
            " --port ${MODEL_SERVER_PORT:-11434}\n",
            [(1, 11434)],
        ),
        # Other flags in between: the bounded gap carries them.
        ("model-server --verbose -p ${V:-8080}\n", [(1, 8080)]),
        # The underscore and fully-spaced component-name separators.
        ("model_server -p ${V:-8080}\n", [(1, 8080)]),
        ("model server -p ${V:-8080}\n", [(1, 8080)]),
        # The flag is case-insensitive like every other shape.
        ("MODEL-SERVER -P ${V:-8080}\n", [(1, 8080)]),
        # A prose sentence about how the server is started.
        ("run the model server with --port ${MS_PORT:-9000}\n", [(1, 9000)]),
        # A quoted command line (a spec's code span).
        ('"model-server -p ${V:-9000}"\n', [(1, 9000)]),
        # A commented definition line is still a claim (like the other
        # per-line shapes).
        ("# start the model server: --port ${V:-7000}\n", [(1, 7000)]),
        # The name fused inside a larger binary name (a path-like token,
        # as in the ExecStart form) — the no-lookbehind looseness every
        # other shape has: a definition claim a human reviews.
        ("a-model-server -p ${V:-11434}\n", [(1, 11434)]),
    ],
)
def test_scan_text_finds_flag_default_form(text, expected):
    assert portdiscovery.scan_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # A wrapper without a number is not a claim (the expansion is
        # just an unset-variable reference, no default to read).
        "model-server -p ${PORT:-}\n",
        "model-server --port ${PORT}\n",
        "model-server -p ${PORT:-} or 11434 manually\n",
        # Long tokens that START with `-p` are not the flag: the
        # character after `-p` must be the value, not more letters.
        "model-server -profile ${V:-11434}\n",
        # A suffixed long flag is not `--port` (the word boundary after
        # `port` keeps `--ports` out).
        "model-server --ports ${V:-11434}\n",
        # Direction: the flag must come AFTER the component name.
        # Before it, the line's subject is whatever owns the flag.
        "-p ${V:-11434} for the model server\n",
        # No model-server name on the line: another component's flag.
        "daemon --port ${V:-11434}\n",
        "--port ${V:-11434}\n",
        # Out-of-window numbers are skipped (the shared normalize_port
        # path), 6 digits included.
        "model-server -p ${V:-99999}\n",
        "model-server -p ${V:-65536}\n",
        "model-server -p ${V:-1234567}\n",
        # A 6-digit glued value: the number token cannot extend past
        # five digits, and it cannot start mid-number.
        "model-server -p${V:-114345}\n",
        # A plural name is not the component (no word boundary after
        # the singular stem).
        "model servers -p ${V:-11434}\n",
        # A nested expansion (braces inside the wrapper) is not a value:
        # the wrapper reads to the FIRST separator, never into nesting.
        "model-server -p ${V:-${OTHER:-11434}}\n",
    ],
)
def test_scan_text_rejects_flag_default_non_definitions(text):
    assert portdiscovery.scan_text(text) == []


def test_scan_text_flag_default_is_the_only_shape_that_sees_wrapped_values():
    # The bare-value flag lines are the OLD shapes' candidates exactly
    # as before: one hit each, and the flag-default pattern itself is
    # silent on them (its value position requires the wrapper).
    assert portdiscovery.scan_text("model-server -p 11434\n") == [(1, 11434)]
    assert portdiscovery.scan_text("model-server --port 11434\n") == [
        (1, 11434)
    ]
    assert (
        portdiscovery.MODEL_SERVER_FLAG_DEFAULT_PATTERN.search(
            "model-server -p 11434"
        )
        is None
    )
    assert (
        portdiscovery.MODEL_SERVER_FLAG_DEFAULT_PATTERN.search(
            "model-server --port 11434"
        )
        is None
    )
    # The wrapper value is what the new shape adds: the same flag line
    # with an expansion in the value position is a candidate only via
    # the flag-default pattern (the prose and short-flag shapes both
    # read a bare number there).
    assert portdiscovery.scan_text("model-server -p ${V:-11434}\n") == [
        (1, 11434)
    ]
    assert (
        portdiscovery.MODEL_SERVER_SHORT_FLAG_PATTERN.search(
            "model-server -p ${V:-11434}"
        )
        is None
    )


def test_scan_text_flag_default_composes_with_the_other_shapes():
    # A file mixing the flag-default shape with the short-flag, the
    # fused address-key and the section shapes reports every definition,
    # once each, in line order.
    assert portdiscovery.scan_text(
        "model-server -p 8080\n"
        "model-server --port ${V:-9000}\n"
        'MODEL_SERVER_URL="http://127.0.0.1:11434"\n'
        "[model-server]\n"
        "port = 7000\n"
    ) == [(1, 8080), (2, 9000), (3, 11434), (5, 7000)]
    # Two flag values on one line: the bare one is the short-flag
    # shape's, the wrapped one the flag-default shape's — both are
    # separate definition claims, both are reported.
    assert portdiscovery.scan_text(
        "model-server -p 9000 -p ${V:-11434}\n"
    ) == [(1, 9000), (1, 11434)]
    # The listen shape still owns the `--listen <address>:<port>` flag
    # line (no `-p`/`--port` token there — no duplicate).
    assert portdiscovery.scan_text(
        "model-server --listen 127.0.0.1:11434\n"
    ) == [(1, 11434)]


def test_scan_text_flag_default_dedups_with_the_identifier_shape():
    # A line whose wrapper carries a fused model-server-port identifier
    # as its inner variable is still ONE candidate: the identifier shape
    # cannot reach the number through the flag (the value separator
    # before the wrapper is a flag, not an `=`/`:` key separator), so no
    # duplicate (lineno, port) pair is produced.
    assert portdiscovery.scan_text(
        "ExecStart=/opt/pengy/bin/model-server"
        " --port ${MODEL_SERVER_PORT:-11434}\n"
    ) == [(1, 11434)]
    # The identifier shape's own wrapper form is untouched: a plain
    # assignment line (no flag, no spaced name) is still exactly one
    # candidate, reported by the identifier shape alone.
    assert portdiscovery.scan_text(
        'MODEL_SERVER_PORT="${MODEL_SERVER_PORT:-11434}"\n'
    ) == [(1, 11434)]


def test_find_discovers_flag_default_shape_in_a_tree(tmp_path):
    # The flag-default shape as it would look upstream: a systemd unit
    # file and a service script using the ExecStart fallback form, next
    # to a file of negatives that must stay silent.
    _write(
        tmp_path / "etc" / "systemd" / "system" / "pengy-model-server.service",
        "[Service]\n"
        "EnvironmentFile=-/etc/pengy/model-server.env\n"
        "ExecStart=/opt/pengy/bin/model-server"
        " --port ${MODEL_SERVER_PORT:-11434}\n",
    )
    _write(
        tmp_path / "usr" / "lib" / "model-server.sh",
        "#!/bin/sh\n"
        "exec model-server --verbose -p ${V:-11434}\n",
    )
    _write(
        tmp_path / "negatives.sh",
        "model-server -profile ${V:-11434}\n"
        "model-server --ports ${V:-11434}\n"
        "model-server -p ${PORT:-}\n"
        "-p ${V:-11434} for the model server\n"
        "daemon --port ${V:-11434}\n"
        "model-server -p ${V:-99999}\n",
    )
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [(c.path, c.lineno, c.port) for c in candidates] == [
        ("etc/systemd/system/pengy-model-server.service", 3, 11434),
        ("usr/lib/model-server.sh", 2, 11434),
    ]
    # The evidence lines are the lines a human reviews.
    assert candidates[0].line == (
        "ExecStart=/opt/pengy/bin/model-server"
        " --port ${MODEL_SERVER_PORT:-11434}"
    )
    assert candidates[1].line == "exec model-server --verbose -p ${V:-11434}"


def test_cli_finds_flag_default_candidates(tmp_path, capsys):
    # The flag-default shape is a first-class candidate end to end: the
    # CLI surfaces the definition line as evidence and stops at the
    # human-review next step, like the other forms.
    _write(
        tmp_path / "etc" / "systemd" / "system" / "pengy-model-server.service",
        "[Service]\n"
        "ExecStart=/opt/pengy/bin/model-server"
        " --port ${MODEL_SERVER_PORT:-11434}\n",
    )
    assert main(["find-model-server-port", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert (
        "candidate: etc/systemd/system/pengy-model-server.service:2:"
        " port 11434" in out
    )
    assert (
        "ExecStart=/opt/pengy/bin/model-server"
        " --port ${MODEL_SERVER_PORT:-11434}" in out
    )  # the ExecStart line is the evidence
    assert "one distinct port candidate: 11434" in out
    assert "plan-port-pin 11434 --patch" in out


# ---------------------------------------------------------------------------
# find_model_server_port — UTF-8 BOM handling (utf-8-sig)
# ---------------------------------------------------------------------------
# A UTF-8 BOM (U+FEFF) is a file-level encoding marker, not line content.
# Left in place it sits on line 1 before '^' and breaks every
# line-start-anchored shape (the section opener, the Kconfig option
# opener) — a definition on line 1 of a BOM-marked file was invisible to
# the scan. The read path decodes with utf-8-sig, which strips ONE
# leading BOM and decodes BOM-less files identically to utf-8.

_BOM = b"\xef\xbb\xbf"


def test_find_strips_a_leading_utf8_bom_before_the_section_opener(tmp_path):
    # A BOM-marked TOML/INI section: the opener is on line 1, so the
    # BOM sat directly in front of the line-start-anchored opener
    # pattern. The port key on line 2 must be a candidate.
    (tmp_path / "model-server.toml").write_bytes(
        _BOM + b"[model-server]\nport = 11434\n"
    )
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [(c.path, c.lineno, c.port) for c in candidates] == [
        ("model-server.toml", 2, 11434)
    ]
    # The BOM is stripped, so the evidence line is clean source.
    assert candidates[0].line == "port = 11434"


def test_find_strips_a_leading_utf8_bom_before_the_kconfig_opener(tmp_path):
    # A BOM-marked buildroot Config.in: the 'config <symbol>' line is on
    # line 1 — the line-start-anchored Kconfig opener needs the BOM gone
    # for the indented 'default' value line to be a candidate.
    (tmp_path / "Config.in").write_bytes(
        _BOM + b"config BR2_PENGY_MODEL_SERVER_PORT\n"
        b"\tdefault 11434\n"
    )
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [(c.path, c.lineno, c.port) for c in candidates] == [
        ("Config.in", 2, 11434)
    ]
    assert candidates[0].line == "\tdefault 11434".strip()


def test_find_bom_file_with_unanchored_shape_finds_as_before(tmp_path):
    # The unanchored shapes (prose, identifier) never needed the BOM
    # stripped — pin the behavior so the utf-8-sig change is proven a
    # pure no-op for them.
    (tmp_path / "spec.md").write_bytes(
        _BOM + b"The model server's default port is 11434.\n"
    )
    (tmp_path / "env.sh").write_bytes(_BOM + b"MODEL_SERVER_PORT=9000\n")
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [(c.path, c.lineno, c.port) for c in candidates] == [
        ("env.sh", 1, 9000),
        ("spec.md", 1, 11434),
    ]


def test_find_strips_only_one_leading_bom(tmp_path):
    # utf-8-sig strips the BOM only at the START of the file (where an
    # encoding marker lives). A U+FEFF on a later line is line content
    # (a concatenated file), not an encoding marker: it still breaks the
    # line-start-anchored opener, and the scan must not pretend
    # otherwise.
    (tmp_path / "model-server.toml").write_bytes(
        b"\n" + _BOM + b"[model-server]\nport = 11434\n"
    )
    assert portdiscovery.find_model_server_port(tmp_path) == []


def test_bom_is_not_a_binary_skip_trigger(tmp_path):
    # A BOM file is text: the binary heuristic (NUL in first 8 KiB) must
    # not skip it — EF BB BF carries no NUL.
    (tmp_path / "model-server.toml").write_bytes(
        _BOM + b"[model-server]\nport = 11434\n"
    )
    data = (tmp_path / "model-server.toml").read_bytes()
    assert not portdiscovery.is_binary(data)
    assert portdiscovery.find_model_server_port(tmp_path) != []


def test_cli_finds_candidates_in_a_bom_marked_file(tmp_path, capsys):
    # End to end: a BOM-marked config file is a ready-to-review
    # candidate through the CLI, like a BOM-less one.
    (tmp_path / "model-server.toml").write_bytes(
        _BOM + b"[model-server]\nport = 11434\n"
    )
    assert main(["find-model-server-port", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "candidate: model-server.toml:2: port 11434" in out
    assert "port = 11434" in out
    assert "one distinct port candidate: 11434" in out
    assert "plan-port-pin 11434 --patch" in out


# ---------------------------------------------------------------------------
# scan_text — the Kconfig *conditional* default form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        # The standard buildroot idiom: the value stays a literal decimal,
        # the condition after the ``if`` is metadata, not part of the value.
        (
            "config BR2_PENGY_MODEL_SERVER_PORT\n"
            "    default 11434 if BR2_PENGY_LOCAL\n",
            [(2, 11434)],
        ),
        # The string-typed (quoted) option form and the conditional
        # clause compose.
        (
            "config BR2_PENGY_MODEL_SERVER_PORT\n"
            '    default "11434" if BR2_PENGY_LOCAL\n',
            [(2, 11434)],
        ),
        # Any Kconfig condition expression after the ``if`` is tolerated
        # (or/and/not — the value is what matters, not the condition).
        (
            "config BR2_PENGY_MODEL_SERVER_PORT\n"
            "    default 11434 if BR2_A || BR2_B && !BR2_C\n",
            [(2, 11434)],
        ),
        # A trailing comment after the condition is tolerated.
        (
            "config BR2_PENGY_MODEL_SERVER_PORT\n"
            "    default 11434 if BR2_X # the default for the local board\n",
            [(2, 11434)],
        ),
        # Multiple conditional defaults (the legitimate buildroot form):
        # every value line is a candidate, in line order — the scan
        # reports the evidence, a human confirms which port PengyOS
        # intends.
        (
            "config BR2_PENGY_MODEL_SERVER_PORT\n"
            "    default 11434 if BR2_A\n"
            "    default 9000 if BR2_B\n",
            [(2, 11434), (3, 9000)],
        ),
        # The conditional value follows a help block exactly as a bare
        # default does — only AFTER the help text ends.
        (
            "config BR2_PENGY_MODEL_SERVER_PORT\n"
            "    help\n"
            "      prose about the port\n"
            "    default 11434 if BR2_A\n",
            [(4, 11434)],
        ),
        # A ``# comment`` containing the word ``if`` is still just a
        # comment: the bare default stays a candidate.
        (
            "config BR2_PENGY_MODEL_SERVER_PORT\n"
            "    default 11434 # the default if BR2_X is set\n",
            [(2, 11434)],
        ),
        # The unconditional forms keep working (the conditional clause is
        # optional, a pure extension of the value line).
        (
            "config BR2_PENGY_MODEL_SERVER_PORT\n"
            "    default 11434\n",
            [(2, 11434)],
        ),
    ],
)
def test_scan_text_finds_kconfig_conditional_default_form(text, expected):
    assert portdiscovery.scan_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # A DANGLING ``if`` (no condition) is malformed Kconfig, not a
        # value: the ``if`` must be followed by a condition.
        "config BR2_PENGY_MODEL_SERVER_PORT\n    default 11434 if\n",
        # Arithmetic with a condition: the value is an expression, not a
        # literal number — not a candidate, with or without the ``if``.
        "config BR2_PENGY_MODEL_SERVER_PORT\n    default 4000 + 1 if BR2_X\n",
        # A condition-looking token without the ``if`` separator is not a
        # conditional default (``ifx`` is not ``if``).
        "config BR2_PENGY_MODEL_SERVER_PORT\n    default 11434 ifx BR2_X\n",
        # Out-of-window values are skipped in the conditional form too.
        "config BR2_PENGY_MODEL_SERVER_PORT\n    default 99999 if BR2_X\n",
        "config BR2_PENGY_MODEL_SERVER_PORT\n    default 65536 if BR2_X\n",
    ],
)
def test_scan_text_rejects_kconfig_conditional_default_non_definitions(text):
    assert portdiscovery.scan_text(text) == []


def test_scan_text_kconfig_conditional_default_help_block_is_free_text():
    # A conditional-default-LOOKING line inside the help text is prose,
    # not a value: only a body line after the help block ends counts.
    assert portdiscovery.scan_text(
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        "    default 11434 if BR2_A\n"
        "    help\n"
        "      the default 9999 if BR2_B is not right\n"
    ) == [(2, 11434)]
    # And a value AFTER a help block that mentions the idiom: the help
    # prose never contributes a candidate.
    assert portdiscovery.scan_text(
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        "    help\n"
        "      uses default NNNN if BR2_X upstream\n"
        "    default 8080 if BR2_A\n"
    ) == [(4, 8080)]


def test_scan_text_kconfig_conditional_default_composes_with_directives():
    # The option-scope rules are unchanged: a directive or dedent after a
    # conditional default closes the scope, and the next option re-arms it.
    assert portdiscovery.scan_text(
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        "    default 11434 if BR2_A\n"
        "config BR2_PENGY_MODEL_SERVER_TIMEOUT\n"
        "    default 9000 if BR2_B\n"
    ) == [(2, 11434)]
    assert portdiscovery.scan_text(
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        "    default 11434 if BR2_A\n"
        "if BR2_LINUX\n"
        "    default 8080 if BR2_B\n"
    ) == [(2, 11434)]


def test_find_discovers_kconfig_conditional_default_in_a_tree(tmp_path):
    # The conditional idiom as it would look upstream: a buildroot
    # external-tree Config.in defining the option with conditional
    # defaults. The tree scan reports every value line.
    (tmp_path / "Config.in").write_text(
        'menu "Pengy services"\n'
        "\n"
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        '    int "Default port for the local model server"\n'
        "    range 1 65535\n"
        "    default 11434 if BR2_PENGY_QEMU\n"
        '    default "11434" if BR2_PENGY_USB\n'
        "\n"
        "endmenu\n",
        encoding="utf-8",
    )
    candidates = portdiscovery.find_model_server_port(tmp_path)
    assert [(c.path, c.lineno, c.port) for c in candidates] == [
        ("Config.in", 6, 11434),
        ("Config.in", 7, 11434),
    ]
    assert candidates[0].line == "default 11434 if BR2_PENGY_QEMU"
    assert candidates[1].line == 'default "11434" if BR2_PENGY_USB'


def test_cli_finds_kconfig_conditional_default_candidates(tmp_path, capsys):
    # End to end: a conditional default in a buildroot Config.in is a
    # ready-to-review candidate through the CLI.
    (tmp_path / "Config.in").write_text(
        "config BR2_PENGY_MODEL_SERVER_PORT\n"
        "    int \"Default port for the local model server\"\n"
        "    default 11434 if BR2_PENGY_QEMU\n",
        encoding="utf-8",
    )
    assert main(["find-model-server-port", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "candidate: Config.in:3: port 11434" in out
    assert "default 11434 if BR2_PENGY_QEMU" in out
    assert "one distinct port candidate: 11434" in out
    assert "plan-port-pin 11434 --patch" in out
