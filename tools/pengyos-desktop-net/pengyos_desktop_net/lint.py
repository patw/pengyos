"""lint — check an emitted/installed network overlay for the spec's invariants.

``lint`` reads a build-consumed overlay (the tree ``emit`` produced, or the tree
installed on a target) and asserts the four guarantees the spec makes:

1. **NetworkManager is the chosen network service.** The overlay contains a
   NetworkManager config (``NetworkManager/``), and `systemd-networkd` does not
   also own the Wi-Fi link (no ``networkd`` ``.network`` file and no enabled
   ``systemd-networkd`` service claiming the link).
2. **The Wi-Fi stack + firmware are referenced.** The overlay contains a
   ``wpa_supplicant`` config *and* a Wi-Fi firmware manifest.
3. **The panel default includes the network applet.** The COSMIC panel default
   lists ``cosmic-applet-network``.
4. **No conflicting ``networkd`` ownership.** Covered by rule 1's negative check.

A failure returns a non-empty list of :class:`Violation` and a truthy ``ok``.
The CLI exits non-zero when there are violations.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

NETWORK_APPLET = "cosmic-applet-network"


@dataclass(frozen=True)
class Violation:
    """A single lint failure: the rule that failed and why."""

    rule: str
    message: str


@dataclass(frozen=True)
class Result:
    """The outcome of linting an overlay: ``ok`` plus any violations."""

    ok: bool
    violations: list[Violation]

    @property
    def messages(self) -> list[str]:
        return [f"{v.rule}: {v.message}" for v in self.violations]


def _iter_files(overlay: str) -> list[str]:
    """Return the overlay's files as ``<rel-path>`` strings (sorted, stable)."""
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(overlay):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, overlay)
            found.append(rel.replace(os.sep, "/"))
    return sorted(found)


def _classify(rel: str) -> set[str]:
    """Classify an overlay file into the kinds it contributes to."""
    kinds: set[str] = set()
    low = rel.lower()
    if "networkmanager" in low:
        kinds.add("networkmanager")
    if low.endswith("wpa_supplicant.conf") or "wpa_supplicant" in low:
        kinds.add("wpa_supplicant")
    if low.endswith("wifi-firmware.txt") or "wifi-firmware" in low:
        kinds.add("wifi-firmware")
    if low.endswith("panel.toml"):
        kinds.add("cosmic-panel")
    if "cosmic-settings" in low and low.endswith(".toml"):
        kinds.add("cosmic-settings")
    return kinds


def _read(overlay: str, rel: str) -> str:
    with open(os.path.join(overlay, rel), "r", encoding="utf-8") as fh:
        return fh.read()


def _networkd_violations(overlay: str, files: list[str]) -> list[Violation]:
    """systemd-networkd must not own the Wi-Fi link."""
    violations: list[Violation] = []
    network_files = [f for f in files if f.lower().endswith(".network")]
    if network_files:
        violations.append(
            Violation(
                "no-networkd-ownership",
                f"systemd-networkd owns a link via {network_files}; "
                "NetworkManager must be the sole network service",
            )
        )
    enabled_services = [
        f for f in files if "systemd-networkd" in f.lower()
    ]
    if enabled_services:
        violations.append(
            Violation(
                "no-networkd-ownership",
                f"systemd-networkd is referenced/enabled via {enabled_services}",
            )
        )
    return violations


def lint_overlay(overlay: str) -> Result:
    """Lint the overlay at ``overlay`` and return a :class:`Result`."""
    if not os.path.isdir(overlay):
        return Result(
            ok=False,
            violations=[
                Violation(
                    "overlay-exists",
                    f"{overlay!r} is not a directory",
                )
            ],
        )

    files = _iter_files(overlay)
    kinds: set[str] = set()
    for rel in files:
        kinds |= _classify(rel)

    violations: list[Violation] = []

    # Rule 1: NetworkManager is the chosen network service.
    if "networkmanager" not in kinds:
        violations.append(
            Violation(
                "nm-enabled",
                "no NetworkManager config found in overlay (NM is not the "
                "network service)",
            )
        )

    # Rule 2: the Wi-Fi stack + firmware are referenced.
    if "wpa_supplicant" not in kinds:
        violations.append(
            Violation(
                "wifi-stack",
                "no wpa_supplicant config found (Wi-Fi backend not referenced)",
            )
        )
    if "wifi-firmware" not in kinds:
        violations.append(
            Violation(
                "wifi-firmware",
                "no Wi-Fi firmware manifest found (firmware not referenced)",
            )
        )

    # Rule 3: the panel default includes the network applet. Match a real
    # ``applet="..."`` entry, not a stray mention (e.g. a comment), so a panel
    # that merely *talks about* the applet does not pass.
    panel_entry = f'applet="{NETWORK_APPLET}"'
    panel_found = False
    for rel in files:
        if "panel.toml" in rel:
            panel_found = True
            content = _read(overlay, rel)
            if panel_entry not in content:
                violations.append(
                    Violation(
                        "network-applet-in-panel",
                        f"{rel} does not include {NETWORK_APPLET}",
                    )
                )
    if not panel_found:
        violations.append(
            Violation(
                "network-applet-in-panel",
                "no cosmic panel default found in overlay",
            )
        )

    # Rule 1 (negative): no conflicting networkd ownership.
    violations.extend(_networkd_violations(overlay, files))

    return Result(ok=not violations, violations=violations)


def lint_json(overlay: str) -> str:
    """Lint ``overlay`` and return a JSON string result."""
    result = lint_overlay(overlay)
    return json.dumps(
        {
            "ok": result.ok,
            "violations": [
                {"rule": v.rule, "message": v.message} for v in result.violations
            ],
        },
        indent=2,
    ) + "\n"
