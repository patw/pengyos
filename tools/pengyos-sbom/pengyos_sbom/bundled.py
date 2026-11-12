"""Bundled dependencies: the Rust crates and Go modules compiled into a package.

Buildroot knows a cargo or golang package as one name and one version, but the
binary it builds links hundreds of third-party crates or modules, fetched by the
package's own lockfile. None of them appear in legal-info or show-info, so an
SBOM built from those alone lists `cosmic ee5f9c568b92` and not the 1,900 crates
COSMIC is made of, and no scanner can see an advisory against one of them.

This reads them back out of the package's build directory
(``<output>/build/<name>-<version>/``), which is where the source was unpacked:

* Rust: every ``Cargo.lock`` in the package's own tree, crates.io entries only
  (path and git dependencies are the package itself or a fork it pins).
* Go: ``vendor/modules.txt`` (``# <module> <version>`` lines).

Lockfiles that belong to a dependency rather than the package are skipped: a
crate unpacked under ``vendor/`` keeps its own ``Cargo.lock``, and that lists what
*its* authors built against, not what we compiled. Counting them turned 3,600
real crates into 9,000 (with ``regex 0.1.80`` and ``tokio 0.1.22`` among them).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, Tuple

from .model import Bundled

ECO_CARGO = "cargo"
ECO_GO = "golang"

# Directory names under a package that hold someone else's lockfiles, or optional
# parts of a C package Buildroot does not build (git's contrib/libgit-rs, pango's
# meson subprojects/, conmon's tools/).
_SKIP_DIRS = frozenset({
    "vendor", "VENDOR", "target", "testdata", "tests", "fuzz", "examples",
    ".cargo", "cargo-home", "registry", ".git", "contrib", "subprojects", "tools",
})
# The compiler's own sources carry Go and Rust trees (libgo, libgrust) for front
# ends we do not build.
_SKIP_PACKAGES = ("gcc-",)

_LOCK_NAME = re.compile(r'^name = "([^"]+)"', re.M)
_LOCK_VERSION = re.compile(r'^version = "([^"]+)"', re.M)
_LOCK_SOURCE = re.compile(r'^source = "([^"]+)"', re.M)
_GO_MODULE = re.compile(r"^# (\S+) (v\S+)")


def parse_cargo_lock(text: str) -> Iterable[Bundled]:
    for block in text.split("[[package]]")[1:]:
        n, v, s = _LOCK_NAME.search(block), _LOCK_VERSION.search(block), _LOCK_SOURCE.search(block)
        if n and v and s and "crates.io" in s.group(1):
            yield Bundled(ECO_CARGO, n.group(1), v.group(1))


def parse_go_modules(text: str) -> Iterable[Bundled]:
    for line in text.splitlines():
        m = _GO_MODULE.match(line)
        if m:
            yield Bundled(ECO_GO, m.group(1), m.group(2))


def _own(path: Path, root: Path, keep_last: int) -> bool:
    """True when no directory between ``root`` and the file is a skip dir."""
    parts = path.relative_to(root).parts[:-keep_last] if keep_last else path.relative_to(root).parts
    return not (_SKIP_DIRS & set(parts))


def _cargo_built(lock: Path, pkg_dir: Path) -> bool:
    """Cargo ran here: a ``target/`` directory beside the lockfile or above it in the
    package (COSMIC shares one at its top). A C package's optional Rust part
    (fontconfig's fontations backend, qemu's rust/) ships a Cargo.lock but is never
    built, so it has none."""
    d = lock.parent
    while True:
        t = d / "target"
        # cargo marks its own: qemu has a source dir named target/ (its CPU targets)
        if (t / "CACHEDIR.TAG").is_file() or (t / ".rustc_info.json").is_file():
            return True
        if d == pkg_dir:
            return False
        d = d.parent


def scan_package_dir(pkg_dir: Path) -> Tuple[Bundled, ...]:
    """Every crate / module the package at ``pkg_dir`` builds against, sorted."""
    found = set()
    if pkg_dir.name.startswith(_SKIP_PACKAGES):
        return ()
    for lock in pkg_dir.rglob("Cargo.lock"):
        if _own(lock, pkg_dir, 1) and _cargo_built(lock, pkg_dir):
            found.update(parse_cargo_lock(lock.read_text(errors="replace")))
    for mods in pkg_dir.rglob("modules.txt"):
        # <pkg>/[sub/]vendor/modules.txt: the vendor dir itself is allowed.
        if mods.parent.name == "vendor" and _own(mods, pkg_dir, 2):
            found.update(parse_go_modules(mods.read_text(errors="replace")))
    return tuple(sorted(found))


def scan_build_dir(build_dir: Path, components) -> Dict[str, Tuple[Bundled, ...]]:
    """``{target component name: bundled deps}`` for every target component whose
    ``<name>-<version>`` directory exists under ``build_dir`` and bundles any."""
    out: Dict[str, Tuple[Bundled, ...]] = {}
    for comp in components:
        if comp.scope != "target":
            continue
        pkg_dir = build_dir / (f"{comp.name}-{comp.version}" if comp.version else comp.name)
        if not pkg_dir.is_dir():
            continue
        deps = scan_package_dir(pkg_dir)
        if deps:
            out[comp.name] = deps
    return out


def purl(dep: Bundled) -> str:
    """Package URL: ``pkg:cargo/<name>@<v>`` / ``pkg:golang/<module>@<v>``."""
    return f"pkg:{dep.ecosystem}/{dep.name}@{dep.version}"
