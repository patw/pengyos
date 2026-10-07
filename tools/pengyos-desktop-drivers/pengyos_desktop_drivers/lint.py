"""``lint`` -- validate a kernel fragment + firmware list.

Responsibility: given a
fragment file and a firmware file, enforce four rules and raise
:class:`~pengyos_desktop_drivers.errors.LintViolation` (with every failing
rule's reasons collected) when any fails:

1. **Symbol existence** -- every enabled ``CONFIG_*`` symbol in the fragment
   exists in the vendored 2026.08 symbol list.
2. **No vendor/proprietary acceleration** -- the fragment contains none of the
   forbidden symbols (NVIDIA, the open KMS drivers' vendor-accel toggles, ...).
3. **Firmware subset** -- every line in the firmware list is an upstream
   ``linux-firmware`` name **and** the list is complete: at least one entry is
   present under every curated prefix (so a list missing the wifi stack -- or
   any other curated prefix -- is rejected).
4. **Class coverage** -- the fragment enables at least one display, one input,
   and one wifi driver.

The four rule identifiers are the stable keys the CLI and tests key on; the
exact forbidden-symbol set is grounded in this task against the 2026.08 list.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from . import data, profile
from .errors import LintViolation, PengyOSError

# Rule identifiers -- the stable keys the CLI and tests key on.
RULE_SYMBOL_EXISTS = "symbol-exists"
RULE_NO_VENDOR_ACCEL = "no-vendor-accel"
RULE_FIRMWARE_SUBSET = "firmware-subset"
RULE_CLASS_COVERAGE = "class-coverage"

# The forbidden-symbol categories, described for the contract. Each maps a rule
# id to a human-readable description of what it rejects.
FORBIDDEN_SYMBOL_CATEGORIES: dict[str, str] = {
    RULE_NO_VENDOR_ACCEL: (
        "vendor-proprietary / vendor-acceleration symbols "
        "(e.g. NVIDIA, AMDGPU/RADEON vendor-accel toggles)"
    ),
}

# The driver classes the linter requires at least one symbol for. Grounded in
# the spec's "at least one display + one input + one wifi driver present".
REQUIRED_CLASSES: list[str] = ["display", "input", "wifi"]

# The reason string the completeness half of the firmware-subset rule reports
# when a curated prefix has no entry in the firmware list.
FIRMWARE_INCOMPLETE_REASON = (
    "firmware list is missing entries for curated prefix(es): {prefixes}"
)

# The forbidden CONFIG_* symbols. These are the vendor-proprietary /
# vendor-acceleration toggles the generic profile must never enable. The set is
# deliberately conservative: it targets the actual proprietary stack (NVIDIA,
# the Nouveau / accel toggles) plus the open-driver vendor-accel knobs that
# would pull in non-generic acceleration. Symbols absent from the 2026.08 list
# simply can never be enabled in this build, so forbidding them is a no-op
# guard that still documents intent and future-proofs the rule.
FORBIDDEN_SYMBOLS: tuple[str, ...] = (
    "CONFIG_NVIDIA",
    "CONFIG_DRM_NOUVEAU",
    "CONFIG_DRM_ACCEL",
    "CONFIG_DRM_RADEON_DPM",
    "CONFIG_DRM_AMDGPU_DC",
    "CONFIG_DRM_AMDGPU_SI",
    "CONFIG_DRM_AMDGPU_KIQ",
    "CONFIG_DRM_AMDGPU_SEV",
    "CONFIG_DRM_I915_ENABLE_HDCP",
)

# Matches an enabled kernel config line, e.g. ``CONFIG_FOO=y`` / ``=m``.
_ENABLED_SYMBOL_RE = re.compile(r"^CONFIG_([A-Z0-9_]+)\s*=\s*[ym]\s*$")
# Matches any CONFIG_* symbol name on a line (enabled or commented-out), used
# only to classify a line as "config" vs. prose/header.
_ANY_SYMBOL_RE = re.compile(r"^CONFIG_([A-Z0-9_]+)")


def _read_lines(path: str | Path) -> list[str]:
    """Return the stripped, non-empty lines of ``path`` (UTF-8)."""
    text = Path(path).read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _enabled_symbols(lines: Iterable[str]) -> set[str]:
    """Return the ``CONFIG_*`` symbols enabled (``=y``/``=m``) in ``lines``.

    Only lines that *set* a config are considered; the vendored fragment header
    (commented prose) and ``# CONFIG_X is not set`` lines are ignored.
    """
    symbols: set[str] = set()
    for line in lines:
        if line.startswith("#"):
            continue
        match = _ENABLED_SYMBOL_RE.match(line)
        if match:
            symbols.add(f"CONFIG_{match.group(1)}")
    return symbols


def _fragment_class_symbols(
    symbols: set[str], classes: Iterable[str] | None = None
) -> dict[str, set[str]]:
    """Map each class (or ``REQUIRED_CLASSES``) to its enabled symbols.

    A class "covers" a required class when at least one of its declared symbols
    is enabled in the fragment.
    """
    wanted = set(classes) if classes else set(profile.CLASS_ORDER)
    by_class: dict[str, set[str]] = {}
    for cls in profile.classes():
        if cls.name in wanted:
            enabled = {sym for sym in cls.symbols if sym in symbols}
            by_class[cls.name] = enabled
    return by_class


def _check_symbol_existence(
    lines: list[str], symbols: set[str]
) -> tuple[bool, list[str]]:
    enabled = _enabled_symbols(lines)
    missing = sorted(enabled - symbols)
    if not missing:
        return True, []
    return False, [f"fragment enables symbols absent from the 2026.08 list: {', '.join(missing)}"]


def _check_no_vendor_accel(
    lines: list[str], symbols: set[str]
) -> tuple[bool, list[str]]:
    enabled = _enabled_symbols(lines)
    hit = sorted(enabled & set(FORBIDDEN_SYMBOLS))
    if not hit:
        return True, []
    # Only report the ones actually reachable in this build; the rest are
    # documented guards that can never fire here.
    reachable = [s for s in hit if s in symbols]
    report = reachable or hit
    return False, [
        f"fragment enables forbidden vendor/proprietary/accel symbols: {', '.join(report)}"
    ]


def _check_firmware_subset(
    fw_lines: list[str], upstream: set[str]
) -> tuple[bool, list[str]]:
    """Every firmware line must be an upstream ``linux-firmware`` name.

    The subset half of the ``firmware-subset`` rule: a line that is not an
    upstream name is rejected. Completeness (every curated prefix present) is
    the separate :func:`_check_firmware_completeness` half -- both feed the same
    rule id so a dirty list surfaces every firmware problem at once.
    """
    missing = sorted(n for n in fw_lines if n not in upstream)
    if not missing:
        return True, []
    return False, [f"firmware list contains names not in upstream linux-firmware: {', '.join(missing)}"]


def _check_firmware_completeness(fw_lines: list[str]) -> tuple[bool, list[str]]:
    """Every curated prefix must have at least one firmware entry.

    The completeness half of the ``firmware-subset`` rule. This is what makes
    "dropping the wifi firmware" (or any other curated prefix) fire: the emitted
    Desktop profile selects an entry under every curated prefix, so a list that
    drops one -- e.g. the wifi stack (``intel/iwlwifi/`` + ``rtlwifi/`` +
    ``rtl_bt/``) -- leaves that prefix uncovered and is rejected.

    An empty curated-prefix set (no prefixes declared) is vacuously complete, so
    an intentionally-empty firmware list is still a valid subset.
    """
    prefixes = profile.firmware_prefixes()
    if not prefixes:
        return True, []
    covered = {p for p in prefixes if any(n.startswith(p) for n in fw_lines)}
    uncovered = sorted(set(prefixes) - covered)
    if not uncovered:
        return True, []
    return False, [FIRMWARE_INCOMPLETE_REASON.format(prefixes=", ".join(uncovered))]


def _check_class_coverage(
    lines: list[str], classes: Iterable[str]
) -> tuple[bool, list[str]]:
    enabled = _enabled_symbols(lines)
    by_class = _fragment_class_symbols(enabled, classes)
    missing = [name for name in classes if not by_class.get(name)]
    if not missing:
        return True, []
    return False, [f"fragment missing a symbol for required class(es): {', '.join(missing)}"]


def lint_fragment(
    fragment_path: str | Path,
    firmware_path: str | Path,
    as_json: bool = False,
) -> dict[str, Any]:
    """Validate ``fragment_path`` and ``firmware_path`` against the four rules.

    Returns a result dict::

        {
            "ok": bool,
            "rules": {rule_id: bool, ...},
            "errors": [str, ...],
            "fragment": str,
            "firmware": str,
        }

    Every rule is evaluated and **all** failing reasons are collected in
    ``errors`` (the CLI prints them all before exiting). When at least one rule
    fails, raises :class:`~pengyos_desktop_drivers.errors.LintViolation`; the
    returned dict's ``ok`` is ``False`` and mirrors ``.errors``.

    :class:`~pengyos_desktop_drivers.errors.PengyOSError` is raised (not a
    bare ``LintViolation``) when a file is missing or unreadable, so the CLI
    surfaces it as a plain usage-style error.
    """
    frag_path = Path(fragment_path)
    fw_path = Path(firmware_path)
    for label, path in (("fragment", frag_path), ("firmware", fw_path)):
        if not path.exists():
            raise PengyOSError(f"{label} file not found: {path}")

    frag_lines = _read_lines(frag_path)
    fw_lines = _read_lines(fw_path)

    symbols = data.load_symbols()
    upstream = set(data.load_firmware_names())
    required = profile.required_for_coverage()

    # The firmware-subset rule is two halves (subset + completeness); both feed
    # the same rule id so a dirty list surfaces every firmware problem at once.
    subset_ok, subset_reasons = _check_firmware_subset(fw_lines, upstream)
    complete_ok, complete_reasons = _check_firmware_completeness(fw_lines)
    firmware_ok = subset_ok and complete_ok

    checks = (
        (RULE_SYMBOL_EXISTS, _check_symbol_existence(frag_lines, symbols)),
        (RULE_NO_VENDOR_ACCEL, _check_no_vendor_accel(frag_lines, symbols)),
        (RULE_FIRMWARE_SUBSET, (firmware_ok, subset_reasons + complete_reasons)),
        (RULE_CLASS_COVERAGE, _check_class_coverage(frag_lines, required)),
    )
    rules = {rule_id: ok for rule_id, (ok, _reasons) in checks}
    errors: list[str] = []
    for rule_id, (_ok, reason_list) in checks:
        errors.extend(reason_list)

    result: dict[str, Any] = {
        "ok": not errors,
        "rules": rules,
        "errors": errors,
        "fragment": str(frag_path),
        "firmware": str(fw_path),
    }
    if errors:
        raise LintViolation(errors, rules=rules)
    return result
