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
import time
from dataclasses import dataclass
from pathlib import Path

#: Buildroot's list of target files by package (relative to the output dir).
FILE_LIST = Path("build") / "packages-file-list.txt"
#: The file list as it stood before ``configure`` removed any build dir.
SNAPSHOT = ".pengyos-files-before.txt"
#: Leftovers found by ``build``, one ``pkg,./path`` per line.
REPORT = ".pengyos-leftovers.txt"
#: Paths whose mtime :func:`prepare_rebuild` aged, and the package that owned each
#: one. Append-only history, written in the output dir root so it survives
#: ``make clean``. It is what makes an aged file's ownership decidable again (see
#: :func:`trustworthy_owners`).
AGED = ".pengyos-aged.txt"
#: When the last ``configure`` finished, as an epoch. Packages whose build dir is
#: newer than this were installed by the round that is about to run.
CONFIGURED = ".pengyos-configured"

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


def trustworthy_owners(
    path: str,
    owners: set[str],
    aged: dict[str, set[str]],
    installed: set[str] | None = None,
) -> set[str]:
    """The claims about ``path`` that can be trusted to explain an install.

    :func:`prepare_rebuild` ages the mtime of every file a to-be-rebuilt package
    listed. Buildroot's ``.files-list.txt`` is a ``find`` before/after compared on
    mtime, so every package installed *after* an aging absorbs every aged file it
    can see. Measured on the Desktop tree on 2026-10-20: ``ldacbt``, which installs
    four files, lists 13544; ``libfreeaptx`` 13551; ``libmtp`` 25707. Those claims
    say nothing about who installed the file, and they made the
    ``all(owners are disabled)`` rule unsatisfiable for any aged file - the guard
    reported "no leftovers" for a published 26.10.37 image that shipped
    ``libupower-glib.so.3.1.0``, a file of the disabled stock ``upower``.

    So a claim on an aged path counts only in two cases:

    * it is the claim of a package the path was aged *for* - that package's own
      install put the path in its list, and the aging only made it re-list it;
    * it is the claim of a package installed by the current round, and no package
      the path was aged for was installed by that round. Then the aged-for package
      cannot have written the file, and the round's install is the only thing left
      that can have. This is what keeps ``upowerd`` (written by ``upower-pengyos``
      at 21:15, while stock ``upower`` was not installed at all) off the list.

    Everything else is an absorption artifact and is dropped.
    """
    aged_for = aged.get(path)
    if not aged_for:
        return owners
    if installed is None:
        return owners & aged_for
    keep = set()
    for pkg in owners:
        if pkg in aged_for:
            keep.add(pkg)
        elif pkg in installed and not (aged_for & installed):
            keep.add(pkg)
    return keep


def mark_configured(output: Path) -> None:
    """Record when this ``configure`` finished, for :func:`installed_packages`."""
    (output / CONFIGURED).write_text(str(int(time.time())) + "\n", encoding="utf-8")


def installed_packages(output: Path) -> set[str]:
    """Packages whose build dir was (re)created after the last ``configure``.

    These are the packages the current round installs, so their file lists are
    this round's. An absent record returns every package, which is the safe
    reading: nothing is excluded, and the guard degrades to its old behaviour.
    """
    build = output / "build"
    if not build.is_dir():
        return set()
    packages = {p for ps in parse_file_list(_read(output / FILE_LIST)).values() for p in ps}
    raw = _read(output / CONFIGURED).strip()
    if not raw.isdigit():
        return packages
    since = float(raw)
    keep = set()
    for d in build.iterdir():
        if not d.is_dir() or "-" not in d.name:
            continue
        pkg = _package_of_dir(d.name, packages)
        if pkg is None:
            continue
        try:
            if d.stat().st_mtime >= since:
                keep.add(pkg)
        except OSError:
            pass
    return keep


def find_leftovers(
    before: dict[str, set[str]],
    now: dict[str, set[str]],
    disabled: set[str],
    target: Path,
    aged: dict[str, set[str]] | None = None,
    installed: set[str] | None = None,
) -> dict[str, str]:
    """``{path: package that installed it}`` for files no package installs now."""
    owners_of = aged or {}
    found: dict[str, str] = {}
    for path in set(now) | set(before):
        live = trustworthy_owners(path, now.get(path, set()), owners_of, installed)
        prior = trustworthy_owners(path, before.get(path, set()), owners_of, installed)
        if live and all(_is_disabled(p, disabled) for p in live):
            found[path] = sorted(live)[0]
        elif not live and prior:
            # Owned before, and nothing that can be trusted to have installed it
            # claims it now: either the package dropped it on a rebuild, or it is
            # off and only absorption claims are left.
            found[path] = sorted(prior)[0]
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

    Every aged path is recorded in :data:`AGED` against the package it was aged
    for, because the aging is what makes the later file lists untrustworthy (see
    :func:`trustworthy_owners`).
    """
    owners = parse_file_list(_read(output / FILE_LIST))
    packages = {p for ps in owners.values() for p in ps}
    rebuilt = {pkg for d in build_dirs if (pkg := _package_of_dir(d.name, packages))}
    target = output / "target"
    touched = 0
    record: set[tuple[str, str]] = set()
    for path, pkgs in owners.items():
        if pkgs & rebuilt:
            try:
                os.utime(target / path, (0, 0), follow_symlinks=False)
                touched += 1
                record.update((pkg, path) for pkg in sorted(pkgs & rebuilt))
            except OSError:
                pass
    if record:
        _append_aged(output, record)
    return touched


def _append_aged(output: Path, record: set[tuple[str, str]]) -> None:
    """Union the aging record with this round's aged paths."""
    known = {(pkg, path) for path, pkgs in load_aged(output).items() for pkg in pkgs}
    lines = sorted(known | record)
    (output / AGED).write_text(
        "".join(f"{pkg},{path}\n" for pkg, path in lines), encoding="utf-8"
    )


def load_aged(output: Path) -> dict[str, set[str]]:
    """``{path: packages it was aged for}`` from :data:`AGED`."""
    return parse_file_list(_read(output / AGED))


def _load_report(
    output: Path, now: dict[str, set[str]], disabled: set[str]
) -> dict[str, str]:
    """Recorded leftovers that are still in ``target/`` and still unowned."""
    owners_of = load_aged(output)
    installed = installed_packages(output)
    kept = {}
    for path, owners in parse_file_list(_read(output / REPORT)).items():
        live = {
            p
            for p in trustworthy_owners(path, now.get(path, set()), owners_of, installed)
            if not _is_disabled(p, disabled)
        }
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
    found.update(
        find_leftovers(
            before,
            now,
            disabled,
            output / "target",
            aged=load_aged(output),
            installed=installed_packages(output),
        )
    )
    (output / SNAPSHOT).unlink(missing_ok=True)
    return _result(output, found)


def current(output: Path) -> Leftovers:
    """The recorded leftovers that are still there (report-only, no new scan)."""
    now = parse_file_list(_read(output / FILE_LIST))
    disabled = disabled_packages(_read(output / ".config"))
    return _result(output, _load_report(output, now, disabled))
