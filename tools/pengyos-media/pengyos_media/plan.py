"""Build a medium plan from an edition release (spec.md).

``build_plan`` reads an edition release manifest (JSON) and produces a validated
:class:`~pengyos_media.model.MediumPlan` — the medium's layout (an ESP, the live
squashfs/rootfs, the embedded installer, the boot entry) for an edition. It is
**pure**: it only reads the release manifest and returns a plan; it never runs
any tool. Item 3 emits the build argv from this plan; item 5 lints it.

The release manifest is the versioned artifact ``pengyos-release`` produces;
``plan`` only needs its ``version`` (required) and ``channel`` (optional,
default ``testing``) — the edition comes from the ``--edition`` flag.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pengyos_media.model import Edition, MediumPlan, make_plan
from pengyos_media.validate import validate_plan


class PlanBuildError(Exception):
    """Raised when a plan cannot be built from a release manifest.

    ``code`` is a stable machine-readable reason (so the CLI can name the
    problem and tests can assert on it); ``message`` is the human text.
    """

    def __init__(self, message: str, *, code: str = "build-error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def read_release_manifest(path: str | Path) -> dict[str, Any]:
    """Read and parse the edition release manifest (JSON).

    Raises :class:`PlanBuildError` if the file is missing/unreadable or not
    valid JSON (neither touches the network; both are local failures).
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanBuildError(
            f"cannot read release manifest {path}: {exc.strerror or exc}",
            code="manifest-unreadable",
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanBuildError(
            f"invalid release manifest {path}: {exc}", code="manifest-bad-json"
        ) from exc
    if not isinstance(data, dict):
        raise PlanBuildError(
            f"invalid release manifest {path}: expected a JSON object",
            code="manifest-not-object",
        )
    return data


def build_plan(
    edition: str,
    release: dict[str, Any],
    *,
    os_release_id: str | None = None,
) -> MediumPlan:
    """Build a validated :class:`MediumPlan` for an edition + release.

    ``edition`` is the edition id (desktop/core/server); ``release`` is the
    parsed release manifest. ``version`` is read from the manifest (required —
    an empty version is a hard error, the medium ships a specific release);
    ``channel`` defaults to ``testing``. The plan is validated before it is
    returned (a well-formed plan is the contract ``plan`` emits).
    """
    version = str(release.get("version") or "").strip()
    if not version:
        raise PlanBuildError(
            "release manifest has no version (release.version is required)",
            code="release-missing-version",
        )
    channel = str(release.get("channel") or "testing").strip() or "testing"

    plan = make_plan(edition, version, channel=channel, os_release_id=os_release_id)
    errors = validate_plan(plan)
    if errors:
        # A plan built by build_plan is well-formed by construction; this is a
        # defensive guard (and a clear message if the model grows).
        raise PlanBuildError(
            "built plan failed validation: "
            + ", ".join(f"{e.code}: {e.message}" for e in errors),
            code="plan-invalid",
        )
    return plan
