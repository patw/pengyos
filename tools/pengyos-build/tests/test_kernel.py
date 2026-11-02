"""A kernel version change rebuilds the module packages and drops the old module tree."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pengyos_build import kernel
from pengyos_build.configure import configure_target
from pengyos_build.targets import Target


def _config(version: str | None) -> str:
    if version is None:
        return "BR2_x86_64=y\n# BR2_LINUX_KERNEL is not set\n"
    return (
        "BR2_x86_64=y\nBR2_LINUX_KERNEL=y\nBR2_LINUX_KERNEL_CUSTOM_VERSION=y\n"
        f'BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE="{version}"\n'
        f'BR2_LINUX_KERNEL_VERSION="{version}"\nBR2_PACKAGE_NVIDIA_OPEN=y\n'
    )


# --- the pieces ------------------------------------------------------------------


def test_kernel_version_reads_the_resolved_symbol() -> None:
    assert kernel.kernel_version(_config("7.3.1")) == "7.3.1"


def test_no_kernel_no_version() -> None:
    assert kernel.kernel_version(_config(None)) is None


def test_module_packages_are_the_dependents_of_linux() -> None:
    deps = {
        "linux": {"host-kmod", "host-openssl"},
        "nvidia-open": {"linux", "libglvnd"},
        "v4l2loopback": {"linux"},
        "linux-firmware": set(),
        "mesa3d": {"libdrm"},
    }
    assert kernel.module_packages(deps) == {"nvidia-open", "v4l2loopback"}


def _tree(target: Path, *releases: str) -> None:
    (target / "usr" / "lib" / "modules").mkdir(parents=True, exist_ok=True)
    (target / "lib").symlink_to("usr/lib")  # merged-usr, as every edition is
    for release in releases:
        (target / "usr/lib/modules" / release / "kernel").mkdir(parents=True)


def test_old_module_tree_found_once_through_the_merged_usr_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    _tree(target, "7.2.13", "7.3.1")
    assert kernel.old_module_trees(target, "7.2.13", "7.3.1") == (
        (target / "usr/lib/modules/7.2.13").resolve(),
    )


def test_local_version_suffix_counts_as_the_same_kernel(tmp_path: Path) -> None:
    target = tmp_path / "target"
    _tree(target, "7.2.13-pengyos", "7.3.1-pengyos")
    assert [p.name for p in kernel.old_module_trees(target, "7.2.13", "7.3.1")] == [
        "7.2.13-pengyos"
    ]


def test_a_prefix_of_another_version_is_not_that_version(tmp_path: Path) -> None:
    # 7.3.1 -> 7.3.10 must keep 7.3.10; 7.3 -> 7.3.1 must not take 7.3.1.
    target = tmp_path / "target"
    _tree(target, "7.3.1", "7.3.10")
    assert [p.name for p in kernel.old_module_trees(target, "7.3.1", "7.3.10")] == ["7.3.1"]
    assert kernel.old_module_trees(target, "7.3", "7.3.1") == ()


def test_no_module_dir_no_trees(tmp_path: Path) -> None:
    assert kernel.old_module_trees(tmp_path / "target", "7.2.13", "7.3.1") == ()


# --- through configure ---------------------------------------------------------------


def _target(external: Path) -> Target:
    return Target(
        name="desk", br_version="2026.08", external_tree=external,
        defconfig="d_defconfig", board="",
    )


def _make_writing(config: str):
    def spawner(argv, **kwargs):
        out = next(a[2:] for a in argv if a.startswith("O="))
        Path(out, ".config").write_text(config)
        return subprocess.CompletedProcess(list(argv), 0)

    return spawner


def _info() -> str:
    deps = {"linux": ["host-kmod"], "nvidia-open": ["linux", "libglvnd"],
            "v4l2loopback": ["linux"], "libglvnd": []}
    return json.dumps({name: {"dependencies": d} for name, d in deps.items()})


def _built_tree(tmp_path: Path, version: str):
    root = tmp_path / "root"
    (root / "buildroot-2026.08").mkdir(parents=True)
    external = tmp_path / "ext"
    external.mkdir()
    target = _target(external)
    output = root / "output" / "desk"
    configure_target(target, root, env={"PATH": "/usr/bin"}, resolver=lambda n: None,
                     runner=_make_writing(_config(version)), show_info=lambda s, o, e: _info())
    for d in (f"linux-{version}", "nvidia-open-615.78.08", "v4l2loopback-0.15.1", "libglvnd-1.7.0"):
        (output / "build" / d).mkdir(parents=True)
    _tree(output / "target", version)
    (output / "target/usr/lib/modules" / version / "updates").mkdir()
    return root, target, output


def _reconfigure(root: Path, target: Target, version: str, info=_info):
    return configure_target(target, root, env={"PATH": "/usr/bin"}, resolver=lambda n: None,
                            runner=_make_writing(_config(version)),
                            show_info=lambda s, o, e: info())


def test_kernel_bump_rebuilds_module_packages_and_drops_old_tree(tmp_path: Path) -> None:
    # 2026-11-02, 7.2.13 -> 7.3.1: nvidia-open and v4l2loopback kept their 7.2.13
    # builds, and usr/lib/modules/7.2.13 would have shipped beside 7.3.1.
    root, target, output = _built_tree(tmp_path, "7.2.13")
    result = _reconfigure(root, target, "7.3.1")
    assert set(result.rebuilt) == {"nvidia-open-615.78.08", "v4l2loopback-0.15.1"}
    assert not (output / "build/nvidia-open-615.78.08").exists()
    assert not (output / "build/v4l2loopback-0.15.1").exists()
    assert (output / "build/libglvnd-1.7.0").exists()
    assert not (output / "target/usr/lib/modules/7.2.13").exists()
    text = str(result)
    assert "kernel 7.2.13 -> 7.3.1: rebuilding" in text
    assert "removed target/usr/lib/modules/7.2.13" in text


def test_same_kernel_touches_nothing(tmp_path: Path) -> None:
    root, target, output = _built_tree(tmp_path, "7.3.1")
    result = _reconfigure(root, target, "7.3.1")
    assert result.rebuilt == ()
    assert (output / "build/nvidia-open-615.78.08").exists()
    assert (output / "target/usr/lib/modules/7.3.1/updates").is_dir()
    assert " -> 7.3.1" not in str(result)


def test_kernel_bump_without_show_info_says_clean_build(tmp_path: Path) -> None:
    root, target, output = _built_tree(tmp_path, "7.2.13")
    result = _reconfigure(root, target, "7.3.1", info=lambda: None)
    assert "CLEAN BUILD NEEDED: kernel 7.2.13 -> 7.3.1" in str(result)
    # The old tree is dead either way, so it still goes.
    assert not (output / "target/usr/lib/modules/7.2.13").exists()


def test_turning_the_kernel_off_removes_nothing(tmp_path: Path) -> None:
    root, target, output = _built_tree(tmp_path, "7.2.13")
    _reconfigure(root, target, None)
    assert (output / "target/usr/lib/modules/7.2.13").is_dir()
    assert (output / "build/nvidia-open-615.78.08").exists()
