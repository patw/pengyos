"""Post-apply docs sync for the pengy-agent's model-server port pin.

The follow-up "pin the local model server's port in the
pengy-agent network grant" is blocked until PengyOS defines the model
server's default port upstream (the port must never be guessed). The
*mechanical* part of that transition — the profile grant plus its local
``# sanctioned:`` line, the ``SANCTIONED_NETWORK_RULES`` lockstep entry,
and the two test pins — is fully automated: ``portdiscovery`` re-checks
upstream, ``portpin`` plans/renders/applies it, and ``portstatus``
probes where the transition stands. What remained manual after a
successful ``apply-port-pin`` is the first step of
``portpin.POST_APPLY_STEPS``: the *prose docs* still describe the
port-unbound state. This module automates exactly that step.

:func:`sync_docs` (CLI: ``sync-port-pin-docs <port> [repo-root]
[--scan-root ROOT] [--dry-run]``) updates the four doc surfaces named by
that step, in order:

* ``profiles/pengy-agent`` — the ``# --- network`` comment block (the
  "the port is deliberately unbound …" lines),
* ``README.md`` — the ``pengy-agent`` profile row (``peer=(ip=127.0.0.1)``
  in the loopback-grant cell),
* ``spec.md`` — the pinned-grant paragraph ("the port is deliberately
  unbound until …"),
* ``CHANGELOG.md`` — a new first entry under ``## [Unreleased]`` /
  ``### Added`` recording the pin.

Each surface is recognized by its *exact* pre-sync (unbound) anchor and
its *exact* post-sync (pinned) marker:

* **editable** — the pre-sync anchor is present exactly once; the
  surface is rewritten to the pinned form for the requested port;
* **synced** — the pre-sync anchor is gone and the pinned marker is
  present exactly once; the surface is left untouched (the sync is
  idempotent: a re-run is a no-op);
* **drifted** — neither form matches (a hand edit, a typo, or a doubled
  anchor); the sync fails loudly and, being all-or-nothing, writes
  *nothing*.

The gate: the repo must already be in the ``pinned:<N>`` state for the
requested port — all four mechanical transition files consistently
pinned (``portstatus.inspect_transition``). Syncing docs for a port the
repo does not pin (or before ``apply-port-pin`` ran) exits ``1`` and
writes nothing; the upstream scan is reported as *evidence* only (the
gate for the port itself was the upstream evidence at apply time, and is
never guessed here either). The edited profile comment is re-validated
with the strict contract before anything is written.

Exit codes (CLI): ``0`` synced or already in sync, ``1`` the repo is not
``pinned:<port>``, ``2`` on bad input (missing roots, invalid port) or
doc drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Union

from . import portdiscovery, portpin, portstatus, validator
from .portstatus import PortPinStatus, PortStatusError

__all__ = [
    "PORT_PIN_DOC_FILES",
    "DOC_FILE_EDITABLE",
    "DOC_FILE_SYNCED",
    "DocSyncError",
    "RepoNotPinnedError",
    "DocFileEdit",
    "check_transition_port",
    "compute_doc_edits",
    "sync_docs",
    "render_doc_diff",
]

#: The repo files (relative to the tool root) the post-apply docs sync
#: updates, in the fixed order the command reports them. These are the
#: four prose surfaces named by ``portpin.POST_APPLY_STEPS`` — they are
#: deliberately NOT part of ``portpin.PORT_PIN_TRANSITION_FILES`` (the
#: mechanical transition is the four anchor files the state probe
#: classifies; the docs are reviewed prose, synced separately).
PORT_PIN_DOC_FILES = (
    "profiles/pengy-agent",
    "README.md",
    "spec.md",
    "CHANGELOG.md",
)

DOC_FILE_EDITABLE = "editable"
DOC_FILE_SYNCED = "synced"


class DocSyncError(Exception):
    """A doc surface has drifted from both the pre-sync and the
    post-sync form (or the edited profile is not strict-clean). The
    sync is all-or-nothing: nothing is written."""


class RepoNotPinnedError(DocSyncError):
    """The repo is not in the ``pinned:<port>`` state — the mechanical
    transition has not been applied (or was applied to a different
    port). The docs must not be synced ahead of the transition."""


@dataclass(frozen=True)
class DocFileEdit:
    """One doc surface of the sync: the relative path (within the tool
    root), the pre-/post-sync content, and the surface status (``edited``
    or ``already in sync``)."""

    rel: str
    old: str
    new: str
    status: str


# ---------------------------------------------------------------------------
# Exact pre-sync (unbound) anchors — pinned to the committed docs
# ---------------------------------------------------------------------------

#: The pre-sync form of the ``profiles/pengy-agent`` network comment
#: block (stripped form; the block must occur exactly once, contiguous).
PROFILE_PRE_BLOCK = (
    "# loopback interface, so the peer IP is pinned to 127.0.0.1. The port is",
    "# deliberately unbound: the model server's port is a deployment choice",
    "# (see the follow-up in TODO.md), and every non-loopback IP",
    "# (including ::1, which the model server does not bind) stays denied.",
)


def _profile_post_block(port: int) -> tuple:
    return (
        "# loopback interface, so the peer IP is pinned to 127.0.0.1. The port is",
        f"# pinned to {port} — the model server's default port as defined upstream",
        "# in PengyOS — and every non-loopback IP",
        "# (including ::1, which the model server does not bind) stays denied.",
    )


#: The pre-sync inline anchor of the README profile row (must occur
#: exactly once in the whole file).
README_PRE_SUB = "`peer=(ip=127.0.0.1)`, pure client"


def _readme_post_sub(port: int) -> str:
    return f"`peer=(ip=127.0.0.1 port={port})`, pure client"


#: The pre-sync form of the spec.md pinned-grant paragraph (stripped
#: block, contiguous, exactly once).
SPEC_PRE_BLOCK = (
    "`network inet tcp peer=(ip=127.0.0.1),` — loopback TCP to the local",
    "model server, pure client (a `peer=` conditional cannot carry",
    "bind/listen, so the agent cannot listen); the port is deliberately",
    "unbound until the model server's default port is defined (see",
    "TODO.md follow-up). Every other profile is socket-free.",
)


def _spec_post_block(port: int) -> tuple:
    return (
        f"`network inet tcp peer=(ip=127.0.0.1 port={port}),` — loopback TCP to",
        "the local model server, pure client (a `peer=` conditional cannot",
        "carry bind/listen, so the agent cannot listen); the port is the model",
        "server's default port as defined upstream in PengyOS. Every other",
        "profile is socket-free.",
    )


def _changelog_marker(port: int) -> str:
    """The first line of the CHANGELOG entry — its unique presence is
    the 'already synced' marker for that surface."""
    return f"- Pinned the model server's port to {port} in the pengy-agent's"


def _changelog_bullet(port: int) -> tuple:
    return (
        _changelog_marker(port),
        f"  loopback `network` grant (`peer=(ip=127.0.0.1 port={port})`) — the",
        "  model server's default port as defined upstream in PengyOS; the",
        "  grant is sanctioned by `SANCTIONED_NETWORK_RULES` plus a",
        "  profile-local `# sanctioned:` directive, and the profile stays",
        "  strict-clean.",
    )


# ---------------------------------------------------------------------------
# Anchor helpers — one exact occurrence, fail loud on drift
# ---------------------------------------------------------------------------


def _count_block(lines: list, block: tuple) -> int:
    """How many contiguous runs of *block* (matched on the stripped
    form) occur in *lines*."""
    n = len(block)
    return sum(
        1
        for i in range(len(lines) - n + 1)
        if all(lines[i + j].strip() == block[j] for j in range(n))
    )


def _find_block(lines: list, block: tuple, label: str) -> int:
    """Index of the single contiguous run of *block* (matched on the
    stripped form, so indentation is preserved but not required to
    match), or a loud failure (zero or multiple occurrences)."""
    n = len(block)
    hits = [
        i
        for i in range(len(lines) - n + 1)
        if all(lines[i + j].strip() == block[j] for j in range(n))
    ]
    if len(hits) != 1:
        raise DocSyncError(
            f"{label}: the pre-sync anchor block occurs {len(hits)} "
            "times — the docs have drifted from the sync recipe; "
            "re-sync by hand"
        )
    return hits[0]


def _replace_block(
    text: str, pre: tuple, post: tuple, label: str
) -> str:
    """Replace the single contiguous *pre* block with *post*, preserving
    the block's indentation and the last line's line ending."""
    lines = text.splitlines(keepends=True)
    start = _find_block(lines, pre, label)
    n = len(pre)
    original_first = lines[start]
    indent = original_first[: len(original_first) - len(original_first.lstrip())]
    last = lines[start + n - 1]
    eol = last[len(last.rstrip("\r\n")):]
    body = [l.rstrip("\r\n") for l in post]
    replaced = [indent + line + eol for line in body[:-1]]
    replaced.append(indent + body[-1] + eol)
    return "".join(lines[:start] + replaced + lines[start + n:])


def _count_line(lines: list, stripped: str) -> int:
    return sum(1 for line in lines if line.strip() == stripped)


# ---------------------------------------------------------------------------
# Per-surface transforms (pure: text in, (text, status) out)
# ---------------------------------------------------------------------------


def _transform_profile(text: str, port: int) -> Tuple[str, str]:
    lines = text.splitlines()
    pre = PROFILE_PRE_BLOCK
    post = _profile_post_block(port)
    count = _count_block(lines, pre)
    if count == 1:
        # editable: the pre-sync block occurs exactly once
        return _replace_block(text, pre, post, "profiles/pengy-agent"), DOC_FILE_EDITABLE
    if count == 0 and _count_line(lines, post[1]) == 1:
        # already synced: pre block gone, pinned marker unique
        return text, DOC_FILE_SYNCED
    raise DocSyncError(
        "profiles/pengy-agent: the pre-sync comment block occurs "
        f"{count} times and the pinned marker occurs "
        f"{_count_line(lines, post[1])} times — the docs have drifted "
        "from the sync recipe; re-sync by hand"
    )


def _transform_readme(text: str, port: int) -> Tuple[str, str]:
    pre = README_PRE_SUB
    post = _readme_post_sub(port)
    pre_count = text.count(pre)
    if pre_count == 1:
        return text.replace(pre, post), DOC_FILE_EDITABLE
    if pre_count == 0 and text.count(post) == 1:
        return text, DOC_FILE_SYNCED
    raise DocSyncError(
        "README.md: the pre-sync profile-row anchor occurs "
        f"{pre_count} times (and the pinned form {text.count(post)} "
        "times) — the docs have drifted from the sync recipe; "
        "re-sync by hand"
    )


def _transform_spec(text: str, port: int) -> Tuple[str, str]:
    pre = SPEC_PRE_BLOCK
    post = _spec_post_block(port)
    lines = text.splitlines()
    count = _count_block(lines, pre)
    if count == 1:
        return _replace_block(text, pre, post, "spec.md"), DOC_FILE_EDITABLE
    marker_count = _count_line(lines, post[0])
    if count == 0 and marker_count == 1:
        return text, DOC_FILE_SYNCED
    raise DocSyncError(
        "spec.md: the pre-sync pinned-grant paragraph occurs "
        f"{count} times and the pinned marker occurs {marker_count} "
        "times — the docs have drifted from the sync recipe; "
        "re-sync by hand"
    )


def _transform_changelog(text: str, port: int) -> Tuple[str, str]:
    marker = _changelog_marker(port)
    lines = text.splitlines(keepends=True)
    stripped = [l.rstrip("\r\n") for l in lines]
    if _count_line(stripped, marker) == 1:
        return text, DOC_FILE_SYNCED
    if _count_line(stripped, marker) > 1:
        raise DocSyncError(
            "CHANGELOG.md: the pin entry occurs more than once — the "
            "docs have drifted from the sync recipe; re-sync by hand"
        )
    unreleased = [i for i, l in enumerate(stripped) if l.strip() == "## [Unreleased]"]
    if len(unreleased) != 1:
        raise DocSyncError(
            "CHANGELOG.md: the '## [Unreleased]' header occurs "
            f"{len(unreleased)} times — the docs have drifted from the "
            "sync recipe; re-sync by hand"
        )

    # The Unreleased section's own '### Added': the first one before the
    # next '## ' version header (later sections, e.g. '### Fixed', may
    # re-appear in released versions and must not confuse the anchor).
    next_version = [
        i
        for i, l in enumerate(stripped)
        if i > unreleased[0]
        and l.startswith("## ")
        and l.strip() != "## [Unreleased]"
    ]
    bound = next_version[0] if next_version else len(stripped)
    added = [
        i
        for i, l in enumerate(stripped)
        if unreleased[0] < i < bound and l.strip() == "### Added"
    ]
    if len(added) != 1:
        raise DocSyncError(
            "CHANGELOG.md: the '### Added' section of the Unreleased "
            f"block occurs {len(added)} times — the docs have drifted "
            "from the sync recipe; re-sync by hand"
        )
    at = added[0]
    original = lines[at]
    eol = original[len(original.rstrip("\r\n")):]
    bullet = [line + eol for line in _changelog_bullet(port)]
    return "".join(lines[: at + 1] + bullet + lines[at + 1:]), DOC_FILE_EDITABLE


_DOC_TRANSFORMS = {
    "profiles/pengy-agent": _transform_profile,
    "README.md": _transform_readme,
    "spec.md": _transform_spec,
    "CHANGELOG.md": _transform_changelog,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_transition_port(
    repo_root: Union[str, Path],
    scan_root: Union[str, Path, None] = None,
    port: Union[int, str] = "",
) -> PortPinStatus:
    """The gate: inspect the transition state under *repo_root* and
    require it to be ``pinned:<port>``.

    Raises :class:`RepoNotPinnedError` (the docs must not be synced
    ahead of / beside the mechanical transition) and
    :class:`PortStatusError` when a transition file is missing (bad
    input — the CLI exits 2). Read-only.
    """
    n = portpin.normalize_port(port)
    status = portstatus.inspect_transition(repo_root, scan_root)
    if status.state != portstatus.STATE_PINNED or status.port != n:
        detail = ", ".join(f"{f.rel}: {f.form}" for f in status.files)
        raise RepoNotPinnedError(
            f"repo is not pinned to port {n}: state {status.state} "
            f"({detail}) — apply the mechanical transition first "
            f"(pengyos-apparmor apply-port-pin {n})"
        )
    return status


def compute_doc_edits(
    port: Union[int, str],
    repo_root: Union[str, Path] = ".",
    scan_root: Union[str, Path, None] = None,
) -> tuple:
    """Compute the complete docs sync for *port* against the repo under
    *repo_root* (default: the current directory).

    Returns one :class:`DocFileEdit` per :data:`PORT_PIN_DOC_FILES`
    entry, in that order. Pure: never writes a file. The gate
    (:func:`check_transition_port`) runs first; each surface is then
    transformed (:func:`_transform_profile` and friends) — a drifted
    surface raises :class:`DocSyncError` before any content is
    returned, so the caller cannot partially apply a drifted sync. The
    edited profile is additionally re-validated: it must be
    strict-clean (0 errors, 0 warnings) or the sync fails loudly.
    """
    n = portpin.normalize_port(port)
    check_transition_port(repo_root, scan_root, n)
    root = Path(repo_root)
    edits: list[DocFileEdit] = []
    for rel in PORT_PIN_DOC_FILES:
        path = root / rel
        if not path.is_file():
            raise DocSyncError(f"doc file missing under {root}: {rel}")
        old = path.read_text(encoding="utf-8")
        new, status = _DOC_TRANSFORMS[rel](old, n)
        if status == DOC_FILE_EDITABLE and new == old:
            raise DocSyncError(
                f"{rel}: the sync produced no change — the docs have "
                "drifted from the sync recipe"
            )
        edits.append(DocFileEdit(rel=rel, old=old, new=new, status=status))
    for edit in edits:
        if edit.rel == "profiles/pengy-agent":
            report = validator.validate_text(edit.new, "pengy-agent")
            if not report.ok or report.warnings:
                raise DocSyncError(
                    "profiles/pengy-agent: the synced comment edit is "
                    f"not strict-clean ({len(report.errors)} error(s), "
                    f"{len(report.warnings)} warning(s)) — refusing to "
                    "write"
                )
    return tuple(edits)


def sync_docs(
    port: Union[int, str],
    repo_root: Union[str, Path] = ".",
    scan_root: Union[str, Path, None] = None,
) -> tuple:
    """Compute the docs sync (:func:`compute_doc_edits`) and write the
    *editable* surfaces in place (the already-synced ones are left
    byte-identical). Returns the computed
    :class:`DocFileEdit` records.

    All-or-nothing: any gate failure or drift raises before the first
    write. Idempotent: a second run computes four ``already in sync``
    edits and writes nothing. The writes are staged-atomic
    (``portpin.atomic_commit`` — temp file + fsync + atomic rename),
    so an interrupted sync can never leave a torn doc file: either a
    surface carries its complete pre-sync or its complete post-sync
    content.
    """
    edits = compute_doc_edits(port, repo_root, scan_root)
    editable = [e for e in edits if e.status == DOC_FILE_EDITABLE]
    if editable:
        portpin.atomic_commit(Path(repo_root), editable)
    return edits


def render_doc_diff(edits: tuple) -> str:
    """A unified diff (``a/`` → ``b/``, 3 lines of context) over the
    *editable* edits of a computed docs sync — the reviewable view the
    ``--dry-run`` mode prints. Surfaces already in sync contribute
    nothing."""
    import difflib

    sections = []
    for edit in edits:
        if edit.status != DOC_FILE_EDITABLE:
            continue
        diff = [
            " " if not line or line[0] not in "-+@ " else line
            for line in difflib.unified_diff(
                edit.old.splitlines(),
                edit.new.splitlines(),
                fromfile=f"a/{edit.rel}",
                tofile=f"b/{edit.rel}",
                lineterm="",
                n=3,
            )
        ]
        if diff:
            sections.append("\n".join(diff))
    return "\n".join(sections) + "\n" if sections else ""
