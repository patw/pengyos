"""``wire`` — install the emitted recipes + defconfig + board overlay + native
deps into a ``BR2_EXTERNAL`` tree, wire ``Config.in``, and register the
``pengyos-desktop`` target idempotently.


``wire`` is the *assembly* step that closes the gap between the emitted recipes
(see :mod:`.emit`) and a buildable ``BR2_EXTERNAL`` tree:

1. it installs the emitted COSMIC cargo recipes from ``--sources/package/`` into
   the tree's ``package/`` dir (a merge — the tree's own recipes are kept);
2. it installs the sibling native-dep recipes (the ``pengyos-cosmic-native-deps``
   catalogue) into the tree's ``package/`` dir, so every ``BR2_PACKAGE_*`` the
   defconfig references resolves;
3. it installs the desktop defconfig(s) from ``--sources/configs/`` into the
   tree's ``configs/`` dir;
4. it installs the board overlay(s) from ``--sources/board/`` into the tree's
   ``board/`` dir;
5. it wires the tree's ``Config.in`` — adding a ``source`` line for every
   installed package (idempotent, so a re-run never duplicates a line);
6. it registers the ``pengyos-desktop`` target in ``targets.toml`` (idempotent).

Every step is idempotent: wiring an already-wired tree is a no-op (byte-
identical ``Config.in`` / ``targets.toml``), so ``wire`` is safe to re-run.

Runtime depends only on the Python standard library; the native-dep catalogue is
imported lazily (it lives on ``sys.path`` only under pytest / an installed user).
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# --- BR2_EXTERNAL tree layout ------------------------------------------------

#: The ``package/`` dir a recipe lives in (relative to the tree root).
_PACKAGE_DIR = "package"
#: The ``configs/`` dir a defconfig lives in.
_CONFIGS_DIR = "configs"
#: The ``board/`` dir a board overlay lives in.
_BOARD_DIR = "board"
#: The ``Config.in`` file at the tree root.
_CONFIG_IN = "Config.in"

#: The Buildroot env var that names the external tree (set by ``external.desc``).
_BR2_EXTERNAL_ENV = "$BR2_EXTERNAL_PENGYOS_PATH"

#: The ``endmenu`` marker in the tree's ``Config.in`` (the section is wired
#: just before it).
_ENDMENU = "endmenu"

#: The comment that opens the single COSMIC-recipes section in ``Config.in``.
_SECTION_COMMENT = (
    "\t# COSMIC cargo recipes (pengyos-cosmic-build emit) — pinned to vendored "
    "digests."
)

#: The ``[[targets]]`` block the desktop target is registered as.
_TARGET_NAME = "pengyos-desktop"
_TARGET_BR_VERSION = "2026.08"
_TARGET_EXTERNAL_TREE = "buildroot-external"
_TARGET_DEFCONFIG = "pengyos_desktop_defconfig"


class WireError(ValueError):
    """A wiring step failed (a required input is missing).

    Raised by :func:`wire` so a missing input is a *reported* condition (a clean
    CLI exit), never a crash. A ``ValueError`` subclass so the CLI's
    ``except (ValueError, ...)`` catches it (see :mod:`.cli`).
    """


@dataclass(frozen=True)
class WireReport:
    """The outcome of :func:`wire`: what was installed into the tree.

    ``installed_packages`` is the sorted set of package dirs now present under
    the tree's ``package/`` (emitted + native-dep). ``defconfigs`` / ``boards``
    are the installed defconfig / board names. ``config_in_changed`` is whether
    ``Config.in`` was rewritten (new ``source`` lines added); ``target_registered``
    is whether the ``pengyos-desktop`` target was (re)written.
    """

    repo: Path
    installed_packages: tuple[str, ...]
    defconfigs: tuple[str, ...]
    boards: tuple[str, ...]
    config_in_changed: bool = False
    target_registered: bool = False

    def as_dict(self) -> dict:
        """A deterministic, JSON-serialisable view."""
        return {
            "command": "wire",
            "ok": True,
            "repo": str(self.repo),
            "installed_packages": list(self.installed_packages),
            "defconfigs": list(self.defconfigs),
            "boards": list(self.boards),
            "config_in_changed": self.config_in_changed,
            "target_registered": self.target_registered,
        }


# --- file-tree install ------------------------------------------------------


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy the *src* file tree into *dst*, merging (overwriting by file).

    A shallow merge: every file under *src* is written to the matching path
    under *dst* (parents created as needed); files already present at *dst* are
    overwritten with the *src* bytes. Directories that exist only at *dst* are
    left alone. This is idempotent — re-running over identical bytes is a no-op.
    """
    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir():
            _copy_tree(child, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target)


def _copy_file(src: Path, dst: Path) -> None:
    """Copy a single file into *dst* (parents created). Idempotent."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _installed_package_dirs(repo_pkg: Path) -> list[str]:
    """The sorted names of the package dirs now under *repo_pkg*."""
    if not repo_pkg.is_dir():
        return []
    return sorted(
        d.name
        for d in repo_pkg.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


# --- Config.in wiring -------------------------------------------------------


def _source_line(pkg: str) -> str:
    """The ``source`` line that exposes one package's ``Config.in``."""
    return f'\tsource "{_BR2_EXTERNAL_ENV}/package/{pkg}/Config.in"'


def _installed_source_lines(config_in: str) -> set[str]:
    """The ``source ".../Config.in"`` lines already present in *config_in*.

    A Buildroot ``source`` line quotes the path, so it ends with
    ``Config.in"`` (the closing quote) — the matcher accounts for that.
    """
    return {
        line.strip()
        for line in config_in.splitlines()
        if line.strip().startswith('source "') and line.strip().endswith('Config.in"')
    }


def add_config_in_sources(config_in: str, packages: Iterable[str]) -> tuple[str, bool]:
    """Return *config_in* with a ``source`` line for each *package* added.

    Idempotent: a package whose ``source`` line is already present is left
    alone, and a re-run with the same package set yields byte-identical output.
    The source lines live in a single dedicated section (opened by
    :data:`_SECTION_COMMENT`) inserted just before the trailing ``endmenu``; an
    incremental run (a package added later) rewrites that one section in place
    rather than stacking a second one. Returns the rewritten text and whether
    anything changed.

    *packages* is iterated in sorted order for deterministic output.
    """
    packages = sorted(packages)
    wanted = [_source_line(pkg) for pkg in packages]
    already = _installed_source_lines(config_in)  # stripped canonical lines
    # Compare on the stripped form: ``_source_line`` emits the canonical
    # ``<tab>source "..."`` line, but the matcher normalises indentation, so a
    # line present under a different indent still counts as already sourced.
    missing = [line for line in wanted if line.strip() not in already]
    if not missing:
        return config_in, False

    had_trailing_newline = config_in.endswith("\n")
    body = config_in.rstrip("\n")
    lines = body.split("\n")

    # Locate the section opener (if any) and the endmenu (the last non-empty
    # line). The section spans the opener comment through the blank line before
    # endmenu. A real tree's ``Config.in`` may already carry a *different*
    # "COSMIC cargo recipes" comment (the recipes lane's placeholder), so the
    # opener is matched by that phrase rather than an exact string — wire reuses
    # whatever opener is there rather than stacking a second section.
    comment_idx = next(
        (i for i, ln in enumerate(lines) if _is_section_comment(ln)),
        None,
    )
    endmenu_idx = len(lines) - 1
    while endmenu_idx > 0 and lines[endmenu_idx] == "":
        endmenu_idx -= 1
    assert lines[endmenu_idx] == _ENDMENU, "Config.in has no endmenu to wire under"

    # The section owns the source lines for the packages *this* lane adds (the
    # cargo recipes). On every rewrite it is rebuilt as: the opener comment,
    # then the source lines that already live *inside this section* (the cargo
    # lines added in a prior run — a package added earlier still belongs here),
    # then the genuinely-new wanted lines (wanted packages not yet sourced
    # anywhere). Anything between the opener and ``endmenu`` that is not a source
    # line is dropped (e.g. the recipes lane's stale two-line placeholder
    # comment); the trailing blank line is restored.
    #
    # A wanted package whose ``source`` line already lives *outside* this section
    # (e.g. a native dep sourced in an earlier section) is left where it is: it
    # is in *already* but not in this section, so it is neither in
    # ``section_sources`` nor in ``new_lines`` — it is not re-added here.
    # Re-adding it would source its ``Config.in`` twice, which redefines its
    # kconfig ``config`` entries and fails the Buildroot parse.
    section_sources = [
        ln
        for ln in lines[comment_idx:endmenu_idx]
        if ln.strip().startswith('source "') and ln.strip().endswith('Config.in"')
    ]
    new_lines = [line for line in wanted if line.strip() not in already]
    if comment_idx is not None:
        section = [lines[comment_idx], *section_sources, *new_lines, ""]
        lines = lines[:comment_idx] + section + lines[endmenu_idx:]
    else:
        # No section yet: build one before endmenu. Only the not-yet-sourced
        # wanted lines go in (adding an already-sourced line here would source
        # its ``Config.in`` twice); the rest of the file is untouched.
        section = [_SECTION_COMMENT, *new_lines, ""]
        lines = lines[:endmenu_idx] + section + lines[endmenu_idx:]

    out = "\n".join(lines)
    return out + ("\n" if had_trailing_newline else ""), True


def _is_section_comment(line: str) -> bool:
    """Whether *line* opens the COSMIC-recipes section (a ``# ...`` comment).

    Matched by phrase rather than an exact string so a tree already carrying the
    recipes lane's placeholder comment ("...pengyos-cosmic-pkg / -recipes...")
    is recognised as already having the section.
    """
    stripped = line.strip()
    return stripped.startswith("#") and "COSMIC cargo recipes" in stripped


# --- targets.toml registration ----------------------------------------------


def _target_block(defconfig: str) -> str:
    """The ``[[targets]]`` block for the desktop target."""
    return (
        "\n[[targets]]\n"
        f'name = "{_TARGET_NAME}"\n'
        f'br_version = "{_TARGET_BR_VERSION}"\n'
        f'external_tree = "{_TARGET_EXTERNAL_TREE}"\n'
        f'defconfig = "{defconfig}"\n'
    )


def _has_target(targets_text: str, name: str = _TARGET_NAME) -> bool:
    """Whether *targets_text* already registers *name* (a ``[[targets]]`` entry)."""
    return f'name = "{name}"' in targets_text


def register_target(targets_text: str, defconfig: str = _TARGET_DEFCONFIG) -> tuple[str, bool]:
    """Return *targets_text* with the desktop target registered.

    Idempotent: if the target is already registered the text is returned
    unchanged. Otherwise the ``[[targets]]`` block is appended (the file is
    normalised to end in exactly one trailing newline first). Returns the
    rewritten text and whether anything changed.
    """
    if _has_target(targets_text):
        return targets_text, False
    body = targets_text.rstrip("\n") + "\n"
    return body + _target_block(defconfig), True


# --- the native-dep recipes -------------------------------------------------


def _native_dep_package_names() -> tuple[str, ...]:
    """The native-dep recipe names from the committed catalogue (sorted)."""
    from pengyos_cosmic_native_deps.recipes import default_recipe_catalogue

    return tuple(sorted(r.name for r in default_recipe_catalogue()))


# --- the orchestrator -------------------------------------------------------


def wire(
    repo: Path | str,
    sources: Path | str,
    *,
    targets: Path | str | None = None,
    defconfig: str = _TARGET_DEFCONFIG,
    native_dep_names: tuple[str, ...] | None = None,
) -> WireReport:
    """Install everything into the ``BR2_EXTERNAL`` tree and register the target.

    * ``repo`` — the ``BR2_EXTERNAL`` tree root (its ``package/`` / ``configs/``
      / ``board/`` / ``Config.in`` are written into).
    * ``sources`` — the staging tree: ``package/`` (emitted recipes),
      ``configs/`` (the desktop defconfig(s)), ``board/`` (the board overlay(s)).
    * ``targets`` — the ``targets.toml`` path the desktop target is registered
      in; ``None`` skips registration (used by tests that only check the tree).
    * ``defconfig`` — the defconfig name the target resolves to.
    * ``native_dep_names`` — the native-dep recipe names to install; ``None``
      loads them from the committed catalogue.

    Raises :class:`WireError` when ``sources`` is missing. The tree is wired
    even if ``targets`` is ``None`` (registration is a separate, opt-in step).
    """
    repo = Path(repo)
    sources = Path(sources)
    if not sources.is_dir():
        raise WireError(f"sources tree not found: {sources}")

    native_dep_names = native_dep_names or _native_dep_package_names()

    # 1. Install the emitted COSMIC cargo recipes (sources/package/).
    source_pkg = sources / _PACKAGE_DIR
    repo_pkg = repo / _PACKAGE_DIR
    if source_pkg.is_dir():
        _copy_tree(source_pkg, repo_pkg)

    # 2. Install the sibling native-dep recipes (via the native-deps emit).
    _install_native_deps(repo_pkg, native_dep_names)

    # 3. Install the desktop defconfig(s).
    source_configs = sources / _CONFIGS_DIR
    repo_configs = repo / _CONFIGS_DIR
    defconfigs = _install_files(source_configs, repo_configs)

    # 4. Install the board overlay(s).
    source_board = sources / _BOARD_DIR
    repo_board = repo / _BOARD_DIR
    boards = _install_dirs(source_board, repo_board)

    # 5. Wire Config.in (add a source line per installed package).
    config_in_path = repo / _CONFIG_IN
    config_in_changed = False
    installed = _installed_package_dirs(repo_pkg)
    if config_in_path.is_file():
        current = config_in_path.read_text(encoding="utf-8")
        updated, changed = add_config_in_sources(current, installed)
        if changed:
            config_in_path.write_text(updated, encoding="utf-8")
            config_in_changed = True

    # 6. Register the desktop target (opt-in; a missing targets.toml is not an
    #    error — the tree is still wired).
    target_registered = False
    if targets is not None:
        target_registered = _register_target_file(Path(targets), defconfig)

    return WireReport(
        repo=repo,
        installed_packages=tuple(installed),
        defconfigs=tuple(sorted(defconfigs)),
        boards=tuple(sorted(boards)),
        config_in_changed=config_in_changed,
        target_registered=target_registered,
    )


def _install_native_deps(repo_pkg: Path, native_dep_names: tuple[str, ...]) -> None:
    """Install the native-dep recipes into *repo_pkg* (via the native-deps emit)."""
    from pengyos_cosmic_native_deps.emit import emit as native_emit
    from pengyos_cosmic_native_deps.recipes import default_recipe_catalogue

    scratch = repo_pkg.parent / f".wire-native-{id(repo_pkg)}"
    try:
        native_emit(default_recipe_catalogue(), scratch)
        emitted_pkg = scratch / _PACKAGE_DIR
        if emitted_pkg.is_dir():
            for dep in native_dep_names:
                src = emitted_pkg / dep
                if src.is_dir():
                    _copy_tree(src, repo_pkg / dep)
    finally:
        if scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)


def _install_files(src_dir: Path, dst_dir: Path) -> list[str]:
    """Copy every file in *src_dir* into *dst_dir*; return the installed names."""
    if not src_dir.is_dir():
        return []
    dst_dir.mkdir(parents=True, exist_ok=True)
    names = []
    for child in sorted(src_dir.iterdir()):
        if child.is_file() and not child.name.startswith("."):
            _copy_file(child, dst_dir / child.name)
            names.append(child.name)
    return names


def _install_dirs(src_dir: Path, dst_dir: Path) -> list[str]:
    """Copy every subdir in *src_dir* into *dst_dir*; return the installed names."""
    if not src_dir.is_dir():
        return []
    dst_dir.mkdir(parents=True, exist_ok=True)
    names = []
    for child in sorted(src_dir.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            _copy_tree(child, dst_dir / child.name)
            names.append(child.name)
    return names


def _register_target_file(targets_path: Path, defconfig: str) -> bool:
    """Append the desktop target to *targets_path*; return whether it changed."""
    targets_path = Path(targets_path)
    if not targets_path.is_file():
        raise WireError(f"targets.toml not found: {targets_path}")
    current = targets_path.read_text(encoding="utf-8")
    updated, changed = register_target(current, defconfig)
    if changed:
        targets_path.write_text(updated, encoding="utf-8")
    return changed


# --- CLI entry point --------------------------------------------------------


def main(args: object) -> int:
    """CLI entry point for ``wire``."""
    import json

    repo = Path(args.repo)  # type: ignore[attr-defined]
    sources = Path(args.sources)  # type: ignore[attr-defined]
    targets = getattr(args, "targets", None)
    # --targets "" (or omitted) skips target registration: only a *non-empty*
    # --targets path registers the desktop target (in that file). The default
    # is therefore None (skip), NOT the pengyos-build targets.toml — defaulting
    # to it would register the target in a real sibling file on every bare
    # `wire --repo --sources` run, which is a surprising side effect. An empty
    # string is treated as None (skip), never as the cwd.
    targets = Path(targets) if targets else None
    defconfig = getattr(args, "defconfig", _TARGET_DEFCONFIG)

    try:
        report = wire(repo, sources, targets=targets, defconfig=defconfig)
    except WireError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"command": "wire", "ok": False, "error": str(exc)}))
        else:
            print(f"pengyos-cosmic-build: wire: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(f"Wired {repo}")
        print(f"  packages: {', '.join(report.installed_packages)}")
        if report.defconfigs:
            print(f"  defconfigs: {', '.join(report.defconfigs)}")
        if report.boards:
            print(f"  boards: {', '.join(report.boards)}")
        if report.config_in_changed:
            print("  Config.in: added source line(s)")
        if report.target_registered:
            print(f"  target: registered {_TARGET_NAME}")
    return 0
