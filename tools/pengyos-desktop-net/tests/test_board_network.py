"""Tests that the Desktop board wires NetworkManager as the network service.

The ``pengyos-desktop-net`` emit lane renders the network defaults as a
build-consumed overlay (NetworkManager config, wpa_supplicant, the COSMIC panel
default pinning ``cosmic-applet-network`` …). That overlay only gets onto a
running board if the Buildroot-external Desktop board **selects** the packages
that provide the network service and the network applet.

This module checks the Desktop's network wiring:

* ``NetworkManager`` is **not** a hallucinated ``BR2_PACKAGE_NETWORKMANAGER``
  (no-underscore) symbol — the correct stock 2026.08 symbol is
  ``BR2_PACKAGE_NETWORK_MANAGER`` (with underscore, ``package/network-manager/``).
  The Desktop defconfig must select the *real* symbol, or Buildroot silently
  drops it and the board has no network service.
* ``cosmic-applet-network`` ships from the cosmic-build release (dropped in by
  ``post-build.sh``), so there is no Buildroot recipe for it — the board wires
  it via the emitted panel default, not a ``BR2_`` symbol.

The suite is standard-library-only at test time, so it needs no network, root,
display, or hardware — it only reads committed files. The opt-in integration
run (``PENGYOS_IT=1``) resolves the Desktop defconfig against a real Buildroot
2026.08 checkout and asserts ``BR2_PACKAGE_NETWORK_MANAGER=y`` actually resolves.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = parent of tools/ (the Buildroot-external board + configs live here).
ROOT = Path(__file__).resolve().parents[3]
DESKTOP_DEFCONFIG = (
    ROOT / "buildroot-external" / "configs" / "pengyos_desktop_defconfig"
)

# The *correct* stock 2026.08 symbol for NetworkManager (with underscore).
NM_SYMBOL = "BR2_PACKAGE_NETWORK_MANAGER"
# The hallucinated no-underscore name the task warned about: Buildroot has no
# such symbol, so selecting it would be a silent no-op.
HALLUCINATED_NM_SYMBOL = "BR2_PACKAGE_NETWORKMANAGER"
NM_CLI_SYMBOL = "BR2_PACKAGE_NETWORK_MANAGER_CLI"
WPA_SUPPLICANT_SYMBOL = "BR2_PACKAGE_WPA_SUPPLICANT"
# The Wi-Fi driver interface NetworkManager's wpa_supplicant plugin drives. NM
# selects it automatically once WPA_SUPPLICANT is on, but the board only comes
# online over Wi-Fi if it actually resolves — so rung (b) asserts it too.
WPA_SUPPLICANT_NL80211_SYMBOL = "BR2_PACKAGE_WPA_SUPPLICANT_NL80211"

# A ``BR2_X=y`` assignment line (Buildroot defconfigs use the bare name, no
# ``CONFIG_`` prefix). The capture group returns just the symbol *name* (without
# the ``=y``), so ``findall`` yields clean symbol identifiers.
ASSIGN_RE = re.compile(r"^(?P<name>BR2_[A-Z0-9_]+)=\s*y\s*$", re.MULTILINE)


def _desktop_defconfig_text() -> str:
    assert DESKTOP_DEFCONFIG.is_file(), (
        f"Desktop defconfig missing at {DESKTOP_DEFCONFIG}"
    )
    return DESKTOP_DEFCONFIG.read_text("utf-8")


def _assigned_symbols(text: str) -> set[str]:
    """The set of ``BR2_X=y`` symbols the defconfig assigns."""
    return set(ASSIGN_RE.findall(text))


# --- hermetic: the Desktop defconfig wires the real NetworkManager symbol ----

def test_desktop_defconfig_exists():
    """The Desktop defconfig is present to wire against."""
    assert DESKTOP_DEFCONFIG.is_file()


def test_desktop_defconfig_selects_networkmanager():
    """The Desktop defconfig selects the real ``BR2_PACKAGE_NETWORK_MANAGER``.

    This is the core of the item: without this the board has no network service
    (the stock panel + cosmic-settings talk to NM over D-Bus and nothing answers).
    """
    symbols = _assigned_symbols(_desktop_defconfig_text())
    assert NM_SYMBOL in symbols, (
        "the Desktop defconfig must select "
        f"{NM_SYMBOL}=y (NetworkManager is the network service)"
    )


def test_desktop_defconfig_selects_nmcli():
    """The Desktop defconfig enables ``nmcli`` (the CLI path to the same service)."""
    symbols = _assigned_symbols(_desktop_defconfig_text())
    assert NM_CLI_SYMBOL in symbols, (
        "the Desktop defconfig must select "
        f"{NM_CLI_SYMBOL}=y (nmcli is the CLI path to NetworkManager)"
    )


def test_desktop_defconfig_keeps_wpa_supplicant_backend():
    """The Wi-Fi backend (wpa_supplicant) is still selected — NM drives it."""
    symbols = _assigned_symbols(_desktop_defconfig_text())
    assert WPA_SUPPLICANT_SYMBOL in symbols, (
        "the Desktop defconfig must keep "
        f"{WPA_SUPPLICANT_SYMBOL}=y (the Wi-Fi backend NetworkManager drives)"
    )


def test_desktop_defconfig_uses_real_symbol_not_hallucination():
    """The defconfig uses the underscored symbol, not the hallucinated one.

    ``BR2_PACKAGE_NETWORKMANAGER`` (no underscore) does not exist in Buildroot
    2026.08 — selecting it is a silent no-op. This guards the exact mistake the
    item warns about.
    """
    text = _desktop_defconfig_text()
    assert HALLUCINATED_NM_SYMBOL not in text, (
        f"the Desktop defconfig must not mention the non-existent "
        f"{HALLUCINATED_NM_SYMBOL} (select the real {NM_SYMBOL} instead)"
    )


def test_desktop_defconfig_no_stale_not_a_package_note():
    """The stale "NetworkManager is NOT a Buildroot package" NOTE is gone.

    That note documented the (now-refuted) premise that NM had no recipe. Its
    presence means the wiring was never done.
    """
    text = _desktop_defconfig_text()
    assert "NetworkManager itself is NOT a Buildroot 2026.08 package" not in text, (
        "the stale 'NetworkManager is NOT a Buildroot package' note should be "
        "replaced by the real NM enablement"
    )


def test_desktop_defconfig_symbols_are_wellformed_br2_names():
    """Every assigned symbol is a well-formed ``BR2_`` kconfig name.

    A sanity guard on the defconfig's own syntax (independent of the vendored
    list, which this stdlib-only suite does not vendor).
    """
    text = _desktop_defconfig_text()
    for symbol in _assigned_symbols(text):
        assert symbol.startswith("BR2_"), f"{symbol!r} is not a BR2_ symbol"
        assert re.fullmatch(r"BR2_[A-Z][A-Z0-9_]*", symbol), (
            f"{symbol!r} is not a valid kconfig symbol name"
        )


def test_desktop_defconfig_nm_symbol_is_valid_br2_name_form():
    """The NetworkManager symbol follows Buildroot's ``BR2_PACKAGE_<NAME>`` form."""
    assert re.fullmatch(r"BR2_PACKAGE_[A-Z][A-Z0-9_]*", NM_SYMBOL) is not None


# --- hermetic: cosmic-applet-network ships from cosmic-build, NOT a recipe ------

COSMIC_APPLET_NETWORK = "cosmic-applet-network"
# A Buildroot recipe would select a ``BR2_PACKAGE_COSMIC_APPLET_NETWORK`` symbol.
# The decision is that it does NOT: the applet
# ships from the cosmic-build release, dropped in by post-build.sh, not a recipe.
COSMIC_APPLET_BR2_SYMBOL = "BR2_PACKAGE_COSMIC_APPLET_NETWORK"
# The post-build.sh that drops the cosmic-applets bundle (which provides the
# network applet) into the Desktop image.
DESKTOP_POST_BUILD = (
    ROOT / "buildroot-external" / "board" / "desktop" / "post-build.sh"
)


def test_cosmic_applet_network_has_no_buildroot_symbol():
    """cosmic-applet-network is NOT selected as a BR2_ symbol (no Buildroot recipe).

    The network applet ships from
    the cosmic-build release, not a Buildroot recipe: it is a Rust/Vala applet
    with heavy COSMIC deps (cosmic-config, cosmic-settings-shared, …) that are
    NOT stock 2026.08 packages, so a from-source recipe is out of scope for this
    lane. Selecting a non-existent ``BR2_PACKAGE_COSMIC_APPLET_NETWORK`` symbol
    would be a silent no-op. This test locks that decision in: the Desktop
    defconfig must not mention such a symbol.
    """
    text = _desktop_defconfig_text()
    assert COSMIC_APPLET_BR2_SYMBOL not in text, (
        f"the Desktop defconfig must not select {COSMIC_APPLET_BR2_SYMBOL} "
        f"(cosmic-applet-network ships from cosmic-build, not a Buildroot recipe)"
    )


def test_cosmic_applet_network_ships_via_post_build():
    """The board drops cosmic-applets (which provides the network applet) via post-build.sh.

    The GUI path to Wi-Fi is cosmic-applet-network, which lives in the
    cosmic-applets bundle from the host cosmic-build release. The Desktop
    board's post-build.sh copies that bundle into the image — so the applet
    ships from cosmic-build, NOT a Buildroot recipe. This test locks that in:
    post-build.sh must drop the cosmic-applets bundle.
    """
    assert DESKTOP_POST_BUILD.is_file(), (
        f"Desktop post-build.sh missing at {DESKTOP_POST_BUILD}"
    )
    content = DESKTOP_POST_BUILD.read_text("utf-8")
    assert "cosmic-applets" in content, (
        "the Desktop post-build.sh must drop the cosmic-applets bundle "
        "(which provides cosmic-applet-network) from the cosmic-build release"
    )


def test_cosmic_applet_network_wired_via_emitted_panel_default():
    """The emitted panel default pins cosmic-applet-network (the GUI path is wired).

    Even though the applet ships from cosmic-build (not a recipe), the emitted
    panel default must list it so the stock COSMIC panel — which omits the
    network applet — gains a GUI path to Wi-Fi on every session. This is the
    emitted-overlay half of the "decide" decision (the post-build.sh half is
    test_cosmic_applet_network_ships_via_post_build).
    """
    from pengyos_desktop_net import emit as emit_mod

    panel = next(
        (a for a in emit_mod.artifacts_for() if a.kind == "cosmic-panel"),
        None,
    )
    assert panel is not None, "no cosmic-panel artifact was emitted"
    assert f'applet="{COSMIC_APPLET_NETWORK}"' in panel.content, (
        f"the emitted panel default must pin {COSMIC_APPLET_NETWORK}"
    )


# --- hermetic-but-external: rung (b) kconfig resolution against a real checkout

def _buildroot_checkout() -> Path | None:
    """Return the Buildroot checkout to resolve against, or None.

    Rung (b) of the verification ladder needs a real Buildroot source tree
    (for its kconfig `Config.in` files) plus `make` and a C compiler (the
    kconfig `conf` tool is built from `support/kconfig`). When all three are
    present we resolve the Desktop defconfig and assert the network symbols
    actually land in `.config` — the real proof the wiring is not a
    hallucination. When any is absent we SKIP (never fail): the hermetic part
    of the suite (the static defconfig reads above) still runs, and the
    opt-in integration run (`PENGYOS_IT=1`) covers the same resolution against
    a pinned checkout.
    """
    env = os.environ.get("PENGYOS_BUILDROOT")
    root = Path(env) if env else _default_buildroot()
    if not (root / "Makefile").is_file():
        return None
    if shutil.which("make") is None:
        return None
    if shutil.which("cc") is None:
        return None
    return root


def _kconfig_skip_reason(root: Path) -> str | None:
    """Return a skip reason, or None when rung (b) may run."""
    if os.environ.get("PENGYOS_IT") == "1":
        # The opt-in integration run owns the pinned checkout; rung (b) here
        # resolves the *existing* verification tree instead. Keep them distinct.
        return (
            "opt-in integration run (PENGYOS_IT=1) owns kconfig resolution; "
            "skip the verification-tree rung (b)"
        )
    return None


def _resolve_defconfig(root: Path, odir: Path) -> str:
    """Run ``make … pengyos_desktop_defconfig`` and return the .config text."""
    cmd = [
        "make",
        "-C",
        str(root),
        f"BR2_EXTERNAL={ROOT / 'buildroot-external'}",
        f"O={odir}",
        "pengyos_desktop_defconfig",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(odir.parent),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        f"make defconfig failed (rc={proc.returncode})\n"
        f"cmd: {' '.join(cmd)}\n--- stdout ---\n{proc.stdout[-4000:]}\n"
        f"--- stderr ---\n{proc.stderr[-4000:]}"
    )
    config = odir / ".config"
    assert config.is_file(), "make defconfig did not produce a .config"
    return config.read_text("utf-8")


def test_desktop_defconfig_network_wiring_resolves_kconfig():
    """The network symbols actually resolve in a real Buildroot kconfig (rung b).

    The static reads above prove the Desktop defconfig *mentions* the real
    ``BR2_PACKAGE_NETWORK_MANAGER`` symbol; this proves Buildroot *keeps* it
    (it is not silently dropped by an unmet dependency — a silent no-op that
    would leave the board with no network service). This is the cheapest rung
    of the verification ladder that answers "is the network wiring correct",
    and it is hermetic at test time (no network/root/display/hardware — just a
    local kconfig resolve against a pinned checkout). Skipped when no checkout
    / make / cc is available.
    """
    root = _buildroot_checkout()
    if root is None:
        pytest.skip(
            "no Buildroot checkout + make + cc available for rung (b) kconfig "
            "resolution (set PENGYOS_BUILDROOT to point at one)"
        )
    reason = _kconfig_skip_reason(root)
    if reason is not None:
        pytest.skip(reason)
    odir = (Path.home() / "tmp") / f"br-kcfg-{os.getpid()}"
    if odir.exists():
        shutil.rmtree(odir)
    odir.mkdir(parents=True, exist_ok=True)
    try:
        resolved = _resolve_defconfig(root, odir)
        # Buildroot writes resolved assignments as ``BR2_X=y`` (no CONFIG_ prefix).
        assert re.search(rf"^{re.escape(NM_SYMBOL)}=\s*y\s*$", resolved, re.MULTILINE), (
            f"{NM_SYMBOL} did not resolve to =y in the resolved .config\n"
            f"--- NETWORK_MANAGER lines ---\n"
            + "\n".join(
                line for line in resolved.splitlines() if "NETWORK_MANAGER" in line
            )
        )
        # nmcli (the CLI path to the same service) resolves alongside NM.
        assert re.search(
            rf"^{re.escape(NM_CLI_SYMBOL)}=\s*y\s*$", resolved, re.MULTILINE
        ), f"{NM_CLI_SYMBOL} did not resolve to =y in the resolved .config"
        # The Wi-Fi backend NM drives resolves too.
        assert re.search(
            rf"^{re.escape(WPA_SUPPLICANT_SYMBOL)}=\s*y\s*$", resolved, re.MULTILINE
        ), f"{WPA_SUPPLICANT_SYMBOL} did not resolve to =y in the resolved .config"
        # The Wi-Fi driver interface NM's wpa_supplicant plugin drives resolves.
        # The board only comes online over Wi-Fi if this lands (auto-selected by
        # WPA_SUPPLICANT, but a silent no-op would leave no Wi-Fi driver).
        assert re.search(
            rf"^{re.escape(WPA_SUPPLICANT_NL80211_SYMBOL)}=\s*y\s*$",
            resolved,
            re.MULTILINE,
        ), (
            f"{WPA_SUPPLICANT_NL80211_SYMBOL} did not resolve to =y in the "
            f"resolved .config"
        )
        # The hallucinated no-underscore name is nowhere in the resolved config.
        assert HALLUCINATED_NM_SYMBOL not in resolved
    finally:
        shutil.rmtree(odir, ignore_errors=True)


# --- opt-in integration: NM actually resolves in a real Buildroot 2026.08 ----

def _it_skip_reason() -> str | None:
    """Return a skip reason, or None when the integration run may proceed."""
    if os.environ.get("PENGYOS_IT") != "1":
        return (
            "integration test: set PENGYOS_IT=1 to run "
            "(needs a local Buildroot 2026.08 checkout + make + a C compiler)"
        )
    root = Path(os.environ.get("PENGYOS_BUILDROOT", str(_default_buildroot())))
    if not (root / "Makefile").is_file():
        return (
            f"no Buildroot checkout at {root} "
            "(set PENGYOS_BUILDROOT to a Buildroot 2026.08 source tree)"
        )
    if shutil.which("make") is None:
        return "make not found on PATH"
    if shutil.which("cc") is None:
        return "no host C compiler on PATH (the kconfig stage builds support/kconfig)"
    return None


def _default_buildroot() -> Path:
    return Path.home() / "build" / "pengyos-2608" / "buildroot"


def _resolve_desktop_defconfig(odir: Path) -> Path:
    """Run ``make … pengyos_desktop_defconfig`` and return the .config."""
    cmd = [
        "make",
        "-C",
        str(_default_buildroot()),
        f"BR2_EXTERNAL={ROOT / 'buildroot-external'}",
        f"O={odir}",
        "pengyos_desktop_defconfig",
    ]
    proc = subprocess.run(
        cmd,
        cwd=odir.parent,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        f"make defconfig failed (rc={proc.returncode})\n"
        f"cmd: {' '.join(cmd)}\n--- stdout ---\n{proc.stdout[-4000:]}\n"
        f"--- stderr ---\n{proc.stderr[-4000:]}"
    )
    config = odir / ".config"
    assert config.is_file(), "make defconfig did not produce a .config"
    return config


def test_desktop_defconfig_networkmanager_resolves():
    """``BR2_PACKAGE_NETWORK_MANAGER=y`` actually resolves in Buildroot 2026.08.

    The static check (above) proves the defconfig *mentions* the symbol; this
    proves Buildroot *keeps* it (it is not silently dropped by an unmet
    dependency). Ladder rung (b): kconfig resolution against the real checkout.
    """
    skip = _it_skip_reason()
    if skip is not None:
        pytest.skip(skip)
    odir = (Path.home() / "tmp") / f"br-it-{os.getpid()}"
    if odir.exists():
        shutil.rmtree(odir)
    odir.mkdir(parents=True, exist_ok=True)
    try:
        config = _resolve_desktop_defconfig(odir)
        resolved = config.read_text("utf-8")
        # Buildroot writes resolved assignments as ``BR2_X=y`` (no CONFIG_ prefix).
        assert re.search(rf"^{re.escape(NM_SYMBOL)}=\s*y\s*$", resolved, re.MULTILINE), (
            f"{NM_SYMBOL} did not resolve to =y in the resolved .config\n"
            f"--- NETWORK_MANAGER lines ---\n"
            + "\n".join(
                line
                for line in resolved.splitlines()
                if "NETWORK_MANAGER" in line
            )
        )
        # And the hallucinated no-underscore name is nowhere in the resolved config.
        assert HALLUCINATED_NM_SYMBOL not in resolved
    finally:
        shutil.rmtree(odir, ignore_errors=True)


def test_desktop_defconfig_wpa_supplicant_resolves():
    """``BR2_PACKAGE_WPA_SUPPLICANT=y`` resolves alongside NM (the Wi-Fi backend)."""
    skip = _it_skip_reason()
    if skip is not None:
        pytest.skip(skip)
    odir = (Path.home() / "tmp") / f"br-it-wpa-{os.getpid()}"
    if odir.exists():
        shutil.rmtree(odir)
    odir.mkdir(parents=True, exist_ok=True)
    try:
        config = _resolve_desktop_defconfig(odir)
        resolved = config.read_text("utf-8")
        assert re.search(
            rf"^{re.escape(WPA_SUPPLICANT_SYMBOL)}=\s*y\s*$",
            resolved,
            re.MULTILINE,
        ), f"{WPA_SUPPLICANT_SYMBOL} did not resolve to =y in the resolved .config"
    finally:
        shutil.rmtree(odir, ignore_errors=True)
