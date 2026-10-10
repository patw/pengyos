"""Recipe-staleness detection (pengyos_build.recipe).

The case this exists for: ``external.mk`` gained ``PIPEWIRE_CONF_OPTS +=
-Dflatpak=enabled`` and ``package/xdg-desktop-portal-gtk/`` gained
``-Dsettings=enabled``. Neither moves a symbol of its own package, so the
``.config`` diff in stale.py sees nothing, Buildroot's stamps stay green, and the
26.10.19 Desktop NVIDIA image shipped with both changes absent - built, boot-checked,
signed and published, and wrong.

Hermetic: git is an injected runner, so no git binary and no network are needed.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from pengyos_build import recipe
from pengyos_build.recipe import (
    attributed_stems,
    enabled_stems,
    newest_paths,
    recipe_paths,
    recipe_staleness,
    stem_of,
)

EXT = "buildroot-external"
NOW = 1_700_000_000
OLDER = 1_600_000_000

CONFIG = """\
BR2_x86_64=y
BR2_PACKAGE_PIPEWIRE=y
BR2_PACKAGE_PIPEWIRE_PULSE=y
BR2_PACKAGE_XDG_DESKTOP_PORTAL_GTK=y
BR2_PACKAGE_XDG_DESKTOP_PORTAL_GTK_APPCHOOSER=y
BR2_PACKAGE_LIBGLIB2=y
BR2_PACKAGE_FONTCONFIG=y
BR2_PACKAGE_HOST_CMAKE=y
BR2_TARGET_ROOTFS_EXT2=y
BR2_PACKAGE_DISABLED_THING="n"
"""


class FakeGit:
    """In-memory stand-in for the three git calls the check makes."""

    def __init__(self, toplevel: str | None = "", log: str = "", diff: str = ""):
        self.toplevel = toplevel
        self.log = log
        self.diff = diff
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if "rev-parse" in argv:
            return self.toplevel + "\n" if self.toplevel else ""
        if "-p" in argv:
            return self.diff
        if "--name-only" in argv:
            return self.log
        return ""

    @property
    def call_count(self) -> int:
        return len(self.calls)


def tree(tmp_path: Path, *built: str) -> dict[str, Path]:
    """A repo holding an external tree and a configured output dir with build dirs."""
    root = tmp_path / "repo"
    external = root / EXT
    (external / "package" / "xdg-desktop-portal-gtk").mkdir(parents=True)
    (external / "patches" / "pipewire" / "1.6.6").mkdir(parents=True)
    (external / "board" / "desktop").mkdir(parents=True)
    (external / "external.mk").write_text("PIPEWIRE_CONF_OPTS = \\\n")
    output = root / "output" / "demo"
    (output / "build").mkdir(parents=True)
    (output / "images").mkdir(parents=True)
    (output / "images" / "rootfs.ext2").write_text("img")
    (output / ".config").write_text(CONFIG)
    for name in built:
        (output / "build" / name).mkdir()
        (output / "build" / name / ".stamp_built").write_text("")
    return {"root": root, "external": external, "output": output}


def stamp(path: Path, name: str, when: float) -> Path:
    """Set a package's .stamp_built to a known time (the build date is the fact under test)."""
    d = path / "build" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / ".stamp_built").write_text("")
    os.utime(d / ".stamp_built", (when, when))
    return d


def name_log(epoch: int, *paths: str) -> str:
    return f"{epoch}\n" + "\n".join(paths) + "\n\n"


def mk_diff(epoch: int, *lines: str) -> str:
    body = "\n".join("+" + line for line in lines)
    return f"{epoch}\n\n--- a/{EXT}/external.mk\n+++ b/{EXT}/external.mk\n{body}\n"


class TestParsing:
    def test_enabled_stems_are_package_symbols_only(self):
        stems = enabled_stems(CONFIG)
        assert {"PIPEWIRE", "XDG_DESKTOP_PORTAL_GTK", "LIBGLIB2", "FONTCONFIG"} <= stems
        # host packages and non-package symbols are not targets of a rebuild
        assert "HOST_CMAKE" in stems  # BR2_PACKAGE_HOST_* is a package symbol
        assert "TARGET_ROOTFS_EXT2" not in stems
        assert "XDG_DESKTOP_PORTAL_GTK_APPCHOOSER" in stems  # a sub-option, no build dir

    def test_newest_path_wins_when_a_path_is_touched_twice(self):
        log = name_log(NOW, f"{EXT}/external.mk") + name_log(OLDER, f"{EXT}/external.mk")
        assert newest_paths(log)[f"{EXT}/external.mk"] == NOW

    def test_stem_of_takes_the_longest_enabled_prefix(self):
        stems = enabled_stems(CONFIG)
        assert stem_of("XDG_DESKTOP_PORTAL_GTK_CONF_OPTS", stems) == "XDG_DESKTOP_PORTAL_GTK"
        assert stem_of("PIPEWIRE_CONF_OPTS", stems) == "PIPEWIRE"
        assert stem_of("BR2_PACKAGE_PIPEWIRE", stems) is None
        assert stem_of("CONF_OPTS", stems) is None

    def test_a_shorter_enabled_stem_does_not_steal_the_longer_one(self):
        # PIPEWIRE_PULSE is itself an enabled symbol: a change to it is a change to
        # pipewire-pulse, not to pipewire. First-match would answer PIPEWIRE.
        stems = enabled_stems(CONFIG)
        assert {"PIPEWIRE", "PIPEWIRE_PULSE"} <= stems
        assert stem_of("PIPEWIRE_PULSE_CONF_OPTS", stems) == "PIPEWIRE_PULSE"

    def test_bare_number_context_line_is_not_a_commit_epoch(self):
        # A diff context line that is only digits must not become the epoch for the
        # paths that follow it - that would date a recipe to 1970 and rebuild it.
        log = f"{NOW}\n{EXT}/external.mk\n1234\n{EXT}/package/pipewire/pipewire.mk\n"
        assert newest_paths(log) == {
            f"{EXT}/external.mk": NOW,
            f"{EXT}/package/pipewire/pipewire.mk": NOW,
        }


class TestExternalMkAttribution:
    def test_assignment_target_is_charged_to_its_package(self):
        diff = mk_diff(NOW, "PIPEWIRE_CONF_OPTS += -Dflatpak=enabled")
        assert attributed_stems(diff, enabled_stems(CONFIG)) == {"PIPEWIRE": NOW}

    def test_prerequisite_on_the_right_is_not_charged(self):
        # The 26.10.19 line, verbatim in shape: pipewire's rule gains a prerequisite.
        diff = mk_diff(NOW, "$(PIPEWIRE_TARGET_CONFIGURE): $(LIBGLIB2_TARGET_INSTALL_STAGING)")
        got = attributed_stems(diff, enabled_stems(CONFIG))
        assert got == {"PIPEWIRE": NOW}
        assert "LIBGLIB2" not in got  # naming libglib2 is not changing libglib2

    def test_dependency_line_names_the_package_being_changed(self):
        diff = mk_diff(NOW, "XDG_DESKTOP_PORTAL_GTK_DEPENDENCIES += fontconfig gsettings")
        got = attributed_stems(diff, enabled_stems(CONFIG))
        assert got == {"XDG_DESKTOP_PORTAL_GTK": NOW}
        assert "FONTCONFIG" not in got

    def test_comment_only_change_charges_nothing(self):
        diff = f"{NOW}\n\n--- a/{EXT}/external.mk\n+++ b/{EXT}/external.mk\n+  # PipeWire: a note\n"
        assert attributed_stems(diff, enabled_stems(CONFIG)) == {}

    def test_context_lines_are_not_changes(self):
        diff = (
            f"{NOW}\n\n--- a/{EXT}/external.mk\n+++ b/{EXT}/external.mk\n"
            f" PIPEWIRE_CONF_OPTS = \\\n -Dbluez5=enabled\n"
        )
        assert attributed_stems(diff, enabled_stems(CONFIG)) == {}


class TestRecipePaths:
    def test_authored_recipe_dir_maps_to_its_stem(self):
        newest = {f"{EXT}/package/xdg-desktop-portal-gtk/xdg-desktop-portal-gtk.mk": NOW}
        stems = enabled_stems(CONFIG)
        assert recipe_paths(EXT, stems, newest) == {"XDG_DESKTOP_PORTAL_GTK": NOW}

    def test_patch_dir_of_a_stock_package_maps_to_it(self):
        newest = {f"{EXT}/patches/pipewire/1.6.6/0001-fix.patch": NOW}
        assert recipe_paths(EXT, enabled_stems(CONFIG), newest) == {"PIPEWIRE": NOW}

    def test_a_package_nobody_touched_is_absent(self):
        newest = {f"{EXT}/package/suricata/suricata.mk": NOW}
        assert recipe_paths(EXT, enabled_stems(CONFIG), newest) == {}


class TestStaleness:
    def test_stamp_older_than_the_recipe_is_stale(self, tmp_path: Path):
        t = tree(tmp_path, "pipewire-1.6.6")
        stamp(t["output"], "pipewire-1.6.6", OLDER)
        git = FakeGit(
            toplevel=str(t["root"]),
            log=name_log(NOW, f"{EXT}/external.mk"),
            diff=mk_diff(NOW, "PIPEWIRE_CONF_OPTS += -Dflatpak=enabled"),
        )
        report = recipe_staleness(t["external"], t["output"], git=git)
        assert [p.name for p in report.rebuild] == ["pipewire-1.6.6"]

    def test_stamp_newer_than_the_recipe_is_fresh(self, tmp_path: Path):
        t = tree(tmp_path, "pipewire-1.6.6")
        stamp(t["output"], "pipewire-1.6.6", NOW + 600)
        git = FakeGit(
            toplevel=str(t["root"]),
            log=name_log(NOW, f"{EXT}/external.mk"),
            diff=mk_diff(NOW, "PIPEWIRE_CONF_OPTS += -Dflatpak=enabled"),
        )
        assert recipe_staleness(t["external"], t["output"], git=git).rebuild == ()

    def test_both_fixes_at_once(self, tmp_path: Path):
        """The 26.10.19 NVIDIA case: two packages, two shapes of change."""
        t = tree(tmp_path, "pipewire-1.6.6", "xdg-desktop-portal-gtk-1.15.3")
        stamp(t["output"], "pipewire-1.6.6", OLDER)
        stamp(t["output"], "xdg-desktop-portal-gtk-1.15.3", OLDER)
        git = FakeGit(
            toplevel=str(t["root"]),
            log=name_log(NOW, f"{EXT}/package/xdg-desktop-portal-gtk/xdg-desktop-portal-gtk.mk"),
            diff=mk_diff(NOW, "PIPEWIRE_CONF_OPTS += -Dflatpak=enabled"),
        )
        got = sorted(p.name for p in recipe_staleness(t["external"], t["output"], git=git).rebuild)
        assert got == ["pipewire-1.6.6", "xdg-desktop-portal-gtk-1.15.3"]

    def test_a_change_to_an_unbuilt_package_needs_nothing(self, tmp_path: Path):
        t = tree(tmp_path, "linux-7.2.5")
        stamp(t["output"], "linux-7.2.5", OLDER)
        git = FakeGit(
            toplevel=str(t["root"]),
            log=name_log(NOW, f"{EXT}/package/fontconfig/fontconfig.mk"),
            diff="",
        )
        assert recipe_staleness(t["external"], t["output"], git=git).rebuild == ()

    def test_a_configured_but_unbuilt_package_is_skipped(self, tmp_path: Path):
        t = tree(tmp_path)  # no build dirs at all
        git = FakeGit(
            toplevel=str(t["root"]),
            log=name_log(NOW, f"{EXT}/external.mk"),
            diff=mk_diff(NOW, "PIPEWIRE_CONF_OPTS += -Dflatpak=enabled"),
        )
        report = recipe_staleness(t["external"], t["output"], git=git)
        assert report.rebuild == () and report.checked == 0

    def test_no_config_is_a_quiet_empty_report(self, tmp_path: Path):
        t = tree(tmp_path, "pipewire-1.6.6")
        (t["output"] / ".config").unlink()
        git = FakeGit(toplevel=str(t["root"]), log=name_log(NOW, f"{EXT}/external.mk"))
        assert recipe_staleness(t["external"], t["output"], git=git).rebuild == ()
        assert git.call_count == 0  # bails out before git is touched at all

    def test_a_non_git_tree_is_reported_not_an_error(self, tmp_path: Path):
        t = tree(tmp_path, "pipewire-1.6.6")
        report = recipe_staleness(t["external"], t["output"], git=FakeGit())
        assert report.rebuild == ()
        assert report.notes and "not in git" in report.notes[0]

    def test_no_external_change_means_no_rebuild(self, tmp_path: Path):
        t = tree(tmp_path, "pipewire-1.6.6")
        stamp(t["output"], "pipewire-1.6.6", NOW)
        git = FakeGit(
            toplevel=str(t["root"]),
            log=name_log(NOW, "CHANGELOG.md"),
            diff="",
        )
        report = recipe_staleness(t["external"], t["output"], git=git)
        assert report.rebuild == () and report.notes == ()

    def test_three_git_calls_and_no_tree_scan(self, tmp_path: Path):
        """The cost contract: three scoped git calls, whatever the tree holds."""
        t = tree(tmp_path, "pipewire-1.6.6")
        stamp(t["output"], "pipewire-1.6.6", OLDER)
        git = FakeGit(
            toplevel=str(t["root"]),
            log=name_log(NOW, f"{EXT}/external.mk"),
            diff=mk_diff(NOW, "PIPEWIRE_CONF_OPTS += -Dflatpak=enabled"),
        )
        recipe_staleness(t["external"], t["output"], git=git)
        assert git.call_count == 3
        verbs = [" ".join(c) for c in git.calls]
        assert all(c[0] == "git" and c[1] == "-C" for c in git.calls)
        assert sum("--name-only" in v for v in verbs) == 1
        assert sum(" -p " in v for v in verbs) == 1
        assert all(v.endswith(f"{EXT}") or v.endswith(f"{EXT}/external.mk") for v in verbs[1:])


class TestBoardNotes:
    def test_board_change_after_the_image_was_written_is_flagged(self, tmp_path: Path):
        t = tree(tmp_path, "pipewire-1.6.6")
        os.utime(t["output"] / "images" / "rootfs.ext2", (OLDER, OLDER))
        git = FakeGit(toplevel=str(t["root"]), log=name_log(NOW, f"{EXT}/board/desktop/users.table"))
        report = recipe_staleness(t["external"], t["output"], git=git)
        assert report.rebuild == ()
        assert len(report.notes) == 1 and "rootfs" in report.notes[0]

    def test_board_change_the_image_already_has_is_silent(self, tmp_path: Path):
        t = tree(tmp_path, "pipewire-1.6.6")
        os.utime(t["output"] / "images" / "rootfs.ext2", (NOW + 600, NOW + 600))
        git = FakeGit(toplevel=str(t["root"]), log=name_log(NOW, f"{EXT}/board/desktop/users.table"))
        assert recipe_staleness(t["external"], t["output"], git=git).notes == ()


class TestConfigureIntegration:
    def test_configure_removes_the_recipe_stale_build_dir(self, tmp_path: Path, monkeypatch):
        """configure is where the finding is acted on: the build dir goes, make rebuilds it."""
        from pengyos_build import configure
        from pengyos_build.configure import configure_target

        root = tmp_path / "root"
        (root / "buildroot-2026.02.3").mkdir(parents=True)
        t = tree(tmp_path, "pipewire-1.6.6")
        output = root / "output" / "demo"
        output.parent.mkdir(parents=True, exist_ok=True)
        t["output"].rename(output)
        t["output"] = output
        stamp(output, "pipewire-1.6.6", OLDER)

        def fake_report(external, output):
            return recipe.RecipeReport(rebuild=(output / "build" / "pipewire-1.6.6",), checked=1)

        monkeypatch.setattr(configure.recipe, "recipe_staleness", fake_report)

        def spawner(argv, **kwargs):
            out = kwargs.get("stdout")
            if out is not None:
                out.write("make\n")
            return subprocess.CompletedProcess(list(argv), 0)

        target = configure.Target(
            name="demo",
            br_version="2026.02.3",
            external_tree=t["external"],
            defconfig="demo_defconfig",
            board="",
        )
        result = configure_target(
            target,
            root,
            runner=spawner,
            env={"PATH": "/usr/bin"},
            show_info=lambda s, o, e: None,
        )
        assert result.rebuilt == ("pipewire-1.6.6",)
        assert not (t["output"] / "build" / "pipewire-1.6.6").exists()
