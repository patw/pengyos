"""Validate a medium plan (spec.md, item 2).

``validate_plan`` is the single source of truth for "a well-formed plan". It is
what ``plan`` (item 3) runs the plan through before emitting it, and what
``lint`` (item 5) runs a plan through to accept/reject it.

The checks fall into two groups:

* **Closed vocabulary** — ``kind`` is a known :class:`MediumKind`, ``edition``
  is a known :class:`Edition`, and the live rootfs ``format`` is a known
  :class:`LiveRootfsFormat` (item 2's "with a validator"). An unknown value
  cannot silently slip into a plan.
* **The medium carries its parts** (spec.md "lint" / acceptance criteria):
  * the **installer** is present (``installer.binary`` is non-empty);
  * a **boot entry** is present (its ``kind`` and ``loader`` are non-empty);
  * the **release** is present (``release.version`` is non-empty);
  * the **os-release names the edition** (``os_release.id`` == ``edition``);
  * and the edition agrees across the plan, the release and the os-release
    (``edition`` == ``release.edition`` == ``os_release.id``).

Every failure carries a stable machine-readable ``code`` (so callers and tests
can name the exact problem) plus a human ``message``. :func:`validate_plan`
returns the list of all problems (so one run reports every defect);
:func:`plan_is_valid` is the boolean convenience wrapper.

Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass

from pengyos_media.model import Edition, MediumKind, MediumPlan, LiveRootfsFormat


@dataclass(frozen=True)
class PlanError:
    """A single validation problem: a stable ``code`` and a human ``message``."""

    code: str
    message: str


# Validation codes. Stable on purpose: callers (and the lint CLI's JSON verdict)
# key off them, so they must not change once published.
INVALID_KIND = "invalid-kind"
INVALID_EDITION = "invalid-edition"
INVALID_LIVE_FS_FORMAT = "invalid-live-fs-format"
RELEASE_MISSING_VERSION = "release-missing-version"
INSTALLER_MISSING = "installer-missing"
BOOT_ENTRY_MISSING = "boot-entry-missing"
EDITION_MISMATCH_RELEASE = "edition-mismatch-release"
OS_RELEASE_MISMATCH = "os-release-mismatch"


def validate_plan(plan: MediumPlan) -> list[PlanError]:
    """Return every problem with ``plan`` (empty list when it is well-formed)."""
    errors: list[PlanError] = []

    # --- Closed vocabulary -------------------------------------------------
    try:
        MediumKind(plan.kind)
    except ValueError:
        errors.append(
            PlanError(
                INVALID_KIND,
                f"unknown medium kind {plan.kind!r} "
                f"(expected one of {[k.value for k in MediumKind]})",
            )
        )

    try:
        edition = Edition(plan.edition)
    except ValueError:
        edition = None
        errors.append(
            PlanError(
                INVALID_EDITION,
                f"unknown edition {plan.edition!r} "
                f"(expected one of {[e.value for e in Edition]})",
            )
        )

    try:
        LiveRootfsFormat(plan.live_rootfs.format)
    except ValueError:
        errors.append(
            PlanError(
                INVALID_LIVE_FS_FORMAT,
                f"unknown live rootfs format {plan.live_rootfs.format!r} "
                f"(expected one of {[f.value for f in LiveRootfsFormat]})",
            )
        )

    # --- The medium carries its parts (spec.md "lint") ---------------------
    if not plan.installer.binary:
        errors.append(
            PlanError(
                INSTALLER_MISSING,
                "the medium omits the installer (installer.binary is empty)",
            )
        )

    if not plan.boot_entry.kind or not plan.boot_entry.loader:
        errors.append(
            PlanError(
                BOOT_ENTRY_MISSING,
                "the medium omits the boot entry (boot_entry.kind/loader is empty)",
            )
        )

    if not plan.release.version:
        errors.append(
            PlanError(
                RELEASE_MISSING_VERSION,
                "the medium omits the release (release.version is empty)",
            )
        )

    # --- Agreement across the plan -----------------------------------------
    # edition == release.edition == os_release.id: the edition names the medium,
    # the release it ships from, and the os-release it carries all agree.
    if edition is not None:
        if plan.release.edition != edition.value:
            errors.append(
                PlanError(
                    EDITION_MISMATCH_RELEASE,
                    f"edition {plan.edition!r} != release edition {plan.release.edition!r}",
                )
            )
        if plan.os_release.id != edition.value:
            errors.append(
                PlanError(
                    OS_RELEASE_MISMATCH,
                    f"os-release id {plan.os_release.id!r} does not name the "
                    f"edition {edition.value!r}",
                )
            )

    return errors


def plan_is_valid(plan: MediumPlan) -> bool:
    """Whether ``plan`` is well-formed (no validation problems)."""
    return not validate_plan(plan)
