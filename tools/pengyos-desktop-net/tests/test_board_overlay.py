"""Tests that the emitted overlay is wired into the PengyOS Desktop board.

The pengyos-desktop-net deliverable has two halves that must agree:

* the Python ``emit`` lane (``tools/pengyos-desktop-net``) renders the network
  defaults as a golden, deterministic overlay; and
* the Buildroot-external Desktop board
  (``buildroot-external/board/desktop/rootfs-overlay``) ships those
  exact files so the build actually consumes them.

This item ("wire the emitted overlay into the board (defconfig/files)") closes
that gap. These tests assert the two halves are byte-identical (modulo the
on-target path shift — emitted ``/etc/...`` maps to overlay ``etc/...`` and the
emitted ``~`` user-home marker maps to the overlay ``/etc/skel`` skel default),
and that the board overlay is lint-clean: NetworkManager owns networking, the
Wi-Fi stack + firmware are referenced, the panel default lists
``cosmic-applet-network``, and no ``systemd-networkd`` claims a link.

The suite is standard-library-only at test time, so it needs no network, root,
display, or hardware — it only reads committed files.
"""

from __future__ import annotations

import os
from pathlib import Path

from pengyos_desktop_net import emit as em
from pengyos_desktop_net import lint as ln
from pengyos_desktop_net import profile as prof

# Repo root = parent of tools/ (the Buildroot-external board lives here).
ROOT = Path(__file__).resolve().parents[3]
BOARD_OVERLAY = ROOT / "buildroot-external" / "board" / "desktop" / "rootfs-overlay"


def _board_rel(emitted_path: str) -> str:
    """Map an emitted on-target path to its location in the board overlay.

    Emitted ``/etc/...`` lives at overlay ``etc/...`` (the overlay root is the
    target root). The emitted ``~`` user-home marker is shipped as a skel
    default at ``/etc/skel`` so every fresh user gets the COSMIC defaults.
    """
    if emitted_path.startswith("~"):
        return "etc/skel/" + emitted_path[1:].lstrip("/")
    return emitted_path.lstrip("/")


def _board_overlay_exists() -> bool:
    return BOARD_OVERLAY.is_dir()


def _board_files() -> dict[str, str]:
    """Return ``{overlay-relative-path: content}`` for the board overlay.

    Symlinks are skipped: ``emit`` renders regular files only, so symlinks in
    the overlay (systemd unit-enablement links, some absolute and therefore
    dangling on the host, e.g. ``seatd.service -> /usr/lib/systemd/system/...``)
    are not part of the artifact set these tests compare and reading them would
    raise ``FileNotFoundError`` on a broken target.
    """
    files: dict[str, str] = {}
    for dirpath, _dirnames, filenames in os.walk(BOARD_OVERLAY):
        for name in filenames:
            full = Path(dirpath) / name
            if full.is_symlink():
                continue
            rel = full.relative_to(BOARD_OVERLAY).as_posix()
            try:
                files[rel] = full.read_text("utf-8")
            except (UnicodeDecodeError, OSError):
                # Binary overlay assets (e.g. the default wallpaper PNG) are not
                # emitted artifacts and are not compared as text.
                continue
    return files


def test_board_overlay_present():
    """The Desktop board overlay exists (the wiring target)."""
    assert _board_overlay_exists(), (
        f"board overlay missing at {BOARD_OVERLAY} — wire the emitted overlay in"
    )


def test_board_files_match_emitted_content():
    """Every emitted artifact's content is shipped verbatim in the board."""
    assert _board_overlay_exists()
    emitted = em.artifacts_for()
    board = _board_files()
    for art in emitted:
        rel = _board_rel(art.path)
        assert rel in board, (
            f"emitted {art.name!r} (overlay/{rel}) is not wired into the board overlay"
        )
        assert board[rel] == art.content, (
            f"board file overlay/{rel} drifted from the emitted overlay"
        )


def test_board_files_match_emit_output(tmp_path: Path):
    """The emit lane and the board overlay are byte-identical end to end.

    ``emit`` into a scratch dir and diff against the committed board overlay:
    the two halves of the deliverable must agree exactly.
    """
    assert _board_overlay_exists()
    out = tmp_path / "emitted"
    em.emit(str(out))
    board = _board_files()
    for art in em.artifacts_for():
        # emit writes to <out>/<on-target-path-without-leading-slash> (keeps '~')
        emitted_on_disk = (out / art.path.lstrip("/")).read_text("utf-8")
        # the board ships the same bytes under the on-target skel path
        assert board[_board_rel(art.path)] == emitted_on_disk, (
            f"board overlay/{_board_rel(art.path)} != emit output for {art.name!r}"
        )


def test_board_overlay_covers_all_kinds():
    """The board overlay ships every emitted artifact (all five kinds)."""
    assert _board_overlay_exists()
    board = _board_files()
    # The canonical on-target board path for each emitted artifact (one per kind).
    expected_board_paths = {
        "etc/NetworkManager/NetworkManager.conf",
        "etc/NetworkManager/conf.d/10-pengyos.conf",
        "etc/wpa_supplicant.conf",
        "wifi-firmware.txt",
        "etc/skel/.config/cosmic/panel.toml",
        "etc/skel/.config/cosmic-settings/network.toml",
    }
    missing = expected_board_paths - set(board)
    assert not missing, f"board overlay missing {sorted(missing)}"


def test_board_overlay_passes_lint():
    """The wired board overlay passes every lint invariant."""
    assert _board_overlay_exists()
    result = ln.lint_overlay(str(BOARD_OVERLAY))
    assert result.ok, result.messages


def test_board_overlay_has_no_networkd_ownership():
    """systemd-networkd does not claim a link in the board overlay."""
    assert _board_overlay_exists()
    board = _board_files()
    network_files = [rel for rel in board if rel.lower().endswith(".network")]
    assert not network_files, (
        f"systemd-networkd owns a link via {network_files}; "
        "NetworkManager must be the sole network service"
    )


def test_board_panel_pins_network_applet():
    """The board's panel default pins cosmic-applet-network (the GUI path)."""
    assert _board_overlay_exists()
    board = _board_files()
    panel = next((c for rel, c in board.items() if rel.endswith("panel.toml")), None)
    assert panel is not None, "board overlay has no panel.toml"
    assert f'applet="{prof.COSMIC_APPLET_NETWORK}"' in panel


def test_board_wifi_stack_and_firmware_referenced():
    """The board overlay references the Wi-Fi backend and its firmware."""
    assert _board_overlay_exists()
    board = _board_files()
    assert any(rel.endswith("wpa_supplicant.conf") for rel in board), (
        "board overlay has no wpa_supplicant config"
    )
    assert any("wifi-firmware" in rel for rel in board), (
        "board overlay has no Wi-Fi firmware manifest"
    )
