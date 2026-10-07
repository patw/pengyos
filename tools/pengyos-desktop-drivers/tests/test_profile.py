"""Driver-profile-model tests.

These pin the declarative profile in ``pengyos_desktop_drivers.profile`` and,
crucially, **ground** it against the vendored reference data: every declared
fragment symbol must exist in ``symbols.2026.08.txt``, and every curated firmware
prefix must match at least one upstream name in ``firmware.upstream.txt``. A
profile that references a dropped-out symbol or a dead firmware prefix fails here
loudly rather than emitting something broken.
"""

from __future__ import annotations

import pengyos_desktop_drivers
from pengyos_desktop_drivers import data, profile


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #
def test_profile_is_exposed_as_package_data() -> None:
    # `from pengyos_desktop_drivers import profile` works (re-exported).
    assert profile is pengyos_desktop_drivers.profile
    assert profile.classes()


def test_required_classes_present_in_canonical_order() -> None:
    names = [cls.name for cls in profile.classes()]
    # The five classes the spec names.
    for required in ("display", "input", "wifi", "storage", "net"):
        assert required in names
    # And they appear in the documented canonical order.
    assert names == list(profile.CLASS_ORDER)
    assert names == ["display", "input", "wifi", "storage", "net"]


def test_every_class_has_symbols() -> None:
    for cls in profile.classes():
        assert cls.symbols, f"{cls.name} must enable at least one symbol"
        assert all(sym.startswith("CONFIG_") for sym in cls.symbols), (
            f"{cls.name} has a non-CONFIG symbol: {cls.symbols!r}"
        )


def test_classes_have_no_empty_firmware_prefixes() -> None:
    # A class that declares a firmware prefix must actually list some.
    for cls in profile.classes():
        assert all(prefix.endswith("/") for prefix in cls.firmware_prefixes), (
            "firmware prefixes are path prefixes and must end with '/'"
        )


def test_all_symbols_deduped_and_ordered_by_class() -> None:
    symbols = profile.all_symbols()
    assert symbols, "the profile must enable at least one symbol"
    assert len(symbols) == len(set(symbols)), "all_symbols() must deduplicate"
    # First symbol is the display root, which is the first class.
    assert symbols[0] == "CONFIG_DRM"


def test_firmware_prefixes_deduped_and_ordered() -> None:
    prefixes = profile.firmware_prefixes()
    assert prefixes, "the Desktop profile must curate at least one firmware prefix"
    assert len(prefixes) == len(set(prefixes)), "firmware_prefixes() must deduplicate"
    # display comes before wifi, so its prefixes precede wifi's.
    assert prefixes[0] == "i915/"
    assert "intel/iwlwifi/" in prefixes


def test_symbols_by_class_matches_classes() -> None:
    by_class = profile.symbols_by_class()
    assert set(by_class) == set(profile.CLASS_ORDER)
    # No cross-class dedup here (each class lists its own symbols); the
    # class-level union must equal the deduped all_symbols().
    unioned: set[str] = set()
    for syms in by_class.values():
        unioned |= set(syms)
    assert set(profile.all_symbols()) == unioned


def test_class_for_roundtrip() -> None:
    for cls in profile.classes():
        assert profile.class_for(cls.name) is cls
    assert profile.class_for("does-not-exist") is None


def test_required_for_coverage_is_display_input_wifi() -> None:
    # Matches lint's class-coverage rule: at least one display + input + wifi.
    assert profile.required_for_coverage() == ("display", "input", "wifi")


# --------------------------------------------------------------------------- #
# Grounding against the vendored reference data
# --------------------------------------------------------------------------- #
def test_declared_symbols_exist_in_2026_08_list() -> None:
    symbols = data.load_symbols()
    assert symbols, "the vendored symbol list must be non-empty"
    # The model's own self-check names the first missing symbol.
    profile.assert_symbols_present(symbols)


def test_each_class_symbol_is_real() -> None:
    symbols = data.load_symbols()
    for cls in profile.classes():
        for symbol in cls.symbols:
            assert symbol in symbols, f"{cls.name}: {symbol} not in 2026.08 list"


def test_firmware_prefixes_match_upstream_names() -> None:
    names = data.load_firmware_names()
    assert names, "the upstream firmware oracle must be non-empty"
    for prefix in profile.firmware_prefixes():
        matched = [n for n in names if n.startswith(prefix)]
        assert matched, f"firmware prefix {prefix} matches no upstream name"


def test_specific_firmware_prefixes_present() -> None:
    names = data.load_firmware_names()
    # The spec's curated set: i915/amdgpu/radeon/iwlwifi + Realtek wifi/bt.
    for prefix in ("i915/", "amdgpu/", "radeon/", "intel/iwlwifi/", "rtlwifi/", "rtl_bt/"):
        assert any(n.startswith(prefix) for n in names), (
            f"expected upstream firmware under {prefix}"
        )


# --------------------------------------------------------------------------- #
# Generic-only guardrail: no vendor-acceleration / proprietary symbols declared
# --------------------------------------------------------------------------- #
def test_profile_declares_no_vendor_acceleration_symbols() -> None:
    symbols = profile.all_symbols()
    # NVIDIA is the canonical proprietary stack the spec forbids.
    assert not any("NVIDIA" in s for s in symbols), "no NVIDIA symbols in the profile"
    # The generic KMS drivers must not pull in an explicit "accel" toggle.
    assert "CONFIG_DRM_ACCEL" not in symbols
    # And no proprietary vendor GPU driver is enabled directly.
    assert not any(s in symbols for s in ("CONFIG_DRM_NOUVEAU",))


def test_profile_uses_open_kms_drivers_only() -> None:
    display = profile.class_for("display")
    assert display is not None
    kms = {s for s in display.symbols if "DRM" in s}
    # The actual GPU KMS drivers are the open mainline ones.
    for driver in ("CONFIG_DRM_I915", "CONFIG_DRM_AMDGPU", "CONFIG_DRM_RADEON"):
        assert driver in kms, f"expected open KMS driver {driver}"
