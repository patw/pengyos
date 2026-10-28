"""The cosmic-files row in cosmic-components.tsv must build the app and the
applet as TWO cargo invocations, app first.

WHY
---
`cosmic-files` (the file manager app) and `cosmic-files-applet` (the desktop)
are one cargo workspace, and the applet depends on the app crate with the
`desktop-applet` feature:

    [dependencies.cosmic-files]          # cosmic-files-applet/Cargo.toml
    path = ".."
    features = ["desktop", "gvfs", "wayland", "desktop-applet"]

Building them in a single `cargo build --release --workspace` (what the TSV used
to say) lets cargo feature-unify `desktop-applet` onto the *app* binary. The app
then also runs the applet's output-event handler and creates a full-screen
`Layer::Bottom`, all-edges-anchored layer surface showing `~/Desktop` — the file
manager draws a second, interactive copy of the file view on the desktop
wallpaper. Reproduced 2026-10-06 in QEMU on PengyOS Desktop 26.10.12; the
marker string in the compiled binary is "already had surface ID".

Building `-p cosmic-files` alone keeps the applet out of the graph, so the app
binary never sees the feature; `-p cosmic-files-applet` is a second invocation.
The cosmic package (buildroot-external/package/cosmic) also fails the build if
the app binary carries the marker — this test catches the regression earlier, without cargo.
"""
from __future__ import annotations

import pathlib

TSV = pathlib.Path(__file__).resolve().parents[1] / "cosmic-components.tsv"


def _rows() -> dict[str, str]:
    """component name -> cargo-arguments column (skipping comments/blanks)."""
    rows: dict[str, str] = {}
    for line in TSV.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        rows[parts[0]] = parts[3]
    return rows


def test_cosmic_files_builds_app_then_applet_separately():
    args = _rows()["cosmic-files"]
    cmds = [c.strip() for c in args.split("&&")]
    assert len(cmds) == 2, (
        "cosmic-files must be two cargo invocations (app, then applet); "
        f"got: {args!r}"
    )
    app, applet = cmds
    # The app invocation: only the app package, never the workspace.
    assert "--workspace" not in app, app
    assert "-p cosmic-files" in app, app
    assert "-p cosmic-files-applet" not in app, app
    # The applet invocation: the applet package, and no workspace build either.
    assert "-p cosmic-files-applet" in applet, applet
    assert "--workspace" not in applet, applet


def test_no_component_builds_the_cosmic_files_workspace():
    # Guard against the leak reappearing on any row that pulls in cosmic-files.
    for name, args in _rows().items():
        if name == "cosmic-files" or "cosmic-files" in args:
            assert "--workspace" not in args, f"{name}: {args!r}"
