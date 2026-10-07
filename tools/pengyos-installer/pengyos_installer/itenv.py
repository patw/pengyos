"""Integration-test (IT) environment helpers for ``pengyos-installer``.

This module is the opt-in real-harness half of the test suite. The unit
suite is hermetic (an injected command runner, no disk, no root); the IT
tests — ``tests/test_integration.py`` — install onto a **loop device** and
boot the result with the sibling tool ``pengyos-qemu-verify``. That IT
requires root (loop devices, ``parted``/``mkfs.*``/``mount``) and real
binaries (``parted``, ``mkfs.vfat``, ``mkfs.ext4``, ``rsync``, ``blkid``,
``grub-install``, ``efibootmgr``, ``losetup``, ``qemu-system-x86_64`` plus
OVMF firmware), so it is strictly gated behind ``PENGYOS_IT=1``.

Everything in here that can be pure is pure: the argv builders
(:func:`losetup_attach_argv`, :func:`losetup_detach_argv`,
:func:`qemu_verify_boot_argv`) and the environment check
(:func:`missing_requirements`) take injected callables, so the unit tests
golden them without touching a device. The two functions that *do* act —
:func:`attach_loop` / :func:`detach_loop` — run only inside an IT that has
already passed :func:`missing_requirements` (root + every binary present).

The loop-device naming workaround
---------------------------------

The plan names its partitions via :func:`pengyos_installer.plan.
partition_device`: a loop device's name ends in a digit, so the plan emits
the ``p``-suffixed form ``<target>p1`` / ``<target>p2`` (the same rule as
NVMe). The kernel creates a loop device's partitions as
``/dev/loopNp1`` / ``/dev/loopNp2`` — exactly that ``p`` form — so the plan's
argv is already correct for a real ``/dev/loopN``.

The IT nonetheless installs to a **symlink tree** in a temp dir, so the
installed image (and qemu-verify's serial report) land under the test's
``tmp_path`` rather than at a fixed system path, and so the target's basename
is the real loop name (which is what makes the ``/sys/block`` check in
:func:`~pengyos_installer.plan.collect_target_probe` pass):

* ``<workdir>/loopN``       -> ``/dev/loopN`` (the installer's *target*)
* ``<workdir>/loopNp1``     -> ``/dev/loopNp1`` (partition 1 = the ESP)
* ``<workdir>/loopNp2``     -> ``/dev/loopNp2`` (partition 2 = root)

The symlink *names* are the ``p``-suffixed form the plan emits (see
:func:`attach_loop`), created *before* the install runs; they resolve as soon
as ``parted`` writes the GPT label and the kernel materialises
``/dev/loopNp1``/``p2``. Every tool in the plan (``parted``, ``mkfs.*``,
``mount``, ``blkid``, ``efibootmgr``, ``grub-install``) works through the
symlinks.
"""

from __future__ import annotations

import os
import stat as _stat
from dataclasses import dataclass
from typing import Callable, List, Sequence, Tuple

#: The environment variable that switches on the integration tests.
IT_ENV_VAR = "PENGYOS_IT"
#: The value of :data:`IT_ENV_VAR` that enables them (anything else skips).
IT_ENABLED_VALUE = "1"

#: Environment variable naming an explicit ``--rootfs-src`` for the IT.
ROOTFS_SRC_ENV_VAR = "PENGYOS_IT_ROOTFS_SRC"

#: Per-boot timeout for the qemu-verify step, in seconds.
BOOT_TIMEOUT_ENV_VAR = "PENGYOS_IT_TIMEOUT"
BOOT_TIMEOUT_DEFAULT = 300.0

#: Serial-console readiness marker the installed system must reach.
#: Mirrors the ``pengyos-qemu-verify`` default (PengyOS M1 reaches a login
#: prompt; the default assertion set then also checks for no kernel panic).
DEFAULT_EXPECT = "login:"

#: Sparse image size for the IT target disk: the documented 4 GiB minimum
#: plus headroom for a copied rootfs.
DEFAULT_IMAGE_SIZE_BYTES = 6 * 1024**3

#: Binaries the IT needs, in a stable order. Each must resolve on PATH
#: (``shutil.which``); the requirement message names the binary only — the
#: distro package that provides it is host-dependent.
REQUIRED_BINARIES: Tuple[str, ...] = (
    "parted",
    "mkfs.vfat",
    "mkfs.ext4",
    "rsync",
    "blkid",
    "grub-install",
    "efibootmgr",
    "losetup",
    "qemu-system-x86_64",
    "pengyos-qemu-verify",
)


def it_enabled(environ: "os._Environ[str] | None" = None) -> bool:
    """True if the integration tests are opted in (``PENGYOS_IT=1``).

    ``environ`` defaults to ``os.environ``; tests pass a plain dict.
    """
    env = os.environ if environ is None else environ
    return env.get(IT_ENV_VAR, "") == IT_ENABLED_VALUE


def missing_requirements(
    *,
    euid: int,
    which: Callable[[str], "str | None"],
    environ: "os._Environ[str] | None" = None,
    binaries: Sequence[str] = REQUIRED_BINARIES,
) -> List[str]:
    """The list of things the IT cannot run because of (stable order).

    Pure over its three inputs (so tests inject fakes and no host state is
    read here):

    * ``"root: requires euid 0 (loop devices, parted/mkfs/mount)"`` unless
      ``euid == 0``;
    * one ``"binary: <name> not found on PATH"`` per missing binary, in the
      order of ``binaries``.

    An empty list means the IT may proceed.
    """
    missing: list[str] = []
    if euid != 0:
        missing.append("root: requires euid 0 (loop devices, parted/mkfs/mount)")
    for name in binaries:
        if which(name) is None:
            missing.append(f"binary: {name} not found on PATH")
    return missing


def find_rootfs_source(
    environ: "os._Environ[str] | None" = None,
    home: "str | None" = None,
    exists: Callable[[str], bool] = os.path.exists,
) -> "str | None":
    """The ``--rootfs-src`` the IT should install (or ``None`` to skip).

    Discovery order (stable):

    1. ``$PENGYOS_IT_ROOTFS_SRC`` — an explicit path (must exist);
    2. ``$HOME/build/pengyos/output/images/rootfs.tar`` — the buildroot
       output of the PengyOS Core build (the same candidate
       ``pengyos-qemu-verify`` uses for its prebuilt image);
    3. otherwise ``None`` (the IT skips: the running host root is *not* a
       valid source — the installer's default is the *PengyOS* live stick's
       ``/``, which a test host does not have).

    Pure over ``environ``/``home``/``exists`` so tests inject fixtures.
    """
    env = os.environ if environ is None else environ
    explicit = env.get(ROOTFS_SRC_ENV_VAR)
    if explicit and exists(explicit):
        return explicit
    home_dir = home if home is not None else os.environ.get("HOME")
    if home_dir:
        candidate = f"{home_dir}/build/pengyos/output/images/rootfs.tar"
        if exists(candidate):
            return candidate
    return None


# --- loop-device lifecycle (argv builders are pure; the actors run under IT)


def losetup_attach_argv(image: str) -> Tuple[str, ...]:
    """argv that attaches ``image`` to a free loop device.

    ``losetup --show -f <image>`` prints the device path (e.g.
    ``/dev/loop7``) on stdout. Golden-stable.
    """
    return ("losetup", "--show", "-f", image)


def losetup_detach_argv(device: str) -> Tuple[str, ...]:
    """argv that detaches ``device``. Golden-stable."""
    return ("losetup", "-d", device)


@dataclass(frozen=True)
class LoopSetup:
    """What :func:`attach_loop` produced.

    ``device`` is the real ``/dev/loopN``; ``target`` and ``partitions``
    are the symlink paths the installer plan must use (see the module
    docstring for why).
    """

    image: str
    device: str
    target: str
    partitions: Tuple[str, str]
    workdir: str


def _partition_nodes(device: str) -> Tuple[str, str]:
    """The kernel's real partition-node paths for a loop ``device``."""
    return (device + "p1", device + "p2")


def attach_loop(
    image: str,
    workdir: str,
    runner: Callable[[Sequence[str]], "object"],
) -> LoopSetup:
    """Attach ``image`` to a free loop device and build the symlink tree.

    Runs (through ``runner``, which must execute for real — the IT's
    ``SubprocessRunner``):

    1. ``losetup --show -f <image>`` and takes the device from stdout;
    2. creates ``<workdir>/loopN`` -> ``/dev/loopN`` and the two partition
       symlinks ``<workdir>/loopNp{1,2}`` -> ``/dev/loopNp{1,2}`` — the
       symlink *names* match the ``p``-suffixed partition paths the plan
       emits (these resolve once ``parted`` writes the table and the kernel
       creates the ``p``-suffixed nodes).

    Raises :class:`RuntimeError` if ``losetup`` fails or prints no device.
    """
    r = runner(losetup_attach_argv(image))
    out = getattr(r, "stdout", "") or ""
    dev = out.strip().splitlines()[0].strip() if out.strip() else ""
    if getattr(r, "returncode", 1) != 0 or not dev.startswith("/dev/loop"):
        err = (getattr(r, "stderr", "") or "").strip()
        raise RuntimeError(f"losetup attach failed for {image}: {err or dev or out}")

    name = os.path.basename(dev)
    target = os.path.join(workdir, name)
    p1, p2 = _partition_nodes(dev)
    # The symlink *names* must match what the plan emits for these partitions
    # (plan.partition_device): the loop name ends in a digit, so the plan
    # uses the p-suffixed form `<workdir>/loopNp{1,2}`.
    part1 = os.path.join(workdir, name + "p1")
    part2 = os.path.join(workdir, name + "p2")
    os.symlink(dev, target)
    os.symlink(p1, part1)
    os.symlink(p2, part2)
    return LoopSetup(
        image=image,
        device=dev,
        target=target,
        partitions=(part1, part2),
        workdir=workdir,
    )


def detach_loop(
    setup: LoopSetup,
    runner: Callable[[Sequence[str]], "object"],
    *,
    best_effort: bool = False,
) -> List[str]:
    """Detach the loop device and remove the symlink tree.

    ``losetup -d <device>`` (a failure is an error unless ``best_effort``,
    in which case it is collected as a warning string), then the symlinks
    are removed (symlink removal never fails for a file we created; a
    dangling one — partition never created — is removed just as well).
    Returns the list of warnings.
    """
    warnings: list[str] = []
    r = runner(losetup_detach_argv(setup.device))
    if getattr(r, "returncode", 1) != 0:
        msg = (
            f"losetup detach failed for {setup.device}: "
            f"{(getattr(r, 'stderr', '') or '').strip()}"
        )
        if best_effort:
            warnings.append(msg)
        else:
            raise RuntimeError(msg)
    for path in (setup.target, *setup.partitions):
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        os.remove(path)
    return warnings


def make_sparse_image(path: str, size_bytes: int) -> None:
    """Create a zero-length file with apparent size ``size_bytes`` (sparse).

    The kernel serves the unwritten tail as zeros, so a 6 GiB image costs
    nothing on disk until the install writes it. Any I/O failure (the path
    is a directory, the fs rejects the size, ...) is a :class:`RuntimeError`,
    so the caller has one error path.
    """
    try:
        with open(path, "wb") as fh:
            fh.truncate(size_bytes)
    except OSError as exc:
        raise RuntimeError(f"cannot create sparse image {path}: {exc}") from exc
    st = os.stat(path)
    if st.st_size != size_bytes:
        raise RuntimeError(f"cannot size {path} to {size_bytes} bytes")
    if not _stat.S_ISREG(st.st_mode):
        raise RuntimeError(f"{path} is not a regular file")


# --- booting the result with pengyos-qemu-verify ----------------------------

def qemu_verify_boot_argv(
    image: str,
    *,
    timeout: "float | int" = BOOT_TIMEOUT_DEFAULT,
    expect: Sequence[str] = (DEFAULT_EXPECT,),
    json_out: "str | None" = None,
    junit_out: "str | None" = None,
    ram_mb: "int | None" = None,
) -> Tuple[str, ...]:
    """argv that boots ``image`` with ``pengyos-qemu-verify`` (UEFI/GRUB).

    An installed PengyOS disk boots from firmware (OVMF) through the
    ``EFI/BOOT`` loader the installer wrote, so no ``--kernel`` is given.
    ``expect`` defaults to :data:`DEFAULT_EXPECT` and each entry becomes a
    ``--expect`` flag; ``timeout`` becomes ``--timeout`` (a whole number of
    seconds, so the argv stays golden-stable); ``json_out``/``junit_out``
    add report writers. ``ram_mb`` (MiB) adds ``--ram`` when given.
    """
    argv: list[str] = ["pengyos-qemu-verify", "boot", image, "--uefi"]
    for e in expect:
        argv += ["--expect", e]
    argv += ["--timeout", str(int(timeout))]
    if ram_mb is not None:
        argv += ["--ram", str(int(ram_mb))]
    if json_out is not None:
        argv += ["--json", json_out]
    if junit_out is not None:
        argv += ["--junit", junit_out]
    return tuple(argv)
