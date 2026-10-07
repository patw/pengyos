"""Stale-package detection after a .config change (pengyos_build.stale)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pengyos_build import stale
from pengyos_build.configure import configure_target
from pengyos_build.targets import Target

OLD = """\
BR2_x86_64=y
BR2_PACKAGE_LINUX_FIRMWARE=y
BR2_PACKAGE_LINUX_FIRMWARE_I915=y
BR2_PACKAGE_MESA3D=y
BR2_PACKAGE_MESA3D_GBM=y
BR2_PACKAGE_HOST_MESA3D=y
BR2_PACKAGE_SWAYIMG=y
BR2_PACKAGE_PROVIDES_LIBEGL="mesa3d"
BR2_TARGET_ROOTFS_EXT2_SIZE="3000M"
"""


def _build(tmp_path: Path, *names: str) -> Path:
    build = tmp_path / "build"
    for name in names:
        (build / name).mkdir(parents=True)
    return build


def test_sub_option_change_rebuilds_its_package(tmp_path: Path) -> None:
    # The 2026-09-29 case: RTW88 turned on after linux-firmware was built.
    build = _build(tmp_path, "linux-firmware-20260810", "linux-7.2.5", "linux-headers-7.1.13")
    new = OLD + "BR2_PACKAGE_LINUX_FIRMWARE_RTL_RTW88=y\n"
    report = stale.stale_packages(OLD, new, build)
    assert [p.name for p in report.rebuild] == ["linux-firmware-20260810"]


def test_longest_package_prefix_wins(tmp_path: Path) -> None:
    build = _build(tmp_path, "mesa3d-26.1.8", "host-mesa3d-26.1.8")
    new = OLD.replace("BR2_PACKAGE_MESA3D_GBM=y\n", "")
    assert [p.name for p in stale.stale_packages(OLD, new, build).rebuild] == ["mesa3d-26.1.8"]


def test_host_package_option(tmp_path: Path) -> None:
    build = _build(tmp_path, "mesa3d-26.1.8", "host-mesa3d-26.1.8")
    new = OLD + "BR2_PACKAGE_HOST_MESA3D_OPENCL=y\n"
    assert [p.name for p in stale.stale_packages(OLD, new, build).rebuild] == ["host-mesa3d-26.1.8"]


def test_newly_enabled_package_needs_nothing(tmp_path: Path) -> None:
    build = _build(tmp_path, "linux-firmware-20260810")
    new = OLD + "BR2_PACKAGE_LIBGLVND=y\nBR2_PACKAGE_LIBGLVND_DISPATCH_EGL=y\n"
    assert stale.stale_packages(OLD, new, build).rebuild == ()


def test_unbuilt_package_is_skipped(tmp_path: Path) -> None:
    build = _build(tmp_path, "linux-7.2.5")  # linux-firmware never built
    new = OLD + "BR2_PACKAGE_LINUX_FIRMWARE_RTL_RTW88=y\n"
    assert stale.stale_packages(OLD, new, build).rebuild == ()


def test_non_package_symbols_are_ignored(tmp_path: Path) -> None:
    build = _build(tmp_path, "mesa3d-26.1.8")
    new = OLD.replace('"mesa3d"', '"libglvnd"').replace("3000M", "4000M")
    assert stale.stale_packages(OLD, new, build).rebuild == ()


def test_disabled_package_is_reported(tmp_path: Path) -> None:
    build = _build(tmp_path, "swayimg-5.6")
    new = OLD.replace("BR2_PACKAGE_SWAYIMG=y\n", "")
    report = stale.stale_packages(OLD, new, build)
    assert report.rebuild == ()
    assert report.disabled == ("swayimg",)


def test_unchanged_config_rebuilds_nothing(tmp_path: Path) -> None:
    build = _build(tmp_path, "linux-firmware-20260810", "mesa3d-26.1.8")
    assert stale.stale_packages(OLD, OLD, build) == stale.StaleReport()


def test_owning_package() -> None:
    packages = {"LINUX", "LINUX_FIRMWARE", "XLIB_LIBX11"}
    assert stale.owning_package("BR2_PACKAGE_LINUX_FIRMWARE_RTL_RTW88", packages) == "LINUX_FIRMWARE"
    assert stale.owning_package("BR2_PACKAGE_XLIB_LIBX11_FOO", packages) == "XLIB_LIBX11"
    assert stale.owning_package("BR2_LINUX_KERNEL_VERSION", packages) is None


# --- through configure_target -------------------------------------------------


def _target(external: Path) -> Target:
    return Target(
        name="desk", br_version="2026.08", external_tree=external, defconfig="d_defconfig", board=""
    )


def _make_writing(config: str):
    def spawner(argv, **kwargs):
        out = next(a[2:] for a in argv if a.startswith("O="))
        Path(out, ".config").write_text(config)
        return subprocess.CompletedProcess(list(argv), 0)

    return spawner


def _setup(tmp_path: Path) -> tuple[Path, Target, Path]:
    root = tmp_path / "root"
    (root / "buildroot-2026.08").mkdir(parents=True)
    external = tmp_path / "ext"
    external.mkdir()
    target = _target(external)
    output = root / "output" / "desk"
    return root, target, output


def _configure(root: Path, target: Target, config: str):
    return configure_target(
        target, root, env={"PATH": "/usr/bin"}, resolver=lambda name: None, runner=_make_writing(config)
    )


def test_configure_removes_stale_build_dir(tmp_path: Path) -> None:
    root, target, output = _setup(tmp_path)
    _configure(root, target, OLD)  # first configure: nothing to compare
    fw = output / "build" / "linux-firmware-20260810"
    (fw / ".stamp_built").parent.mkdir(parents=True)
    (fw / ".stamp_built").touch()

    result = _configure(root, target, OLD + "BR2_PACKAGE_LINUX_FIRMWARE_RTL_RTW88=y\n")
    assert result.rebuilt == ("linux-firmware-20260810",)
    assert not fw.exists()
    assert "will rebuild: linux-firmware-20260810" in str(result)


def test_first_configure_rebuilds_nothing(tmp_path: Path) -> None:
    root, target, _ = _setup(tmp_path)
    assert _configure(root, target, OLD).rebuilt == ()


# --- dependency changes (make show-info) ---------------------------------------

import json


def _info(**deps: list[str]) -> str:
    return json.dumps({name: {"dependencies": d} for name, d in deps.items()})


def test_dependency_change_rebuilds_the_package(tmp_path: Path) -> None:
    # 2026-09-29: avahi enabled -> cups now depends on it; cups's own options
    # did not change, so the option-based check missed it.
    build = _build(tmp_path, "cups-2.4.14", "avahi-0.8", "zlib-1.3.1")
    old = stale.parse_show_info(_info(cups=["dbus", "zlib"], zlib=[]))
    new = stale.parse_show_info(_info(cups=["avahi", "dbus", "zlib"], zlib=[], avahi=["dbus"]))
    assert [p.name for p in stale.dependency_changes(old, new, build)] == ["cups-2.4.14"]


def test_unchanged_dependencies_rebuild_nothing(tmp_path: Path) -> None:
    build = _build(tmp_path, "cups-2.4.14")
    deps = stale.parse_show_info(_info(cups=["dbus"]))
    assert stale.dependency_changes(deps, deps, build) == ()


def test_dependency_change_of_unbuilt_package_is_skipped(tmp_path: Path) -> None:
    build = _build(tmp_path)
    old = stale.parse_show_info(_info(cups=["dbus"]))
    new = stale.parse_show_info(_info(cups=["dbus", "avahi"]))
    assert stale.dependency_changes(old, new, build) == ()


def test_configure_rebuilds_on_dependency_change(tmp_path: Path) -> None:
    root, target, output = _setup(tmp_path)
    _configure(root, target, OLD)
    cups = output / "build" / "cups-2.4.14"
    cups.mkdir(parents=True)
    infos = iter([_info(cups=["dbus"]), _info(cups=["avahi", "dbus"])])
    result = configure_target(
        target, root, env={"PATH": "/usr/bin"}, resolver=lambda name: None,
        runner=_make_writing(OLD + "BR2_PACKAGE_AVAHI=y\n"),
        show_info=lambda source, out, env: next(infos),
    )
    assert result.rebuilt == ("cups-2.4.14",)
    assert not cups.exists()


def test_configure_survives_show_info_failure(tmp_path: Path) -> None:
    root, target, output = _setup(tmp_path)
    _configure(root, target, OLD)
    result = configure_target(
        target, root, env={"PATH": "/usr/bin"}, resolver=lambda name: None,
        runner=_make_writing(OLD), show_info=lambda source, out, env: None,
    )
    assert result.rebuilt == ()
