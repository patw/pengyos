"""``lint`` -- prove the NVIDIA driver is contained to the opt-in editions.

The NVIDIA editions are composed from the ``configs/layers/`` stack:
``pengyos_server_nvidia_defconfig`` (core + server + nvidia-compute) and
``pengyos_desktop_nvidia_defconfig`` (core + server + desktop + nvidia-compute +
nvidia). The driver is the in-tree ``package/nvidia-open`` recipe: NVIDIA's open
GPU kernel modules plus the matching proprietary userspace, both from one
``.run``. The design rule (SPEC §2.5) is *containment*: the driver ships only in
those opt-in images.

Rules (one clear error each):

1. **driver-in-variant** -- each NVIDIA defconfig sets ``BR2_PACKAGE_NVIDIA_OPEN=y``;
   the Desktop's also sets ``BR2_PACKAGE_NVIDIA_OPEN_GRAPHICS=y`` and
   ``BR2_PACKAGE_LIBGLVND=y`` (graphics depends on libglvnd; without it kconfig
   silently drops the option and the desktop has no NVIDIA EGL).
2. **driver-not-in-default** -- no ``BR2_PACKAGE_NVIDIA_OPEN*`` symbol appears in
   any other ``configs/*_defconfig``.
3. **no-legacy-driver** -- Buildroot's own ``nvidia-driver`` (the 390 legacy
   branch: no Turing+, no GBM, does not build on our kernel) is set nowhere.
4. **package-declared** -- ``package/nvidia-open/Config.in`` declares the symbol
   and the tree's ``Config.in`` sources it (else the symbol does not exist).
5. **version-pinned** -- the ``.hash`` pins the ``.run`` of the version the
   ``.mk`` names (a version bump without a new hash fails the download).
6. **target-registered** -- ``pengyos-server-nvidia`` and ``pengyos-desktop-nvidia``
   are declared in the tree's ``targets.toml``.
7. **driver-resolves** (only with ``--buildroot``) -- each NVIDIA defconfig,
   resolved by Buildroot's kconfig into a scratch output dir, keeps its required
   symbols.
   Rules 1-6 and 8 read files; this one catches an unmet ``depends on``.
8. **driver-not-stubbed** -- no ``modprobe.d`` file under ``board/`` routes an
   ``nvidia*`` module to ``install ...`` or ``blacklist``s it. Boards are shared
   with the NVIDIA editions (Desktop NVIDIA reuses ``board/desktop``), so such a
   line silently leaves the driver unloaded there (26.10.6: simpledrm, one
   monitor).
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .errors import LintViolation, PengyOSError

PACKAGE = "nvidia-open"
DRIVER_SYMBOL = "BR2_PACKAGE_NVIDIA_OPEN"
GRAPHICS_SYMBOL = "BR2_PACKAGE_NVIDIA_OPEN_GRAPHICS"

#: Each NVIDIA defconfig -> (its target in targets.toml, the symbols it must set).
VARIANTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "pengyos_server_nvidia_defconfig": ("pengyos-server-nvidia", (DRIVER_SYMBOL,)),
    "pengyos_desktop_nvidia_defconfig": (
        "pengyos-desktop-nvidia",
        (DRIVER_SYMBOL, GRAPHICS_SYMBOL, "BR2_PACKAGE_LIBGLVND"),
    ),
}

RULE_DRIVER_IN_VARIANT = "driver-in-variant"
RULE_DRIVER_NOT_IN_DEFAULT = "driver-not-in-default"
RULE_NO_LEGACY_DRIVER = "no-legacy-driver"
RULE_PACKAGE_DECLARED = "package-declared"
RULE_VERSION_PINNED = "version-pinned"
RULE_TARGET_REGISTERED = "target-registered"
RULE_DRIVER_RESOLVES = "driver-resolves"
RULE_DRIVER_NOT_STUBBED = "driver-not-stubbed"

RULES: tuple[str, ...] = (
    RULE_DRIVER_IN_VARIANT,
    RULE_DRIVER_NOT_IN_DEFAULT,
    RULE_NO_LEGACY_DRIVER,
    RULE_PACKAGE_DECLARED,
    RULE_VERSION_PINNED,
    RULE_TARGET_REGISTERED,
    RULE_DRIVER_RESOLVES,
    RULE_DRIVER_NOT_STUBBED,
)

# Any assignment of a driver symbol (=y, =m, ="...") counts as a leak; the
# ``\w*`` covers sub-options such as BR2_PACKAGE_NVIDIA_OPEN_VULKAN.
_DRIVER_APPEARS = re.compile(r"^\s*" + DRIVER_SYMBOL + r"\w*\s*=", re.MULTILINE)
_LEGACY_SET = re.compile(r"^\s*BR2_PACKAGE_NVIDIA_DRIVER\w*\s*=\s*y\b", re.MULTILINE)
# modprobe treats '-' and '_' alike, so this covers nvidia-drm and nvidia_drm.
_DRIVER_STUBBED = re.compile(r"^\s*(install|blacklist)\s+nvidia[\w-]*\b", re.MULTILINE)


def _is_set(text: str, symbol: str) -> bool:
    return re.search(r"^\s*" + re.escape(symbol) + r"\s*=\s*y\b", text, re.MULTILINE) is not None


def _read(path: Path) -> str:
    if not path.is_file():
        raise PengyOSError(f"not found: {path}")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Offline rules
# ---------------------------------------------------------------------------


def check_driver_in_variant(variant: str, variant_text: str) -> list[str]:
    return [
        f"{symbol} is not set to 'y' in {variant}"
        for symbol in VARIANTS[variant][1]
        if not _is_set(variant_text, symbol)
    ]


def check_driver_not_in_default(configs: Path) -> list[str]:
    problems = []
    for path in sorted(configs.glob("*_defconfig")):
        if path.name in VARIANTS:
            continue
        if _DRIVER_APPEARS.search(path.read_text(encoding="utf-8")):
            problems.append(f"{DRIVER_SYMBOL}* appears in {path.name}")
    return problems


def check_no_legacy_driver(configs: Path) -> list[str]:
    return [
        f"legacy BR2_PACKAGE_NVIDIA_DRIVER* is set in {path.name} (the 390 branch "
        "cannot drive Turing+ GPUs or a Wayland session; use nvidia-open)"
        for path in sorted(configs.glob("*_defconfig"))
        if _LEGACY_SET.search(path.read_text(encoding="utf-8"))
    ]


def check_package_declared(repo: Path) -> list[str]:
    problems = []
    pkg_config = repo / "package" / PACKAGE / "Config.in"
    if not pkg_config.is_file():
        return [f"package recipe missing: {pkg_config}"]
    if not re.search(r"^config\s+" + DRIVER_SYMBOL + r"\s*$", _read(pkg_config), re.MULTILINE):
        problems.append(f"{pkg_config} does not declare {DRIVER_SYMBOL}")
    source_line = f"package/{PACKAGE}/Config.in"
    top = repo / "Config.in"
    if not top.is_file() or not any(
        line.strip().startswith("source") and source_line in line
        for line in _read(top).splitlines()
    ):
        problems.append(f"{top} does not source {source_line}")
    return problems


def check_version_pinned(repo: Path) -> list[str]:
    pkg = repo / "package" / PACKAGE
    mk, hashes = pkg / f"{PACKAGE}.mk", pkg / f"{PACKAGE}.hash"
    if not mk.is_file() or not hashes.is_file():
        return [f"{PACKAGE}.mk / {PACKAGE}.hash missing under {pkg}"]
    m = re.search(r"^NVIDIA_OPEN_VERSION\s*=\s*(\S+)", _read(mk), re.MULTILINE)
    if not m:
        return [f"{mk} does not set NVIDIA_OPEN_VERSION"]
    run = f"NVIDIA-Linux-x86_64-{m.group(1)}.run"
    if not re.search(r"^sha256\s+[0-9a-f]{64}\s+" + re.escape(run) + r"\s*$", _read(hashes), re.MULTILINE):
        return [f"{hashes} has no sha256 for {run} (version bumped without a new hash?)"]
    return []


def check_driver_not_stubbed(repo: Path) -> list[str]:
    return [
        f"{path.relative_to(repo)}:{text.count(chr(10), 0, m.start()) + 1}: "
        f"'{m.group(0).strip()}' keeps the NVIDIA driver from loading in the "
        "NVIDIA editions, which share this board"
        for path in sorted((repo / "board").glob("**/modprobe.d/*.conf"))
        for text in [path.read_text(encoding="utf-8")]
        for m in _DRIVER_STUBBED.finditer(text)
    ]


def _target_names(text: str) -> list[str]:
    """Target names in a targets.toml (a plain TOML read; no schema needed)."""
    import tomllib

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PengyOSError(f"targets.toml is not valid TOML: {exc}") from exc
    return [t.get("name") for t in data.get("targets", []) if isinstance(t, dict)]


def check_target_registered(targets_toml: Path) -> list[str]:
    if not targets_toml.is_file():
        return [f"targets file not found: {targets_toml}"]
    try:
        names = _target_names(targets_toml.read_text(encoding="utf-8"))
    except PengyOSError as exc:
        return [str(exc)]
    return [
        f"{target} not declared in {targets_toml}"
        for target, _ in VARIANTS.values()
        if target not in names
    ]


# ---------------------------------------------------------------------------
# Rung (b): resolve through Buildroot's kconfig
# ---------------------------------------------------------------------------


def parse_config(text: str) -> dict[str, str]:
    """``.config`` -> ``{symbol: value}``; ``# X is not set`` -> ``"not set"``."""
    config: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("BR2_") and "=" in line:
            name, _, value = line.partition("=")
            config[name.strip()] = value.strip()
        elif line.startswith("# ") and line.endswith(" is not set"):
            config[line[2:].split()[0]] = "not set"
    return config


def resolve_variant_config(
    repo: Path,
    buildroot: Path,
    variant: str,
    *,
    make: Callable[..., subprocess.CompletedProcess] | None = None,
) -> dict[str, str]:
    """``make <variant>`` into a scratch ``O=`` dir; return the resolved ``.config``.

    Only kconfig runs (no toolchain, nothing compiled), and nothing is written
    into the Buildroot checkout or any real output dir.
    """
    if not buildroot.is_dir():
        raise PengyOSError(f"buildroot tree not found: {buildroot}")
    with tempfile.TemporaryDirectory(prefix="nvidia-lint-") as out:
        run = (make or subprocess.run)(
            ["make", "-C", str(buildroot), f"O={out}", f"BR2_EXTERNAL={repo}", variant],
            check=False,
            capture_output=True,
            text=True,
        )
        if run.returncode != 0:
            raise PengyOSError(
                f"make {variant} failed (exit {run.returncode}) against {buildroot}"
            )
        config = Path(out) / ".config"
        if not config.is_file():
            raise PengyOSError(f"make did not produce {config}")
        return parse_config(config.read_text(encoding="utf-8"))


def check_driver_resolves(variant: str, config: dict[str, str]) -> list[str]:
    return [
        f"{symbol} does not survive kconfig in {variant} (resolved to "
        f"{config.get(symbol, 'absent')!r}) -- an unmet 'depends on'?"
        for symbol in VARIANTS[variant][1]
        if config.get(symbol) != "y"
    ]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def default_targets_toml(repo_path: Path) -> Path:
    """``<tree>/targets.toml`` if present, else the repository root's canonical
    one (the BR2_EXTERNAL tree is ``<repo>/buildroot-external``)."""
    in_tree = repo_path / "targets.toml"
    return in_tree if in_tree.is_file() else repo_path.parent / "targets.toml"


def lint_variant(
    repo: str | os.PathLike[str],
    *,
    buildroot: str | os.PathLike[str] | None = None,
    targets_toml: str | os.PathLike[str] | None = None,
    make: Callable[..., subprocess.CompletedProcess] | None = None,
) -> dict:
    """Run every rule against the BR2_EXTERNAL tree ``repo``.

    Raises :class:`LintViolation` listing every failure (``rules`` maps each rule
    id to pass/fail); returns a report dict when clean.
    """
    repo_path = Path(repo).resolve()
    if not repo_path.is_dir():
        raise PengyOSError(f"BR2_EXTERNAL tree not found: {repo_path}")
    configs = repo_path / "configs"
    if not configs.is_dir():
        raise PengyOSError(f"no configs/ dir in {repo_path}")
    variant_texts = {variant: _read(configs / variant) for variant in VARIANTS}
    targets = Path(targets_toml) if targets_toml is not None else default_targets_toml(repo_path)

    results: dict[str, list[str]] = {
        RULE_DRIVER_IN_VARIANT: [
            problem
            for variant, text in variant_texts.items()
            for problem in check_driver_in_variant(variant, text)
        ],
        RULE_DRIVER_NOT_IN_DEFAULT: check_driver_not_in_default(configs),
        RULE_NO_LEGACY_DRIVER: check_no_legacy_driver(configs),
        RULE_PACKAGE_DECLARED: check_package_declared(repo_path),
        RULE_VERSION_PINNED: check_version_pinned(repo_path),
        RULE_TARGET_REGISTERED: check_target_registered(targets),
        RULE_DRIVER_NOT_STUBBED: check_driver_not_stubbed(repo_path),
    }
    if buildroot is not None:
        results[RULE_DRIVER_RESOLVES] = [
            problem
            for variant in VARIANTS
            for problem in check_driver_resolves(
                variant, resolve_variant_config(repo_path, Path(buildroot), variant, make=make)
            )
        ]

    rules = {rule: not problems for rule, problems in results.items()}
    errors = [problem for problems in results.values() for problem in problems]
    if errors:
        raise LintViolation(errors, rules=rules)
    return {
        "ok": True,
        "rules": rules,
        "variant_defconfigs": list(VARIANTS),
        "buildroot": None if buildroot is None else str(buildroot),
    }
