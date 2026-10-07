"""Tests for the nftables ruleset validator (pengyos_net.nft_validate).

Covers both halves of the validator item:

* **syntax** — the structural nft parser (comments/strings, ``;``- or
  newline-terminated statements, balanced braces, duplicate blocks),
  exercised directly on crafted rule fragments;
* **policy** — the baseline pinning of ``nftables/pengyos.nft``
  (default-deny inbound, no ``accept`` before the allow table, inbound
  allow list exactly tcp/22), plus negative cases for each rule.

Everything runs without root and without network access — the ruleset
is checked offline by the stdlib-only parser, not the ``nft`` binary
(``nft -c`` needs privileges); real application is a non-goal
(see spec.md).
"""
import os

import pytest

from pengyos_net.nft_validate import (
    EXPECTED_NEW_ACCEPTS,
    NftSyntaxError,
    chain_policy,
    chain_statements,
    dport_accepts,
    parse_nft_ruleset,
    strip_comments,
    validate_ruleset,
    validate_ruleset_text,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NFT_DIR = os.path.join(REPO_ROOT, "nftables")
RULESET = os.path.join(NFT_DIR, "pengyos.nft")


def _read():
    with open(RULESET, encoding="utf-8") as fh:
        return fh.read()


# ------------------------------------------------------------- syntax API


def test_strip_comments_preserves_strings():
    text = 'iif "lo#not-a-comment" accept\n# a comment\n// line\n/* b\nlock */'
    out = strip_comments(text)
    assert '"lo#not-a-comment"' in out
    assert "# a comment" not in out
    assert "// line" not in out
    assert "block" not in out


def test_strip_comments_unterminated_raises():
    with pytest.raises(NftSyntaxError, match="unterminated"):
        strip_comments("/* never closed")
    with pytest.raises(NftSyntaxError, match="unterminated"):
        strip_comments('iif "never closed')


def test_parse_produces_ordered_items():
    node = parse_nft_ruleset(
        "flush ruleset\n"
        "table inet t {\n"
        "  chain i {\n"
        "    type filter hook input priority 0; policy drop;\n"
        "  }\n"
        "}\n"
    )
    assert node["items"][0] == ("stmt", "flush ruleset")
    assert node["items"][1] == ("block", "t")
    i_items = node["blocks"]["t"]["blocks"]["i"]["items"]
    assert i_items[0][0] == "stmt"
    assert i_items[0][1].startswith("type filter hook input")


def test_parser_handles_combined_dport_list():
    node = parse_nft_ruleset(
        'table inet t { chain i { type filter hook input priority 0;'
        ' policy drop; tcp dport 22, 8080 accept; } }'
    )
    _, stmts = chain_statements(node, "t", "i")
    assert dport_accepts(stmts) == {("tcp", "22"), ("tcp", "8080")}


def test_parser_handles_service_names_verbatim():
    node = parse_nft_ruleset(
        "table inet t { chain i { tcp dport ssh, 22 accept; } }"
    )
    _, stmts = chain_statements(node, "t", "i")
    # 'ssh' is not resolvable offline; it is reported verbatim.
    assert dport_accepts(stmts) == {("tcp", "ssh"), ("tcp", "22")}


def test_parser_rejects_unbalanced_open():
    with pytest.raises(NftSyntaxError, match="unbalanced"):
        parse_nft_ruleset("table inet t { chain i {")


def test_parser_rejects_unbalanced_close():
    with pytest.raises(NftSyntaxError, match="unbalanced"):
        parse_nft_ruleset("table inet t { } }")


def test_parser_rejects_headerless_block():
    with pytest.raises(NftSyntaxError, match="without a header"):
        parse_nft_ruleset("flush ruleset; {")


def test_parser_rejects_duplicate_block_names():
    with pytest.raises(NftSyntaxError, match="duplicate"):
        parse_nft_ruleset("table inet t { chain i { } chain i { } }")


def test_parser_rejects_missing_semicolon():
    with pytest.raises(NftSyntaxError, match="missing ';'"):
        parse_nft_ruleset('table inet t { chain i { iif "lo" accept } }')


def test_parser_rejects_trailing_unterminated_statement():
    with pytest.raises(NftSyntaxError, match="missing trailing ';'"):
        parse_nft_ruleset("flush ruleset")


def test_chain_policy_extraction():
    node = parse_nft_ruleset(
        "table inet t { chain i { type filter hook input priority 0;"
        " policy drop; } }"
    )
    _, stmts = chain_statements(node, "t", "i")
    assert chain_policy(stmts) == "drop"


def test_chain_policy_missing_raises():
    node = parse_nft_ruleset("table inet t { chain i { } }")
    _, stmts = chain_statements(node, "t", "i")
    with pytest.raises(NftSyntaxError, match="no policy"):
        chain_policy(stmts)


def test_chain_policy_garbage_raises():
    node = parse_nft_ruleset(
        "table inet t { chain i { policy drop foo; } }"
    )
    _, stmts = chain_statements(node, "t", "i")
    with pytest.raises(NftSyntaxError, match="unrecognised policy"):
        chain_policy(stmts)


def test_dport_accepts_requires_value():
    with pytest.raises(NftSyntaxError, match="dport without value"):
        dport_accepts(["tcp dport accept"])


def test_dport_accepts_rejects_bad_port():
    with pytest.raises(NftSyntaxError, match="bad port"):
        dport_accepts(["tcp dport 1.2 accept"])


def test_dport_accepts_rejects_unknown_proto():
    with pytest.raises(NftSyntaxError, match="unrecognised dport accept"):
        dport_accepts(["dport 22 accept"])


def test_chain_statements_missing_table_raises():
    node = parse_nft_ruleset("table inet t { chain i { } }")
    with pytest.raises(KeyError, match="table"):
        chain_statements(node, "nope", "i")


def test_chain_statements_missing_chain_raises():
    node = parse_nft_ruleset("table inet t { chain i { } }")
    with pytest.raises(KeyError, match="chain"):
        chain_statements(node, "t", "nope")


# ----------------------------------------------------------- policy: API


def test_validate_ruleset_returns_result_ok():
    result = validate_ruleset_text(_read())
    assert result is not None
    assert result.ok, result.errors
    assert result.errors == []
    assert bool(result) is True
    assert "OK" in repr(result)


def test_validate_ruleset_file_entrypoint(tmp_path):
    target = tmp_path / "pengyos.nft"
    target.write_text(_read(), encoding="utf-8")
    assert validate_ruleset(str(target)).ok


def test_validation_result_failure_repr():
    result = validate_ruleset_text("garbage { } }")
    assert not result.ok
    assert not bool(result)
    assert result.root is None
    assert "syntax" in result.errors[0]
    assert "FAIL" in repr(result)


# --------------------------------------------- policy: baseline rule file


def test_ruleset_file_present():
    assert os.path.isfile(RULESET), "nftables/pengyos.nft missing"
    assert [f for f in os.listdir(NFT_DIR) if f.endswith(".nft")], (
        "no .nft file in nftables/"
    )


def test_ruleset_is_root_executable_script():
    text = _read()
    assert text.startswith("#!/usr/sbin/nft -f\n"), (
        "ruleset must be applicable via 'nft -f'"
    )


def test_ruleset_is_green():
    result = validate_ruleset(RULESET)
    assert result.ok, result.errors


def test_ruleset_drop_policy_removed_fails():
    text = _read().replace(
        "type filter hook input priority 0; policy drop;",
        "type filter hook input priority 0; policy accept;",
    )
    result = validate_ruleset_text(text)
    assert not result.ok
    assert any("default-deny" in e for e in result.errors)


def test_ruleset_forward_accept_fails():
    text = _read().replace(
        "type filter hook forward priority 0; policy drop;",
        "type filter hook forward priority 0; policy accept;",
    )
    result = validate_ruleset_text(text)
    assert not result.ok
    assert any("default-deny" in e for e in result.errors)


def test_ruleset_output_reject_fails():
    text = _read().replace(
        "type filter hook output priority 0; policy accept;",
        "type filter hook output priority 0; policy reject;",
    )
    result = validate_ruleset_text(text)
    assert not result.ok
    assert any("output chain policy" in e for e in result.errors)


def test_ruleset_extra_open_port_fails():
    text = _read().replace(
        "tcp dport 22 ct state new accept",
        "tcp dport 22 ct state new accept\n        tcp dport 80 ct state new accept",
    )
    result = validate_ruleset_text(text)
    assert not result.ok
    assert any("unrecognised statement" in e for e in result.errors)


def test_ruleset_ssh_removed_fails():
    text = _read().replace("tcp dport 22 ct state new accept\n", "")
    result = validate_ruleset_text(text)
    assert not result.ok
    assert any("allow list" in e for e in result.errors)


def test_ruleset_flush_removed_fails():
    text = _read().replace("flush ruleset\n", "")
    result = validate_ruleset_text(text)
    assert not result.ok
    assert any("flush ruleset" in e for e in result.errors)


def test_ruleset_second_table_fails():
    text = _read() + "table inet rogue { }\n"
    result = validate_ruleset_text(text)
    assert not result.ok
    assert any("expected exactly table inet pengyos" in e for e in result.errors)


def test_ruleset_missing_chain_fails():
    text = _read().replace(
        "    chain forward {\n"
        "        type filter hook forward priority 0; policy drop;\n"
        "    }\n",
        "",
    )
    result = validate_ruleset_text(text)
    assert not result.ok
    assert any("input/forward/output" in e for e in result.errors)


# ----------------------- policy: no accept before the allow table


def test_accept_before_allow_table_fails():
    text = _read().replace(
        "flush ruleset\n",
        "flush ruleset\naccept\n",
    )
    result = validate_ruleset_text(text)
    assert not result.ok
    assert any("accept before allow table" in e for e in result.errors)


def test_accept_statement_before_table_fails():
    text = _read().replace(
        "table inet pengyos {",
        'chain open { iif "any" accept; }\ntable inet pengyos {',
    )
    result = validate_ruleset_text(text)
    assert not result.ok
    assert any("accept before allow table" in e for e in result.errors)


def test_accepts_inside_allow_table_are_allowed():
    # The baseline itself carries four accepts inside the allow table;
    # none of them may be reported as pre-table.
    result = validate_ruleset_text(_read())
    assert result.ok, result.errors
    assert not any("accept before allow table" in e for e in result.errors)


def test_accepts_after_allow_table_are_not_flagged():
    text = _read() + 'chain after { iif "any" accept; }\n'
    result = validate_ruleset_text(text)
    assert not any("accept before allow table" in e for e in result.errors)


def test_dport_accept_before_table_is_flagged():
    # A top-level dport accept is the realistic sneaky case.
    text = _read().replace(
        "flush ruleset\n",
        "flush ruleset\ntcp dport 80 accept\n",
    )
    result = validate_ruleset_text(text)
    assert not result.ok
    assert any("accept before allow table" in e for e in result.errors)


def test_accept_mention_in_non_accept_position_is_not_flagged():
    # 'accept' only counts as a verdict when it is a standalone token of
    # a top-level statement; e.g. a drop statement is fine pre-table.
    text = _read().replace(
        "flush ruleset\n",
        "flush ruleset\nct state invalid drop\n",
    )
    result = validate_ruleset_text(text)
    assert not any("accept before allow table" in e for e in result.errors)


# -------------------------------------------------- multi-error behaviour


def test_multiple_violations_all_reported():
    text = _read().replace(
        "flush ruleset\n",
        "accept\n",
    ).replace(
        "type filter hook input priority 0; policy drop;",
        "type filter hook input priority 0; policy accept;",
    )
    result = validate_ruleset_text(text)
    assert not result.ok
    assert any("accept before allow table" in e for e in result.errors)
    assert any("default-deny" in e for e in result.errors)


def test_expected_new_accepts_pins_ssh_only():
    assert EXPECTED_NEW_ACCEPTS == {("tcp", "22")}
