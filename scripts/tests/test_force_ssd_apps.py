"""Tests for the Desktop's per-app force-server-side-decoration patch.

Firefox (GTK3, client-side decorations) asks the compositor for ``ClientSide`` and COSMIC
grants it — the client's request wins, and Firefox's own frame draws a close button only,
so its users get no minimize and no maximize while Discord and cosmic-files on the same
session show all three. ``decoration_preference`` is one global preference, so the only way
to say "server-side, for THIS app" is a compositor-side allowlist.

That is ``pengyos-force-ssd-apps``: a ``force_ssd_app_ids: Vec<String>`` in
``CosmicCompConfig``, honoured at every decoration negotiation point, plus the Super+D
Minimize binding COSMIC does not ship. Three things are checked here because each one has
already been the failure:

1. the patch must be in the lane directory ``build-cosmic.sh`` reads, and must carry the
   config field, the binding, and the allowlist check at *every* negotiation point (a
   handler that honours it in ``new_decoration`` only loses the moment the app re-requests);
2. the KDE handler must be **silent** once it has asserted ``Server``. GTK3 answers every
   ``Server`` configure with a fresh ``request_mode(Client)``, so echoing the client's mode
   back runs hot — measured on behoweather 2026-10-09: Firefox 84% CPU, cosmic-comp 20%,
   ``WAYLAND_DEBUG`` ~100 MB/min;
3. the shipped allowlist must be installed by ``post-build.sh`` at the path cosmic-config
   reads system defaults from, and must parse as a list of app-ids.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BOARD = REPO / "buildroot-external/board/desktop"
PATCH_DIR = BOARD / "cosmic-patches/cosmic-comp"
PATCHES = sorted(PATCH_DIR.glob("*.patch"))
POST_BUILD = BOARD / "post-build.sh"
ALLOWLIST = BOARD / "cosmic-settings-defaults/force_ssd_app_ids"
TARGET = "usr/share/cosmic/com.system76.CosmicComp/v1/force_ssd_app_ids"


def added(patch: Path) -> str:
    """Only the lines the patch adds, so a check cannot pass on context lines."""
    return "\n".join(l[1:] for l in patch.read_text().splitlines()
                     if l.startswith("+") and not l.startswith("+++"))


def test_the_patch_is_in_the_lane_build_cosmic_reads():
    """build-cosmic.sh applies ${PATCHES}/<component>/*.patch. A patch filed anywhere
    else is a patch that never ships, and nothing in the build says so."""
    assert PATCHES, f"no patch in {PATCH_DIR}"
    assert len(PATCHES) == 1, [p.name for p in PATCHES]


def test_the_patch_declares_the_config_field_and_the_minimize_binding():
    text = added(PATCHES[0])
    assert "pub force_ssd_app_ids: Vec<String>," in text
    assert "force_ssd_app_ids: Vec::new()," in text, "Default must seed the field"
    assert '(modifiers: [Super], key: "d"): Minimize' in text, \
        "COSMIC ships no minimize binding; the forced-SSD apps need one"


def test_the_allowlist_is_honoured_at_every_negotiation_point():
    """The allowlist is authoritative only if every path that picks a mode consults it.
    cosmic-comp decides a decoration mode in seven places — update_decorations, the xdg
    handler's new_decoration / request_mode / unset_mode, and the KDE server-decoration
    handler's new_decoration (both branches) and request_mode — so the patch must carry
    seven call sites, not a representative one. A handler that honours the allowlist at
    window creation only loses the moment the app re-requests."""
    text = added(PATCHES[0])
    assert "fn force_ssd_surface" in text, "the helper is missing"
    calls = text.count("force_ssd_surface(")
    assert calls >= 7, f"{calls} call site(s); every negotiation point needs one"
    assert "state.update_decorations();" in text, \
        "a config change to the allowlist must re-negotiate live"
    touched = {l.split(" b/")[-1] for l in PATCHES[0].read_text().splitlines()
               if l.startswith("diff --git")}
    assert touched == {"cosmic-comp-config/src/lib.rs", "data/keybindings.ron",
                       "src/config/mod.rs", "src/wayland/handlers/decoration.rs"}, \
        sorted(touched)


def test_the_kde_handler_is_silent_after_asserting_server():
    """The loop guard. In KDE request_mode the forced branch must re-assert Server, say
    nothing when it is already Server, and return without echoing the client's mode."""
    text = added(PATCHES[0])
    assert "if data.mode != Some(KdeMode::Server)" in text, \
        "must stay silent when the surface is already Server"
    assert "obj.mode(KdeMode::Server)" in text
    assert re.search(r"^\s*return;\s*$", text, re.M), \
        "the forced branch must return before the normal path echoes the request"
    assert "obj.mode(mode)" not in text, \
        "echoing the client's requested mode is the hot loop (firefox 84% CPU, 2026-10-09)"


def test_the_shipped_allowlist_is_installed_by_post_build():
    """post-build.sh copies the COSMIC data it finds; the allowlist must go in with it,
    into the directory cosmic-config reads system defaults from (the user override is the
    same key under ~/.config)."""
    text = POST_BUILD.read_text()
    assert f"install -Dm0644 \"${{BOARD_DIR}}/cosmic-settings-defaults/force_ssd_app_ids\" \\\n" \
           f"        \"${{TARGET_DIR}}/{TARGET}\"" in text, \
        "post-build.sh must install the shipped allowlist"
    assert ALLOWLIST.is_file(), f"{ALLOWLIST} is missing"


def test_the_shipped_allowlist_is_a_list_of_app_ids():
    """A malformed shipped default is not a build error — cosmic-config just gets no
    allowlist and every app silently keeps its own frame."""
    raw = ALLOWLIST.read_text().strip()
    assert raw.startswith("[") and raw.endswith("]"), raw
    ids = re.findall(r'"([^"]*)"', raw)
    assert ids, f"empty allowlist: {raw!r}"
    for i in ids:
        assert i and i == i.strip(), f"blank/padded app-id {i!r}"
        assert i == i.lower(), f"app-ids are matched case-insensitively; keep them lowercase: {i!r}"


def test_the_divergence_is_declared_with_an_expiry():
    """CONTRIBUTING: every divergence gets a ledger entry BEFORE it lands, and an entry
    with no expiry condition is not an approved divergence. A host-built component is
    invisible to pkg-stats, so it must also name its own observe source."""
    ledger = tomllib.loads((REPO / "divergence.toml").read_text())
    entry = [d for d in ledger.get("divergence", [])
             if d.get("id") == "cosmic-comp-force-ssd-app-ids"]
    assert entry, "cosmic-comp-force-ssd-app-ids is not in divergence.toml"
    d = entry[0]
    assert d["component"] == "cosmic-comp"
    assert REPO / d["patches_dir"] == PATCH_DIR, d["patches_dir"]
    assert d.get("expiry"), "an entry with no expiry is not an approved divergence"
    assert d.get("observe_source"), "a host-built pin must name its own observe source"
    assert "cosmic-components.tsv" in d["observe_source"]
    assert not d.get("closes"), "this patch closes no CVE; an ignore would outlive it"


def test_negative_control_an_empty_allowlist_is_not_a_configuration():
    """The allowlist is a *list of app-ids*, not a list of comments: a file that parses
    as an empty list must fail the test above, not ship as 'configured'."""
    assert not re.findall(r'"([^"]*)"', '[]'), "the parser must not invent ids"
    assert ALLOWLIST.read_text().strip() != "[]", \
        "shipping an empty allowlist means Firefox is not fixed and the build is green"
