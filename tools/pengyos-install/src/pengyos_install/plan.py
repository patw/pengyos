"""The pure install-plan renderer.

.. warning:: **PARKED (2026-09-28).** This is the A/B + UKI install path from
   ``docs/install-update-architecture.md``, which is parked. Its argv is not
   executable against a real disk (see the package README) and no front-end
   uses it. The working install path is :mod:`pengyos_install.image`.

``plan`` is the *engine core*: it turns the two validated JSON inputs — the signed
``Release`` manifest and the target ``Layout`` — into an ordered, deterministic
:class:`~pengyos_install.models.Plan`: a list of :class:`~pengyos_install.models.Step`
objects, **every step a shell-quotable argv** (never a single shell string).

The plan is the artifact the :mod:`pengyos_install.install` engine executes and
the same artifact the updater consumes. It is **pure** (no I/O, no shell, no
side effects) so it is golden-testable: for a fixed (release, layout) triple the
rendered plan is byte-stable.

## The plan, in order

::

    partition   create the GPT table + partitions on the target device
    fs          create the filesystems on each partition
    slot        write the rootfs to the INACTIVE slot, then commit (atomic)
    uki         write the Unified Kernel Image onto the ESP
    loader      install the systemd-boot loader entries on the ESP
    data        create the data subvolumes on the data partition
    provision   first-boot provisioning (fstab / hostname / users+ssh / network)

The write is **atomic**: the rootfs is always written to the *inactive* slot
(the one the release does not name) and only *committed* to the active slot at
the end — a crash mid-write leaves the other slot bootable. The release's
``slot`` is authoritative for the active slot; the layout cross-checks it against
the declared slots.

The provision phase renders the first-boot steps (fstab, hostname, users / SSH
keys, network) from ``install.toml`` via :mod:`pengyos_install.provision` (a
later unit). The plan emits it as a single ``pengyos-install provision-plan`` CLI
step so the plan is self-contained — the CLI reads ``install.toml`` itself.
"""

from __future__ import annotations

from pengyos_install.models import (
    Layout,
    Plan,
    Release,
    Step,
    ValidationError,
)

__all__ = [
    "PlanBuilder",
    "render_plan",
    "PROVISION_PLAN_SCRIPT",
]

# The name of the console script that renders the first-boot provisioning steps.
# The plan emits a single step that runs it against the mounted target.
PROVISION_PLAN_SCRIPT = "pengyos-install"


class PlanBuilder:
    """Accumulate :class:`Step`s into an ordered :class:`Plan`.

    A thin, explicit builder so the step order is readable and each phase is a
    named method (easy to extend / unit-test in isolation).
    """

    def __init__(self) -> None:
        self._steps: list[Step] = []

    def add(
        self,
        argv,
        *,
        description: str = "",
    ) -> "PlanBuilder":
        """Append a step (return ``self`` for chaining).

        *argv* may be a :class:`Step` (already built) or a sequence of argv
        parts (built via :meth:`Step.from_argv`).
        """
        if isinstance(argv, Step):
            self._steps.append(argv)
        else:
            self._steps.append(
                Step.from_argv(argv, description=description)
            )
        return self

    def plan(self) -> Plan:
        """Materialise the accumulated steps into a :class:`Plan`."""
        return Plan.from_steps(self._steps)


def _partition_steps(layout: Layout, builder: PlanBuilder) -> None:
    """Phase 1: create the partition table and the partitions.

    ``parted`` is used for both GPT and msdos; ``mklabel`` (re)creates the table,
    then one ``mkpart`` per declared partition. The ESP is a FAT *type* so most
    tools recognise it; the root slots are plain partitions (the release names
    which one is active).
    """
    target = layout.target
    table = layout.table

    builder.add(
        ["parted", target, "--script", "mklabel", table],
        description=f"create the {table} partition table on {target}",
    )

    # ESP first (index 1), then the two root slots, then data — stable order.
    builder.add(
        [
            "parted",
            target,
            "--script",
            "mkpart",
            "esp",
            "fat16",
            "0%",
            "100%",
        ],
        description="create the ESP partition",
    )

    for name in sorted(layout.slots):
        builder.add(
            [
                "parted",
                target,
                "--script",
                "mkpart",
                name,
                "ext4",
                "0%",
                "100%",
            ],
            description=f"create the {name} root partition",
        )

    builder.add(
        [
            "parted",
            target,
            "--script",
            "mkpart",
            "data",
            "btrfs",
            "0%",
            "100%",
        ],
        description="create the data partition",
    )


def _fs_steps(layout: Layout, builder: PlanBuilder) -> None:
    """Phase 2: create a filesystem on each partition.

    ESP -> FAT, root slots -> ext4, data -> btrfs (so the data subvolumes have
    somewhere to live).
    """
    builder.add(
        ["mkfs.fat", "-n", "EFI", layout.esp.partition],
        description="format the ESP as FAT",
    )
    for name in sorted(layout.slots):
        builder.add(
            ["mkfs.ext4", "-L", f"pengyos-{name}", layout.slot(name).partition],
            description=f"format the {name} root slot as ext4",
        )
    builder.add(
        ["mkfs.btrfs", "-L", "data", layout.data.partition],
        description="format the data partition as btrfs",
    )


def _slot_steps(
    release: Release, layout: Layout, builder: PlanBuilder
) -> None:
    """Phase 3: write the rootfs to the INACTIVE slot, then commit (atomic).

    The release names the active slot (``release.slot``); the rootfs is written
    to the *other* slot, then the active slot is *committed* by re-writing it.
    This makes the write atomic: a crash mid-write leaves the untouched slot
    bootable. The active slot is cross-checked against the declared slots.
    """
    active = release.slot
    layout.assert_has_slot(active)
    inactive = _inactive_slot(active, layout)

    active_slot = layout.slot(active)
    inactive_slot = layout.slot(inactive)

    builder.add(
        [
            "dd",
            f"if={release.rootfs.path}",
            f"of={inactive_slot.partition}",
            "status=progress",
            "conv=fsync",
        ],
        description=f"write rootfs to the inactive slot {inactive}",
    )
    builder.add(
        [
            "dd",
            f"if={release.rootfs.path}",
            f"of={active_slot.partition}",
            "status=progress",
            "conv=fsync",
        ],
        description=f"commit the rootfs to the active slot {active}",
    )


def _uki_steps(release: Release, layout: Layout, builder: PlanBuilder) -> None:
    """Phase 4: write the Unified Kernel Image onto the ESP.

    The UKI is dropped at the firmware boot path on the ESP so OVMF/UEFI finds it
    without any loader (the classic ``bootx64.efi`` fallback name).
    """
    dest = layout.esp.partition
    builder.add(
        [
            "dd",
            f"if={release.uki.path}",
            f"of={dest}/EFI/BOOT/bootx64.efi",
            "status=progress",
            "conv=fsync",
        ],
        description="write the UKI to the ESP EFI boot path",
    )


def _loader_steps(layout: Layout, builder: PlanBuilder) -> None:
    """Phase 5: install the systemd-boot loader entries on the ESP."""
    builder.add(
        ["install-bootctl", layout.esp.partition, "--dir-only"],
        description="install the systemd-boot loader entries on the ESP",
    )


def _data_steps(layout: Layout, builder: PlanBuilder) -> None:
    """Phase 6: create the data subvolumes on the (btrfs) data partition."""
    data = layout.data.partition
    for subvol in ("/data", "/data/home", "/data/var/log"):
        builder.add(
            ["btrfs", "subvolume", "create", f"{data}{subvol}"],
            description=f"create the data subvolume {subvol}",
        )


def _provision_step(
    layout_path: str = "layout.json", *, slot: str = "a"
) -> Step:
    """Phase 7: first-boot provisioning, emitted as a single CLI step.

    The provision phase renders the first-boot steps (fstab, hostname, users /
    SSH keys, network) from ``install.toml`` via :mod:`pengyos_install.provision`.
    The plan emits a single ``pengyos-install provision-plan`` CLI step so the
    plan is self-contained — the CLI reads ``install.toml`` (and the layout, for
    the fstab partition paths) itself. ``--layout`` / ``--slot`` are threaded
    through from the planning inputs so the fstab lists the correct partitions.
    """
    return Step.from_argv(
        [
            PROVISION_PLAN_SCRIPT,
            "provision-plan",
            "--layout",
            layout_path,
            "--slot",
            slot,
            "--config",
            "install.toml",
        ],
        description="first-boot provisioning (fstab / hostname / users / network)",
    )


def render_plan(
    release: Release,
    layout: Layout,
    *,
    layout_path: str = "layout.json",
) -> Plan:
    """Render the ordered install plan from the release + layout.

    Pure: no I/O, no shell, no side effects. Deterministic for a fixed
    (release, layout) triple — the plan is golden/reproducible.

    Raises :class:`ValidationError` (via :meth:`Layout.assert_has_slot`) if the
    release's slot is not one of the declared slots.

    ``layout_path`` is threaded into the trailing ``provision-plan`` step so the
    fstab the first boot renders lists the correct partition paths (defaults to
    ``layout.json``; the ``plan`` CLI passes the layout path it was given).
    """
    builder = PlanBuilder()
    _partition_steps(layout, builder)
    _fs_steps(layout, builder)
    _slot_steps(release, layout, builder)
    _uki_steps(release, layout, builder)
    _loader_steps(layout, builder)
    _data_steps(layout, builder)
    builder.add(
        _provision_step(layout_path, slot=release.slot),
    )
    return builder.plan()


def _inactive_slot(active: str, layout: Layout) -> str:
    """Return the *other* declared slot (the inactive one for the write)."""
    others = [name for name in layout.slots if name != active]
    if not others:
        raise ValidationError(
            f"layout must declare at least two slots for an atomic write; "
            f"only {active!r} is declared"
        )
    return others[0]
