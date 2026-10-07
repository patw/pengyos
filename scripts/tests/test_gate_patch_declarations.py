"""Tests for the gate's carried-patch declaration check (scripts/pengyos_gate.py).

Why the check exists: a patch delivered through BR2_GLOBAL_PATCH_DIR is invisible to
pkg-stats (`set_patch_count()` walks only the package's own directory), so without
`<PKG>_IGNORE_CVES` the tree keeps reporting a CVE as open after we shipped the fix.
Found live on 2026-10-07: the libxml2 patch set — 7 patches, published in 26.10.8 —
had no declaration, while `buildroot-external/patches/README.md` rule 3 and the
divergence ledger both named the fix. Both directions are reproduced here.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pengyos_gate as gate  # noqa: E402

LEDGER = '''[[divergence]]
id = "libxml2-2.15.3-cve-2026"
kind = "patch-set"
component = "libxml2"
pinned_version = "2.15.3"
patches_dir = "buildroot-external/patches/libxml2/2.15.3"
closes = ["CVE-2026-86137", "CVE-2026-86138"]
'''


def make_repo(tmp_path: Path, external_mk: str, ledger: str = LEDGER,
              name: str = "repo") -> Path:
    repo = tmp_path / name
    patches = repo / "buildroot-external/patches/libxml2/2.15.3"
    patches.mkdir(parents=True, exist_ok=True)
    (patches / "0001-xmlregexp.patch").write_text("patch")
    (patches / "0002-dict.patch").write_text("patch")
    (repo / "buildroot-external/external.mk").write_text(external_mk)
    (repo / "divergence.toml").write_text(ledger)
    return repo


def check(tmp_path: Path, external_mk: str, ledger: str = LEDGER) -> list:
    repo = make_repo(tmp_path, external_mk, ledger)
    return [c for c in gate.check_patch_declarations(repo) if "libxml2" in c.name]


def test_declared_patch_set_passes(tmp_path):
    """The positive control: the tree says exactly what the ledger says it closes."""
    checks = check(tmp_path, "LIBXML2_IGNORE_CVES += CVE-2026-86137\n"
                             "LIBXML2_IGNORE_CVES += CVE-2026-86138\n")
    assert checks, "the patch set was not checked at all"
    assert all(c.ok for c in checks), [c.detail for c in checks]
    assert "2 patch(es), 2 CVE(s) declared" in checks[0].detail


def test_undeclared_patch_set_fails_the_way_it_failed_live(tmp_path):
    """2026-10-07: patches carried, published, and pkg-stats never told."""
    checks = check(tmp_path, "# nothing declared here\n")
    assert checks and all(not c.ok for c in checks)
    assert "pkg-stats is not told" in checks[0].detail
    assert "CVE-2026-86137" in checks[0].detail and "CVE-2026-86138" in checks[0].detail


def test_partial_declaration_names_the_missing_cve(tmp_path):
    checks = check(tmp_path, "LIBXML2_IGNORE_CVES += CVE-2026-86137\n")
    assert checks and not checks[0].ok
    assert "CVE-2026-86138" in checks[0].detail
    assert "CVE-2026-86137" not in checks[0].detail.split("missing")[-1]


def test_declaration_outliving_the_ledger_fails(tmp_path):
    """An ignore for a CVE this entry does not close would hide a real exposure."""
    checks = check(tmp_path, "LIBXML2_IGNORE_CVES += CVE-2026-86137\n"
                             "LIBXML2_IGNORE_CVES += CVE-2026-86138\n"
                             "LIBXML2_IGNORE_CVES += CVE-2099-00001\n")
    assert checks and not checks[0].ok
    assert "CVE-2099-00001" in checks[0].detail and "outliving" in checks[0].detail


def test_ledger_naming_a_directory_that_is_not_there_fails(tmp_path):
    repo = make_repo(tmp_path, "LIBXML2_IGNORE_CVES += CVE-2026-86137 CVE-2026-86138\n")
    import shutil
    shutil.rmtree(repo / "buildroot-external/patches/libxml2")
    checks = [c for c in gate.check_patch_declarations(repo) if "libxml2" in c.name]
    assert checks and not checks[0].ok
    assert "does not exist" in checks[0].detail


def test_empty_patch_directory_is_not_a_pass(tmp_path):
    repo = make_repo(tmp_path, "LIBXML2_IGNORE_CVES += CVE-2026-86137 CVE-2026-86138\n")
    for p in (repo / "buildroot-external/patches/libxml2/2.15.3").glob("*.patch"):
        p.unlink()
    checks = [c for c in gate.check_patch_declarations(repo) if "libxml2" in c.name]
    assert checks and not checks[0].ok
    assert "no .patch file" in checks[0].detail


def test_dashed_component_maps_to_buildroots_variable_name(tmp_path):
    """Buildroot's variable prefix is the package name uppercased with dashes as
    underscores: gst1-plugins-good -> GST1_PLUGINS_GOOD."""
    ledger = '''[[divergence]]
id = "gst-good-1.24.13-cve-2026"
kind = "patch-set"
component = "gst1-plugins-good"
pinned_version = "1.24.13"
patches_dir = "buildroot-external/patches/gst1-plugins-good/1.24.13"
closes = ["CVE-2026-73433"]
'''
    repo = tmp_path / "repo"
    (repo / "buildroot-external/patches/gst1-plugins-good/1.24.13").mkdir(parents=True)
    (repo / "buildroot-external/patches/gst1-plugins-good/1.24.13/0001-qtdemux.patch").write_text("p")
    (repo / "buildroot-external/external.mk").write_text(
        "GST1_PLUGINS_GOOD_IGNORE_CVES += CVE-2026-73433\n")
    (repo / "divergence.toml").write_text(ledger)
    checks = [c for c in gate.check_patch_declarations(repo) if "gst-good" in c.name]
    assert checks and all(c.ok for c in checks), [c.detail for c in checks]


def test_declaration_in_an_authored_recipe_counts_too(tmp_path):
    """A package we author declares its ignores in its own .mk, not in external.mk.
    The check reads both, so the rule does not depend on where the recipe lives."""
    ledger = '''[[divergence]]
id = "pengyos-thing-1.0-cve-2026"
kind = "patch-set"
component = "pengyos-thing"
pinned_version = "1.0"
patches_dir = "buildroot-external/patches/pengyos-thing/1.0"
closes = ["CVE-2026-11111"]
'''
    repo = tmp_path / "repo"
    (repo / "buildroot-external/patches/pengyos-thing/1.0").mkdir(parents=True)
    (repo / "buildroot-external/patches/pengyos-thing/1.0/0001-fix.patch").write_text("p")
    (repo / "buildroot-external/package/pengyos-thing").mkdir(parents=True)
    (repo / "buildroot-external/package/pengyos-thing/pengyos-thing.mk").write_text(
        "PENGYOS_THING_IGNORE_CVES += CVE-2026-11111\n")
    (repo / "buildroot-external/external.mk").write_text("# nothing here\n")
    (repo / "divergence.toml").write_text(ledger)
    checks = [c for c in gate.check_patch_declarations(repo) if "pengyos-thing" in c.name]
    assert checks and all(c.ok for c in checks), [c.detail for c in checks]


def test_a_declaration_in_the_generated_file_counts(tmp_path):
    """pengyos-nightly writes its declarations to patches/cve-declarations.mk, not to
    external.mk. The check reads both, or every nightly patch set would fail the gate."""
    repo = make_repo(tmp_path, "# no declarations here\n")
    (repo / "buildroot-external/patches/cve-declarations.mk").write_text(
        "# >>> libxml2\nLIBXML2_IGNORE_CVES += CVE-2026-86137 CVE-2026-86138\n# <<< libxml2\n")
    checks = [c for c in gate.check_patch_declarations(repo) if "libxml2" in c.name]
    assert checks and all(c.ok for c in checks), [c.detail for c in checks]


def test_the_gate_actually_runs_the_declaration_check(tmp_path, monkeypatch, capsys):
    """A check that exists but is never called is the exact failure class this gate
    exists to catch — a green suite around a tree nobody inspects. Pin the wiring."""
    for name in ("check_wiring_lints", "check_group_overlays", "check_overlay_executables",
                 "check_background_sources"):
        monkeypatch.setattr(gate, name, lambda *a, **k: [])

    bad = make_repo(tmp_path, "# nothing declared\n", name="bad")
    assert gate.main(["run", "--repo", str(bad), "--mode", "fast", "--base", "none"]) == 1
    assert "patch-declaration" in capsys.readouterr().out

    good = make_repo(tmp_path, "LIBXML2_IGNORE_CVES += CVE-2026-86137 CVE-2026-86138\n",
                     name="good")
    assert gate.main(["run", "--repo", str(good), "--mode", "fast", "--base", "none"]) == 0


def test_a_repo_with_no_patch_sets_claims_nothing(tmp_path):
    repo = tmp_path / "repo"
    (repo / "buildroot-external").mkdir(parents=True)
    (repo / "divergence.toml").write_text('[[divergence]]\nid = "x"\nkind = "fork"\n')
    checks = gate.check_patch_declarations(repo)
    assert len(checks) == 1 and checks[0].ok and "no patch set" in checks[0].detail
