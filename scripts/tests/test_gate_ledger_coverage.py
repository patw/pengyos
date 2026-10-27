"""Tests for the gate's ledger-coverage check (scripts/pengyos_gate.py).

Why the check exists: `check_patch_declarations` reads the ledger and asks whether the
tree agrees, so a patch set could be landed with no entry at all and nothing said so.
Found live on 2026-10-18: five patch sets under `buildroot-external/patches`, two entries
in `divergence.toml`. ffmpeg (12 patches, 12 CVEs), gst1-plugins-good (6, 2) and
gst1-plugins-bad (1, 1) had been written by pengyos-nightly over three nights, so three
divergences carried no expiry condition and no observe source.

Both directions are reproduced here: a set with no entry, and an entry that is present but
not whole (no expiry, or a kind that is not one of the three the ledger allows).

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pengyos_gate as gate  # noqa: E402

ENTRY = '''[[divergence]]
id = "ffmpeg-6.1.5-cve-2026"
kind = "patch-set"
component = "ffmpeg"
carried_since = "2026-10-10"
expiry = "Buildroot pins an ffmpeg outside the NVD range; the patch set is deleted then"
observe_source = "pkg-stats"
upstream_status = "merged"
patches_dir = "buildroot-external/patches/ffmpeg/6.1.5"
closes = ["CVE-2026-70628"]
'''


def make_repo(tmp_path: Path, ledger: str, sets: dict[str, int], name: str = "repo") -> Path:
    repo = tmp_path / name
    (repo / "buildroot-external/patches").mkdir(parents=True)
    for d, n in sets.items():
        pdir = repo / d
        pdir.mkdir(parents=True, exist_ok=True)
        for i in range(1, n + 1):
            (pdir / f"{i:04d}-fix.patch").write_text("patch")
    (repo / "divergence.toml").write_text(ledger)
    return repo


def coverage(repo: Path) -> list:
    return [c for c in gate.check_ledger_covers_patch_sets(repo)
            if c.name.startswith("patch-set-ledgered")]


def schema(repo: Path) -> list:
    return [c for c in gate.check_ledger_covers_patch_sets(repo)
            if c.name.startswith("ledger-entry")]


def test_ledgered_patch_set_passes(tmp_path):
    """The positive control: 12 patches on disk, one complete entry naming them."""
    repo = make_repo(tmp_path, ENTRY, {"buildroot-external/patches/ffmpeg/6.1.5": 12})
    checks = coverage(repo)
    assert checks and all(c.ok for c in checks), [c.detail for c in checks]
    assert "12 patch(es), 1 CVE(s) in the ledger" in checks[0].detail


def test_undeclared_patch_set_fails_the_way_it_failed_live(tmp_path):
    """2026-10-18: patches landed by the nightly lane, never written into the ledger."""
    repo = make_repo(tmp_path, "", {"buildroot-external/patches/ffmpeg/6.1.5": 12})
    checks = coverage(repo)
    assert checks and not checks[0].ok
    assert "no divergence.toml entry" in checks[0].detail
    assert "buildroot-external/patches/ffmpeg/6.1.5" in checks[0].detail
    # the failure has to say what would fix it, or the next reader just deletes the check
    assert "carried_since" in checks[0].detail and "expiry" in checks[0].detail


def test_one_unledgered_set_does_not_hide_the_others(tmp_path):
    """Per-set granularity: a covered set stays green while its neighbour is red."""
    repo = make_repo(tmp_path, ENTRY, {"buildroot-external/patches/ffmpeg/6.1.5": 2,
                                      "buildroot-external/patches/upower/0.99.19": 1})
    checks = {c.name: c for c in coverage(repo)}
    assert checks["patch-set-ledgered:buildroot-external/patches/ffmpeg/6.1.5"].ok
    assert not checks["patch-set-ledgered:buildroot-external/patches/upower/0.99.19"].ok


def test_a_patch_in_a_nested_directory_is_its_own_uncovered_set(tmp_path):
    """Deliberately conservative. Buildroot applies the *.patch files directly inside
    `patches/<pkg>/<version>/`, so a patch one level deeper is not carried by that set -
    and a file sitting in the tree that no entry explains is what the check is for. It is
    reported as its own set rather than silently folded into the parent."""
    repo = make_repo(tmp_path, ENTRY, {"buildroot-external/patches/ffmpeg/6.1.5": 1})
    (repo / "buildroot-external/patches/ffmpeg/6.1.5/sub").mkdir()
    (repo / "buildroot-external/patches/ffmpeg/6.1.5/sub/0002-more.patch").write_text("p")
    checks = {c.name: c for c in coverage(repo)}
    assert checks["patch-set-ledgered:buildroot-external/patches/ffmpeg/6.1.5"].ok
    nested = checks["patch-set-ledgered:buildroot-external/patches/ffmpeg/6.1.5/sub"]
    assert not nested.ok and "no divergence.toml entry" in nested.detail


FORK = '''[[divergence]]
id = "distrobox-1.8.2.5-fork"
kind = "fork"
component = "distrobox"
carried_since = "2026-09-28"
expiry = "an upstream release carries the fix: delete the patch"
observe_source = "pkg-stats"
upstream_status = "not-offered"
recipe = "buildroot-external/package/distrobox"
'''


def test_unledgered_recipe_patch_fails_the_way_it_was_found(tmp_path):
    """2026-10-27: five authored recipes carried patches of ours and the check only
    looked under patches/. The failure names the recipe and asks for a fork entry."""
    repo = make_repo(tmp_path, ENTRY, {"buildroot-external/package/distrobox": 1})
    checks = {c.name: c for c in coverage(repo)}
    c = checks["patch-set-ledgered:buildroot-external/package/distrobox"]
    assert not c.ok and "authored recipe" in c.detail
    assert 'kind = "fork"' in c.detail and 'recipe = "buildroot-external/package/distrobox"' in c.detail


def test_recipe_patches_are_claimed_by_the_entrys_recipe(tmp_path):
    """The forks name their directory as `recipe` (ffmpeg-pengyos, hplip-pengyos) or as
    `patches_dir` (libdisplay-info-pengyos); either claims the recipe's patches."""
    repo = make_repo(tmp_path, FORK, {"buildroot-external/package/distrobox": 2})
    checks = coverage(repo)
    assert checks and all(c.ok for c in checks), [c.detail for c in checks]
    as_dir = FORK.replace("recipe = ", "patches_dir = ")
    repo = make_repo(tmp_path, as_dir, {"buildroot-external/package/distrobox": 1}, name="dir")
    assert all(c.ok for c in coverage(repo))


def test_a_recipe_is_one_set_whatever_its_patch_layout(tmp_path):
    """Buildroot also reads <pkg>/<version>/*.patch; those belong to the recipe's entry,
    not to a set of their own, and a recipe with no patches claims nothing."""
    repo = make_repo(tmp_path, FORK, {"buildroot-external/package/distrobox/1.8.2.5": 1,
                                     "buildroot-external/package/sudo-rs": 0})
    names = [c.name for c in coverage(repo)]
    assert names == ["patch-set-ledgered:buildroot-external/package/distrobox"]


def test_a_tree_with_no_patch_sets_claims_nothing(tmp_path):
    repo = make_repo(tmp_path, ENTRY, {})
    checks = coverage(repo)
    assert len(checks) == 1 and checks[0].ok and "no patch set" in checks[0].detail


def test_entry_without_an_expiry_fails(tmp_path):
    """The header's rule: an entry with no expiry condition is not an approved
    divergence. Presence in the file is not the property being checked."""
    ledger = ENTRY.replace(
        'expiry = "Buildroot pins an ffmpeg outside the NVD range; the patch set is deleted then"\n', "")
    repo = make_repo(tmp_path, ledger, {"buildroot-external/patches/ffmpeg/6.1.5": 1})
    checks = schema(repo)
    assert checks and not checks[0].ok
    assert "missing expiry" in checks[0].detail


def test_entry_missing_observe_source_fails(tmp_path):
    """A fork or an authored pin is invisible to pkg-stats and must name its own observe
    source, or it rots silently (the PengyR case)."""
    ledger = ENTRY.replace('observe_source = "pkg-stats"\n', "")
    repo = make_repo(tmp_path, ledger, {"buildroot-external/patches/ffmpeg/6.1.5": 1})
    checks = schema(repo)
    assert checks and not checks[0].ok and "missing observe_source" in checks[0].detail


def test_unknown_kind_fails(tmp_path):
    ledger = ENTRY.replace('kind = "patch-set"', 'kind = "vibes"')
    repo = make_repo(tmp_path, ledger, {"buildroot-external/patches/ffmpeg/6.1.5": 1})
    checks = schema(repo)
    assert checks and not checks[0].ok and "not one of" in checks[0].detail


def test_complete_entry_passes_the_schema_check(tmp_path):
    repo = make_repo(tmp_path, ENTRY, {"buildroot-external/patches/ffmpeg/6.1.5": 1})
    checks = schema(repo)
    assert checks and all(c.ok for c in checks), [c.detail for c in checks]
    assert "patch-set, complete" in checks[0].detail


def test_unreadable_ledger_fails_instead_of_passing(tmp_path):
    repo = make_repo(tmp_path, "this is not toml = = =", {})
    checks = gate.check_ledger_covers_patch_sets(repo)
    assert len(checks) == 1 and not checks[0].ok and "unreadable" in checks[0].detail


def test_the_gate_actually_runs_the_coverage_check(tmp_path, monkeypatch, capsys):
    """A check that exists but is never called is the failure class this gate exists to
    catch. Pin the wiring in main(), both directions."""
    for name in ("check_wiring_lints", "check_group_overlays", "check_overlay_devices",
                 "check_overlay_executables", "check_script_executables",
                 "check_board_script_executables", "check_background_sources",
                 "check_patch_declarations"):
        monkeypatch.setattr(gate, name, lambda *a, **k: [], raising=False)

    bad = make_repo(tmp_path, ENTRY, {"buildroot-external/patches/upower/0.99.19": 1},
                    name="bad")
    assert gate.main(["run", "--repo", str(bad), "--mode", "fast", "--base", "none"]) == 1
    assert "patch-set-ledgered" in capsys.readouterr().out

    good = make_repo(tmp_path, ENTRY, {"buildroot-external/patches/ffmpeg/6.1.5": 1},
                     name="good")
    assert gate.main(["run", "--repo", str(good), "--mode", "fast", "--base", "none"]) == 0
