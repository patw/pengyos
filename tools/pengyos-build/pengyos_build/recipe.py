"""Find built packages whose **recipe** changed after they were built.

The companion to :mod:`pengyos_build.stale`. That module catches a ``.config``
change; this one catches the change that leaves no symbol of its own package
behind — an edit to a ``.mk``, to a ``Config.in`` select, to a patch — which is
exactly the shape of a ``br2-external`` override.

Found on 2026-10-10 building 26.10.19: ``external.mk`` gained
``PIPEWIRE_CONF_OPTS += -Dflatpak=enabled`` and
``package/xdg-desktop-portal-gtk/`` gained ``-Dsettings=enabled``. The Desktop
image carried both. The Desktop NVIDIA image carried **neither** — its stamps for
those two packages were from the previous release's build, Buildroot's stamps are
not tied to the external tree, so ``make`` saw nothing to do. The build was green,
the boot checks were green, the image was signed and published, and the change was
absent from it.

The rule is a comparison of two times per package:

* the newest commit that touched the files defining that package, and
* the package's ``.stamp_built`` — when Buildroot last actually built it.

A stamp older than the recipe is a package that must be dircleaned.

Cost is deliberately bounded, because this runs before every build: three git
invocations scoped to the external tree (``rev-parse``, one ``log --name-only``,
one ``log -p`` limited to ``external.mk``) plus one ``stat`` per *candidate*
package — a package whose recipe a commit actually named. A release commit that
touches nothing in the external tree costs three git calls and zero stats.

Attribution is the only subtle part. ``external.mk`` overrides many packages from
one file, so a commit touching it must be charged to the packages it *changes*:
the ``NAME_SUFFIX`` identifiers on the left of the first ``=`` or ``:`` in its
changed lines (``PIPEWIRE_CONF_OPTS +=``, ``$(PIPEWIRE_TARGET_CONFIGURE):``),
matched longest-first against the stems enabled in ``.config``. Two things are
deliberately not charged: the right-hand side of a line —
``$(PIPEWIRE_TARGET_CONFIGURE): $(LIBGLIB2_TARGET_INSTALL_STAGING)`` makes
libglib2 a prerequisite of pipewire's rule, not a change to libglib2 — and every
package, for a line that only mentions it. Charging the whole enabled set for any
edit to ``external.mk`` would dirclean the world on a comment.

Conservative by design: a recipe committed *after* the package was built is
flagged even when the file was edited earlier and the build already picked it up
(the 26.10.19 Desktop tree is that case). The price is one package rebuilt; the
alternative is shipping an image without the change.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .stale import _build_dirs, _dir_for, parse_config

#: Prefix of every Buildroot package symbol in ``.config``.
PREFIX = "BR2_PACKAGE_"
#: The one file that overrides stock Buildroot recipes from the external tree.
EXTERNAL_MK = "external.mk"

#: A ``NAME_SUFFIX`` identifier in a Makefile line (``PIPEWIRE_CONF_OPTS``).
_MAKE_VAR = re.compile(r"\b([A-Z][A-Z0-9_]*_[A-Z0-9_]+)")
#: A diff line that is an added or removed line, not a file header.
_DIFF_LINE = re.compile(r"^[+-](?![+-])")
#: The part of a Makefile line a change is *about*: everything left of the first
#: ``=`` or ``:`` — an assignment target (``PIPEWIRE_CONF_OPTS +=``), a rule
#: target (``$(PIPEWIRE_TARGET_CONFIGURE):``), or a whole ``define``/``ifeq`` line.
#: The right-hand side is deliberately excluded: ``$(LIBGLIB2_TARGET_INSTALL_STAGING)``
#: appearing as a *prerequisite* names libglib2 without changing it.
_SUBJECT = re.compile(r"^[^=:]*")
#: A commit epoch is a 10-digit unix time (2001-09-09 onward). Requiring that is
#: what stops a diff context line that happens to be a bare number from being
#: read as the start of a new commit.
MIN_EPOCH = 1_000_000_000


GitRunner = Callable[[Sequence[str]], str]


def _git(argv: Sequence[str]) -> str:
    """Run git, returning stdout; '' on any failure (a non-git tree is not an error)."""
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True, check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


@dataclass(frozen=True)
class RecipeReport:
    """``rebuild``: build dirs whose package must be dircleaned.

    ``notes``: changes the check cannot express as a package rebuild — a board
    file (applied when the image is assembled, not per package) or an
    ``external.mk`` line naming no enabled package.
    ``checked``: candidates examined, so a quiet run is visible as a real result.
    """

    rebuild: tuple[Path, ...] = ()
    notes: tuple[str, ...] = ()
    checked: int = 0

    def __str__(self) -> str:
        parts = [f"recipe staleness: {len(self.rebuild)} to rebuild, {self.checked} checked"]
        if self.rebuild:
            parts.append("rebuild: " + " ".join(p.name for p in self.rebuild))
        parts.extend("note: " + n for n in self.notes)
        return "\n".join(parts)


def enabled_stems(config_text: str) -> set[str]:
    """Stems of the package symbols set to ``y`` (``PIPEWIRE`` for ``BR2_PACKAGE_PIPEWIRE``)."""
    return {
        s[len(PREFIX):]
        for s, v in parse_config(config_text).items()
        if s.startswith(PREFIX) and v == "y"
    }


def newest_paths(log_text: str) -> dict[str, int]:
    """``git log --no-renames --name-only --format=%ct`` output -> ``{path: newest epoch}``.

    ``git log`` prints newest commit first, so the first time a path appears is
    the newest commit that touched it.
    """
    newest: dict[str, int] = {}
    epoch = None
    for raw in log_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.isdigit():
            # A bare number is a commit epoch or a diff context line, never a path.
            if int(line) >= MIN_EPOCH:
                epoch = int(line)
            continue
        if epoch is not None:
            newest.setdefault(line, epoch)
    return newest


def stem_of(token: str, stems: set[str]) -> str | None:
    """The enabled package stem a ``NAME_SUFFIX`` identifier belongs to.

    Longest prefix wins, so ``XDG_DESKTOP_PORTAL_GTK_CONF_OPTS`` resolves to
    ``XDG_DESKTOP_PORTAL_GTK`` and not to ``XDG``.
    """
    tokens = token.split("_")
    for n in range(len(tokens), 0, -1):
        stem = "_".join(tokens[:n])
        if stem in stems:
            return stem
    return None


def attributed_stems(diff_log: str, stems: set[str]) -> dict[str, int]:
    """``{stem: newest epoch}`` for the packages *changed* by an ``external.mk`` diff log.

    Only added and removed lines count, and only the identifiers on the left of
    the first ``=`` or ``:``: those are what the change is about. A package named
    on the right-hand side — ``$(PIPEWIRE_TARGET_CONFIGURE):
    $(LIBGLIB2_TARGET_INSTALL_STAGING)`` — is a prerequisite of someone else's
    rule, not a change to it, and charging it would rebuild libglib2 every time a
    comment about it moved.
    """
    attributed: dict[str, int] = {}
    epoch = None
    for raw in diff_log.splitlines():
        if raw.strip().isdigit():
            if int(raw.strip()) >= MIN_EPOCH:
                epoch = int(raw.strip())
            continue
        if epoch is None or not _DIFF_LINE.match(raw):
            continue
        subject = _SUBJECT.match(raw[1:])
        if subject is None:
            continue
        for token in _MAKE_VAR.findall(subject.group(0)):
            stem = stem_of(token, stems)
            if stem is not None:
                attributed.setdefault(stem, epoch)
    return attributed


def recipe_paths(external_rel: str, stems: set[str], newest: dict[str, int]) -> dict[str, int]:
    """``{stem: newest epoch}`` from the per-package paths of the external tree.

    An authored recipe lives in ``package/<name>/`` (``-`` in the dir, ``_`` in
    the stem); a patch set for a *stock* package lives in ``patches/<name>/``.
    """
    out: dict[str, int] = {}
    rel = external_rel.rstrip("/")
    for stem in stems:
        name = stem.lower().replace("_", "-")
        for prefix in (f"{rel}/package/{name}/", f"{rel}/patches/{name}/"):
            for path, epoch in newest.items():
                if path.startswith(prefix):
                    out[stem] = max(out.get(stem, 0), epoch)
    return out


def repo_root(external: Path, git: GitRunner = _git) -> Path | None:
    """The git root containing ``external`` (None when the tree is not in git)."""
    top = git(["git", "-C", str(external), "rev-parse", "--show-toplevel"]).strip()
    return Path(top) if top else None


def recipe_staleness(
    external: Path | str,
    output: Path | str,
    *,
    git: GitRunner | None = None,
    repo: Path | str | None = None,
) -> RecipeReport:
    """Build dirs in ``output`` whose recipe changed after Buildroot built them.

    ``external`` is the ``br2-external`` tree, ``output`` the target's ``O=``
    directory. A missing ``.config`` (never configured), a missing output dir, or
    an external tree that is not in git all return an empty report: this is an
    optimisation of the build, never a precondition for it.
    """
    run = git if git is not None else _git
    external, output = Path(external), Path(output)
    config = output / ".config"
    if not config.is_file() or not output.is_dir():
        return RecipeReport()

    root = Path(repo) if repo is not None else repo_root(external, run)
    if root is None:
        return RecipeReport(notes=("external tree is not in git; nothing to compare against",))
    try:
        rel = external.resolve().relative_to(root.resolve())
    except ValueError:
        return RecipeReport(notes=("external tree is outside the git root; skipped",))

    stems = enabled_stems(config.read_text(encoding="utf-8"))
    if not stems:
        return RecipeReport()

    newest = newest_paths(
        run(["git", "-C", str(root), "log", "--no-renames", "--name-only", "--format=%ct", "--", str(rel)])
    )
    if not newest:
        return RecipeReport()

    mk = attributed_stems(
        run(["git", "-C", str(root), "log", "--no-renames", "--format=%ct", "-p", "--", f"{rel}/{EXTERNAL_MK}"]),
        stems,
    )
    by_path = recipe_paths(str(rel), stems, newest)

    epochs: dict[str, int] = {}
    for stem, epoch in by_path.items():
        epochs[stem] = max(epochs.get(stem, 0), epoch)
    for stem, epoch in mk.items():
        epochs[stem] = max(epochs.get(stem, 0), epoch)

    dirs = _build_dirs(output / "build")
    rebuild: list[Path] = []
    checked = 0
    for stem, epoch in sorted(epochs.items()):
        path = _dir_for(stem, dirs)
        if path is None:
            continue  # not built here: the build will build it from the current recipe
        stamp = path / ".stamp_built"
        if not stamp.is_file():
            continue  # configured, never built
        checked += 1
        if stamp.stat().st_mtime < epoch:
            rebuild.append(path)

    notes = _board_notes(str(rel), newest, output / "images")
    return RecipeReport(rebuild=tuple(rebuild), notes=notes, checked=checked)


def _board_notes(rel: str, newest: dict[str, int], images: Path) -> tuple[str, ...]:
    """Board files are applied when the image is assembled, not per package.

    The actionable question is whether any of them changed *after the rootfs image
    was last written* — only then is there board content the current image does not
    have. Buildroot regenerates the rootfs on every build that touches ``target/``,
    so the note is a heads-up, not a rebuild list.
    """
    board = [p for p in newest if p.startswith(f"{rel.rstrip('/')}/board/")]
    if not board:
        return ()
    newest_board = max(newest[p] for p in board)
    roots = [p for p in images.glob("rootfs*") if p.is_file()] if images.is_dir() else []
    if not roots:
        return ()
    image_mtime = max(p.stat().st_mtime for p in roots)
    if newest_board <= image_mtime:
        return ()  # the image on disk was assembled after the last board change
    return (
        f"{len(board)} board file(s) changed after the rootfs image was last written; "
        "board files are applied when the image is assembled, so this build must "
        "regenerate the rootfs for them to land",
    )
