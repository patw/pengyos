"""Tests for the report renderer (:mod:`pengyos_hardening.check.report`).

The report is the third submodule (spec.md): it owns *how* an audit looks,
while the auditor owns *what* the findings are. These tests pin the stable,
deterministic text and JSON layouts (golden) and the summary counts they are
built from.

The report is golden-able because its layout never depends on dict iteration
order or wall-clock time: findings are sorted by severity/path/rule-id, the
severity breakdown is always all five levels (zero-filled), and the domain
breakdown follows the baseline's declaration order.
"""

from __future__ import annotations

import io
import json

from pengyos_hardening.baseline import load_baseline
from pengyos_hardening.check import audit
from pengyos_hardening.check.report import render_json, render_text, summary

from conftest import HARDENED_TREE, mutation_tree

# A placeholder the golden tests substitute for the (machine-specific) absolute
# tree path, so the golden is portable across machines.
_TREE_TOKEN = "TREE"


def _golden(text: str, tree) -> str:
    """Normalise a rendered report by swapping the tree path for a token.

    The header underline is a run of ``=`` equal to the header width, so we
    collapse it to a token too — otherwise the golden would pin the
    machine-specific path length rather than the layout.
    """
    import re

    text = text.replace(str(tree), _TREE_TOKEN)
    # Collapse the header's ``=`` underline to a single token.
    return re.sub(r"=+\n", "___\n", text, count=1)


# --- summary counts -------------------------------------------------------

def test_summary_counts_add_up():
    """passed = rules_audited − skipped − rules_that_fired."""
    result = audit(mutation_tree("ssh-password-auth"), load_baseline())
    summ = summary(result)
    assert summ["rules_audited"] == 18
    # Every matcher kind is implemented (no skipped rules).
    assert summ["skipped"] == 0
    assert summ["rules_with_findings"] == 1
    assert summ["findings"] == 1
    # 18 audited − 0 skipped − 1 fired == 17 passed.
    assert summ["passed"] == 17


def test_summary_severity_is_zero_filled_all_five():
    """The severity breakdown always lists all five levels, zero-filled."""
    result = audit(mutation_tree("ssh-permit-root-login"), load_baseline())
    by_sev = summary(result)["by_severity"]
    assert set(by_sev) == {"critical", "high", "medium", "low", "info"}
    # The mutation is a critical finding.
    assert by_sev["critical"] == 1


def test_summary_by_domain_follows_declaration_order():
    """Findings per domain follow the baseline's domain declaration order."""
    result = audit(mutation_tree("ssh-permit-root-login"), load_baseline())
    by_domain = summary(result)["by_domain"]
    # ssh fired once (the mutation); every other domain is 0.
    assert by_domain["ssh"] == 1
    assert by_domain["defconfig"] == 0
    assert by_domain["firewall"] == 0


def test_summary_clean_tree_all_passed():
    """A clean tree reports every audited rule as passed (minus skipped)."""
    result = audit(HARDENED_TREE, load_baseline())
    summ = summary(result)
    assert summ["findings"] == 0
    # Every matcher kind is implemented, so all 18 audited rules pass.
    assert summ["passed"] == 18
    assert summ["skipped"] == 0
    assert summ["rules_with_findings"] == 0


# --- text: golden ---------------------------------------------------------

def test_render_text_clean_tree_is_golden():
    """The hardened tree renders to a fixed, deterministic golden block."""
    text = render_text(audit(HARDENED_TREE, load_baseline()), HARDENED_TREE)
    expected = (
        "pengyOS hardening audit — TREE\n"
        "___\n"
        "\n"
        "18 rule(s) audited · 0 finding(s) · 0 skipped · 18 passed\n"
        "by severity: 0 critical, 0 high, 0 medium, 0 low, 0 info\n"
        "\n"
        "OK — no deviations from the baseline.\n"
    )
    assert _golden(text, HARDENED_TREE) == expected


def test_render_text_mutation_tree_is_golden():
    """A mutation tree renders its single finding with file:line evidence."""
    tree = mutation_tree("ssh-permit-root-login")
    text = render_text(audit(tree, load_baseline()), tree)
    expected = (
        "pengyOS hardening audit — TREE\n"
        "___\n"
        "\n"
        "18 rule(s) audited · 1 finding(s) · 0 skipped · 17 passed\n"
        "by severity: 1 critical, 0 high, 0 medium, 0 low, 0 info\n"
        "\n"
        "Findings (1):\n"
        "  [critical] ssh-permit-root-login  —  "
        "PermitRootLogin is 'yes', expected 'no'\n"
        "      etc/ssh/sshd_config:3\n"
    )
    assert _golden(text, tree) == expected


def test_render_text_skipped_list_is_single_line():
    """The skipped-rule list renders as one deterministic line (not wrapped).

    Every matcher kind the baseline declares is implemented, so no committed
    rule is ever skipped — but the report still renders a skipped block when a
    rule's matcher is not implemented. This pins that layout directly against a
    synthetic result.
    """
    from pengyos_hardening.check.audit import AuditLocation, AuditResult
    from pengyos_hardening.errors import Finding

    # The skipped block renders only when there are findings (a clean tree
    # returns early) and auditable locations (an empty tree returns even
    # earlier), so this carries a location, one finding, and a skipped rule.
    result = AuditResult(
        locations=[AuditLocation(root=HARDENED_TREE)],
        findings=[
            Finding(
                rule_id="ssh-permit-root-login",
                severity="critical",
                message="PermitRootLogin is 'yes', expected 'no'",
                path="etc/ssh/sshd_config",
                line=3,
            )
        ],
        skipped=["defconfig-no-telnet"],
    )
    text = render_text(result, HARDENED_TREE)
    skipped_section = text.split("Skipped (matcher not yet implemented):")[1]
    lines = skipped_section.strip().splitlines()
    assert len(lines) == 1
    assert "defconfig-no-telnet" in lines[0]


def test_render_text_no_locations():
    """A tree with no auditable locations reports that, not a crash."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        text = render_text(audit(Path(d), load_baseline()), Path(d))
    assert "no auditable locations found" in text


def test_render_text_is_deterministic():
    """Rendering the same tree twice yields byte-identical text."""
    tree = mutation_tree("ssh-password-auth")
    first = render_text(audit(tree, load_baseline()), tree)
    second = render_text(audit(tree, load_baseline()), tree)
    assert first == second


# --- JSON: golden ---------------------------------------------------------

def test_render_json_is_deterministic_and_sorted():
    """JSON output is stable: re-serialising yields identical bytes."""
    tree = mutation_tree("ssh-permit-root-login")
    doc = render_json(audit(tree, load_baseline()), tree)
    once = json.dumps(doc, indent=2, sort_keys=True)
    again = json.dumps(render_json(audit(tree, load_baseline()), tree),
                       indent=2, sort_keys=True)
    assert once == again


def test_render_json_has_summary_block():
    """The JSON carries the same summary the text header is built from."""
    tree = mutation_tree("ssh-password-auth")  # PasswordAuthentication on line 4
    doc = render_json(audit(tree, load_baseline()), tree)
    assert doc["summary"]["rules_audited"] == 18
    assert doc["summary"]["findings"] == 1
    # 18 audited − 0 skipped − 1 fired == 17 passed.
    assert doc["summary"]["passed"] == 17
    assert doc["summary"]["by_severity"]["critical"] == 1
    # findings are the machine-readable Finding shape.
    assert doc["findings"][0]["rule_id"] == "ssh-password-auth"
    assert doc["findings"][0]["line"] == 4


def test_render_json_severity_breakdown_all_five():
    """The JSON severity breakdown is zero-filled across all five levels."""
    doc = render_json(audit(HARDENED_TREE, load_baseline()), HARDENED_TREE)
    assert set(doc["summary"]["by_severity"]) == {
        "critical", "high", "medium", "low", "info",
    }


# --- ordering -------------------------------------------------------------

def test_findings_sorted_by_severity_then_path_then_rule():
    """Findings are ordered by severity rank, then path, then rule id.

    Flip two *critical* rules off in one tree: the two findings share a
    severity, so the tie-break is rule id (empty-passwords < permit-root-login).
    """
    import shutil
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        src = mutation_tree("ssh-password-auth")  # PasswordAuthentication no
        dst = Path(d) / "t"
        shutil.copytree(src, dst)
        sshd = dst / "board" / "pengyos-server" / "rootfs-overlay" / "etc" / "ssh" / "sshd_config"
        lines = [
            ("PermitRootLogin yes" if "PermitRootLogin" in l else l)
            for l in sshd.read_text(encoding="utf-8").splitlines()
        ]
        sshd.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = audit(dst, load_baseline())
        assert len(result.findings) == 2
        # Both critical, same path -> tie-break by rule id (password-auth <
        # permit-root-login alphabetically). sorted_findings() applies that order.
        ordered = result.sorted_findings()
        assert [f.rule_id for f in ordered] == [
            "ssh-password-auth",
            "ssh-permit-root-login",
        ]
        assert all(f.severity == "critical" for f in ordered)


# --- sorted dispatch: the report owns finding order -------------------------

def test_render_text_emits_findings_in_severity_order():
    """The text report emits findings in severity-first order (critical first).

    The report is the *only* layer that orders findings, so this pins the
    *emitted* order directly against ``sorted_findings()`` — deliberately
    feeding the findings out of order (low before critical) and asserting the
    report re-orders them, rather than trusting the auditor's arrival order.
    """
    from pengyos_hardening.check.audit import AuditLocation, AuditResult
    from pengyos_hardening.errors import Finding

    result = AuditResult(
        locations=[AuditLocation(root=HARDENED_TREE)],
        findings=[
            Finding(
                "sysctl-no-hardening", "low", "low msg",
                "etc/sysctl.d/10.conf", 3,
            ),
            Finding(
                "firewall-input-policy-deny", "high", "high msg",
                "etc/nftables.conf", 8,
            ),
            Finding(
                "ssh-permit-root-login", "critical", "crit msg",
                "etc/ssh/sshd_config", 3,
            ),
        ],
    )
    text = render_text(result, HARDENED_TREE)
    # critical surfaces before high before low — the worst deviations on top.
    assert text.index("ssh-permit-root-login") < text.index("firewall-input-policy-deny")
    assert text.index("firewall-input-policy-deny") < text.index("sysctl-no-hardening")
    # the emitted order is exactly sorted_findings().
    emitted = [f.rule_id for f in result.sorted_findings()]
    for rule_id in emitted:
        assert rule_id in text


def test_render_json_emits_findings_in_severity_order():
    """The JSON report lists findings in severity-first order, not arrival."""
    from pengyos_hardening.check.audit import AuditLocation, AuditResult
    from pengyos_hardening.errors import Finding

    result = AuditResult(
        locations=[AuditLocation(root=HARDENED_TREE)],
        findings=[
            Finding(
                "sysctl-no-hardening", "low", "low msg",
                "etc/sysctl.d/10.conf", 3,
            ),
            Finding(
                "firewall-input-policy-deny", "high", "high msg",
                "etc/nftables.conf", 8,
            ),
            Finding(
                "ssh-permit-root-login", "critical", "crit msg",
                "etc/ssh/sshd_config", 3,
            ),
        ],
    )
    doc = render_json(result, HARDENED_TREE)
    assert [f["rule_id"] for f in doc["findings"]] == [
        "ssh-permit-root-login",
        "firewall-input-policy-deny",
        "sysctl-no-hardening",
    ]


# --- CLI: report dispatch -------------------------------------------------

def test_cli_check_text_and_json_match_renderers(capsys):
    """The CLI's text/json output matches the module renderers exactly."""
    from pengyos_hardening.cli import main
    from pengyos_hardening.check.report import write_text

    tree = mutation_tree("ssh-permit-root-login")
    result = audit(tree, load_baseline())

    main(["check", "--tree", str(tree)])
    cli_text = capsys.readouterr().out

    import io
    buf = io.StringIO()
    write_text(result, tree, stream=buf)
    assert cli_text == buf.getvalue()


def test_cli_check_json_matches_render_json(capsys):
    """The CLI --json output is exactly render_json serialised."""
    import json as _json

    from pengyos_hardening.cli import main

    tree = mutation_tree("ssh-permit-root-login")
    result = audit(tree, load_baseline())

    main(["check", "--json", "--tree", str(tree)])
    cli_json = capsys.readouterr().out

    # The CLI serialises render_json(...) with indent=2, sort_keys=True + newline.
    expected = _json.dumps(render_json(result, tree), indent=2, sort_keys=True) + "\n"
    assert cli_json == expected
