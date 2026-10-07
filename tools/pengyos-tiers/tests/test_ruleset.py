"""Tests for the committed default ruleset (tiers.toml).

These pin the SPEC §Verification examples: /etc config -> T1, bootloader
binary/keys -> T2, kernel package -> T2, unknown path -> T2 (default-deny),
plus the precedence/specificity behaviour (a critical /etc/pengyos target is
T2 even though /etc/* is broadly T1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_tiers.approvals import Approval
from pengyos_tiers.change import Change
from pengyos_tiers.classifier import classify
from pengyos_tiers.rules import RulesFile

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "tiers.toml"


@pytest.fixture(scope="module")
def rules():
    return RulesFile.load(COMMITTED)


def _classify(rules, action, path, target=None):
    kwargs = {"action": action, "path": path}
    if target is not None:
        kwargs["target"] = target
    return classify(Change.from_dict(kwargs), rules)


# --- SPEC Verification examples ----------------------------------------------


def test_etc_config_is_tier1(rules):
    cls = _classify(rules, "modify", "/etc/foo")
    assert cls.tier == 1
    assert cls.matched
    assert cls.approvals == (Approval.AGENT_AUTO,)
    assert cls.needs_human is False


def test_bootloader_entry_is_tier1(rules):
    cls = _classify(rules, "modify", "/boot/efi/EFI/fedora/loader/entries/arch.conf")
    assert cls.tier == 1
    assert cls.needs_human is False


def test_bootloader_keys_is_tier2(rules):
    cls = _classify(rules, "modify", "/boot/efi/EFI/fedora/keys.db")
    assert cls.tier == 2
    assert cls.approvals == (Approval.HUMAN_SIGN,)
    assert cls.needs_human is True


def test_bootloader_binary_is_tier2(rules):
    cls = _classify(rules, "modify", "/boot/efi/EFI/fedora/BOOTX64.EFI")
    assert cls.tier == 2
    assert cls.needs_human is True


def test_kernel_package_is_tier2(rules):
    cls = _classify(rules, "modify", "/usr/lib/modules/6.8.0-pengyos/kernel/fs/ext4.ko.zst")
    assert cls.tier == 2
    assert cls.needs_human is True


def test_kernel_image_is_tier2(rules):
    cls = _classify(rules, "modify", "/boot/vmlinuz-pengyos")
    assert cls.tier == 2


# --- precedence + specificity ------------------------------------------------


def test_critical_etc_target_beats_broad_etc(rules):
    # /etc/* is broadly T1, but an explicitly-named critical target under
    # /etc/pengyos is T2 — the critical rule is listed first and wins.
    cls = _classify(rules, "modify", "/etc/pengyos/hwprov.json", target="update-trust-root")
    assert cls.tier == 2
    assert cls.needs_human is True


def test_non_critical_etc_under_pengyos_is_tier1(rules):
    # Same path, but without the critical target it falls through to the broad
    # /etc/* rule -> T1.
    cls = _classify(rules, "modify", "/etc/pengyos/hwprov.json", target="other-target")
    assert cls.tier == 1
    assert cls.needs_human is False


# --- default-deny ------------------------------------------------------------


def test_unknown_path_is_default_denied_tier2(rules):
    cls = _classify(rules, "modify", "/opt/whatever")
    assert cls.tier == 2
    assert not cls.matched
    assert cls.rule_name is None
    assert cls.approvals == (Approval.HUMAN_SIGN,)
    assert cls.needs_human is True


def test_default_deny_reason_is_human_sign(rules):
    cls = _classify(rules, "modify", "/no/such/known/path")
    assert "default-deny" in cls.reason.lower()
    assert "tier 2" in cls.reason.lower()
