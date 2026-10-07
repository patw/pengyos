"""Port-pin transition planner for the pengy-agent's model-server grant.

The follow-up "pin the local model server's port in the
pengy-agent network grant" is blocked until PengyOS defines the model
server's default port (the port must not be guessed). This module turns
the pinned mechanical recipe into a testable,
dry-run planner so that, the moment the port exists, the transition is a
one-command edit plus a reviewed sanction update:

* :func:`port_pinned_rule` renders the exact apparmor.d(5) rule string
  ``network inet tcp peer=(ip=127.0.0.1 port=N)`` (the space inside
  ``peer=(…)`` is part of the verbatim sanction match),
* :func:`apply_port_pin` performs the single-file profile edit as a
  *pure* text transformation — replace the unbound grant line with the
  port-pinned one and insert the matching profile-local
  ``# sanctioned:`` directive — raising :class:`PortPinTransitionError`
  (fail-loud) when the profile does not carry the unbound grant, already
  pins a port, or carries the grant more than once,
* :func:`verify_port_pin` validates the transformed text with the normal
  ``validator`` contract: a successful pin is **strict-clean**
  (0 errors, 0 warnings) thanks to the local sanction directive,
* :func:`render_transition_patch` renders the *complete* mechanical part
  of the transition as a unified diff over the four repo files
  (:data:`PORT_PIN_TRANSITION_FILES`): the profile grant + local
  ``# sanctioned:`` line, the ``SANCTIONED_NETWORK_RULES`` lockstep
  entry, and the two test pins. Every anchor line is matched verbatim
  in the current repo content — if any of the four files has drifted
  from the transition recipe, rendering fails loudly instead of
  emitting a patch that would not apply.

Nothing here modifies files. The planner is deliberately a dry run: the
patch is printed for a human to review and ``git apply``, and the
documentation updates remain reviewed edits (see
:data:`REMAINING_MANUAL_STEPS`).
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from . import validator

__all__ = [
    "MODEL_SERVER_PEER_IP",
    "UNBOUND_MODEL_SERVER_RULE",
    "PORT_PIN_TRANSITION_FILES",
    "REMAINING_MANUAL_STEPS",
    "PortPinTransitionError",
    "PortPinPlan",
    "TransitionFileEdit",
    "normalize_port",
    "port_pinned_rule",
    "sanction_directive",
    "plan_port_pin",
    "apply_port_pin",
    "verify_port_pin",
    "render_transition_patch",
    "POST_APPLY_STEPS",
    "compute_transition",
    "apply_transition",
    "atomic_commit",
    "STAGING_SUFFIX",
]

#: The loopback address the pengy-agent's model-server grant is pinned to
#: (the model server binds loopback; every non-loopback IP stays denied).
MODEL_SERVER_PEER_IP = "127.0.0.1"

#: The committed, port-unbound form of the pengy-agent's model-server
#: grant — the exact line ``profiles/pengy-agent`` carries today and the
#: sole entry of ``validator.SANCTIONED_NETWORK_RULES`` (pinned in the
#: test suite). When the port is pinned, this line is what
#: :func:`apply_port_pin` replaces.
UNBOUND_MODEL_SERVER_RULE = f"network inet tcp peer=(ip={MODEL_SERVER_PEER_IP})"

#: The repo files (relative to the tool root) the port-pin transition
#: touches mechanically — the complete input to
#: :func:`render_transition_patch`, in the order the patch renders them.
#: Docs (README/spec/CHANGELOG) are deliberately NOT here: they are
#: reviewed prose, not mechanical line edits.
PORT_PIN_TRANSITION_FILES = (
    "profiles/pengy-agent",
    "pengyos_apparmor/validator.py",
    "tests/test_profiles.py",
    "tests/test_validator.py",
)

# Any line in the *pinned* form, with its trailing comma optional:
# used to fail loudly when the profile already pins some port, and
# re-used by the transition-state probe (``portstatus``) to recognize
# the post-transition form in the repo files.
PINNED_RULE_RE = re.compile(
    rf"^network inet tcp peer=\(ip={re.escape(MODEL_SERVER_PEER_IP)} port=(\d+(?:-\d+)?)\),?$"
)


class PortPinTransitionError(Exception):
    """The profile is not in the exact state the port-pin transition
    expects (grant missing, already pinned, or ambiguous)."""


#: The reviewed steps that still must ship once the port is defined —
#: printed by the ``plan-port-pin`` subcommand so a dry run never pretends
#: the transition is complete. The mechanical edits (profile grant + local
#: sanction, the SANCTIONED_NETWORK_RULES lockstep entry, the two test
#: pins) are carried by the rendered unified diff; what remains is applying
#: it, updating the prose docs, and re-running the suite.
REMAINING_MANUAL_STEPS = (
    "apply the rendered unified diff (git apply) — it carries the "
    "mechanical edits: the profile grant plus its local '# sanctioned:' "
    "line, the validator.SANCTIONED_NETWORK_RULES lockstep entry, and "
    "the test pins (test_network_grants in tests/test_profiles.py and "
    "test_committed_grant_is_the_unbound_port_form in "
    "tests/test_validator.py)",
    "update the docs: the profiles/pengy-agent comment block, the README "
    "profile row, the spec.md pinned-grant paragraph, and CHANGELOG.md",
    "re-run the full suite: pytest -q and "
    "pengyos-apparmor validate --strict profiles/",
)
#: The reviewed steps that still must ship *after* the mechanical
#: transition has been applied in place (by
#: :func:`apply_transition` / the ``apply-port-pin`` subcommand). The
#: diff is already applied, so the steps are the reviewed prose docs, the
#: full suite, and flipping the checklist item — the transition is
#: complete and commit-able only once PengyOS has defined the port
#: upstream and the suite is green.
POST_APPLY_STEPS = (
    "update the docs: the profiles/pengy-agent comment block, the README "
    "profile row, the spec.md pinned-grant paragraph, and CHANGELOG.md",
    "re-run the full suite: pytest -q and "
    "pengyos-apparmor validate --strict profiles/",
    "remove the port-pin item from TODO.md once the "
    "suite is green and the port is defined upstream",
)


@dataclass(frozen=True)
class PortPinPlan:
    """The exact, mechanical state of one port-pin transition.

    ``old_rule`` / ``new_rule`` are rule strings *without* the trailing
    comma (the body form used by the validator); ``directive`` is the
    full ``# sanctioned:`` comment line that sanctions the new rule in
    the profile's own file.
    """

    port: int
    profile: str
    old_rule: str
    new_rule: str
    directive: str


def normalize_port(port: Union[int, str]) -> int:
    """Validate *port* and return its canonical ``int`` form.

    Accepts an ``int`` or a decimal ``str``; rejects booleans, other
    types, non-numeric or negative literals, leading zeros (``"011434"``
    would otherwise be ambiguous — octal?), and ports outside
    ``1..65535``.
    """
    if isinstance(port, bool) or not isinstance(port, (int, str)):
        raise PortPinTransitionError(
            f"port must be an int or a decimal string, got {type(port).__name__}"
        )
    if isinstance(port, str):
        text = port.strip()
        if not re.fullmatch(r"\d+", text):
            raise PortPinTransitionError(f"port is not a decimal integer: {port!r}")
        if text != str(int(text)):
            raise PortPinTransitionError(
                f"port has leading zeros: {port!r} (expected canonical decimal)"
            )
        port = int(text)
    if not 1 <= port <= 65535:
        raise PortPinTransitionError(
            f"port {port} is outside 1..65535"
        )
    return port


def port_pinned_rule(port: Union[int, str]) -> str:
    """The exact port-pinned model-server grant rule (no trailing comma)."""
    return f"network inet tcp peer=(ip={MODEL_SERVER_PEER_IP} port={normalize_port(port)})"


def sanction_directive(port: Union[int, str]) -> str:
    """The full profile-local ``# sanctioned:`` line for the pinned rule."""
    return validator.SANCTION_DIRECTIVE + port_pinned_rule(port)


def plan_port_pin(port: Union[int, str], profile: str = "pengy-agent") -> PortPinPlan:
    """Build the transition plan for *port* (the port must be defined
    upstream — this planner never invents one)."""
    n = normalize_port(port)
    return PortPinPlan(
        port=n,
        profile=profile,
        old_rule=UNBOUND_MODEL_SERVER_RULE,
        new_rule=port_pinned_rule(n),
        directive=sanction_directive(n),
    )


def apply_port_pin(profile_text: str, plan: PortPinPlan) -> str:
    """Return the profile text with the unbound grant replaced by the
    port-pinned rule and the local sanction directive inserted directly
    above it (same indentation). Pure: never writes a file.

    Raises :class:`PortPinTransitionError` when the profile does not
    carry exactly one unbound model-server grant line, already pins a
    port, or is ambiguous in a way a mechanical edit must not guess at.
    """
    lines = profile_text.splitlines(keepends=True)
    unbound_lineno = None
    unbound_count = 0
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped in (plan.old_rule, plan.old_rule + ","):
            unbound_count += 1
            unbound_lineno = idx
        m = PINNED_RULE_RE.match(stripped)
        if m:
            raise PortPinTransitionError(
                f"profile already pins a port: {stripped!r} — the transition "
                "expects the port-unbound form"
            )
    if unbound_count == 0:
        raise PortPinTransitionError(
            "profile does not carry the unbound model-server grant "
            f"{plan.old_rule + ','!r} — nothing to pin"
        )
    if unbound_count > 1:
        raise PortPinTransitionError(
            f"profile carries the unbound model-server grant "
            f"{unbound_count} times — a mechanical edit must not guess "
            "which one to pin"
        )

    directive = plan.directive + "\n"
    if directive.strip() in [l.strip() for l in lines]:
        # An identical directive already exists (a partially-done
        # transition): inserting it twice would be noise. The carried
        # check in the validator still pins the directive to a rule the
        # profile actually carries.
        directive_lines: list = []
    else:
        original = lines[unbound_lineno]
        indent = original[: len(original) - len(original.lstrip())]
        directive_lines = [indent + plan.directive + "\n"]

    rule_line = lines[unbound_lineno]
    indent = rule_line[: len(rule_line) - len(rule_line.lstrip())]
    eol = rule_line[len(rule_line.rstrip("\r\n")):]
    tail = rule_line.strip().rpartition(plan.old_rule)[2]
    replaced = indent + plan.new_rule + ("," if tail.strip() == "," else "") + eol

    out = lines[:unbound_lineno] + directive_lines + [replaced] + lines[unbound_lineno + 1:]
    return "".join(out)


def verify_port_pin(profile_text: str, plan: PortPinPlan) -> validator.ProfileReport:
    """Apply the pin to *profile_text* and validate the result with the
    normal validator contract (the profile's declared write prefixes
    come from the registry via the profile name). A successful pin is
    strict-clean: 0 errors, 0 warnings."""
    applied = apply_port_pin(profile_text, plan)
    return validator.validate_text(applied, plan.profile)


# ---------------------------------------------------------------------------
# Transition patch rendering — the complete mechanical diff, dry-run only
# ---------------------------------------------------------------------------


def _replace_once(text: str, old_stripped: str, new_stripped: str, label: str) -> str:
    """Replace the single line whose stripped form equals *old_stripped*
    (indentation and line ending preserved with *new_stripped*). Fails
    loudly when the anchor occurs zero or more than one times — a
    mechanical patch must never guess."""
    lines = text.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.strip() == old_stripped]
    if len(matches) != 1:
        raise PortPinTransitionError(
            f"{label}: anchor line {old_stripped!r} occurs {len(matches)} "
            "times — the repo has drifted from the transition recipe; "
            "re-derive the patch by hand"
        )
    i = matches[0]
    original = lines[i]
    indent = original[: len(original) - len(original.lstrip())]
    eol = original[len(original.rstrip("\r\n")):]
    lines[i] = indent + new_stripped + eol
    return "".join(lines)


def _transition_transform(rel: str, text: str, plan: PortPinPlan) -> str:
    """The post-transition content of one :data:`PORT_PIN_TRANSITION_FILES`
    file. Pure: returns new text, never writes."""
    if rel == "profiles/pengy-agent":
        # The profile edit is the planner's own transformation (grant
        # replaced, local # sanctioned: line inserted) — it carries the
        # fail-loud preconditions itself.
        return apply_port_pin(text, plan)
    if rel == "pengyos_apparmor/validator.py":
        # The lockstep entry in SANCTIONED_NETWORK_RULES: one exact rule
        # per line inside the frozenset.
        return _replace_once(
            text,
            f'"{UNBOUND_MODEL_SERVER_RULE}",',
            f'"{plan.new_rule}",',
            rel,
        )
    if rel == "tests/test_profiles.py":
        # The test_network_grants pin: the expected per-profile grant set.
        return _replace_once(
            text,
            f'expected = {{"pengy-agent": ["{UNBOUND_MODEL_SERVER_RULE}"]}}',
            f'expected = {{"pengy-agent": ["{plan.new_rule}"]}}',
            rel,
        )
    if rel == "tests/test_validator.py":
        # The unbound-form anchor in
        # test_committed_grant_is_the_unbound_port_form: the rule now
        # carries port=N and the assertion flips from "no port" to "the
        # pinned port is present".
        text = _replace_once(
            text,
            "rule = _model_server_rule()",
            f'rule = _model_server_rule("{plan.port}")',
            rel,
        )
        text = _replace_once(
            text,
            'assert "port=" not in rule',
            f'assert f"port={plan.port}" in rule',
            rel,
        )
        return text
    raise PortPinTransitionError(f"unhandled transition file: {rel!r}")


@dataclass(frozen=True)
class TransitionFileEdit:
    """One file of the mechanical transition: the relative path (within
    the tool root), the pre- and post-transition content."""

    rel: str
    old: str
    new: str


def compute_transition(port: Union[int, str], repo_root: Union[str, Path] = ".") -> tuple:
    """Compute the complete mechanical port-pin transition for *port*
    against the repo under *repo_root* (default: the current directory).

    Returns one :class:`TransitionFileEdit` per
    :data:`PORT_PIN_TRANSITION_FILES` entry, in that order. Pure: never
    writes a file. Every anchor is matched verbatim against the current
    file content — a missing file, a missing/duplicated anchor line, or
    a file the transformation leaves unchanged all raise
    :class:`PortPinTransitionError` (the repo has drifted from the
    transition recipe). This is the shared core of
    :func:`render_transition_patch` (dry-run diff) and
    :func:`apply_transition` (in-place write).
    """
    plan = plan_port_pin(port, profile="pengy-agent")
    root = Path(repo_root)
    edits: list[TransitionFileEdit] = []
    for rel in PORT_PIN_TRANSITION_FILES:
        path = root / rel
        if not path.is_file():
            raise PortPinTransitionError(
                f"transition file missing under {root}: {rel}"
            )
        old = path.read_text(encoding="utf-8")
        try:
            new = _transition_transform(rel, old, plan)
        except PortPinTransitionError as exc:
            # Prefix the file label so the drift is locatable (the
            # profile transform's own errors do not carry it).
            msg = str(exc)
            raise PortPinTransitionError(
                msg if msg.startswith(rel) else f"{rel}: {msg}"
            ) from exc
        if new == old:
            raise PortPinTransitionError(
                f"{rel}: the transition produced no change — the repo has "
                "drifted from the transition recipe"
            )
        edits.append(TransitionFileEdit(rel=rel, old=old, new=new))
    return tuple(edits)


#: The suffix of the staging (temporary) files :func:`atomic_commit`
#: creates: each transition file is staged next to its target as
#: ``<target>.<n>.pin-tmp`` and committed with an atomic rename.
STAGING_SUFFIX = ".pin-tmp"


def _stage_one(root: Path, index: int, edit) -> Path:
    """Stage one edit record (anything with ``.rel`` / ``.new``): write
    the post-transition content to a temporary file in the target's own
    directory and fsync it. The temp name carries *index* (the record's
    position in the commit sequence) so every file stages into its own
    temp file. Raises OSError on a failed write (disk full, read-only
    fs, …); the target is untouched and the temp file is removed."""
    target = root / edit.rel
    tmp = target.parent / f"{target.name}.{index}{STAGING_SUFFIX}"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.write_bytes(edit.new.encode("utf-8"))
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def atomic_commit(root: Path, edits: tuple) -> None:
    """Commit a sequence of edit records (anything with ``.rel`` /
    ``.new`` — :class:`TransitionFileEdit` from the port-pin transition
    or ``portdocsync.DocFileEdit`` from the docs sync) in place under
    *root*, staged-atomically.

    Two phases: first every file's new content is written to a
    temporary staging file in the target's own directory and fsynced;
    only then is each staging file swapped over its target with
    ``os.replace`` (atomic on POSIX — the target is always either the
    complete old or the complete new content, never a torn write). If
    the write phase fails, no target is touched at all and every staged
    file is removed. If the commit phase fails partway, the already
    swapped files keep their new content and the not-yet-swapped files
    keep their old content (a *mixed* — never *torn* — state the
    ``port-pin-status`` probe reports ``partial``); the remaining
    staging files are removed.
    """
    staged: list[Path] = []
    try:
        for i, edit in enumerate(edits):
            staged.append(_stage_one(root, i, edit))
    except BaseException:
        for tmp in staged:
            tmp.unlink(missing_ok=True)
        raise
    for i, (edit, tmp) in enumerate(zip(edits, staged)):
        target = root / edit.rel
        try:
            os.replace(tmp, target)
        except BaseException:
            # The temp of the failed rename survived the failed
            # os.replace (src is not consumed) — clean it up too.
            for left in staged[i:]:
                left.unlink(missing_ok=True)
            raise


def apply_transition(port: Union[int, str], repo_root: Union[str, Path] = ".") -> tuple:
    """Apply the complete mechanical port-pin transition for *port* in
    place under *repo_root*.

    Computes the transition (:func:`compute_transition`), validates the
    transformed profile with the normal validator contract (it must be
    strict-clean — 0 errors, 0 warnings — before anything is written),
    and then commits the four transition files with :func:`atomic_commit`
    (staged writes + atomic renames: an interrupted apply can never
    leave a *torn* file — only a mixed pre/post state, which the
    ``port-pin-status`` probe reports ``partial``). Returns the applied
    :class:`TransitionFileEdit` records.

    The transition is one-way: once applied, the profile carries the
    pinned form and :func:`compute_transition` raises again (a double
    transition cannot stack silently). The rendered patch
    (``plan-port-pin <port> --patch``) remains the reviewable reference
    for reconciling a mixed state by hand.
    """
    edits = compute_transition(port, repo_root)
    for edit in edits:
        if edit.rel == "profiles/pengy-agent":
            report = validator.validate_text(edit.new, "pengy-agent")
            if not report.ok or report.warnings:
                raise PortPinTransitionError(
                    "the transformed profile is not strict-clean "
                    f"({len(report.errors)} error(s), "
                    f"{len(report.warnings)} warning(s)) — refusing to write"
                )
    atomic_commit(Path(repo_root), edits)
    return edits


def render_transition_patch(port: Union[int, str], repo_root: Union[str, Path] = ".") -> str:
    """Render the complete mechanical port-pin transition as a unified
    diff (``a/`` → ``b/`` paths, 3 lines of context) over
    :data:`PORT_PIN_TRANSITION_FILES`, relative to *repo_root* (default:
    the current directory).

    Every anchor is matched verbatim against the *current* file content:
    a missing file, a missing/duplicated anchor line, or a file the
    transformation leaves unchanged all raise
    :class:`PortPinTransitionError` — a patch that would not apply is
    worse than no patch. The port must be the one PengyOS has defined
    upstream (this function never invents one); nothing is written.
    """
    edits = compute_transition(port, repo_root)
    sections = []
    for edit in edits:
        old, new, rel = edit.old, edit.new, edit.rel
        # difflib emits blank context lines as ""; `git apply` requires
        # " " (a single space) for those, so pad them.
        diff = [
            " " if not line or line[0] not in "-+@ " else line
            for line in difflib.unified_diff(
                old.splitlines(),
                new.splitlines(),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                lineterm="",
                n=3,
            )
        ]
        if not diff:
            raise PortPinTransitionError(f"{rel}: no diff was rendered")
        sections.append("\n".join(diff))
    # A trailing newline is required: `git apply` treats a final
    # unterminated line as a corrupt patch.
    return "\n".join(sections) + "\n"
