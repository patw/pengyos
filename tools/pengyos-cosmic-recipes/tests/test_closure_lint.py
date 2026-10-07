"""Tests for the recipes-lane closure linter (``pengyos_cosmic_recipes.lint``).

Exercise :func:`closure_lint` over the committed core group: a complete recipe
set closes; a missing recipe, an unclosed dependency and a lint-broken recipe
each fail loudly with the right rule. All checks are stdlib and need no
network, root, display, or hardware.
"""

from __future__ import annotations

import re
from pathlib import Path

from pengyos_cosmic_recipes import closure_lint, emit_group, load_manifest


def _emit_all(tmp_path: Path) -> Path:
    """Emit every group that has components; return the recipe root."""
    E = tmp_path / "package"
    manifest = load_manifest()
    for group, names in manifest.groups.items():
        if names:
            emit_group(manifest, group, out_dir=E)
    return E


def test_closure_closed_when_all_recipes_present(tmp_path):
    """Every component with a lint-clean recipe closes the loop."""
    root = _emit_all(tmp_path)
    report = closure_lint(load_manifest(), root)
    assert report.is_closed is True
    assert bool(report) is True
    assert report.violations == ()
    assert report.components == (
        "cosmic-protocols",
        "libcosmic",
        "cosmic-comp",
        "cosmic-session",
        "cosmic-greeter",
        "cosmic-panel",
        "cosmic-launcher",
        "cosmic-applets",
        "cosmic-bg",
        "cosmic-randr",
        "cosmic-settings",
        "cosmic-settings-daemon",
        "cosmic-osd",
        "cosmic-notifications",
        "cosmic-idle",
        "cosmic-icons",
        "cosmic-files",
        "cosmic-edit",
        "cosmic-term",
        "cosmic-store",
        "cosmic-screenshot",
        "cosmic-player",
    )


def test_closure_fails_when_recipe_missing(tmp_path):
    """Deleting one component's recipe reports a missing-recipe violation."""
    import shutil

    root = _emit_all(tmp_path)
    shutil.rmtree(root / "libcosmic")  # remove the whole component dir
    report = closure_lint(load_manifest(), root)
    assert report.is_closed is False
    rules = [v.rule for v in report.violations]
    assert "missing-recipe" in rules
    assert "unclosed-dep" in rules  # libcosmic is a dep of cosmic-comp


def test_closure_reports_unclosed_dep(tmp_path):
    """Removing cosmic-protocols leaves its dependents' deps unclosed."""
    import shutil

    root = _emit_all(tmp_path)
    # cosmic-protocols has no recipe now; libcosmic and cosmic-comp both depend
    # on it, so their deps are unclosed even though their own recipes exist.
    shutil.rmtree(root / "cosmic-protocols")
    report = closure_lint(load_manifest(), root)
    assert report.is_closed is False
    rules = [v.rule for v in report.violations]
    assert "missing-recipe" in rules  # cosmic-protocols itself
    assert "unclosed-dep" in rules  # libcosmic and cosmic-comp depend on it
    # The dependents keep their own recipes (only the dep is unclosed).
    assert (root / "libcosmic" / "libcosmic.mk").is_file()
    assert (root / "cosmic-comp" / "cosmic-comp.mk").is_file()


def test_closure_reports_lint_violation(tmp_path):
    """A recipe with a floating VERSION fails the closure with the lint rule."""
    root = _emit_all(tmp_path)
    mk = root / "cosmic-comp" / "cosmic-comp.mk"
    text = mk.read_text(encoding="utf-8")
    # Replace whatever the manifest pins today, not a literal SHA: this test is
    # about the floating-ref lint rule, and hard-coding a pin makes it silently
    # vacuous the moment the pin moves.
    pinned = re.search(r"^BR2_PACKAGE_COSMIC_COMP_VERSION = \S+$", text, re.M)
    assert pinned, "no pinned VERSION line to corrupt"
    text = text.replace(pinned.group(0), "BR2_PACKAGE_COSMIC_COMP_VERSION = main")
    mk.write_text(text, encoding="utf-8")
    report = closure_lint(load_manifest(), root)
    assert report.is_closed is False
    rules = [v.rule for v in report.violations]
    assert "version" in rules  # the engine's floating-ref rule


def test_closure_reports_missing_recipe_file(tmp_path):
    """A recipe missing its .hash fails the closure with a missing-recipe rule."""
    root = _emit_all(tmp_path)
    (root / "cosmic-protocols" / "cosmic-protocols.hash").unlink()
    report = closure_lint(load_manifest(), root)
    assert report.is_closed is False
    rules = [v.rule for v in report.violations]
    assert "missing-recipe" in rules


def test_closure_report_string(tmp_path):
    """The report string is human-readable in both states."""
    import shutil

    root = _emit_all(tmp_path)
    assert "closure complete" in str(closure_lint(load_manifest(), root))
    shutil.rmtree(root / "libcosmic")
    assert "closure violation" in str(closure_lint(load_manifest(), root))


def test_closure_empty_recipe_root_fails(tmp_path):
    """An empty recipe root fails the closure (no recipes at all)."""
    report = closure_lint(load_manifest(), tmp_path / "package")
    assert report.is_closed is False
    assert [v.rule for v in report.violations]  # every component missing


def _emit_groups(tmp_path: Path, *groups: str) -> Path:
    """Emit several groups into a fresh dir; return the recipe root."""
    root = tmp_path / "package"
    for group in groups:
        emit_group(load_manifest(), group, out_dir=root)
    return root


def test_closure_login_depends_on_core(tmp_path):
    """The login group closes only once the core group's recipes are present.

    cosmic-greeter (login) depends on libcosmic (core); the closure linter
    checks the whole manifest, so emitting the login group alone leaves that
    dep unclosed, and emitting core + login + shell closes it.
    """
    root = _emit_groups(tmp_path, "login")
    report = closure_lint(load_manifest(), root)
    assert report.is_closed is False
    rules = [v.rule for v in report.violations]
    assert "unclosed-dep" in rules  # cosmic-greeter -> libcosmic
    assert any("libcosmic" in str(v) for v in report.violations)

    # Emitting the core (and shell) groups closes the login dep — and the whole
    # manifest, since the shell group is now authored too.
    _emit_groups(tmp_path, "core", "shell")
    assert closure_lint(load_manifest(), root).is_closed is False  # apps still unemitted
    _emit_groups(tmp_path, "apps")
    report = closure_lint(load_manifest(), root)
    assert report.is_closed is True
    assert report.components == (
        "cosmic-protocols",
        "libcosmic",
        "cosmic-comp",
        "cosmic-session",
        "cosmic-greeter",
        "cosmic-panel",
        "cosmic-launcher",
        "cosmic-applets",
        "cosmic-bg",
        "cosmic-randr",
        "cosmic-settings",
        "cosmic-settings-daemon",
        "cosmic-osd",
        "cosmic-notifications",
        "cosmic-idle",
        "cosmic-icons",
        "cosmic-files",
        "cosmic-edit",
        "cosmic-term",
        "cosmic-store",
        "cosmic-screenshot",
        "cosmic-player",
    )
