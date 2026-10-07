"""QEMU argv construction for the PengyOS boot-test harness.

A *pure* function that turns
the documented CLI inputs into the exact ``qemu-system-x86_64`` argument vector
used for a headless q35 boot. It launches nothing and touches no I/O beyond
an optional ``/dev/kvm`` capability probe, which keeps it directly golden-testable.

Contract (finalised here; also recorded in ``spec.md``):

* Machine type is ``q35`` with a fixed ``-smp 2`` (matches the known-good
  reference boot).
* Acceleration: when ``/dev/kvm`` is readable **and** writable the argv gets
  ``-enable-kvm -cpu host``; otherwise it degrades to ``-cpu max`` (no
  ``-enable-kvm``). The decision is overridable via ``kvm=True/False`` so
  tests and CI can pin either path deterministically.
* Bare-metal kernel boot (``kernel`` given):
  ``-kernel BZIMAGE -drive file=IMAGE,if=virtio,format=raw,snapshot=on
  -append 'root=/dev/vda rw console=ttyS0'``.
  The append string includes ``rw`` to match the known-good reference argv
  (the spec's older sketch omitted it; see the spec's implementation notes).
* UEFI/OVMF firmware boot (``uefi=True``): the guest boots through real UEFI
  firmware rather than QEMU's built-in SeaBIOS, so the firmware is attached
  *before* the disk. Two wirings are emitted, chosen by the located firmware
  shape:

  * **split** (``OvmfFirmware`` with a ``vars`` path) — the canonical
    read-only CODE + writable VARS layout:
    ``-drive if=pflash,format=raw,unit=0,readonly=on,file=<CODE>``
    ``-drive if=pflash,format=raw,unit=1,file=<VARS>``.
  * **combined** (no separate VARS file, e.g. a single 4 MiB ``OVMF.fd``) —
    ``-bios <OVMF.fd>``.

  In UEFI mode the kernel lives on the image's own ESP/GRUB2, so ``kernel``
  (the bare-metal ``-kernel`` path) is *mutually exclusive* with ``uefi``: PengyOS
  images boot through firmware, not a bare ``-kernel`` you pass.
* Firmware/disk boot (no ``kernel``, no ``uefi``): the image is attached as a
  virtio drive with QEMU's built-in SeaBIOS; no ``-kernel``/``-append``.
  (For PengyOS images the correct path is ``uefi=True``.)
* **Guest disks are snapshotted** (``snapshot=True``, the default): the drive
  is rendered ``…,snapshot=on``, so QEMU opens the backing image **read-only**
  and serves every guest write from a throwaway overlay. A verification pass
  must never mutate the artifact it is verifying — without this, each boot
  rewrites the image (kernel journal, logs, timestamps) and silently breaks the
  hash the user is told to check with ``sha256sum -c``. ``snapshot=False``
  writes through to the image, for tests that must persist guest state.
* Headless tail, always present, in this order:
  ``-display none -serial file:LOG -monitor none -no-reboot``.
  The console is serial-only (readiness is read from ``ttyS0``), so
  ``-serial file:`` is the readiness channel; ``-no-reboot`` prevents the VM
  from respawning after a guest reset.

* TPM2 (swtpm) — when a ``tpm_socket`` path is supplied (an already-running
  swtpm unix-socket datapath, see :mod:`.tpm`), the guest is given an emulated
  TPM2 so it can do measured boot / key sealing. The QEMU side is two tokens,
  emitted *after* the disk/firmware section and *before* the headless tail:
  ``-chardev socket,id=<chr>,path=<SOCKET>``
  ``-tpmdev emulator,id=<dev>,chardev=<chr>``
  (ids default to ``tpmchr`` / ``tpm0``). Omitting ``tpm_socket`` leaves the
  argv byte-for-byte identical to the pre-TPM golden baseline, so the TPM
  column composes on top of every existing wiring (KVM/TCG, SeaBIOS/UEFI,
  bare-metal kernel / firmware disk boot).
"""

from __future__ import annotations

import os

__all__ = [
    "DEFAULT_QEMU_BIN",
    "DEFAULT_MACHINE",
    "DEFAULT_SMP",
    "DEFAULT_RAM_MB",
    "DEFAULT_SERIAL_LOG",
    "KVM_DEVICE",
    "BARE_METAL_APPEND",
    "OVMF_ENV_VARS",
    "DEFAULT_OVMF_CANDIDATES",
    "OvmfFirmware",
    "OvmfNotFound",
    "locate_ovmf",
    "kvm_available",
    "TPM_CHARDEV_ID",
    "TPM_DEV_ID",
    "tpm_qemu_args",
    "build_argv",
]

# --- Fixed defaults (golden baseline) --------------------------------------
DEFAULT_QEMU_BIN: str = "qemu-system-x86_64"
DEFAULT_MACHINE: str = "q35"
DEFAULT_SMP: str = "2"
DEFAULT_RAM_MB: int = 512
DEFAULT_SERIAL_LOG: str = "pengyos-serial.log"
KVM_DEVICE: str = "/dev/kvm"

# Exact kernel command line for bare-metal (-kernel) boots. Deliberately
# includes ``rw`` to match the known-good reference argv captured on the first
# builds; a read-only rootfs would not give a usable login prompt.
BARE_METAL_APPEND: str = "root=/dev/vda rw console=ttyS0"

# --- TPM2 (swtpm) QEMU wiring ---------------------------------------------
# Default QEMU object ids for the emulated TPM2 datapath. A ``-chardev``
# socket pointing at the swtpm unix socket is bridged to a ``-tpmdev``
# (``emulator``) backend; the guest's TPM2 device talks to swtpm over it.
TPM_CHARDEV_ID: str = "tpmchr"
TPM_DEV_ID: str = "tpm0"

# --- OVMF / UEFI firmware ---------------------------------------------------
# Environment variables that pin the firmware path, in priority order. The
# harness honours an explicit override first, then the conventional ``OVMF``
# variable QEMU's own docs use.
OVMF_ENV_VARS: "tuple[str, ...]" = ("PENGYOS_OVMF", "OVMF")

# Canonical on-disk locations, in priority order. The first existing, readable
# file wins. Covers the split layout (``OVMF_CODE.fd`` + ``OVMF_VARS.fd``,
# Fedora), the combined 4 MiB blob (``OVMF.fd`` / ``OVMF_CODE_4M.fd`` +
# ``OVMF_VARS_4M.fd``, Debian/Ubuntu) and the EDK2 layout used by some
# distros.
DEFAULT_OVMF_CANDIDATES: "tuple[str, ...]" = (
    "/usr/share/ovmf/OVMF_CODE.fd",
    "/usr/share/OVMF/OVMF_CODE.fd",
    "/usr/share/ovmf/OVMF.fd",
    "/usr/share/OVMF/OVMF.fd",
    "/usr/share/OVMF/OVMF_CODE_4M.fd",
    "/usr/share/edk2/x64/OVMF_CODE.fd",
    "/usr/share/edk2/x64/OVMF.fd",
    "/usr/share/qemu/OVMF.fd",
)


class OvmfNotFound(LookupError):
    """Raised when no usable OVMF firmware can be located.

    Carries the list of paths that were attempted so the message is actionable.
    """

    def __init__(self, attempted: "list[str] | tuple[str, ...]" = ()):  # noqa: D107
        self.attempted = list(attempted)
        if self.attempted:
            where = "\n  ".join(self.attempted)
            msg = ("no OVMF firmware found (tried:\n  " + where +
                   ")\n  Set the PENGYOS_OVMF environment variable or pass "
                   "--ovmf <path> to select it explicitly.")
        else:
            msg = ("no OVMF firmware could be located "
                   "(set PENGYOS_OVMF or pass --ovmf <path>)")
        super().__init__(msg)


class OvmfFirmware:
    """A located OVMF firmware: the read-only CODE region and (optionally) a
    separate, writable VARS region.

    Two shapes are modelled, both exercised by ``build_argv``:

    * **split** — ``code`` and ``vars`` are distinct files (the canonical
      ``OVMF_CODE.fd`` + ``OVMF_VARS.fd`` layout). Wires two pflash drives.
    * **combined** — a single CODE+VARS blob (``OVMF.fd`` / ``OVMF_CODE_4M.fd``)
      with no separate vars file. Wires ``-bios`` (the reliable way to boot a
      combined firmware image, which also sidesteps the VARS-writability issue
      a single pflash unit would hit).
    """

    __slots__ = ("code", "vars")

    def __init__(self, code: str, vars: "str | None" = None):  # noqa: A002
        if not code:
            raise ValueError("OvmfFirmware.code must be a non-empty string")
        self.code = code
        self.vars = vars  # None ⇒ combined single-file firmware

    @property
    def combined(self) -> bool:
        """True when this is a single CODE+VARS blob (wire via ``-bios``)."""
        return self.vars is None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"OvmfFirmware(code={self.code!r}, vars={self.vars!r})"


def _default_vars_path(code: str) -> "str | None":
    """Best-effort: derive a sibling VARS file for a split CODE blob.

    Mirrors the real on-disk layouts, which pair the CODE and VARS files by
    substituting ``_CODE`` for ``_VARS`` in the basename:

    * ``OVMF_CODE.fd``     → ``OVMF_VARS.fd``
    * ``OVMF_CODE_4M.fd``  → ``OVMF_VARS_4M.fd``

    Returns ``None`` (i.e. treat the blob as combined) when no conventional
    VARS sibling exists on disk, so a lone combined ``OVMF.fd`` is correctly
    classified as combined rather than pointing at a non-existent vars file.
    """
    base = os.path.basename(code)
    candidates = []
    if "_CODE" in base:
        candidates.append(base.replace("_CODE", "_VARS"))
    stem = base[:-3] if base.endswith(".fd") else base
    candidates.extend((stem + "_VARS.fd", stem + "_VARS_4M.fd"))
    for candidate in candidates:
        candidate_path = os.path.join(os.path.dirname(code), candidate)
        if os.path.isfile(candidate_path) and os.access(candidate_path, os.R_OK):
            return candidate_path
    return None


def locate_ovmf(
    path: "str | None" = None,
    *,
    env: "dict | None" = None,
    search: "tuple[str, ...] | list[str] | None" = None,
    exist: bool = True,
) -> OvmfFirmware:
    """Locate OVMF firmware and return an :class:`OvmfFirmware`.

    Resolution order (first hit wins):

    1. an explicit ``path`` argument (the ``--ovmf`` CLI flag);
    2. the ``PENGYOS_OVMF`` / ``OVMF`` environment variables;
    3. the canonical :data:`DEFAULT_OVMF_CANDIDATES` (or ``search`` when
       given).

    ``exist=False`` returns the resolved path without verifying it is present
    on disk (used for argv rendering of pending firmware installs). By default
    the located file must be a readable regular file; when nothing is found an
    :class:`OvmfNotFound` is raised with the full list of attempted paths.

    A located split-layout CODE file is paired with its conventional sibling
    VARS file via :func:`_default_vars_path`; a file with no such sibling is
    returned as a combined blob.
    """
    env = dict(os.environ) if env is None else env
    attempted: "list[str]" = []

    def _resolve(candidate: str) -> "OvmfFirmware | None":
        attempted.append(candidate)
        if exist:
            if not (os.path.isfile(candidate) and os.access(candidate, os.R_OK)):
                return None
        else:
            if not candidate:
                return None
        vars_path = _default_vars_path(candidate) if exist else None
        return OvmfFirmware(candidate, vars_path)

    if path:
        got = _resolve(path)
        if got is not None:
            return got
        if exist:
            raise OvmfNotFound(attempted)
        # Non-existent explicit path with exist=False: honour it as-is.
        return OvmfFirmware(path)

    for var in OVMF_ENV_VARS:
        val = (env.get(var) or "").strip()
        if val:
            got = _resolve(val)
            if got is not None:
                return got

    for candidate in (DEFAULT_OVMF_CANDIDATES if search is None else search):
        got = _resolve(candidate)
        if got is not None:
            return got

    raise OvmfNotFound(attempted)


def _as_firmware(
    ovmf: "OvmfFirmware | str | None",
    ovmf_vars: "str | None",
) -> OvmfFirmware:
    """Coerce the ``ovmf``/``ovmf_vars`` arguments into an :class:`OvmfFirmware`.

    * ``OvmfFirmware`` → used as-is (``ovmf_vars`` overrides its ``vars``).
    * a CODE path string → resolved via :func:`locate_ovmf` (which pairs a
      sibling VARS file if one exists); ``ovmf_vars`` then overrides.
    * ``None`` → default lookup via :func:`locate_ovmf`.

    Raises :class:`OvmfNotFound` (propagated) when no firmware can be found.
    """
    if isinstance(ovmf, OvmfFirmware):
        firmware = OvmfFirmware(ovmf.code, ovmf_vars if ovmf_vars is not None else ovmf.vars)
    else:
        # A plain path (or None → default search).
        firmware = locate_ovmf(ovmf)
        if ovmf_vars is not None:
            firmware = OvmfFirmware(firmware.code, ovmf_vars)
    return firmware


def _uefi_firmware_args(firmware: OvmfFirmware) -> "list[str]":
    """Emit the OVMF firmware argv section for a located firmware.

    Split layout → two pflash drives (read-only CODE at unit 0, writable VARS
    at unit 1). Combined single blob → ``-bios``. This is the golden UEFI
    wiring contract (recorded in ``spec.md`` / item 3 notes).
    """
    if firmware.combined:
        return ["-bios", firmware.code]
    return [
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={firmware.code}",
        "-drive", f"if=pflash,format=raw,unit=1,file={firmware.vars}",
    ]


def kvm_available(kvm_path: str = KVM_DEVICE) -> bool:
    """Return True when KVM acceleration is usable for the *current* user.

    Per the spec, the gate is that the KVM character device exists and is
    writable (QEMU must be able to open ``/dev/kvm``). ``os.access`` with the
    effective UID/GID is used so that a group-membership check on
    ``kvm`` works without requiring the file to be world-writable.
    """
    return os.access(kvm_path, os.R_OK | os.W_OK)


def tpm_qemu_args(
    socket_path: str,
    *,
    chardev_id: str = TPM_CHARDEV_ID,
    dev_id: str = TPM_DEV_ID,
) -> "list[str]":
    """Emit the QEMU argv tokens that bridge a swtpm unix socket to a TPM2 device.

    Three tokens, always in this order:

    * ``-chardev socket,id=<chardev_id>,path=<socket_path>`` — a character
      backend that connects to the running swtpm datapath socket.
    * ``-tpmdev emulator,id=<dev_id>,chardev=<chardev_id>`` — the emulated
      TPM2 backend wired to that chardev.
    * ``-device tpm-tis,tpmdev=<dev_id>`` — the TPM the guest sees (TIS, at
      the standard address, with an ACPI TPM2 table from OVMF). Without it the
      backend is attached to nothing and the guest has no TPM: until 0.3.1 the
      "uefi-tpm" smoke cases booted without one.

    Args:
        socket_path: Path of the swtpm ``--server`` unix socket (e.g. as
            produced by :class:`.tpm.Swtpm`). Must be non-empty.
        chardev_id: QEMU chardev object id (default :data:`TPM_CHARDEV_ID`).
        dev_id: QEMU tpmdev object id (default :data:`TPM_DEV_ID`).

    Returns:
        The three-token argv section.

    Raises:
        ValueError: if ``socket_path``, ``chardev_id`` or ``dev_id`` is empty.
    """
    if not socket_path:
        raise ValueError("tpm_socket path must be a non-empty string")
    if not chardev_id:
        raise ValueError("tpm chardev_id must be a non-empty string")
    if not dev_id:
        raise ValueError("tpm dev_id must be a non-empty string")
    return [
        "-chardev", f"socket,id={chardev_id},path={socket_path}",
        "-tpmdev", f"emulator,id={dev_id},chardev={chardev_id}",
        "-device", f"tpm-tis,tpmdev={dev_id}",
    ]


def _disk_drive(image: str, snapshot: bool) -> str:
    """Render the ``-drive`` value for the single guest disk.

    ``snapshot=on`` asks QEMU to open the backing file read-only and divert
    every guest write into a temporary overlay, so the artifact under test
    stays byte-identical (its recorded checksum/hash does not drift). This is
    the default because the harness exists to *verify* pristine artifacts;
    ``snapshot=False`` is the write-through escape hatch.
    """
    spec = f"file={image},if=virtio,format=raw"
    if snapshot:
        spec += ",snapshot=on"
    return spec


def _net_args(hostfwd: "tuple[str, ...] | list[str] | None") -> "list[str]":
    """Render the ``-nic`` user-networking section for host port forwards.

    QEMU's ``q35`` machine otherwise gets an implicit SLIRP user NIC with no
    host forwarding, so a guest service (e.g. sshd) is unreachable from the
    host. When one or more ``hostfwd`` specs are supplied, an explicit
    ``-nic user,hostfwd=<spec>[,hostfwd=<spec>...]`` is emitted (the ``-nic``
    shorthand keeps the machine's default NIC model). No specs → no tokens, so
    the argv stays byte-for-byte the pre-networking golden baseline.
    """
    if not hostfwd:
        return []
    spec = "user"
    for hf in hostfwd:
        if not str(hf).strip():
            raise ValueError("hostfwd specs must be non-empty")
        spec += f",hostfwd={hf}"
    return ["-nic", spec]


def build_argv(
    image: str,
    *,
    kernel: "str | None" = None,
    ram: int = DEFAULT_RAM_MB,
    smp: "str | int" = DEFAULT_SMP,
    serial_log: str = DEFAULT_SERIAL_LOG,
    kvm: "bool | None" = None,
    qemu_bin: str = DEFAULT_QEMU_BIN,
    uefi: bool = False,
    ovmf: "OvmfFirmware | str | None" = None,
    ovmf_vars: "str | None" = None,
    snapshot: bool = True,
    hostfwd: "tuple[str, ...] | list[str] | None" = None,
    tpm_socket: "str | None" = None,
    tpm_chardev_id: str = TPM_CHARDEV_ID,
    tpm_dev_id: str = TPM_DEV_ID,
) -> "list[str]":
    """Build the full ``qemu-system-x86_64`` argv for a headless boot.

    Args:
        image: Path to the disk image (bare-metal rootfs or whole-disk image).
            Validated for non-emptiness; *existence* is a boot-subtask concern
            so that ``probe``/``--dry-run`` can render argvs for pending
            build outputs.
        kernel: Optional bzImage path. ``None`` selects firmware/disk boot
            (no ``-kernel``/``-append``); a path selects bare-metal kernel
            boot with :data:`BARE_METAL_APPEND`.
        ram: Guest memory in MiB; must be ``> 0``.
        smp: Guest vCPU count (string or int); must be a positive integer.
        serial_log: Path of the serial console log; wired as
            ``-serial file:<path>``.
        kvm: ``True``/``False`` pins the accelerator choice; ``None`` (the
            default) probes :func:`kvm_available` at call time.
        qemu_bin: QEMU binary name/path (default ``qemu-system-x86_64``).
        uefi: boot in UEFI/OVMF firmware mode. Implies firmware (disk) boot:
            ``kernel`` must be ``None`` (they are mutually exclusive).
        ovmf: the located firmware. Accepts an :class:`OvmfFirmware` (from
            :func:`locate_ovmf`) or a plain CODE path string. A plain string
            is resolved to an :class:`OvmfFirmware` on demand (pairing a
            sibling VARS file if one exists). ``None`` triggers a default
            lookup via :func:`locate_ovmf`.
        ovmf_vars: optional explicit VARS path, overriding any VARS file
            inferred from ``ovmf`` (useful when the VARS copy lives in a
            writable temp location, which is what the boot subtask does so the
            guest can persist NVRAM across reboots).
        snapshot: attach the guest disk through a QEMU throwaway overlay
            (``snapshot=on``, the default) so the backing image is opened
            read-only and **never mutated by a verification boot**. Pass
            ``False`` only for tests that deliberately persist guest writes
            (e.g. an installer flow).
        hostfwd: optional list of QEMU user-networking port-forward specs
            (each a ``hostfwd=`` value, e.g. ``"tcp::2222-:22"``). Emitted as
            a single ``-nic user,hostfwd=…`` so a booted guest's services can
            be reached from the host (the "boot it and ssh in to inspect"
            path). ``None``/empty (the default) leaves the argv byte-for-byte
            the pre-networking golden baseline.
        tpm_socket: optional path of a running swtpm ``--server`` unix socket.
            When given, the guest is handed an emulated TPM2 via
            :func:`tpm_qemu_args` (``-chardev``/``-tpmdev``), emitted after the
            disk/firmware section and before the headless tail. ``None`` (the
            default) leaves the argv byte-for-byte identical to the pre-TPM
            golden baseline. The swtpm process itself is owned by the boot
            subtask (see :mod:`.tpm`); ``build_argv`` only renders the tokens.
        tpm_chardev_id: QEMU chardev object id for the TPM datapath
            (default :data:`TPM_CHARDEV_ID`).
        tpm_dev_id: QEMU tpmdev object id for the TPM backend
            (default :data:`TPM_DEV_ID`).

    Returns:
        The argument vector including the binary name as ``argv[0]``.

    Raises:
        ValueError: if ``image`` is empty, ``ram`` or ``smp`` is not a
            positive integer, if ``qemu_bin``/``serial_log`` is empty, or if
            ``uefi`` and ``kernel`` are both set (mutually exclusive boot
            modes).
        OvmfNotFound: if ``uefi`` is set and no firmware can be located.
    """
    if not image:
        raise ValueError("image path must be a non-empty string")
    if not qemu_bin:
        raise ValueError("qemu_bin must be a non-empty string")
    if not serial_log:
        raise ValueError("serial_log must be a non-empty string")
    try:
        ram_int = int(ram)
        smp_int = int(smp)
    except (TypeError, ValueError):
        raise ValueError(f"ram and smp must be integers (got ram={ram!r}, smp={smp!r})")
    if ram_int <= 0:
        raise ValueError(f"ram must be a positive integer in MiB (got {ram!r})")
    if smp_int <= 0:
        raise ValueError(f"smp must be a positive integer (got {smp!r})")
    if uefi and kernel:
        raise ValueError(
            "--uefi (OVMF firmware boot) and --kernel (bare-metal -kernel boot) "
            "are mutually exclusive: under OVMF the kernel lives on the image's "
            "own ESP/GRUB2, so pass the image without --kernel."
        )

    use_kvm = kvm_available() if kvm is None else bool(kvm)

    argv: "list[str]" = [
        qemu_bin,
        "-M", DEFAULT_MACHINE,
        "-m", str(ram_int),
        "-smp", str(smp_int),
    ]
    if use_kvm:
        argv += ["-enable-kvm", "-cpu", "host"]
    else:
        argv += ["-cpu", "max"]

    if kernel:
        argv += [
            "-kernel", kernel,
            "-drive", _disk_drive(image, snapshot),
            "-append", BARE_METAL_APPEND,
        ]
    elif uefi:
        firmware = _as_firmware(ovmf, ovmf_vars)
        argv += _uefi_firmware_args(firmware)
        # Firmware/disk boot: the image is the boot disk (a GPT image
        # whose ESP carries GRUB2 + bzImage, read by OVMF).
        argv += ["-drive", _disk_drive(image, snapshot)]
    else:
        # SeaBIOS disk boot: the image is the only disk.
        argv += ["-drive", _disk_drive(image, snapshot)]

    # Host port forwarding (user networking): only when specs are given, so
    # the no-forward case keeps the pre-networking golden argv.
    argv += _net_args(hostfwd)

    if tpm_socket:
        # Emulated TPM2 (swtpm) datapath: after the disk/firmware section,
        # before the headless tail. Composes with every boot mode above.
        argv += tpm_qemu_args(
            tpm_socket, chardev_id=tpm_chardev_id, dev_id=tpm_dev_id
        )

    argv += [
        "-display", "none",
        "-serial", f"file:{serial_log}",
        "-monitor", "none",
        "-no-reboot",
    ]
    return argv
