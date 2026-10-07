"""The deterministic seal plan model.

Given a pengyos-build output directory, the *plan* discovers the real
artifact/SBOM names the build actually produced and computes the ordered steps
the seal will take. It is a pure model: :func:`build_plan` reads nothing but
the build tree via the injected runner and never writes, and :meth:`SealPlan.render`
/:meth:`SealPlan.to_json` only serialise what was computed. The seal stages
themselves (sign, name, repro, witness, verify) are executed by the follow-up
items; the plan merely *describes* them, with the exact argv each step would run.

## Discovery

``pengyos-build`` writes its SBOM and repro report under ``images/<target>.<fmt>.json``
(``<target>`` varies, e.g. ``m2`` or ``pengyos-x86_64``); the external witness,
however, looks for conventional names. The plan reuses the
``pengyos-witness-bundle`` alias convention to discover the real names:

* SBOM — the first existing file matching one of ``SBOM_ALIASES``
  (``images/*.spdx.json`` most specific, then ``sbom.spdx.json``, then the
  broad ``*.spdx.json``), mirroring
  ``pengyos_witness_bundle.layout.CANONICAL_LAYOUT`` sbom aliases.
* repro report — the first existing file matching one of ``REPRO_ALIASES``
  (``repro-report.json``, then ``images/repro-report.json``, then
  ``images/*.repro.json``), a subset of the witness-bundle repro aliases.

The expected artifacts (``images/rootfs.ext4``, ``images/bzImage``) and the
verifier's conventional names (``sbom.spdx.json``, ``repro-report.json``,
``.sig``) are pinned to ``pengyos_verifier.checks`` in :mod:`.cli`; the plan
does not re-derive them, it only discovers the *target-specific* names the build
emitted.

## The ordered steps

The seal stages follow the spec's ``sign -> name -> repro -> witness -> verify``
order:

1. **sign** — one :class:`Step` (``kind="sign"``) per expected artifact,
   ``pengyos-sign sign <artifact> --key <seed> --sig <artifact>.sig``.
2. **name** — one :class:`Step` (``kind="name"``) that symlinks the discovered
   SBOM to the verifier's conventional root name (``sbom.spdx.json``). Absent
   SBOM → no step (the follow-up stage records the absence; the plan lists
   nothing it cannot do).
3. **repro** — one :class:`Step` (``kind="repro"``). Its ``status`` is
   deterministic from the build tree + ``--prev``: ``existing`` when a report is
   already present, ``diff`` when ``--prev`` was given (a diff would be run),
   else ``skip`` (a single build cannot compare itself — a recorded skip, not a
   failure).
4. **witness** — one :class:`Step` (``kind="witness"``),
   ``pengyos-witness-bundle run --build DIR [--pub PUB]``.
5. **verify** — one :class:`Step` (``kind="verify"``),
   ``pengyos-verifier check --build DIR [--attest LOG] [--pub PUB]``.

The step list is deterministic for a given ``(build_dir, key, pub, attest, prev)``.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
from typing import Optional

import fnmatch

from . import cli
from .runner import Runner

# --- Discovery aliases (the witness-bundle convention, pinned here) ----------

#: The SBOM alias globs, most specific first. Mirrors
#: ``pengyos_witness_bundle.layout`` sbom aliases: the build emits
#: ``images/<target>.spdx.json``; the witness reads ``sbom.spdx.json``.
SBOM_ALIASES: tuple[str, ...] = (
    "images/*.spdx.json",
    "sbom.spdx.json",
    "*.spdx.json",
)

#: The repro-report alias globs, most specific first. A subset of the
#: witness-bundle repro aliases: the canonical witness name, then the build's
#: ``images/`` location, then the format-generic form.
REPRO_ALIASES: tuple[str, ...] = (
    "repro-report.json",
    "images/repro-report.json",
    "images/*.repro.json",
)


# --- Step model -------------------------------------------------------------

# The repro status values (deterministic, from the build tree + --prev).
REPRO_EXISTING = "existing"
REPRO_DIFF = "diff"
REPRO_SKIP = "skip"


@dataclass(frozen=True)
class Step:
    """One ordered step in the seal plan.

    Attributes:
        kind:   The seal stage — ``sign`` / ``name`` / ``repro`` / ``witness``
                / ``verify``.
        detail: A short, human one-line description of what this step does.
        argv:   The exact tool argv this step would execute, or ``None`` for a
                filesystem-only step (the ``name`` step symlinks; it has no
                external tool argv).
    """

    kind: str
    detail: str
    argv: tuple[str, ...] | None = None

    def render(self) -> str:
        """Render this step for the human ``plan`` output.

        The argv (when present) is quoted with :func:`shlex.quote` so the
        printed line is copy-pasteable and never ambiguous.
        """
        header = f"  {self.kind:>8}: {self.detail}"
        if self.argv:
            quoted = " ".join(shlex.quote(a) for a in self.argv)
            return f"{header}\n           argv: {quoted}"
        return header


@dataclass(frozen=True)
class SealPlan:
    """The deterministic seal for one build dir.

    Attributes:
        build_dir:  The build directory the plan was computed for.
        steps:      The ordered seal steps.
        target:     The discovered SBOM target name (``images/<target>.spdx.json``
                    → ``<target>``), or ``None`` when no SBOM was found.
        sbom_source: The discovered SBOM build-relative path, or ``None``.
        repro_source: The discovered repro-report build-relative path, or ``None``.
        repro_status: The deterministic repro status (``existing`` / ``diff`` /
            ``skip``).
        repro_argv: The ``pengyos-repro-report diff`` argv when the status is
            ``diff`` (a diff would be run), else ``None``.
    """

    build_dir: str
    steps: tuple[Step, ...]
    target: Optional[str] = None
    sbom_source: Optional[str] = None
    repro_source: Optional[str] = None
    repro_status: str = REPRO_SKIP
    repro_argv: Optional[tuple[str, ...]] = None
    witness_argv: Optional[tuple[str, ...]] = None
    verify_argv: Optional[tuple[str, ...]] = None

    def render(self) -> str:
        """Render the plan as the human ``plan`` output (one step per line)."""
        lines = [
            f"seal plan for {self.build_dir}",
            f"  target: {self.target if self.target is not None else '(none found)'}",
            f"  sbom:   {self.sbom_source if self.sbom_source is not None else '(none found)'}",
            f"  repro:  {self.repro_source if self.repro_source is not None else '(none found)'} "
            f"[{self.repro_status}]",
            "  steps:",
        ]
        lines.extend(step.render() for step in self.steps)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialise the plan to a plain ``dict`` (for JSON)."""

        def step_dict(step: Step) -> dict:
            return {
                "kind": step.kind,
                "detail": step.detail,
                "argv": list(step.argv) if step.argv is not None else None,
            }

        return {
            "build_dir": self.build_dir,
            "target": self.target,
            "sbom_source": self.sbom_source,
            "repro_source": self.repro_source,
            "repro_status": self.repro_status,
            "steps": [step_dict(step) for step in self.steps],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialise the plan to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


# --- Discovery ----------------------------------------------------------------


def _lowest_alias_index(rel: str, aliases: tuple[str, ...]) -> Optional[int]:
    """Return the index of the lowest-index alias matching ``rel``, else ``None``.

    Aliases are ordered most-specific first (``images/*.spdx.json`` before the broad
    ``*.spdx.json``), so the returned index measures *how specific* the match was.
    """
    for index, pat in enumerate(aliases):
        if fnmatch.fnmatch(rel, pat):
            return index
    return None


def _first_match(build_dir: str, aliases: tuple[str, ...],
                 runner: Runner) -> Optional[str]:
    """Return the build-relative POSIX path of the first existing file matching
    one of ``aliases`` (most-specific alias first, earliest path on ties), else
    ``None``.

    Mirrors ``pengyos_witness_bundle.layout.find_matching``'s tie-break: among
    every existing file that matches any alias, the winner is the one matching
    the lowest-index (most specific) alias, and among those the earliest in
    sorted path order. Discovery reads the build tree via the injected runner's
    :meth:`Runner.walk` (never writes) and never raises on a missing file.
    """
    best: tuple[int, str] | None = None  # (lowest_alias_index, rel_path)
    for rel in runner.walk(build_dir):
        index = _lowest_alias_index(rel, aliases)
        if index is None:
            continue
        candidate = (index, rel)
        if best is None or candidate < best:
            best = candidate
    return best[1] if best is not None else None


def discover_sbom(build_dir: str, runner: Runner) -> Optional[str]:
    """Discover the build's SBOM build-relative path (``images/<target>.spdx.json``)
    via the witness-bundle alias convention, else ``None``."""
    return _first_match(build_dir, SBOM_ALIASES, runner)


def target_from_sbom(sbom_source: str) -> Optional[str]:
    """Return the SBOM target name for a discovered path (``images/m2.spdx.json``
    -> ``m2``), or ``None`` for a path without the ``.spdx.json`` suffix.
    Reporting only. (``os.path.splitext`` only strips the last suffix, so the
    full ``.spdx.json`` is removed explicitly.)"""
    base = os.path.basename(sbom_source)
    if not base.endswith(".spdx.json"):
        return None
    return base[: -len(".spdx.json")]


def discover_repro(build_dir: str, runner: Runner) -> Optional[str]:
    """Discover the build's repro-report build-relative path via the
    witness-bundle alias convention, else ``None``."""
    return _first_match(build_dir, REPRO_ALIASES, runner)


# --- Plan construction --------------------------------------------------------


#: The key seed placeholder shown in the plan's signing argv when no key is
#: supplied (``plan`` has no ``--key``; the seal fills the real seed in at run).
KEY_PLACEHOLDER = "<SEED>"


def _sign_argv(artifact: str, key: Optional[str]) -> tuple[str, ...]:
    """The ``pengyos-sign`` argv for one artifact (pengyos-sig-v1 sibling).

    ``key`` is the real seed when the seal runs, or ``None`` (rendered as the
    ``<SEED>`` placeholder) when merely planning — so ``plan`` stays deterministic
    and key-free while still listing the exact expected argv shape.
    """
    sig_path = f"{artifact}{cli.SIGNATURE_SUFFIX}"
    seed = key if key is not None else KEY_PLACEHOLDER
    return (
        "pengyos-sign",
        "sign",
        artifact,
        "--key",
        seed,
        "--sig",
        sig_path,
    )


def build_plan(
    build_dir: str,
    *,
    key: Optional[str] = None,
    pub: Optional[str] = None,
    attest: Optional[str] = None,
    prev: Optional[str] = None,
    runner: Optional[Runner] = None,
) -> SealPlan:
    """Compute the deterministic seal plan for ``build_dir``.

    Reads the build tree only (via the injected runner's :meth:`Runner.walk`);
    never writes. ``key`` is optional: when given, the signing steps carry the
    real seed, otherwise they show the ``<SEED>`` placeholder (``plan`` is
    key-free and stays deterministic). ``pub`` / ``attest`` / ``prev`` shape the
    witness/verify/repro steps.
    """
    runner = runner or _OsRunner()
    build_dir = str(build_dir)

    sbom_source = discover_sbom(build_dir, runner)
    target = target_from_sbom(sbom_source) if sbom_source else None
    repro_source = discover_repro(build_dir, runner)

    # The repro status is deterministic from the tree + --prev: an existing
    # report is ``existing``; with --prev a diff would be run; otherwise a
    # single build cannot compare itself, so it is a recorded skip.
    if repro_source is not None:
        repro_status = REPRO_EXISTING
    elif prev is not None:
        repro_status = REPRO_DIFF
    else:
        repro_status = REPRO_SKIP

    steps: list[Step] = []

    # 1. sign — one step per expected artifact, in pinned order.
    for artifact in cli.EXPECTED_ARTIFACTS:
        steps.append(
            Step(
                kind="sign",
                detail=f"sign {artifact} (pengyos-sig-v1)",
                argv=_sign_argv(artifact, key),
            )
        )

    # 2. name — symlink the discovered SBOM to the verifier's conventional root.
    if sbom_source is not None:
        steps.append(
            Step(
                kind="name",
                detail=f"normalise SBOM {sbom_source} -> {cli.SBOM_ROOT_FILENAME}",
                argv=None,  # a filesystem symlink, no external tool
                # (extra detail carried on the plan, not the step argv)
            )
        )

    # 3. repro — existing / diff / skip.
    repro_argv: tuple[str, ...] | None = None
    if repro_status == REPRO_DIFF:
        repro_argv = (
            "pengyos-repro-report",
            "diff",
            "--a",
            build_dir,
            "--b",
            prev,
        )
    steps.append(
        Step(
            kind="repro",
            detail=(
                f"surface repro report "
                f"({repro_source if repro_source is not None else '(none)'} "
                f"[{repro_status}])"
            ),
            argv=repro_argv,
        )
    )

    # 4. witness — build the witness layout.
    witness_argv = ("pengyos-witness-bundle", "run", "--build", build_dir)
    if pub is not None:
        witness_argv += ("--pub", pub)
    steps.append(
        Step(
            kind="witness",
            detail="build the witness layout",
            argv=witness_argv,
        )
    )

    # 5. verify — run the verifier and surface its verdict + exit code.
    verify_argv = ["pengyos-verifier", "check", "--build", build_dir]
    if attest is not None:
        verify_argv += ["--attest", attest]
    if pub is not None:
        verify_argv += ["--pub", pub]
    steps.append(
        Step(
            kind="verify",
            detail="run pengyos-verifier and surface its verdict",
            argv=tuple(verify_argv),
        )
    )

    return SealPlan(
        build_dir=build_dir,
        steps=tuple(steps),
        target=target,
        sbom_source=sbom_source,
        repro_source=repro_source,
        repro_status=repro_status,
        repro_argv=repro_argv,
        witness_argv=witness_argv,
        verify_argv=verify_argv,
    )


class _OsRunner:
    """A filesystem-backed :class:`Runner` for production use.

    Wraps the plain ``os`` filesystem so :func:`build_plan` needs no injected
    runner when called directly (tests inject a :class:`MemoryRunner`). Only the
    read-only primitives discovery needs are implemented.
    """

    def exists(self, path: "os.PathLike[str] | str") -> bool:
        return os.path.exists(os.fspath(path))

    def is_dir(self, path: "os.PathLike[str] | str") -> bool:
        return os.path.isdir(os.fspath(path))

    def read_bytes(self, path: "os.PathLike[str] | str") -> bytes:
        with open(os.fspath(path), "rb") as fh:
            return fh.read()

    def write_bytes(self, path: "os.PathLike[str] | str", data: bytes) -> None:
        with open(os.fspath(path), "wb") as fh:
            fh.write(data)

    def symlink(self, target: "os.PathLike[str] | str",
                link: "os.PathLike[str] | str") -> None:  # pragma: no cover
        os.symlink(os.fspath(target), os.fspath(link))

    def readlink(self, link: "os.PathLike[str] | str") -> str:
        return os.readlink(os.fspath(link))

    def walk(self, path: "os.PathLike[str] | str") -> list[str]:
        root = os.fspath(path)
        if not os.path.isdir(root):
            return []
        return [
            os.path.relpath(full, root).replace(os.sep, "/")
            for dirpath, _dirnames, filenames in os.walk(root)
            for full in (os.path.join(dirpath, name) for name in filenames)
            if os.path.isfile(full)
        ]


__all__ = [
    "SBOM_ALIASES",
    "REPRO_ALIASES",
    "REPRO_EXISTING",
    "REPRO_DIFF",
    "REPRO_SKIP",
    "KEY_PLACEHOLDER",
    "Step",
    "SealPlan",
    "discover_sbom",
    "discover_repro",
    "target_from_sbom",
    "build_plan",
]
