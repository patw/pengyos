"""Emit the executor argv for a :class:`~pengyos_layout.model.Plan`.

``emit`` is the *executor-facing* layer of the planner: it turns a computed
:class:`~pengyos_layout.model.Plan` into an **ordered list of steps**, each a
shell-quotable ``argv`` for the tools the install engine runs against the target
disk:

* **sgdisk** — create each GPT partition at its byte offset and stamp its GPT
  ``type_guid`` (and unique ``guid``);
* **cryptsetup** — ``luksFormat`` + open a LUKS2 container and expose its
  decrypted mapper (every slot the planner wrapped in LUKS2, never the ESP);
* **mkfs**/*mkswap** — create the filesystem on each slot (or on the decrypted
  mapper for a LUKS slot);
* **mount** — mount every non-swap slot at its mountpoint.

The steps are the *data* the executor runs; this module never shells out, touches
a device, or needs root, so the whole pipeline is unit-testable in isolation.

## The executor pipeline, per partition

For every partition, in plan order, the emitter emits:

1. **create** — ``sgdisk -n <num>:<start>B:<end>B -t <num>:<type_guid> <device>``
   (``<end> = start + size``; the ``B`` suffix makes sgdisk take byte offsets, so
   the emitted layout is byte-precise to the plan);
2. **unique-guid** — ``sgdisk -u u:<guid> <num> <device>`` (the planner's
   deterministic per-partition GUID, so a re-emit is reproducible);
3. **LUKS** — *if the slot is LUKS-wrapped*: ``cryptsetup luksFormat --batch-mode
   <partition>`` then ``cryptsetup open --type luks --name <mapper>
   <partition> <mapper>``; the filesystem is created on the *mapper*, not the
   raw partition;
4. **format** — ``mkfs.<fs>`` for a normal slot, or ``mkswap <partition>`` for a
   swap slot (swap is never mkfs'd);
5. **mount** — ``mount <source> <mountpoint>`` for every slot with a mountpoint
   (swap and the LUKS-internal mapper are not mounted).

The ESP is created and formatted like any other slot but is never LUKS-wrapped
(the planner guarantees ``esp.luks is False``), so firmware reads its FAT32
directly from the raw partition before any key is available.

## Shell-quotable argv

Every step is a plain list of arguments (an ``argv``). That is the point: the
executor can hand it straight to ``subprocess.run(argv)`` with **no shell**, so
there is nothing to quote and no injection surface. ``Step.render()`` adds a
human/replay view via ``shlex.join``; ``Step.argv`` round-trips through
``shlex.split`` losslessly (``shlex.split(step.render()) == step.argv``), which
the tests assert.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import List, Optional

from .model import Plan, Role

# --- executor tool names -----------------------------------------------------
# The executable each step runs. Kept as constants so the emitted argv and the
# emitter agree on the tool name (and a test can assert on it).
SGDISK = "sgdisk"
CRYPTSETUP = "cryptsetup"
MKFS = "mkfs.%s"  # %fs -> "mkfs.ext4" etc.
MKSWAP = "mkswap"
MOUNT = "mount"


# --- the emitted step --------------------------------------------------------
@dataclass
class Step:
    """One executor command: a tool ``kind`` and its shell-quotable ``argv``.

    ``kind`` is the high-level operation (``"sgdisk"``, ``"cryptsetup"``,
    ``"mkfs.ext4"``, ``"mkswap"``, ``"mount"``); ``argv`` is the argument vector
    the executor runs. ``description`` is a human-readable summary for logs.
    """

    kind: str
    argv: List[str]
    description: str = ""

    def render(self) -> str:
        """The argv rendered as a shell command (for logging / replay)."""
        return shlex.join(self.argv)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "argv": list(self.argv), "description": self.description}

    @classmethod
    def from_dict(cls, data: dict) -> "Step":
        return cls(
            kind=data["kind"],
            argv=list(data["argv"]),
            description=data.get("description", ""),
        )


# --- the emitter -------------------------------------------------------------
class PlanEmitter:
    """Turn a :class:`~pengyos_layout.model.Plan` into ordered executor steps.

    ``emit(plan)`` returns the full ordered pipeline (sgdisk create + format +
    mount for every partition). ``emit_partition(plan, index)`` returns just the
    steps for one partition — the unit the executor loops over.
    """

    def __init__(self, plan: Plan) -> None:
        self.plan = plan

    # --- helpers ------------------------------------------------------------
    def _device(self) -> str:
        """The target device from the probe (``/dev/sda``)."""
        return self.plan.probe.device

    def _partition_device(self, index: int) -> str:
        """The partition device path: ``<device><number>`` (GPT numbers are 1-based)."""
        return f"{self._device()}{index + 1}"

    def _mapper_name(self, role: Role) -> str:
        """Deterministic LUKS mapper name for a wrapped slot."""
        return f"{role.value}_crypt"

    def _partition_number(self, index: int) -> str:
        """The GPT partition number (1-based) as sgdisk expects it."""
        return str(index + 1)

    def _type_guid(self, index: int) -> str:
        """The GPT type GUID for a partition (from the plan)."""
        return self.plan.partitions[index].type_guid

    def _unique_guid(self, index: int) -> str:
        """The planner's deterministic unique GUID for a partition."""
        return self.plan.partitions[index].guid

    def _create_argv(self, index: int) -> List[str]:
        """``sgdisk -n <num>:<start>B:<end>B -t <num>:<type_guid> <device>``.

        ``<end> = start + size``. The ``B`` suffix makes sgdisk take byte
        offsets, so the emitted layout is byte-precise to the plan.
        """
        p = self.plan.partitions[index]
        start = p.start
        end = p.start + p.size
        return [
            SGDISK,
            "-n",
            f"{self._partition_number(index)}:{start}B:{end}B",
            "-t",
            f"{self._partition_number(index)}:{self._type_guid(index)}",
            self._device(),
        ]

    def _guid_argv(self, index: int) -> List[str]:
        """``sgdisk -u u:<guid> <num> <device>`` — stamp the deterministic GUID."""
        return [
            SGDISK,
            "-u",
            f"u:{self._unique_guid(index)}",
            self._partition_number(index),
            self._device(),
        ]

    def _luks_format_argv(self, index: int) -> List[str]:
        """``cryptsetup luksFormat --batch-mode <partition>``."""
        return [CRYPTSETUP, "luksFormat", "--batch-mode", self._partition_device(index)]

    def _luks_open_argv(self, index: int) -> List[str]:
        """``cryptsetup open --type luks --name <mapper> <partition> <mapper>``."""
        p = self.plan.partitions[index]
        mapper = self._mapper_name(p.role)
        return [
            CRYPTSETUP,
            "open",
            "--type",
            "luks",
            "--name",
            mapper,
            self._partition_device(index),
            mapper,
        ]

    def _format_argv(self, index: int, source: str) -> List[str]:
        """``mkfs.<inner-fs> <source>`` (or ``mkswap <source>`` for swap).

        ``source`` is the raw partition for a plaintext slot or the LUKS mapper
        for a wrapped one. Swap is never mkfs'd: it gets ``mkswap``. A LUKS slot
        is formatted with the *inner* filesystem on the mapper — there is no
        ``mkfs.luks2``; the container itself is created by ``cryptsetup
        luksFormat``.
        """
        p = self.plan.partitions[index]
        if p.role == Role.SWAP:
            return [MKSWAP, source]
        return [MKFS % p.inner_fs_value(), source]

    def _format_kind(self, index: int) -> str:
        p = self.plan.partitions[index]
        if p.role == Role.SWAP:
            return MKSWAP
        return MKFS % p.inner_fs_value()

    def _mount_argv(self, index: int, source: str) -> List[str]:
        """``mount <source> <mountpoint>`` (only when the slot has a mountpoint)."""
        p = self.plan.partitions[index]
        return [MOUNT, source, p.mount]

    # --- per-partition ------------------------------------------------------
    def emit_partition(self, index: int) -> List[Step]:
        """The executor steps for the partition at ``index`` (in order)."""
        p = self.plan.partitions[index]
        device = self._partition_device(index)
        steps: List[Step] = []

        # 1. create the GPT partition at its byte offset with its type GUID.
        steps.append(
            Step(
                kind=SGDISK,
                argv=self._create_argv(index),
                description=f"create {p.role.value} partition ({p.size} bytes)",
            )
        )
        # 2. stamp the deterministic unique GUID.
        steps.append(
            Step(
                kind=SGDISK,
                argv=self._guid_argv(index),
                description=f"set {p.role.value} unique GUID",
            )
        )

        # 3. LUKS: format + open the container; the source becomes the mapper.
        luks_source: Optional[str] = None
        if p.luks:
            luks_source = f"/dev/mapper/{self._mapper_name(p.role)}"
            steps.append(
                Step(
                    kind=f"{CRYPTSETUP} luksFormat",
                    argv=self._luks_format_argv(index),
                    description=f"luksFormat {p.role.value}",
                )
            )
            steps.append(
                Step(
                    kind=f"{CRYPTSETUP} open",
                    argv=self._luks_open_argv(index),
                    description=f"open LUKS container {p.role.value}",
                )
            )

        # 4. format the slot (partition, or mapper for LUKS). Swap -> mkswap.
        steps.append(
            Step(
                kind=self._format_kind(index),
                argv=self._format_argv(index, luks_source or device),
                description=f"format {p.role.value} ({p.inner_fs_value()})",
            )
        )

        # 5. mount non-swap slots at their mountpoint (LUKS mounts the mapper).
        if p.mount is not None:
            steps.append(
                Step(
                    kind=MOUNT,
                    argv=self._mount_argv(index, luks_source or device),
                    description=f"mount {p.role.value} at {p.mount}",
                )
            )

        return steps

    def emit(self) -> List[Step]:
        """The full ordered executor pipeline for every partition."""
        steps: List[Step] = []
        for index in range(len(self.plan.partitions)):
            steps.extend(self.emit_partition(index))
        return steps


def emit(plan: Plan) -> List[Step]:
    """Convenience wrapper: the ordered executor steps for ``plan``."""
    return PlanEmitter(plan).emit()
