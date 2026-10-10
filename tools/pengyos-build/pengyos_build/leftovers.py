"""Find files in ``target/`` that no package installs any more.

Buildroot never deletes from ``target/``. Rebuilding a package reinstalls what
it installs *now* and leaves everything it installed before, and turning a
package off leaves all of its files. :mod:`pengyos_build.stale` makes the
package rebuild; this module catches what that rebuild left behind.

Found on 2026-10-10: the core layer dropped ``BR2_PACKAGE_SYSTEMD_TIMESYNCD``.
``configure`` dircleaned systemd, and systemd rebuilt with ``-Dtimesyncd=false``,
but the old ``systemd-timesyncd`` binary, unit, D-Bus and polkit files stayed
in the Desktop ``target/``. The preset step then enabled it in the image, so
the image still ran two NTP clients: the very thing the change removed.

The evidence is Buildroot's own ``build/packages-file-list.txt`` (``pkg,./path``
per line, rewritten every build from each package's ``.files-list.txt``). A
rebuilt package's entries are replaced by what it installed this time, and
a package that is turned off keeps its build dir and so its entries. A leftover
is a file that still exists in ``target/`` and is either

* listed for some package in the snapshot :func:`snapshot` took at
  ``configure`` time and listed for no package now, or
* listed now only for packages ``.config`` marks ``# BR2_PACKAGE_<X> is not set``.

A package's list holds only what its install *changed* (a ``find`` before and
after, compared on mtime, inode, mode and size), and meson and cmake skip a file
that is already up to date. So a rebuilt package that rewrites nothing would
drop files from the list that it still installs: 188 false leftovers for systemd
on the first real run. :func:`prepare_rebuild` therefore sets the mtime of every
file a to-be-rebuilt package listed to 0 before ``configure`` removes its build
dir; the reinstall then rewrites, and lists, everything it still installs.
(``BR2_REPRODUCIBLE`` resets image mtimes anyway, and nothing is deleted.)

Files added by overlays and post-build scripts are in neither list, so they are
never reported. The findings are kept in ``<output>/.pengyos-leftovers.txt``
(the output dir root survives ``make clean``) and re-checked against
``target/`` each time they are read, so they disappear after a clean build
or once the files are removed by hand. Nothing here deletes anything.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

#: Buildroot's list of target files by package (relative to the output dir).
FILE_LIST = Path("build") / "packages-file-list.txt"
#: The file list as it stood before ``configure`` removed any build dir.
SNAPSHOT = ".pengyos-files-before.txt"
#: Leftovers found by ``build``, one ``pkg,./path`` per line.
REPORT = ".pengyos-leftovers.txt"

_NOT_SET = re.compile(r"^# BR2_PACKAGE_([A-Z0-9_]+) is not set$")


def parse_file_list(text: str) -> dict[str, set[str]]:
    """``pkg,./path`` lines -> ``{path: {packages}}``."""
    owners: dict[str, set[str]] = {}
    for line in text.splitlines():
        pkg, sep, path = line.partition(",")
        if sep and pkg and path:
            owners.setdefault(path, set()).add(pkg)
    return owners


def disabled_packages(config_text: str) -> set[str]:
    """Package names (``linux-firmware``) that ``.config`` marks as not set.

    A symbol hidden by unmet dependencies is absent rather than "not set", so
    its package is not seen as disabled; only the snapshot rule catches it.
    """
    names = set()
    for line in config_text.splitlines():
        m = _NOT_SET.match(line.strip())
        if m:
            names.add(m.group(1).lower().replace("_", "-"))
    return names


def _is_disabled(pkg: str, disabled: set[str]) -> bool:
    return pkg.lower().replace("_", "-") in disabled


def find_leftovers(
    before: dict[str, set[str]],
    now: dict[str, set[str]],
    disabled: set[str],
    target: Path,
) -> dict[str, str]:
    """``{path: package that installed it}`` for files no package installs now."""
    found: dict[str, str] = {}
    for path, owners in now.items():
        if owners and all(_is_disabled(p, disabled) for p in owners):
            found[path] = sorted(owners)[0]
    for path, owners in before.items():
        if path not in now and owners:
            found.setdefault(path, sorted(owners)[0])
    return {p: pkg for p, pkg in found.items() if _exists(target, p)}


def _exists(target: Path, path: str) -> bool:
    p = target / path
    return p.exists() or p.is_symlink()


@dataclass(frozen=True)
class Leftovers:
    """Leftover ``(package, path)`` pairs, sorted, and where they are recorded."""

    files: tuple[tuple[str, str], ...] = ()
    report: Path | None = None

    def __bool__(self) -> bool:
        return bool(self.files)

    def __str__(self) -> str:
        if not self.files:
            return "leftovers: none in target/"
        counts: dict[str, int] = {}
        for pkg, _ in self.files:
            counts[pkg] = counts.get(pkg, 0) + 1
        per_pkg = ", ".join(f"{pkg} {n}" for pkg, n in sorted(counts.items()))
        return (
            f"CLEAN BUILD NEEDED: {len(self.files)} file(s) in target/ that no "
            f"package installs any more ({per_pkg}); they are in the image. "
            f"List: {self.report}"
        )


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def snapshot(output: Path) -> None:
    """Keep the current file list for the next build to compare against.

    Called by ``configure`` before it removes any build dir. An existing
    snapshot is kept: until a build has consumed it, it is the older and so
    the more complete record.
    """
    src, dst = output / FILE_LIST, output / SNAPSHOT
    if src.is_file() and not dst.exists():
        shutil.copyfile(src, dst)


def _package_of_dir(dirname: str, packages: set[str]) -> str | None:
    """``systemd-258.7`` -> ``systemd``: the longest package with a version after it."""
    best = None
    for pkg in packages:
        rest = dirname[len(pkg) + 1:] if dirname.startswith(pkg + "-") else ""
        if rest and (rest[0].isdigit() or (rest[0] == "v" and rest[1:2].isdigit())):
            if best is None or len(pkg) > len(best):
                best = pkg
    return best


def prepare_rebuild(output: Path, build_dirs: tuple[Path, ...]) -> int:
    """Age the files of packages about to be rebuilt so the reinstall lists them.

    Returns how many files were touched. Missing files and an absent list are
    skipped; this never fails a configure.
    """
    owners = parse_file_list(_read(output / FILE_LIST))
    packages = {p for ps in owners.values() for p in ps}
    rebuilt = {pkg for d in build_dirs if (pkg := _package_of_dir(d.name, packages))}
    target = output / "target"
    touched = 0
    for path, pkgs in owners.items():
        if pkgs & rebuilt:
            try:
                os.utime(target / path, (0, 0), follow_symlinks=False)
                touched += 1
            except OSError:
                pass
    return touched


def _load_report(output: Path, now: dict[str, set[str]], disabled: set[str]) -> dict[str, str]:
    """Recorded leftovers that are still in ``target/`` and still unowned."""
    kept = {}
    for path, owners in parse_file_list(_read(output / REPORT)).items():
        live = {p for p in now.get(path, ()) if not _is_disabled(p, disabled)}
        if not live and _exists(output / "target", path):
            kept[path] = sorted(owners)[0]
    return kept


def _result(output: Path, found: dict[str, str]) -> Leftovers:
    report = output / REPORT
    if not found:
        report.unlink(missing_ok=True)
        return Leftovers()
    lines = sorted(f"{pkg},{path}" for path, pkg in found.items())
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Leftovers(tuple(sorted((pkg, path) for path, pkg in found.items())), report)


def update(output: Path) -> Leftovers:
    """After a successful build: record new leftovers and consume the snapshot."""
    now = parse_file_list(_read(output / FILE_LIST))
    disabled = disabled_packages(_read(output / ".config"))
    before = parse_file_list(_read(output / SNAPSHOT))
    found = _load_report(output, now, disabled)
    found.update(find_leftovers(before, now, disabled, output / "target"))
    (output / SNAPSHOT).unlink(missing_ok=True)
    return _result(output, found)


def current(output: Path) -> Leftovers:
    """The recorded leftovers that are still there (report-only, no new scan)."""
    now = parse_file_list(_read(output / FILE_LIST))
    disabled = disabled_packages(_read(output / ".config"))
    return _result(output, _load_report(output, now, disabled))
