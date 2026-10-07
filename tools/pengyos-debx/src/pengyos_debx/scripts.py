"""List a package's maintainer scripts for inspection — **never** execute them.

This module owns the security invariant that underpins the whole debx tool:

> **Maintainer scripts are listed, never run.**

A ``.deb`` bundles its maintainer scripts (``preinst``/``postinst``/``prerm``/
``postrm``) inside ``control.tar.*``; an ``.rpm`` stores them in header tags
(PREIN/POSTIN/PREUN/POSTUN). Both are read here *only* so their interpreter and
contents can be shown for inspection. The bytes are never written to disk and
never handed to a shell or interpreter.

Public surface:

* :class:`ScriptListing` -- the collection of scripts found in a package.
* :func:`list_scripts`   -- read a package's scripts (never run them).
* :func:`format_listing` -- render a listing for the terminal.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from .formats import ScriptEntry


class ScriptListing:
    """The maintainer scripts read from a single package.

    Wraps a list of :class:`~pengyos_debx.formats.ScriptEntry` and offers
    convenient accessors. Iterating, indexing and len() all work; the list is
    *never* executed.
    """

    def __init__(self, source: str, scripts: Iterable[ScriptEntry]):
        self.source = source
        self._scripts: List[ScriptEntry] = list(scripts)

    def __len__(self) -> int:
        return len(self._scripts)

    def __iter__(self):
        return iter(self._scripts)

    def __getitem__(self, index: int) -> ScriptEntry:
        return self._scripts[index]

    @property
    def names(self) -> List[str]:
        """The script names, in read order."""
        return [s.name for s in self._scripts]

    def by_name(self, name: str) -> Optional[ScriptEntry]:
        """Return the script named ``name``, or ``None``."""
        for s in self._scripts:
            if s.name == name:
                return s
        return None

    @property
    def has_scripts(self) -> bool:
        """Whether the package carried any maintainer scripts."""
        return bool(self._scripts)

    def format(self) -> str:
        """Render this listing for the terminal."""
        return format_listing(self)


def list_scripts(package) -> ScriptListing:
    """Read the maintainer scripts from ``package`` (a ``DebPackage``/``RpmPackage``).

    The scripts are read from the package structure only; they are never
    executed. The package's ``.scripts`` attribute is used directly; if it is
    missing the listing is empty. Raises
    :class:`~pengyos_debx.formats.PackageFormatError` when the package cannot be
    read.
    """
    scripts = list(getattr(package, "scripts", []))
    source = getattr(package, "source", str(package))
    return ScriptListing(source, scripts)


def list_scripts_from_path(path: str) -> ScriptListing:
    """Read a package file at ``path`` and list its maintainer scripts.

    Dispatches on the file magic (:func:`pengyos_debx.formats.classify`) and
    reads either a ``.deb`` or an ``.rpm``. Scripts are never executed.
    """
    from .formats import read_package

    return list_scripts(read_package(path))


def format_listing(listing: ScriptListing) -> str:
    """Render a human-readable listing for the terminal.

    Each script is shown with its name, interpreter and size. A clear refusal
    notice states that the scripts were listed for inspection and never run.
    """
    lines = [
        f"Maintainer scripts in {listing.source} "
        "(read-only; NOT executed):",
    ]
    if not listing.has_scripts:
        lines.append("  (no maintainer scripts found)")
    else:
        for script in listing:
            interpreter = script.interpreter or "(none)"
            lines.append(
                f"  {script.name}  [{interpreter}]  {script.size} bytes"
            )
    lines.append("")
    lines.append(
        "These scripts were listed for inspection and were NOT executed."
    )
    return "\n".join(lines)
