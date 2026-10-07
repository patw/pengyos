"""``converge`` — turn a captured change into a declarative profile patch.

PengyOS profiles are *declarative*: instead of running a command to mutate a
file, you describe the desired state of that file in a profile. ``converge``
closes the loop — it takes a recorded :mod:`.apply` change and emits a
declarative **profile patch** (a TOML document of ``[[file]]`` entries) that
reproduces the change's *effect* without re-running the command.

Best effort, by construction. Only **file-content changes** (a file that was
*created* or *modified* by the command) can be represented declaratively as
"this path should contain this text with this mode". Everything else is
*skipped* with a reason:

* **deleted** files — a declarative profile expresses desired state, not
  removals; there is nothing to declare (see ``skipped``).
* **binary** files — contents that are not valid UTF-8 cannot be written as a
  TOML string.
* files that **no longer exist** on disk — the post-change state that would be
  converged is gone (e.g. the change was already reverted).

The output is emitted with a tiny standard-library TOML writer (Python ships a
TOML *reader*, ``tomllib``, but no *writer*), so the package keeps a
zero-dependency footprint. The emitted document is re-parseable by
``tomllib`` — see the tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

from pengyos_mutations.revert import find_change

__all__ = [
    "ConvergeEntry",
    "ConvergeError",
    "ConvergeResult",
    "converge",
    "emit_toml",
    "render_entry",
    "toml_escape",
]

#: Kinds in the recorded diff that converge can represent declaratively.
_REPRESENTABLE_KINDS = ("created", "modified")


class ConvergeError(RuntimeError):
    """Raised when a change cannot be converged (e.g. it is not recorded)."""


@dataclass
class ConvergeEntry:
    """One declarative ``[[file]]`` entry converged from a change.

    Attributes
    ----------
    path:
        Absolute POSIX path of the file the entry declares.
    contents:
        The decoded (UTF-8) contents the file should have.
    kind:
        How the file changed in the recorded diff: ``"created"`` or
        ``"modified"``.
    mode:
        The file's permission bits as an octal string (e.g. ``"0644"``), when
        it could be stat-ed; ``None`` otherwise.
    """

    path: str
    contents: str
    kind: str
    mode: Optional[str] = None


@dataclass
class ConvergeResult:
    """Outcome of :func:`converge`.

    Attributes
    ----------
    change_id:
        The change that was converged.
    desc:
        The recorded change's human-readable description.
    root:
        The tracked root the change applied to (absolute POSIX path).
    entries:
        The declarative ``[[file]]`` entries written to the patch.
    skipped:
        ``(path, reason)`` pairs for diff entries that could not be
        represented declaratively.
    out:
        The output path the patch was written to (``None`` when nothing was
        written).
    """

    change_id: str
    desc: str
    root: str
    entries: list[ConvergeEntry] = field(default_factory=list)
    skipped: list[Tuple[str, str]] = field(default_factory=list)
    out: Optional[Path] = None

    def summary(self) -> str:
        """A one-line human-readable summary of what converge produced."""
        parts = [
            f"converged change {self.change_id} ({self.desc}) at {self.root}"
        ]
        if self.entries:
            paths = ", ".join(e.path for e in self.entries)
            parts.append(f"{len(self.entries)} file(s) declared: {paths}")
        if self.skipped:
            reasons = "; ".join(f"{p}: {r}" for p, r in self.skipped)
            parts.append(f"{len(self.skipped)} skipped: {reasons}")
        if not self.entries and not self.skipped:
            parts.append("no tracked paths changed")
        return "; ".join(parts)


def toml_escape(value: str) -> str:
    """Escape ``value`` for inclusion in a TOML basic (double-quoted) string."""
    out: list[str] = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def _octal_mode(path: Path) -> Optional[str]:
    """The file's permission bits as an octal string (e.g. ``"0644"``)."""
    try:
        return f"{os.stat(path).st_mode & 0o777:04o}"
    except OSError:
        return None


def _read_contents(path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(contents, reason)`` for a file's contents.

    ``contents`` is the decoded UTF-8 text, or ``None`` with a ``reason`` when
    the file is missing or its bytes are not valid UTF-8.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None, "file no longer present"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "binary content (not valid UTF-8)"


def entries_for_change(
    record: dict, root: Path
) -> Tuple[list[ConvergeEntry], list[Tuple[str, str]]]:
    """Build declarative entries (and skip reasons) from a recorded change.

    ``record`` is the apply record (``find_change`` result); ``root`` is the
    tracked root the change applied to. File-content changes (``created`` /
    ``modified``) become :class:`ConvergeEntry` records; deletions, binary
    content, and missing files are returned as ``(path, reason)`` skips.
    """
    data = record.get("data") or {}
    diff = data.get("diff") or []
    rel_root = root.as_posix().rstrip("/")

    entries: list[ConvergeEntry] = []
    skipped: list[Tuple[str, str]] = []
    for item in diff:
        relpath = item.get("path", "")
        kind = item.get("kind", "")
        if kind not in _REPRESENTABLE_KINDS:
            skipped.append((relpath, "deletion is not declarative state"))
            continue
        abs_path = Path(rel_root) / relpath
        contents, reason = _read_contents(abs_path)
        if contents is None:
            skipped.append((abs_path.as_posix(), reason))
            continue
        entry = ConvergeEntry(
            path=abs_path.as_posix(),
            contents=contents,
            kind=kind,
            mode=_octal_mode(abs_path),
        )
        entries.append(entry)
    return entries, skipped


def render_entry(entry: ConvergeEntry) -> str:
    """Render one :class:`ConvergeEntry` as a TOML ``[[file]]`` block."""
    lines = ["[[file]]", f'path = "{toml_escape(entry.path)}"']
    lines.append(f'contents = "{toml_escape(entry.contents)}"')
    if entry.mode is not None:
        lines.append(f'mode = "{entry.mode}"')
    return "\n".join(lines)


def emit_toml(result: ConvergeResult) -> str:
    """Render a :class:`ConvergeResult` to a TOML profile-patch document."""
    header = [
        "# Converged profile patch",
        f"#   change: {result.change_id}",
        f"#   desc:   {result.desc}",
        f"#   root:   {result.root}",
        f"#   {len(result.entries)} file(s) converged, "
        f"{len(result.skipped)} skipped",
        "",
    ]
    if not result.entries:
        return "\n".join(header).rstrip("\n") + "\n"
    blocks = [render_entry(e) for e in result.entries]
    return "\n".join(header) + "\n".join(blocks) + "\n"


def converge(
    change_id: str,
    out: "str | os.PathLike[str]",
    store: Optional[str] = None,
) -> ConvergeResult:
    """Converge a recorded change into a declarative TOML profile patch.

    Looks up the apply record for ``change_id`` in the attest log, turns each
    representable file-content change into a declarative ``[[file]]`` entry, and
    writes the patch to ``out``. Deletions, binary content, and files that no
    longer exist are recorded as skips (see :class:`ConvergeResult`).

    Raises
    ------
    ConvergeError
        If the change is not recorded (or its record has no tracked root).
    """
    record = find_change(store, change_id)
    if record is None:
        raise ConvergeError(
            f"no recorded change {change_id!r} in the attest log"
        )

    data = record.get("data") or {}
    root = data.get("root")
    if not root:
        raise ConvergeError(f"record {change_id!r} has no tracked root")

    root_path = Path(root).expanduser().resolve()
    desc = data.get("desc") or ""

    entries, skipped = entries_for_change(record, root_path)

    result = ConvergeResult(
        change_id=change_id,
        desc=desc,
        root=root_path.as_posix(),
        entries=entries,
        skipped=skipped,
    )

    patch = emit_toml(result)
    out_path = Path(out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(patch, encoding="utf-8")
    result.out = out_path

    return result
