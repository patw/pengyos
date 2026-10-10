"""Files left in target/ that no package installs any more (pengyos_build.leftovers)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pengyos_build import leftovers
from pengyos_build.build import build_target
from pengyos_build.configure import configure_target
from pengyos_build.targets import Target

TIMESYNCD = (
    "./usr/lib/systemd/systemd-timesyncd",
    "./usr/lib/systemd/system/systemd-timesyncd.service",
)
SYSTEMD_NOW = ("./usr/lib/systemd/systemd", "./usr/lib/systemd/system/chrony-wait.service")

OLD = "BR2_INIT_SYSTEMD=y\nBR2_PACKAGE_SYSTEMD=y\nBR2_PACKAGE_SYSTEMD_TIMESYNCD=y\nBR2_PACKAGE_SWAYIMG=y\n"
NEW = "BR2_INIT_SYSTEMD=y\nBR2_PACKAGE_SYSTEMD=y\n# BR2_PACKAGE_SYSTEMD_TIMESYNCD is not set\nBR2_PACKAGE_SWAYIMG=y\n"


def _list(*entries: tuple[str, str]) -> str:
    return "".join(f"{pkg},{path}\n" for pkg, path in entries)


def _touch(target: Path, *paths: str) -> None:
    for p in paths:
        f = target / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.touch()


# --- the rules -----------------------------------------------------------------


def test_file_dropped_by_a_rebuilt_package_is_a_leftover(tmp_path: Path) -> None:
    _touch(tmp_path, *TIMESYNCD, *SYSTEMD_NOW)
    before = leftovers.parse_file_list(_list(*(("systemd", p) for p in TIMESYNCD + SYSTEMD_NOW)))
    now = leftovers.parse_file_list(_list(*(("systemd", p) for p in SYSTEMD_NOW)))
    found = leftovers.find_leftovers(before, now, set(), tmp_path)
    assert found == {p: "systemd" for p in TIMESYNCD}


def test_files_of_a_disabled_package_are_leftovers(tmp_path: Path) -> None:
    # A package turned off keeps its build dir, so its entries stay in the list.
    _touch(tmp_path, "./usr/bin/swayimg")
    now = leftovers.parse_file_list(_list(("swayimg", "./usr/bin/swayimg")))
    disabled = leftovers.disabled_packages("# BR2_PACKAGE_SWAYIMG is not set\n")
    assert leftovers.find_leftovers({}, now, disabled, tmp_path) == {"./usr/bin/swayimg": "swayimg"}


def test_disabled_names_map_to_package_names() -> None:
    config = "# BR2_PACKAGE_LINUX_FIRMWARE is not set\n# BR2_TARGET_ROOTFS_TAR is not set\n"
    assert leftovers.disabled_packages(config) == {"linux-firmware"}


def test_a_file_another_package_still_installs_is_not_a_leftover(tmp_path: Path) -> None:
    _touch(tmp_path, "./usr/bin/tool")
    before = leftovers.parse_file_list(_list(("old-pkg", "./usr/bin/tool")))
    now = leftovers.parse_file_list(_list(("swayimg", "./usr/bin/tool"), ("old-pkg", "./usr/bin/tool")))
    disabled = {"old-pkg"}
    assert leftovers.find_leftovers(before, now, disabled, tmp_path) == {}


def test_overlay_and_post_build_files_are_never_reported(tmp_path: Path) -> None:
    # In neither list: overlays and post-build scripts add them after packages.
    _touch(tmp_path, "./etc/motd", *SYSTEMD_NOW)
    lst = leftovers.parse_file_list(_list(*(("systemd", p) for p in SYSTEMD_NOW)))
    assert leftovers.find_leftovers(lst, lst, set(), tmp_path) == {}


def test_a_file_already_gone_from_target_is_not_reported(tmp_path: Path) -> None:
    before = leftovers.parse_file_list(_list(("systemd", TIMESYNCD[0])))
    assert leftovers.find_leftovers(before, {}, set(), tmp_path) == {}


def test_a_dangling_symlink_counts_as_present(tmp_path: Path) -> None:
    link = tmp_path / "usr/lib/libgone.so"
    link.parent.mkdir(parents=True)
    link.symlink_to("libgone.so.1")
    before = leftovers.parse_file_list(_list(("gone", "./usr/lib/libgone.so")))
    assert leftovers.find_leftovers(before, {}, set(), tmp_path) == {"./usr/lib/libgone.so": "gone"}


def test_snapshot_keeps_the_oldest_list(tmp_path: Path) -> None:
    (tmp_path / "build").mkdir()
    (tmp_path / leftovers.FILE_LIST).write_text(_list(("systemd", TIMESYNCD[0])))
    leftovers.snapshot(tmp_path)
    (tmp_path / leftovers.FILE_LIST).write_text(_list(("systemd", SYSTEMD_NOW[0])))
    leftovers.snapshot(tmp_path)  # a second configure before any build
    assert TIMESYNCD[0] in (tmp_path / leftovers.SNAPSHOT).read_text()


def test_no_file_list_is_not_an_error(tmp_path: Path) -> None:
    leftovers.snapshot(tmp_path)
    assert not (tmp_path / leftovers.SNAPSHOT).exists()
    assert not leftovers.update(tmp_path)
    assert str(leftovers.current(tmp_path)) == "leftovers: none in target/"


def test_reenabling_the_package_clears_the_report(tmp_path: Path) -> None:
    out = tmp_path
    _touch(out / "target", "./usr/bin/swayimg")
    (out / "build").mkdir()
    (out / leftovers.FILE_LIST).write_text(_list(("swayimg", "./usr/bin/swayimg")))
    (out / ".config").write_text("# BR2_PACKAGE_SWAYIMG is not set\n")
    assert leftovers.update(out)
    (out / ".config").write_text("BR2_PACKAGE_SWAYIMG=y\n")
    assert not leftovers.current(out)
    assert not (out / leftovers.REPORT).exists()


def test_prepare_rebuild_ages_only_the_rebuilt_packages_files(tmp_path: Path) -> None:
    # A reinstall skips files already up to date, and Buildroot lists only what the
    # install changed: aged files get rewritten, so the new list is complete.
    out = tmp_path
    (out / "build").mkdir()
    (out / leftovers.FILE_LIST).write_text(
        _list(("systemd", SYSTEMD_NOW[0]), ("pipewire-pulse", "./usr/bin/pw"), ("pipewire", "./usr/bin/pipewire"))
    )
    _touch(out / "target", SYSTEMD_NOW[0], "./usr/bin/pw", "./usr/bin/pipewire")
    n = leftovers.prepare_rebuild(out, (out / "build" / "systemd-258.7", out / "build" / "pipewire-1.6.6"))
    assert n == 2
    assert (out / "target" / SYSTEMD_NOW[0]).stat().st_mtime == 0
    assert (out / "target" / "usr/bin/pipewire").stat().st_mtime == 0
    assert (out / "target" / "usr/bin/pw").stat().st_mtime != 0  # pipewire-pulse is not pipewire


def test_prepare_rebuild_skips_missing_files_and_lists(tmp_path: Path) -> None:
    assert leftovers.prepare_rebuild(tmp_path, (tmp_path / "build" / "systemd-258.7",)) == 0
    (tmp_path / "build").mkdir()
    (tmp_path / leftovers.FILE_LIST).write_text(_list(("systemd", TIMESYNCD[0])))
    assert leftovers.prepare_rebuild(tmp_path, (tmp_path / "build" / "systemd-258.7",)) == 0


# --- configure -> build, the 2026-10-10 timesyncd case -------------------------


def _setup(tmp_path: Path) -> tuple[Path, Target, Path]:
    root = tmp_path / "root"
    (root / "buildroot-2026.08").mkdir(parents=True)
    external = tmp_path / "ext"
    external.mkdir()
    target = Target(name="desk", br_version="2026.08", external_tree=external, defconfig="d_defconfig", board="")
    return root, target, root / "output" / "desk"


def _make_writing_config(config: str):
    def spawner(argv, **kwargs):
        Path(next(a[2:] for a in argv if a.startswith("O=")), ".config").write_text(config)
        return subprocess.CompletedProcess(list(argv), 0)

    return spawner


def _make_building(file_list: str):
    """A fake build: Buildroot rewrites the file list and deletes nothing."""

    def spawner(argv, **kwargs):
        out = Path(next(a[2:] for a in argv if a.startswith("O=")))
        (out / leftovers.FILE_LIST).write_text(file_list)
        return subprocess.CompletedProcess(list(argv), 0)

    return spawner


def _configure(root: Path, target: Target, config: str):
    return configure_target(
        target, root, env={"PATH": "/usr/bin"}, resolver=lambda name: None, runner=_make_writing_config(config)
    )


def _build(root: Path, target: Target, file_list: str):
    return build_target(
        target, root, jobs=1, env={"PATH": "/usr/bin"}, resolver=lambda name: None, runner=_make_building(file_list)
    )


def test_turned_off_sub_option_is_named_after_the_build(tmp_path: Path) -> None:
    root, target, out = _setup(tmp_path)
    _configure(root, target, OLD)
    systemd = out / "build" / "systemd-258.7"
    systemd.mkdir(parents=True)
    (systemd / ".stamp_built").touch()
    (out / leftovers.FILE_LIST).write_text(_list(*(("systemd", p) for p in TIMESYNCD + SYSTEMD_NOW)))
    _touch(out / "target", *TIMESYNCD, *SYSTEMD_NOW)

    configured = _configure(root, target, NEW)
    assert configured.rebuilt == ("systemd-258.7",)
    assert (out / leftovers.SNAPSHOT).is_file()
    assert (out / "target" / TIMESYNCD[0]).stat().st_mtime == 0  # aged for the reinstall

    built = _build(root, target, _list(*(("systemd", p) for p in SYSTEMD_NOW)))
    assert built.leftovers.files == tuple(sorted(("systemd", p) for p in TIMESYNCD))
    assert "CLEAN BUILD NEEDED: 2 file(s)" in str(built)
    assert "systemd 2" in str(built)
    assert not (out / leftovers.SNAPSHOT).exists()

    # Still reported by the next configure and build, though nothing changed.
    assert any("CLEAN BUILD NEEDED" in n for n in _configure(root, target, NEW).notes)
    assert _build(root, target, _list(*(("systemd", p) for p in SYSTEMD_NOW))).leftovers

    # Removing the files (or a clean build) clears it.
    for p in TIMESYNCD:
        (out / "target" / p).unlink()
    assert not leftovers.current(out)
    assert str(_build(root, target, _list(*(("systemd", p) for p in SYSTEMD_NOW)))).count("\n") == 0


def test_a_build_with_nothing_left_prints_one_line(tmp_path: Path) -> None:
    root, target, out = _setup(tmp_path)
    _configure(root, target, OLD)
    (out / "build").mkdir()
    lst = _list(*(("systemd", p) for p in SYSTEMD_NOW))
    (out / leftovers.FILE_LIST).write_text(lst)
    _touch(out / "target", *SYSTEMD_NOW)
    _configure(root, target, OLD)
    built = _build(root, target, lst)
    assert not built.leftovers
    assert "\n" not in str(built)
