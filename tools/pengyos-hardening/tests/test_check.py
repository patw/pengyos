"""Tests for the linter core (:mod:`pengyos_hardening.check`): the matchers,
the auditor, and the ``check`` CLI dispatch.

These are the behavioural tests for the sshd reader (the ``key_value`` matcher)
plus the sysctl.d (``sysctl_value``), fstab (``mount_option``), nftables
(``nft_policy``) and systemd-unit (``unit_enabled``) matchers that ship
alongside it. The core contract:

* the hardened fixture tree audits clean (zero findings for the rules we can
  evaluate);
* each single-rule mutation yields exactly that rule's finding (and only it) —
  one mutation per rule;
* a matcher kind with no evaluator is skipped, not failed.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from pengyos_hardening.baseline import load_baseline
from pengyos_hardening.check import audit, discover_locations
from pengyos_hardening.check.matchers import (
    KeyValueMatcher,
    MountOptionMatcher,
    NftPolicyMatcher,
    SysctlValueMatcher,
    UnitEnabledMatcher,
    SymbolAbsentMatcher,
    SymbolPresentMatcher,
)
from pengyos_hardening.cli import main

from conftest import HARDENED_TREE, mutation_tree


def _write_sshd_config(root: Path, text: str) -> Path:
    """Write ``text`` to ``<root>/etc/ssh/sshd_config`` and return ``root``."""
    ssh = root / "etc" / "ssh"
    ssh.mkdir(parents=True, exist_ok=True)
    (ssh / "sshd_config").write_text(text, encoding="utf-8")
    return root


def _overlay_root(tree: Path) -> Path:
    """The ``rootfs-overlay`` dir under a BR2_EXTERNAL fixture tree."""
    return tree / "board" / "pengyos-server" / "rootfs-overlay"


# --- the sshd reader: key_value matcher -----------------------------------

def test_key_value_matcher_passes_when_effective_value_matches():
    baseline = load_baseline()
    rule = baseline.by_id("ssh-permit-root-login")
    assert rule.matcher.kind == "key_value"
    finding = KeyValueMatcher().evaluate(rule, _overlay_root(HARDENED_TREE))
    assert finding is None


def test_key_value_matcher_fires_on_mismatch_with_file_line():
    baseline = load_baseline()
    rule = baseline.by_id("ssh-permit-root-login")
    finding = KeyValueMatcher().evaluate(
        rule, _overlay_root(mutation_tree("ssh-permit-root-login"))
    )
    assert finding is not None
    assert finding.rule_id == "ssh-permit-root-login"
    assert finding.severity == "critical"
    assert "PermitRootLogin" in finding.message
    assert finding.path == "etc/ssh/sshd_config"
    assert finding.line == 3  # the line the key is written on


def test_key_value_matcher_fires_when_key_missing():
    baseline = load_baseline()
    rule = baseline.by_id("ssh-pubkey-auth")
    # A config that omits PubkeyAuthentication entirely.
    with tempfile.TemporaryDirectory() as d:
        finding = KeyValueMatcher().evaluate(
            rule, _write_sshd_config(Path(d), "PermitRootLogin no\n")
        )
    assert finding is not None
    assert "not set" in finding.message


@pytest.mark.parametrize(
    "declared,expected",
    [
        ("no", "no"),
        ("No", "no"),
        ("NO", "no"),
        ("off", "no"),
        ("false", "no"),
        ("0", "no"),
        ("yes", "yes"),
        ("YES", "yes"),
        ("on", "yes"),
        ("true", "yes"),
        ("1", "yes"),
    ],
)
def test_key_value_boolean_normalisation(declared, expected):
    """sshd treats yes/no/on/off/true/false/1/0 as booleans; the matcher
    compares them spelling-agnostically."""
    matcher = KeyValueMatcher()
    rule = load_baseline().by_id("ssh-permit-root-login")  # expected "no"
    with tempfile.TemporaryDirectory() as d:
        finding = matcher.evaluate(
            rule,
            _write_sshd_config(Path(d), f"PermitRootLogin {declared}\n"),
        )
    # A falsy declaration matches expected "no" -> no finding; a truthy one fires.
    if expected == "no":
        assert finding is None
    else:
        assert finding is not None


# --- the sysctl.d reader: sysctl_value matcher ----------------------------

def test_sysctl_value_matcher_passes_when_effective_value_matches():
    baseline = load_baseline()
    rule = baseline.by_id("sysctl-kptr-restrict")
    assert rule.matcher.kind == "sysctl_value"
    finding = SysctlValueMatcher().evaluate(rule, _overlay_root(HARDENED_TREE))
    assert finding is None


def test_sysctl_value_matcher_fires_on_mismatch_with_file_line():
    baseline = load_baseline()
    rule = baseline.by_id("sysctl-kptr-restrict")  # expected "2"
    finding = SysctlValueMatcher().evaluate(
        rule, _overlay_root(mutation_tree("sysctl-kptr-restrict"))
    )
    assert finding is not None
    assert finding.rule_id == "sysctl-kptr-restrict"
    assert finding.severity == "high"
    assert "kernel.kptr_restrict" in finding.message
    assert finding.path == "sysctl.d/1"
    assert finding.line == 6  # the line the key is written on


def test_sysctl_value_matcher_fires_when_key_missing():
    baseline = load_baseline()
    rule = baseline.by_id("sysctl-kptr-restrict")
    # A sysctl.d that omits the key entirely.
    with tempfile.TemporaryDirectory() as d:
        overlay = _overlay_root(Path(d))
        sysctl_d = overlay / "etc" / "sysctl.d"
        sysctl_d.mkdir(parents=True, exist_ok=True)
        (sysctl_d / "90.conf").write_text("kernel.dmesg_restrict = 1\n", encoding="utf-8")
        finding = SysctlValueMatcher().evaluate(rule, overlay)
    assert finding is not None
    assert "not set" in finding.message


def test_sysctl_value_matcher_reads_globbed_files():
    """The matcher globs ``etc/sysctl.d/*.conf`` relative to the overlay root."""
    baseline = load_baseline()
    rule = baseline.by_id("sysctl-dmesg-restrict")
    # The hardened tree declares it; a clean audit means the glob resolved.
    finding = SysctlValueMatcher().evaluate(rule, _overlay_root(HARDENED_TREE))
    assert finding is None


# --- the fstab reader: mount_option matcher -------------------------------

def test_mount_option_matcher_passes_when_options_present():
    baseline = load_baseline()
    rule = baseline.by_id("fs-root-nodev-nosuid")
    assert rule.matcher.kind == "mount_option"
    finding = MountOptionMatcher().evaluate(rule, _overlay_root(HARDENED_TREE))
    assert finding is None


def test_mount_option_matcher_fires_on_missing_option_with_file_line():
    baseline = load_baseline()
    rule = baseline.by_id("fs-root-nodev-nosuid")  # / needs nodev,nosuid
    finding = MountOptionMatcher().evaluate(
        rule, _overlay_root(mutation_tree("fs-root-nodev-nosuid"))
    )
    assert finding is not None
    assert finding.rule_id == "fs-root-nodev-nosuid"
    assert finding.severity == "medium"
    assert "nodev" in finding.message and "nosuid" in finding.message
    assert finding.path == "etc/fstab"
    assert finding.line == 3  # the line the mount is declared on


def test_mount_option_matcher_fires_when_mountpoint_absent():
    baseline = load_baseline()
    rule = baseline.by_id("fs-var-nodev-nosuid")
    # A fstab that has no /var entry at all.
    with tempfile.TemporaryDirectory() as d:
        overlay = _overlay_root(Path(d))
        etc = overlay / "etc"
        etc.mkdir(parents=True, exist_ok=True)
        (etc / "fstab").write_text(
            "# fstab\n/dev/sda1  /  ext4  defaults,nodev,nosuid  0  1\n",
            encoding="utf-8",
        )
        finding = MountOptionMatcher().evaluate(rule, overlay)
    assert finding is not None
    assert "not present" in finding.message


def test_mount_option_matcher_requires_all_options():
    """A mount with only one of two required options still fires."""
    baseline = load_baseline()
    rule = baseline.by_id("fs-root-nodev-nosuid")
    with tempfile.TemporaryDirectory() as d:
        overlay = _overlay_root(Path(d))
        etc = overlay / "etc"
        etc.mkdir(parents=True, exist_ok=True)
        # nodev present, nosuid missing.
        (etc / "fstab").write_text(
            "/dev/sda1  /  ext4  defaults,nodev  0  1\n", encoding="utf-8"
        )
        finding = MountOptionMatcher().evaluate(rule, overlay)
    assert finding is not None
    assert "nosuid" in finding.message


# --- the nftables reader: nft_policy matcher ------------------------------

def test_nft_policy_matcher_passes_when_input_policy_denies():
    """The hardened fixture's input chain default-denies -> no finding."""
    baseline = load_baseline()
    rule = baseline.by_id("firewall-input-policy-deny")
    assert rule.matcher.kind == "nft_policy"
    finding = NftPolicyMatcher().evaluate(rule, _overlay_root(HARDENED_TREE))
    assert finding is None


def test_nft_policy_matcher_fires_on_accept_policy_with_file_line():
    """An input chain with `policy accept` fires against the deny baseline."""
    baseline = load_baseline()
    rule = baseline.by_id("firewall-input-policy-deny")
    finding = NftPolicyMatcher().evaluate(
        rule, _overlay_root(mutation_tree("firewall-input-policy-deny"))
    )
    assert finding is not None
    assert finding.rule_id == "firewall-input-policy-deny"
    assert finding.severity == "critical"
    assert "input" in finding.message
    assert finding.path == "etc/nftables.conf"
    assert finding.line == 7  # the `chain input {` line


def test_nft_policy_matcher_canonicalises_deny_to_drop():
    """`policy drop;` (nft's canonical deny) passes a `deny` baseline rule."""
    with tempfile.TemporaryDirectory() as d:
        overlay = _overlay_root(Path(d))
        table = overlay / "etc" / "nftables.conf"
        table.parent.mkdir(parents=True, exist_ok=True)
        table.write_text(
            "table inet filter {\n"
            "    chain input {\n"
            "        type filter hook input priority 0; policy drop;\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        finding = NftPolicyMatcher().evaluate(
            load_baseline().by_id("firewall-input-policy-deny"), overlay
        )
    assert finding is None


def test_nft_policy_matcher_fires_when_chain_absent():
    """An input chain that is not declared at all fires."""
    with tempfile.TemporaryDirectory() as d:
        overlay = _overlay_root(Path(d))
        table = overlay / "etc" / "nftables.conf"
        table.parent.mkdir(parents=True, exist_ok=True)
        # A ruleset with no input chain.
        table.write_text(
            "table inet filter {\n"
            "    chain output {\n"
            "        type filter hook output priority 0; policy accept;\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        finding = NftPolicyMatcher().evaluate(
            load_baseline().by_id("firewall-input-policy-deny"), overlay
        )
    assert finding is not None
    assert "not declared" in finding.message


# --- the systemd-unit reader: unit_enabled matcher ------------------------

def test_unit_enabled_matcher_passes_when_unit_enabled():
    """The hardened fixture has nftables.service enabled in multi-user.wants."""
    baseline = load_baseline()
    rule = baseline.by_id("firewall-unit-enabled")
    assert rule.matcher.kind == "unit_enabled"
    finding = UnitEnabledMatcher().evaluate(rule, _overlay_root(HARDENED_TREE))
    assert finding is None


def test_unit_enabled_matcher_fires_when_unit_disabled():
    """A unit missing from the wants dir fires (policy never loads)."""
    baseline = load_baseline()
    rule = baseline.by_id("firewall-unit-enabled")
    finding = UnitEnabledMatcher().evaluate(
        rule, _overlay_root(mutation_tree("firewall-unit-enabled"))
    )
    assert finding is not None
    assert finding.rule_id == "firewall-unit-enabled"
    assert finding.severity == "high"
    assert "nftables.service" in finding.message
    assert "multi-user.target.wants" in finding.path
    assert finding.line is None


# --- the firewall readers end-to-end via the auditor ----------------------

_FIREWALL_MUTATIONS = (
    "firewall-input-policy-deny",
    "firewall-unit-enabled",
)


def test_each_firewall_mutation_fires_exactly_its_named_rule():
    """One mutation per rule: each firewall mutation tree yields exactly one
    finding, and it is the rule the mutation targets."""
    for rule_id in _FIREWALL_MUTATIONS:
        result = audit(mutation_tree(rule_id), load_baseline(), rule=rule_id)
        assert len(result.findings) == 1, rule_id
        assert result.findings[0].rule_id == rule_id, rule_id


def test_full_audit_on_firewall_mutation_yields_only_that_rule():
    """A full (unfiltered) audit of a firewall mutation surfaces only the
    mutated rule — no other implemented rule fires from a single change."""
    for rule_id in _FIREWALL_MUTATIONS:
        result = audit(mutation_tree(rule_id), load_baseline())
        rule_ids = {f.rule_id for f in result.findings}
        assert rule_ids == {rule_id}, rule_id


# --- the sshd reader end-to-end via the auditor ---------------------------

def test_hardened_tree_audits_clean():
    """The hardened fixture tree yields zero findings, and every rule the
    baseline declares has an evaluator (zero skipped)."""
    result = audit(HARDENED_TREE, load_baseline())
    assert result.findings == [], result.findings
    # All implemented rules pass on the hardened tree — ssh, sysctl, mount,
    # firewall, AND the defconfig symbol matchers.
    for rule_id in (
        "ssh-permit-root-login",
        "sysctl-kptr-restrict",
        "fs-root-nodev-nosuid",
        "firewall-input-policy-deny",
        "firewall-unit-enabled",
        "defconfig-no-telnet",
        "defconfig-no-debug",
        "defconfig-openssh-server",
    ):
        assert rule_id not in result.rule_ids_skipped, rule_id
    assert result.rule_ids_skipped == []


def test_each_ssh_mutation_fires_exactly_its_named_rule():
    """One mutation per rule: each mutation tree yields exactly one finding,
    and it is the rule the mutation targets."""
    for rule_id in (
        "ssh-permit-root-login",
        "ssh-password-auth",
        "ssh-pubkey-auth",
        "ssh-empty-passwords",
        "ssh-x11-forwarding",
        "ssh-tcp-forwarding",
    ):
        result = audit(mutation_tree(rule_id), load_baseline(), rule=rule_id)
        assert len(result.findings) == 1, rule_id
        assert result.findings[0].rule_id == rule_id, rule_id


def test_full_audit_on_a_mutation_yields_only_that_rule():
    """A full (unfiltered) audit of a mutation tree surfaces only the mutated
    rule — no other implemented rule fires from a single-line change."""
    result = audit(mutation_tree("ssh-password-auth"), load_baseline())
    rule_ids = {f.rule_id for f in result.findings}
    assert rule_ids == {"ssh-password-auth"}


# --- the sysctl.d + fstab readers end-to-end via the auditor --------------

_SYSCTL_MOUNT_MUTATIONS = (
    "sysctl-kptr-restrict",
    "sysctl-dmesg-restrict",
    "sysctl-protected-links",
    "sysctl-tcp-syncookies",
    "sysctl-disable-ipv6",
    "fs-root-nodev-nosuid",
    "fs-var-nodev-nosuid",
)


def test_each_sysctl_mount_mutation_fires_exactly_its_named_rule():
    """One mutation per rule: each sysctl/mount mutation tree yields exactly
    one finding, and it is the rule the mutation targets."""
    for rule_id in _SYSCTL_MOUNT_MUTATIONS:
        result = audit(mutation_tree(rule_id), load_baseline(), rule=rule_id)
        assert len(result.findings) == 1, rule_id
        assert result.findings[0].rule_id == rule_id, rule_id


def test_full_audit_on_sysctl_mount_mutation_yields_only_that_rule():
    """A full (unfiltered) audit of a sysctl/mount mutation surfaces only the
    mutated rule — no other implemented rule fires from a single-line change."""
    for rule_id in _SYSCTL_MOUNT_MUTATIONS:
        result = audit(mutation_tree(rule_id), load_baseline())
        rule_ids = {f.rule_id for f in result.findings}
        assert rule_ids == {rule_id}, rule_id


# --- auditor: location discovery + skip behaviour -------------------------

def test_discover_locations_finds_tree_and_overlay():
    locations = discover_locations(HARDENED_TREE)
    assert len(locations) == 1
    loc = locations[0]
    assert loc.root == HARDENED_TREE
    # One overlay root: board/pengyos-server/rootfs-overlay.
    overlay_paths = {str(o) for o in loc.overlay_roots}
    assert any("rootfs-overlay" in p for p in overlay_paths)


def test_audit_all_rules_implemented_on_hardened_tree():
    """Every rule the baseline declares has an evaluator, so the hardened tree
    audits clean with zero skipped rules."""
    result = audit(HARDENED_TREE, load_baseline())
    assert result.rules_audited == 18
    assert result.findings == [], result.findings
    assert result.rule_ids_skipped == []


def test_audit_skips_unimplemented_matchers_not_failed():
    """A rule whose matcher kind has no evaluator is skipped, not failed.

    Every matcher kind the committed baseline declares is implemented, so this
    is exercised against a rule carrying an unimplemented kind: the auditor
    records it as skipped and produces no finding.
    """
    from pengyos_hardening.baseline import Baseline, Matcher, Rule

    rule = Rule(
        id="defconfig-future",
        severity="low",
        domain="defconfig",
        matcher=Matcher(kind="not_a_real_kind", params={}),
    )
    result = audit(HARDENED_TREE, Baseline(rules=[rule]))
    assert result.rules_audited == 1
    assert result.rule_ids_skipped == ["defconfig-future"]
    assert result.findings == []


def test_audit_rule_filter_restricts_to_single_rule():
    result = audit(HARDENED_TREE, load_baseline(), rule="ssh-permit-root-login")
    assert result.rules_audited == 1
    assert result.findings == []  # hardened is clean


def test_audit_nonexistent_rule_id_raises_keyerror():
    with pytest.raises(KeyError):
        audit(HARDENED_TREE, load_baseline(), rule="does-not-exist")


def test_audit_empty_tree_no_locations():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        result = audit(Path(d), load_baseline())
        assert result.locations == []
        assert result.findings == []


# --- the defconfig reader: symbol_absent / symbol_present matchers ----------

# The defconfig symbol matchers read ``configs/*_defconfig`` relative to the
# tree root (not an overlay). Each mutation is a single flipped line in one
# defconfig.

_DEFCONFIG_RULES = (
    "defconfig-no-telnet",      # symbol_absent: BR2_PACKAGE_TELNET
    "defconfig-no-debug",       # symbol_absent: BR2_PACKAGE_VALGRIND
    "defconfig-openssh-server",  # symbol_present: BR2_PACKAGE_OPENSSH_SERVER
)


def test_symbol_absent_matcher_passes_when_disabled():
    """The hardened fixture disables telnet/valgrind (disabled comments), so
    the symbol_absent rules pass."""
    baseline = load_baseline()
    for rule_id in ("defconfig-no-telnet", "defconfig-no-debug"):
        rule = baseline.by_id(rule_id)
        assert rule.matcher.kind == "symbol_absent"
        finding = SymbolAbsentMatcher().evaluate(rule, HARDENED_TREE)
        assert finding is None, rule_id


def test_symbol_absent_matcher_fires_on_set_line_with_file_line():
    """A defconfig that sets a forbidden symbol fires with file:line evidence."""
    baseline = load_baseline()
    rule = baseline.by_id("defconfig-no-telnet")
    finding = SymbolAbsentMatcher().evaluate(
        rule, mutation_tree("defconfig-no-telnet")
    )
    assert finding is not None
    assert finding.rule_id == "defconfig-no-telnet"
    assert finding.severity == "high"
    assert "BR2_PACKAGE_TELNET" in finding.message
    assert finding.path == "configs/pengyos_server_defconfig"
    assert finding.line == 8  # the flipped BR2_PACKAGE_TELNET=y line


def test_symbol_absent_matcher_fires_for_debug():
    baseline = load_baseline()
    rule = baseline.by_id("defconfig-no-debug")
    finding = SymbolAbsentMatcher().evaluate(
        rule, mutation_tree("defconfig-no-debug")
    )
    assert finding is not None
    assert finding.rule_id == "defconfig-no-debug"
    assert "BR2_PACKAGE_VALGRIND" in finding.message
    assert finding.path == "configs/pengyos_server_defconfig"
    assert finding.line == 10


def test_symbol_present_matcher_passes_when_enabled_in_every_defconfig():
    """OpenSSH server is enabled in all three hardened defconfigs -> pass."""
    baseline = load_baseline()
    rule = baseline.by_id("defconfig-openssh-server")
    assert rule.matcher.kind == "symbol_present"
    finding = SymbolPresentMatcher().evaluate(rule, HARDENED_TREE)
    assert finding is None


def test_symbol_present_matcher_fires_when_missing_from_one_defconfig():
    """One defconfig missing the required symbol fires against the first
    (sorted) defconfig that omits it, with file:line evidence."""
    baseline = load_baseline()
    rule = baseline.by_id("defconfig-openssh-server")
    finding = SymbolPresentMatcher().evaluate(
        rule, mutation_tree("defconfig-openssh-server")
    )
    assert finding is not None
    assert finding.rule_id == "defconfig-openssh-server"
    assert finding.severity == "high"
    assert "BR2_PACKAGE_OPENSSH_SERVER" in finding.message
    assert finding.path == "configs/pengyos_server_defconfig"
    assert finding.line == 12  # the last set line in the mutated config


def test_symbol_present_matcher_disabled_comment_is_not_set():
    """A ``# CONFIG_BR2_FOO is not set`` comment must not satisfy symbol_present."""
    baseline = load_baseline()
    rule = baseline.by_id("defconfig-openssh-server")
    # The openssh mutation flips the set line to a disabled comment.
    finding = SymbolPresentMatcher().evaluate(
        rule, mutation_tree("defconfig-openssh-server")
    )
    assert finding is not None
    assert "not set" in finding.message


# --- the defconfig readers end-to-end via the auditor -----------------------

def test_each_defconfig_mutation_fires_exactly_its_named_rule():
    """One mutation per rule: each defconfig mutation tree yields exactly one
    finding, and it is the rule the mutation targets."""
    for rule_id in _DEFCONFIG_RULES:
        result = audit(mutation_tree(rule_id), load_baseline(), rule=rule_id)
        assert len(result.findings) == 1, rule_id
        assert result.findings[0].rule_id == rule_id, rule_id


def test_full_audit_on_defconfig_mutation_yields_only_that_rule():
    """A full (unfiltered) audit of a defconfig mutation surfaces only the
    mutated rule — no other implemented rule fires from a single-line change."""
    for rule_id in _DEFCONFIG_RULES:
        result = audit(mutation_tree(rule_id), load_baseline())
        rule_ids = {f.rule_id for f in result.findings}
        assert rule_ids == {rule_id}, rule_id


def test_defconfig_mutation_evidence_is_file_line():
    """Each defconfig mutation pins the offending file:line."""
    evidence = {
        "defconfig-no-telnet": ("configs/pengyos_server_defconfig", 8),
        "defconfig-no-debug": ("configs/pengyos_server_defconfig", 10),
        "defconfig-openssh-server": ("configs/pengyos_server_defconfig", 12),
    }
    for rule_id, (path, line) in evidence.items():
        finding = audit(mutation_tree(rule_id), load_baseline()).findings[0]
        assert finding.path == path, rule_id
        assert finding.line == line, rule_id


# --- CLI: check dispatch --------------------------------------------------

def test_cli_check_clean_tree_exits_zero(capsys):
    rc = main(["check", "--tree", str(HARDENED_TREE)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no deviations" in out


def test_cli_check_mutation_tree_reports_finding(capsys):
    rc = main(
        ["check", "--rule", "ssh-permit-root-login",
         "--tree", str(mutation_tree("ssh-permit-root-login"))]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "ssh-permit-root-login" in out
    assert "etc/ssh/sshd_config" in out


def test_cli_check_json_to_stdout(capsys):
    rc = main(
        ["check", "--rule", "ssh-permit-root-login",
         "--tree", str(mutation_tree("ssh-permit-root-login")), "--json"]
    )
    assert rc == 0
    document = json.loads(capsys.readouterr().out)
    assert document["finding_count"] == 1
    assert document["findings"][0]["rule_id"] == "ssh-permit-root-login"
    assert document["findings"][0]["line"] == 3


def test_cli_check_missing_tree_dir_exits_two(capsys):
    rc = main(["check", "--tree", "/no/such/dir/here"])
    assert rc == 2
    assert "not a directory" in capsys.readouterr().err


def test_cli_check_firewall_finding_surfaces(capsys):
    """The nftables default-deny finding surfaces through the CLI with its
    file:line evidence."""
    rc = main(
        ["check", "--rule", "firewall-input-policy-deny",
         "--tree", str(mutation_tree("firewall-input-policy-deny"))]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "firewall-input-policy-deny" in out
    assert "etc/nftables.conf" in out


def test_cli_check_firewall_json_to_stdout(capsys):
    """The firewall-unit-enabled finding serialises to JSON via the CLI."""
    rc = main(
        ["check", "--rule", "firewall-unit-enabled",
         "--tree", str(mutation_tree("firewall-unit-enabled")), "--json"]
    )
    assert rc == 0
    document = json.loads(capsys.readouterr().out)
    assert document["finding_count"] == 1
    assert document["findings"][0]["rule_id"] == "firewall-unit-enabled"
    assert "nftables.service" in document["findings"][0]["path"]


def test_cli_check_defconfig_finding_surfaces(capsys):
    """The defconfig symbol finding surfaces through the CLI with its
    file:line evidence."""
    rc = main(
        ["check", "--rule", "defconfig-no-telnet",
         "--tree", str(mutation_tree("defconfig-no-telnet"))]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "defconfig-no-telnet" in out
    assert "configs/pengyos_server_defconfig" in out


def test_cli_check_defconfig_json_to_stdout(capsys):
    """The defconfig-openssh-server finding serialises to JSON via the CLI."""
    rc = main(
        ["check", "--rule", "defconfig-openssh-server",
         "--tree", str(mutation_tree("defconfig-openssh-server")), "--json"]
    )
    assert rc == 0
    document = json.loads(capsys.readouterr().out)
    assert document["finding_count"] == 1
    assert document["findings"][0]["rule_id"] == "defconfig-openssh-server"
    assert document["findings"][0]["path"] == "configs/pengyos_server_defconfig"
    assert document["findings"][0]["line"] == 12


def test_cli_check_malformed_baseline_exits_three(tmp_path, capsys, monkeypatch):
    """A malformed committed baseline (via --path on the baseline) exits 3.

    The check command loads the committed baseline; we point it at a malformed
    one by temporarily overriding the default path.
    """
    bad = tmp_path / "bad.json"
    bad.write_text('{"rules": [{"severity": "low"}]}', encoding="utf-8")
    from pengyos_hardening.baseline import DEFAULT_BASELINE_PATH

    orig = DEFAULT_BASELINE_PATH
    try:
        monkeypatch.setattr(
            "pengyos_hardening.baseline.DEFAULT_BASELINE_PATH", bad
        )
        rc = main(["check", "--tree", str(HARDENED_TREE)])
    finally:
        monkeypatch.setattr(
            "pengyos_hardening.baseline.DEFAULT_BASELINE_PATH", orig
        )
    assert rc == 3
    assert "malformed" in capsys.readouterr().err
