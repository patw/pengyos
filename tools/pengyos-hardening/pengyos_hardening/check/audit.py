"""The auditor: walk a repo tree, collect the audit locations, and evaluate
every baseline rule against them, collecting :class:`Finding`s.

The audit is deliberately matcher-agnostic: it walks the repo, finds each
BR2_EXTERNAL external tree root (and its overlays), and hands every rule to the
matcher layer. Rules resolve their ``file``/``files`` relative to an appropriate
base — overlay rules (``key_value``, ``nft_policy``, ``mount_option``,
``unit_enabled``) resolve against each overlay root; defconfig rules
(``symbol_absent``, ``symbol_present``) resolve against the tree root. A matcher
kind with no evaluator is **skipped** (reported, not failed) so the tool
degrades gracefully as matchers are added one domain at a time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..baseline import Baseline, Rule
from ..errors import Finding
from . import matchers

__all__ = ["AuditResult", "AuditLocation", "discover_locations", "audit"]


@dataclass(frozen=True)
class AuditLocation:
    """A BR2_EXTERNAL external tree root and its overlay roots.

    Files named by the baseline resolve relative to a *base* chosen by the
    auditor: the tree root for defconfig rules, or each overlay root
    (the ``board/<target>/rootfs-overlay`` dir) for overlay rules.
    """

    root: Path
    overlay_roots: list[Path] = field(default_factory=list)


@dataclass
class AuditResult:
    """The outcome of auditing a tree: findings, skipped rules, locations."""

    findings: list[Finding] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    locations: list[AuditLocation] = field(default_factory=list)
    rules_audited: int = 0

    @property
    def rule_ids_skipped(self) -> list[str]:
        """The distinct rule ids skipped (matcher not yet implemented)."""
        seen: list[str] = []
        for rule_id in self.skipped:
            if rule_id not in seen:
                seen.append(rule_id)
        return seen

    def sorted_findings(self) -> list[Finding]:
        """Findings sorted by severity rank, then path, then rule id (stable)."""
        severity_rank = {
            "critical": 0,
            "high": 1,
            "medium": 2,
            "low": 3,
            "info": 4,
        }
        return sorted(
            self.findings,
            key=lambda f: (
                severity_rank.get(f.severity, 99),
                f.path or "",
                f.rule_id,
            ),
        )


# Matcher kinds whose ``file``/``files`` resolve against an overlay root
# (the file lives under ``board/<target>/rootfs-overlay/...``). These grew
# one reader at a time (sshd, sysctl.d, fstab, nftables, systemd units).
OVERLAY_KINDS: frozenset[str] = frozenset(
    {"key_value", "sysctl_value", "mount_option", "nft_policy", "unit_enabled"}
)


#: Matcher kinds whose ``files`` resolve against the *tree root* (the
#: ``BR2_EXTERNAL`` external tree root, where ``configs/*_defconfig`` live).
#: The Buildroot defconfig symbol matchers (``symbol_absent`` /
#: ``symbol_present``) read the defconfigs from the tree root, not an overlay.
SYMBOL_KINDS: frozenset[str] = frozenset({"symbol_absent", "symbol_present"})


def _find_external_tree_roots(tree: Path) -> list[Path]:
    """Return BR2_EXTERNAL external tree roots under ``tree``.

    An external tree root is a directory holding ``external.desc`` (the Buildroot
    marker) — or, when that is absent, the directory holding ``configs/*_defconfig``.
    Roots are found recursively so a repo with several external trees is covered.
    """
    roots: list[Path] = []
    for desc in tree.glob("**/external.desc"):
        if desc.is_file():
            roots.append(desc.parent)
    if not roots:
        for configs_dir in tree.glob("**/configs"):
            if configs_dir.is_dir() and list(configs_dir.glob("*_defconfig")):
                roots.append(configs_dir.parent)
    return sorted(set(roots))


def _overlay_roots_for(tree_root: Path) -> list[Path]:
    """Return the overlay roots (``rootfs-overlay`` dirs) under ``tree_root``.

    The overlay root is the ``rootfs-overlay`` directory itself: baseline
    ``file`` params (``etc/ssh/sshd_config``) resolve against it, so
    ``board/<target>/rootfs-overlay/etc/ssh/sshd_config`` is found.
    """
    overlays = sorted(tree_root.glob("**/rootfs-overlay"))
    return [o for o in overlays if o.is_dir()]


def discover_locations(tree: Path) -> list[AuditLocation]:
    """Discover the auditable external tree roots under ``tree``."""
    locations: list[AuditLocation] = []
    for tree_root in _find_external_tree_roots(tree):
        locations.append(
            AuditLocation(root=tree_root, overlay_roots=_overlay_roots_for(tree_root))
        )
    return locations


def _bases_for(rule: Rule, locations: Iterable[AuditLocation]) -> list[Path]:
    """Return the base directories a rule should be evaluated against.

    Overlay rules (``key_value``, ``sysctl_value``, ``mount_option``,
    ``nft_policy``, ``unit_enabled``) resolve their ``file``/``files`` against
    each overlay root. The defconfig symbol matchers (``symbol_absent``,
    ``symbol_present``) resolve against the *tree root* (where
    ``configs/*_defconfig`` live). A kind with no base here is skipped.
    """
    if rule.matcher.kind in OVERLAY_KINDS:
        return [overlay for loc in locations for overlay in loc.overlay_roots]
    if rule.matcher.kind in SYMBOL_KINDS:
        return [loc.root for loc in locations]
    return []


def _evaluate_rule(rule: Rule, bases: list[Path]) -> Finding | None:
    """Evaluate ``rule`` across all bases; return the first finding found."""
    for base in bases:
        finding = matchers.evaluate(rule, base)
        if finding is not None:
            return finding
    return None


def audit(
    tree: Path,
    baseline: Baseline,
    *,
    rule: str | None = None,
) -> AuditResult:
    """Audit ``tree`` against ``baseline``.

    For each rule (optionally filtered by ``rule`` id), evaluate it against the
    bases its matcher kind resolves against. A matcher kind with no evaluator is
    skipped (recorded in the result, not failed). Findings are returned in
    insertion order — use :meth:`AuditResult.sorted_findings` for a stable order.
    """
    locations = discover_locations(tree)
    result = AuditResult(locations=locations)

    rules = [baseline.by_id(rule)] if rule is not None else baseline.rules
    for rule_obj in rules:
        result.rules_audited += 1
        if rule_obj.matcher.kind not in matchers.IMPLEMENTED_KINDS:
            result.skipped.append(rule_obj.id)
            continue
        try:
            bases = _bases_for(rule_obj, locations)
            finding = _evaluate_rule(rule_obj, bases)
        except NotImplementedError:
            result.skipped.append(rule_obj.id)
            continue
        if finding is not None:
            result.findings.append(finding)
    return result
