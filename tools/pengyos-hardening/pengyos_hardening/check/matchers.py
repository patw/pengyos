"""Matcher evaluators: turn a baseline rule's declarative ``matcher`` into a
concrete :class:`~pengyos_hardening.errors.Finding` (or ``None`` when the rule
passes).

The baseline is the contract; the matcher layer is the reader. Matchers are
added one domain at a time: the ``key_value`` matcher —
the sshd_config reader — is first, then ``sysctl_value`` (sysctl.d) and
``mount_option`` (fstab), then ``nft_policy`` (nftables) and ``unit_enabled``
(systemd ``.wants``), then ``symbol_absent`` / ``symbol_present`` (Buildroot
defconfigs).

A matcher returns:

* ``None`` — the rule passes (the effective value matches), or the file the
  rule expected is absent (a missing file is *not* a deviation we can pin to a
  line — the rule is simply not evaluable there).
* :class:`Finding` — the rule fired: the effective value differs from, or is
  missing relative to, the expected value, with ``file:line`` evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..baseline import Rule
from ..errors import Finding
from .readers import (
    Defconfig,
    parse_defconfig,
    parse_fstab,
    parse_nftables,
    parse_sshd_config,
    parse_sysctl_d,
)

__all__ = ["MatcherImpl", "evaluate", "IMPLEMENTED_KINDS", "NOT_IMPLEMENTED"]


@runtime_checkable
class MatcherImpl(Protocol):
    """A matcher implementation: ``evaluate(rule, root) -> Finding | None``."""

    def evaluate(self, rule: Rule, root: Path) -> Finding | None: ...


def _canonical_nft_policy(policy: str | None) -> str | None:
    """Canonicalise an nftables chain policy for comparison.

    nft's canonical default policies are ``accept``/``drop``/``reject``. The
    baseline expresses the default-deny posture as ``deny`` (the fixture writes
    ``policy deny;``). Both ``deny`` and ``drop`` mean "default-deny", so they
    collapse to ``drop``; ``accept``/``reject`` pass through. A ``None`` policy
    (a chain with no policy keyword) stays ``None`` so the matcher can report it
    distinctly. Unknown tokens map to ``None`` too — they carry no policy to
    compare, so the matcher reports "no default policy".
    """
    if policy is None:
        return None
    return {"deny": "drop", "drop": "drop", "reject": "reject", "accept": "accept"}.get(
        policy.strip().lower()
    )


class KeyValueMatcher:
    """The sshd_config reader: last-wins key/value comparison.

    Backs every ``key_value`` rule (all the ``ssh`` rules read
    ``etc/ssh/sshd_config``). The rule's ``expected`` is compared against the
    effective value read from the file. sshd boolean keys accept yes/no/on/off/
    true/false/1/0 case-insensitively; we compare the *effective* value with
    that understanding so the comparison is spelling-agnostic.
    """

    def evaluate(self, rule: Rule, root: Path) -> Finding | None:
        params = rule.matcher.params
        rel = params["file"]
        key = params["key"]
        expected = str(params["expected"]).strip().lower()
        path = root / rel
        if not path.is_file():
            # The file the rule reads is absent — not evaluable here.
            return None

        config = parse_sshd_config(path.read_text(encoding="utf-8"))
        raw_value = config.get(key)
        lineno = config.line_for(key)

        if raw_value is None:
            return Finding(
                rule_id=rule.id,
                severity=rule.severity,
                message=f"{key} is not set (expected {params['expected']!r})",
                path=str(rel),
                line=lineno,
            )

        actual = raw_value.strip().lower()
        if self._normalise_bool(actual) != self._normalise_bool(expected):
            return Finding(
                rule_id=rule.id,
                severity=rule.severity,
                message=f"{key} is {raw_value!r}, expected {params['expected']!r}",
                path=str(rel),
                line=lineno,
            )
        return None

    @staticmethod
    def _normalise_bool(value: str) -> str:
        """Collapse sshd boolean spellings to a canonical ``yes``/``no``.

        sshd treats yes/no/on/off/true/false/1/0 as booleans. We map truthy and
        falsy spellings to ``yes``/``no`` so the comparison is spelling-agnostic.
        Anything else passes through unchanged (and will simply not match).
        """
        if value in {"yes", "on", "true", "1"}:
            return "yes"
        if value in {"no", "off", "false", "0"}:
            return "no"
        return value


# --- dispatch ---------------------------------------------------------------

class SysctlValueMatcher:
    """The sysctl.d reader: effective last-wins key/value comparison.

    Backs every ``sysctl_value`` rule. The rule's ``files`` is a list of globs
    (relative to the overlay root); they are sorted, parsed, and merged with
    last-write-wins across files (the way the kernel applies ``sysctl.d``). The
    effective value for the rule's ``key`` is compared against ``expected``.
    """

    def evaluate(self, rule: Rule, root: Path) -> Finding | None:
        params = rule.matcher.params
        key = params["key"]
        expected = str(params["expected"]).strip()

        files = self._resolve(params["files"], root)
        values = parse_sysctl_d(files)
        actual = values.get(key)

        if actual is None:
            return Finding(
                rule_id=rule.id,
                severity=rule.severity,
                message=f"{key} is not set (expected {expected!r})",
                path=str("/".join(params["files"])),
                line=None,
            )

        if actual != expected:
            rel_file, lineno = values.source(key)
            return Finding(
                rule_id=rule.id,
                severity=rule.severity,
                message=f"{key} is {actual!r}, expected {expected!r}",
                path=rel_file,
                line=lineno,
            )
        return None

    @staticmethod
    def _resolve(patterns: list[str], root: Path) -> list[str]:
        """Read each glob (sorted) into text; files are merged in sorted order."""
        texts: list[str] = []
        seen: list[Path] = []
        for pattern in patterns:
            for path in sorted(root.glob(pattern)):
                if path.is_file() and path not in seen:
                    seen.append(path)
                    texts.append(path.read_text(encoding="utf-8"))
        return texts


class MountOptionMatcher:
    """The fstab reader: a mount entry must carry the required options.

    Backs every ``mount_option`` rule. The rule names a ``file`` (the fstab), a
    ``mountpoint``, and a comma-separated ``option`` list. The rule fires when
    the entry mounting that mountpoint is missing any of those options (or the
    mountpoint is not present at all).
    """

    def evaluate(self, rule: Rule, root: Path) -> Finding | None:
        params = rule.matcher.params
        rel = params["file"]
        mountpoint = params["mountpoint"]
        required = [opt.strip() for opt in str(params["option"]).split(",") if opt.strip()]

        path = root / rel
        if not path.is_file():
            # The fstab the rule reads is absent — not evaluable here.
            return None

        fstab = parse_fstab(path.read_text(encoding="utf-8"))
        entry = fstab.get(mountpoint)

        if entry is None:
            return Finding(
                rule_id=rule.id,
                severity=rule.severity,
                message=f"mount {mountpoint!r} is not present in the fstab",
                path=rel,
                line=None,
            )

        present = entry.option_set()
        missing = [opt for opt in required if opt not in present]
        if missing:
            return Finding(
                rule_id=rule.id,
                severity=rule.severity,
                message=(
                    f"mount {mountpoint!r} is missing option(s): "
                    f"{', '.join(missing)}"
                ),
                path=rel,
                line=entry.line,
            )
        return None


class NftPolicyMatcher:
    """The nftables reader: a chain's default policy must match the baseline.

    Backs the ``nft_policy`` rule (``firewall-input-policy-deny``). The rule
    names the ``file`` (the ``nft`` ruleset), the ``chain``, and the expected
    ``expected`` policy. The rule fires when the named chain is absent, or its
    default policy differs from ``expected``.

    nft's canonical policies are ``accept``/``drop``/``reject``; the baseline
    expresses the default-deny posture as ``deny``. Both the chain's policy
    (read by :func:`parse_nftables`) and the rule's ``expected`` are run through
    :func:`_canonical_nft_policy` so ``deny`` and ``drop`` compare equal — the
    comparison is spelling-agnostic with the committed fixture.
    """

    def evaluate(self, rule: Rule, root: Path) -> Finding | None:
        params = rule.matcher.params
        rel = params["file"]
        chain = params["chain"]
        expected = _canonical_nft_policy(str(params["expected"]).strip().lower())

        path = root / rel
        if not path.is_file():
            # The ruleset the rule reads is absent — not evaluable here.
            return None

        ruleset = parse_nftables(path.read_text(encoding="utf-8"))
        nft_chain = ruleset.get(chain)

        if nft_chain is None:
            return Finding(
                rule_id=rule.id,
                severity=rule.severity,
                message=f"chain {chain!r} is not declared in {rel}",
                path=str(rel),
                line=None,
            )

        actual = _canonical_nft_policy(nft_chain.policy)
        if actual is None:
            return Finding(
                rule_id=rule.id,
                severity=rule.severity,
                message=(
                    f"chain {chain!r} declares no default policy "
                    f"(expected {params['expected']!r})"
                ),
                path=str(rel),
                line=nft_chain.line,
            )

        if actual != expected:
            return Finding(
                rule_id=rule.id,
                severity=rule.severity,
                message=(
                    f"chain {chain!r} policy is {nft_chain.policy!r}, "
                    f"expected {params['expected']!r}"
                ),
                path=str(rel),
                line=nft_chain.line,
            )
        return None


class UnitEnabledMatcher:
    """The systemd reader: a unit must be enabled in a ``*.target.wants`` dir.

    Backs the ``unit_enabled`` rule (``firewall-unit-enabled``). The rule names
    a ``wants_dir`` (relative to the overlay root) and a ``unit`` (a ``.service``
    name). systemd "enables" a unit by dropping a symlink for it into the
    relevant ``*.target.wants`` directory, so "enabled" means that symlink (or
    the unit file itself) exists at ``wants_dir/unit``. The rule fires when the
    entry is absent.

    Unlike the file-based matchers, this needs no parser — it is a filesystem
    existence check — but it is registered like every other matcher so the
    auditor can evaluate it and so a mutation that drops the symlink fires.
    """

    def evaluate(self, rule: Rule, root: Path) -> Finding | None:
        params = rule.matcher.params
        wants_dir = params["wants_dir"]
        unit = params["unit"]

        entry = root / wants_dir / unit
        if not entry.exists():
            return Finding(
                rule_id=rule.id,
                severity=rule.severity,
                message=(
                    f"unit {unit!r} is not enabled (missing from {wants_dir})"
                ),
                path=str("/".join([wants_dir, unit])),
                line=None,
            )
        return None


class _SymbolMatcher:
    """Common ground for the two defconfig symbol matchers.

    Both read *every* file the rule names (the ``files`` globs, resolved
    relative to the tree root) and ask a single question of each: is ``symbol``
    set to ``y`` here? :meth:`_scan` returns the ``(rel_path, line)`` of the
    set line — or ``None`` when the symbol is not set in that file.
    """

    #: The matcher kind this base class backs.
    kind: str = ""
    #: The human-readable verb for the "set" predicate, for the messages.
    verb: str = ""

    def _resolved(self, patterns: list[str], root: Path) -> list[tuple[str, Path]]:
        """Resolve the rule's ``files`` globs relative to ``root``.

        Each glob is matched against ``root`` (sorted, deduped); the matching
        files are returned as ``(relative_path, Path)`` pairs in glob order —
        the same last-declared-wins ordering the auditor feeds every other
        reader.
        """
        resolved: list[tuple[str, Path]] = []
        seen: set[Path] = set()
        for pattern in patterns:
            for path in sorted(root.glob(pattern)):
                if path.is_file() and path not in seen:
                    seen.add(path)
                    resolved.append((str(path.relative_to(root)), path))
        return resolved

    def _scan(self, files: list[str], symbol: str) -> tuple[str, int] | None:
        """Find the set line for ``symbol`` across ``files`` (first wins).

        ``files`` are the parsed defconfig texts in glob order; the first file
        that sets ``symbol`` wins, and its ``(rel_path, line)`` is returned.
        """
        for rel_path, text in files:
            parsed: Defconfig = parse_defconfig(text)
            line = parsed.line(symbol)
            if line is not None:
                return rel_path, line
        return None


class SymbolAbsentMatcher(_SymbolMatcher):
    """The defconfig reader: a symbol must NOT be set in any defconfig.

    Backs every ``symbol_absent`` rule (``defconfig-no-telnet``,
    ``defconfig-no-debug``). The rule names the ``files`` (the ``*_defconfig``s)
    and a ``symbol`` (a ``BR2_…`` package symbol). The rule fires when the
    symbol is set to ``y`` in *any* of those files — a forbidden surface that
    slipped into one edition. Evidence is the offending ``(file:line)``.

    The Buildroot quirk is the whole story here: a *disabled* symbol is written
    ``# CONFIG_BR2_FOO is not set`` (a comment, WITH the ``CONFIG_`` prefix).
    :func:`~pengyos_hardening.check.readers.parse_defconfig` only records real
    ``BR2_FOO=y`` set-lines, so a disabled symbol is never mistaken for a set
    one.
    """

    kind = "symbol_absent"
    verb = "must not be enabled"

    def evaluate(self, rule: Rule, root: Path) -> Finding | None:
        params = rule.matcher.params
        symbol = str(params["symbol"])
        files = [
            (rel, path.read_text(encoding="utf-8"))
            for rel, path in self._resolved(params["files"], root)
        ]
        hit = self._scan(files, symbol)
        if hit is None:
            return None
        rel_path, line = hit
        return Finding(
            rule_id=rule.id,
            severity=rule.severity,
            message=(
                f"symbol {symbol!r} is set (it {self.verb}); "
                f"disable it in the defconfig"
            ),
            path=rel_path,
            line=line,
        )


class SymbolPresentMatcher(_SymbolMatcher):
    """The defconfig reader: a symbol MUST be set in every defconfig.

    Backs every ``symbol_present`` rule (``defconfig-openssh-server``). The rule
    names the ``files`` (the ``*_defconfig``s) and a ``symbol``. The rule fires
    when the symbol is *not* set to ``y`` in *any* of those files — a required
    package that one edition forgot to enable. Evidence is the first defconfig
    that omits it.
    """

    kind = "symbol_present"
    verb = "must be enabled"

    def evaluate(self, rule: Rule, root: Path) -> Finding | None:
        params = rule.matcher.params
        symbol = str(params["symbol"])
        resolved = self._resolved(params["files"], root)
        if not resolved:
            # No defconfig files at all — the rule reads nothing here.
            return None

        missing: list[tuple[str, int]] = []
        for rel_path, path in resolved:
            text = path.read_text(encoding="utf-8")
            parsed: Defconfig = parse_defconfig(text)
            if not parsed.is_set(symbol):
                # Pin the line the symbol *would* be set on: the last set line
                # in the file, or the first set line if the file has none.
                last_line = max(parsed.symbols.values()) if parsed.symbols else 1
                missing.append((rel_path, last_line))

        if not missing:
            # Every defconfig sets the symbol — the rule passes.
            return None

        rel_path, line = missing[0]
        return Finding(
            rule_id=rule.id,
            severity=rule.severity,
            message=(
                f"symbol {symbol!r} is not set in {rel_path}; "
                f"enable {symbol!r} in every defconfig"
            ),
            path=rel_path,
            line=line,
        )


# --- dispatch ---------------------------------------------------------------

#: Matcher kinds with an implementation. Unknown kinds are a malformed rule at
#: the baseline layer; kinds with no implementation yet are reported as skipped.
IMPLEMENTED_KINDS: frozenset[str] = frozenset(
    {
        "key_value",
        "sysctl_value",
        "mount_option",
        "nft_policy",
        "unit_enabled",
        "symbol_absent",
        "symbol_present",
    }
)

#: Matcher kinds that exist in the schema but have no evaluator yet.
NOT_IMPLEMENTED: frozenset[str] = frozenset()

_MATCHERS: dict[str, MatcherImpl] = {
    "key_value": KeyValueMatcher(),
    "sysctl_value": SysctlValueMatcher(),
    "mount_option": MountOptionMatcher(),
    "nft_policy": NftPolicyMatcher(),
    "unit_enabled": UnitEnabledMatcher(),
    "symbol_absent": SymbolAbsentMatcher(),
    "symbol_present": SymbolPresentMatcher(),
}


def evaluate(rule: Rule, root: Path) -> Finding | None:
    """Evaluate ``rule`` against the tree rooted at ``root``.

    Dispatches on the rule's matcher ``kind``. A kind with no evaluator raises
    :class:`NotImplementedError` (the auditor treats that as "skip this rule"
    at a higher level). Returns ``None`` when the rule passes (or is not
    evaluable), or a :class:`Finding` when it fires.
    """
    kind = rule.matcher.kind
    impl = _MATCHERS.get(kind)
    if impl is None:
        raise NotImplementedError(
            f"no evaluator for matcher kind {kind!r} "
            f"(not yet implemented: {sorted(NOT_IMPLEMENTED)})"
        )
    return impl.evaluate(rule, root)
