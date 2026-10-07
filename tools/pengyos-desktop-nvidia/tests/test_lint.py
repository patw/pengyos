"""The containment lint: each rule passes on a good tree and fires on a mutation."""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from pengyos_desktop_nvidia import lint
from pengyos_desktop_nvidia.errors import LintViolation, PengyOSError

REAL_TREE = pathlib.Path(__file__).resolve().parents[3] / "buildroot-external"

VERSION = "595.104.02"
RUN_HASH = "e421c202e4c79f58c3c7f3161bbe71454ebb3d88936f88205a0e327cd04c59ca"
DESKTOP = "pengyos_desktop_nvidia_defconfig"
SERVER = "pengyos_server_nvidia_defconfig"
REQUIRED = [(v, sym) for v, (_, syms) in lint.VARIANTS.items() for sym in syms]


def make_tree(root: pathlib.Path) -> pathlib.Path:
    """A minimal BR2_EXTERNAL tree that passes every offline rule."""
    configs = root / "configs"
    configs.mkdir(parents=True)
    (configs / "pengyos_desktop_defconfig").write_text(
        "BR2_x86_64=y\nBR2_PACKAGE_MESA3D=y\n"
    )
    (configs / "pengyos_server_defconfig").write_text("BR2_x86_64=y\n")
    (configs / DESKTOP).write_text(
        "BR2_x86_64=y\nBR2_PACKAGE_MESA3D=y\nBR2_PACKAGE_LIBGLVND=y\nBR2_PACKAGE_NVIDIA_OPEN=y\n"
        "BR2_PACKAGE_NVIDIA_OPEN_GRAPHICS=y\n"
    )
    (configs / SERVER).write_text("BR2_x86_64=y\nBR2_PACKAGE_NVIDIA_OPEN=y\n")
    (root / "Config.in").write_text(
        'menu "x"\n\tsource "$BR2_EXTERNAL_PENGYOS_PATH/package/nvidia-open/Config.in"\nendmenu\n'
    )
    pkg = root / "package" / "nvidia-open"
    pkg.mkdir(parents=True)
    (pkg / "Config.in").write_text('config BR2_PACKAGE_NVIDIA_OPEN\n\tbool "nvidia-open"\n')
    (pkg / "nvidia-open.mk").write_text(f"NVIDIA_OPEN_VERSION = {VERSION}\n")
    (pkg / "nvidia-open.hash").write_text(
        f"sha256  {RUN_HASH}  NVIDIA-Linux-x86_64-{VERSION}.run\n"
    )
    modprobe = root / "board" / "desktop" / "rootfs-overlay" / "etc" / "modprobe.d"
    modprobe.mkdir(parents=True)
    (modprobe / "60-pengyos-amdgpu.conf").write_text(
        "options amdgpu modeset=1 psr=1\ninstall nouveau /bin/true\n"
    )
    (root / "targets.toml").write_text(
        "".join(
            f'[[targets]]\nname = "{target}"\nbr_version = "2026.08"\n'
            f'external_tree = "."\ndefconfig = "{defconfig}"\n\n'
            for defconfig, (target, _) in lint.VARIANTS.items()
        )
    )
    return root


@pytest.fixture
def tree(tmp_path: pathlib.Path) -> pathlib.Path:
    return make_tree(tmp_path / "br2")


def fired(tree: pathlib.Path, **kwargs) -> set[str]:
    with pytest.raises(LintViolation) as exc:
        lint.lint_variant(tree, **kwargs)
    return {rule for rule, ok in exc.value.rules.items() if not ok}


def append(path: pathlib.Path, text: str) -> None:
    path.write_text(path.read_text() + text)


def test_clean_tree_passes(tree: pathlib.Path) -> None:
    report = lint.lint_variant(tree)
    assert report["ok"] is True
    assert set(report["rules"]) == set(lint.RULES) - {lint.RULE_DRIVER_RESOLVES}
    assert all(report["rules"].values())


def test_real_tree_passes() -> None:
    assert lint.lint_variant(REAL_TREE)["ok"] is True


@pytest.mark.parametrize(("defconfig", "symbol"), REQUIRED)
def test_variant_missing_a_required_symbol(tree: pathlib.Path, defconfig: str, symbol: str) -> None:
    variant = tree / "configs" / defconfig
    variant.write_text(variant.read_text().replace(f"{symbol}=y\n", ""))
    assert fired(tree) == {lint.RULE_DRIVER_IN_VARIANT}


@pytest.mark.parametrize("defconfig", list(lint.VARIANTS))
def test_variant_symbol_not_set(tree: pathlib.Path, defconfig: str) -> None:
    variant = tree / "configs" / defconfig
    variant.write_text(
        variant.read_text().replace("BR2_PACKAGE_NVIDIA_OPEN=y", "# BR2_PACKAGE_NVIDIA_OPEN is not set")
    )
    assert fired(tree) == {lint.RULE_DRIVER_IN_VARIANT}


def test_server_variant_needs_no_graphics(tree: pathlib.Path) -> None:
    assert "GRAPHICS" not in (tree / "configs" / SERVER).read_text()
    assert lint.lint_variant(tree)["ok"] is True


@pytest.mark.parametrize("default", ["pengyos_desktop_defconfig", "pengyos_server_defconfig"])
@pytest.mark.parametrize("line", ["BR2_PACKAGE_NVIDIA_OPEN=y\n", "BR2_PACKAGE_NVIDIA_OPEN_VULKAN=y\n"])
def test_driver_leaks_into_a_default(tree: pathlib.Path, default: str, line: str) -> None:
    append(tree / "configs" / default, line)
    assert fired(tree) == {lint.RULE_DRIVER_NOT_IN_DEFAULT}


def test_not_set_comment_in_default_is_no_leak(tree: pathlib.Path) -> None:
    append(tree / "configs" / "pengyos_server_defconfig", "# BR2_PACKAGE_NVIDIA_OPEN is not set\n")
    assert lint.lint_variant(tree)["ok"] is True


@pytest.mark.parametrize("defconfig", [DESKTOP, SERVER, "pengyos_desktop_defconfig"])
def test_legacy_driver_is_rejected_anywhere(tree: pathlib.Path, defconfig: str) -> None:
    append(tree / "configs" / defconfig, "BR2_PACKAGE_NVIDIA_DRIVER=y\n")
    assert fired(tree) == {lint.RULE_NO_LEGACY_DRIVER}


def test_package_not_sourced(tree: pathlib.Path) -> None:
    (tree / "Config.in").write_text('menu "x"\nendmenu\n')
    assert fired(tree) == {lint.RULE_PACKAGE_DECLARED}


def test_package_config_missing(tree: pathlib.Path) -> None:
    (tree / "package" / "nvidia-open" / "Config.in").unlink()
    assert fired(tree) == {lint.RULE_PACKAGE_DECLARED}


def test_package_declares_wrong_symbol(tree: pathlib.Path) -> None:
    (tree / "package" / "nvidia-open" / "Config.in").write_text("config BR2_PACKAGE_NVIDIA_OPEN_X\n")
    assert fired(tree) == {lint.RULE_PACKAGE_DECLARED}


def test_version_bumped_without_hash(tree: pathlib.Path) -> None:
    (tree / "package" / "nvidia-open" / "nvidia-open.mk").write_text("NVIDIA_OPEN_VERSION = 600.1\n")
    assert fired(tree) == {lint.RULE_VERSION_PINNED}


def test_target_not_registered(tree: pathlib.Path) -> None:
    (tree / "targets.toml").write_text("")
    assert fired(tree) == {lint.RULE_TARGET_REGISTERED}


def test_server_target_not_registered(tree: pathlib.Path) -> None:
    targets = tree / "targets.toml"
    targets.write_text(targets.read_text().replace('"pengyos-server-nvidia"', '"something-else"'))
    with pytest.raises(LintViolation) as exc:
        lint.lint_variant(tree)
    assert exc.value.errors == [f"pengyos-server-nvidia not declared in {targets}"]


def test_target_file_override(tree: pathlib.Path, tmp_path: pathlib.Path) -> None:
    other = tmp_path / "other.toml"
    other.write_text("")
    assert fired(tree, targets_toml=other) == {lint.RULE_TARGET_REGISTERED}


def test_every_violation_is_reported(tree: pathlib.Path) -> None:
    append(tree / "configs" / "pengyos_server_defconfig", "BR2_PACKAGE_NVIDIA_OPEN=y\n")
    (tree / "targets.toml").write_text("")
    with pytest.raises(LintViolation) as exc:
        lint.lint_variant(tree)
    assert len(exc.value.errors) == 3  # the leak + both NVIDIA targets unregistered


@pytest.mark.parametrize(
    "line",
    [
        "install nvidia /bin/true\n",
        "install nvidia_drm /bin/true\n",
        "  install nvidia-modeset /bin/false\n",
        "blacklist nvidia_uvm\n",
    ],
)
def test_board_stubs_out_the_driver(tree: pathlib.Path, line: str) -> None:
    append(tree / "board" / "desktop" / "rootfs-overlay" / "etc" / "modprobe.d" / "60-pengyos-amdgpu.conf", line)
    with pytest.raises(LintViolation) as exc:
        lint.lint_variant(tree)
    assert {rule for rule, ok in exc.value.rules.items() if not ok} == {lint.RULE_DRIVER_NOT_STUBBED}
    assert exc.value.errors[0].startswith("board/desktop/rootfs-overlay/etc/modprobe.d/60-pengyos-amdgpu.conf:3: ")


def test_comments_and_options_are_not_stubs(tree: pathlib.Path) -> None:
    append(
        tree / "board" / "desktop" / "rootfs-overlay" / "etc" / "modprobe.d" / "60-pengyos-amdgpu.conf",
        "# install nvidia /bin/true\noptions nvidia-drm modeset=1\n",
    )
    assert lint.lint_variant(tree)["ok"] is True


@pytest.mark.parametrize("defconfig", list(lint.VARIANTS))
def test_missing_variant_is_an_error(tree: pathlib.Path, defconfig: str) -> None:
    (tree / "configs" / defconfig).unlink()
    with pytest.raises(PengyOSError):
        lint.lint_variant(tree)


def test_missing_tree_is_an_error(tmp_path: pathlib.Path) -> None:
    with pytest.raises(PengyOSError):
        lint.lint_variant(tmp_path / "nope")


# --- rung (b): resolve through kconfig (make is injected) ----------------------


def fake_make(config_text: str, returncode: int = 0):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        out = next(a[2:] for a in argv if a.startswith("O="))
        pathlib.Path(out, ".config").write_text(config_text)
        return subprocess.CompletedProcess(argv, returncode, "", "")

    run.calls = calls
    return run


RESOLVED = "BR2_PACKAGE_NVIDIA_OPEN=y\nBR2_PACKAGE_NVIDIA_OPEN_GRAPHICS=y\nBR2_PACKAGE_LIBGLVND=y\n"


def test_resolves_passes(tree: pathlib.Path, tmp_path: pathlib.Path) -> None:
    make = fake_make(RESOLVED)
    report = lint.lint_variant(tree, buildroot=tmp_path, make=make)
    assert report["rules"][lint.RULE_DRIVER_RESOLVES] is True
    assert [argv[-1] for argv in make.calls] == list(lint.VARIANTS)
    assert all(f"BR2_EXTERNAL={tree.resolve()}" in argv for argv in make.calls)


def test_resolves_catches_a_silent_drop(tree: pathlib.Path, tmp_path: pathlib.Path) -> None:
    make = fake_make("# BR2_PACKAGE_NVIDIA_OPEN is not set\n")
    assert fired(tree, buildroot=tmp_path, make=make) == {lint.RULE_DRIVER_RESOLVES}


def test_resolves_catches_dropped_graphics(tree: pathlib.Path, tmp_path: pathlib.Path) -> None:
    make = fake_make("BR2_PACKAGE_NVIDIA_OPEN=y\nBR2_PACKAGE_LIBGLVND=y\n")
    with pytest.raises(LintViolation) as exc:
        lint.lint_variant(tree, buildroot=tmp_path, make=make)
    assert exc.value.errors == [
        "BR2_PACKAGE_NVIDIA_OPEN_GRAPHICS does not survive kconfig in pengyos_desktop_nvidia_defconfig "
        "(resolved to 'absent') -- an unmet 'depends on'?"
    ]


def test_resolve_make_failure(tree: pathlib.Path, tmp_path: pathlib.Path) -> None:
    with pytest.raises(PengyOSError):
        lint.lint_variant(tree, buildroot=tmp_path, make=fake_make("", returncode=2))


def test_parse_config() -> None:
    assert lint.parse_config("BR2_A=y\n# BR2_B is not set\nBR2_C=\"x\"\n") == {
        "BR2_A": "y",
        "BR2_B": "not set",
        "BR2_C": '"x"',
    }


@pytest.mark.skipif(not os.environ.get("PENGYOS_IT"), reason="opt-in; set PENGYOS_IT=1 and PENGYOS_BUILDROOT")
def test_real_tree_resolves_against_buildroot() -> None:
    buildroot = pathlib.Path(os.environ["PENGYOS_BUILDROOT"])
    report = lint.lint_variant(REAL_TREE, buildroot=buildroot)
    assert report["rules"][lint.RULE_DRIVER_RESOLVES] is True
