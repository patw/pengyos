"""Integration tests — opt-in behind ``PENGYOS_IT=1``.

The unit suite never needs a real QEMU, network, root, display, or hardware.
This module is the one deliberate exception, and it is *strictly* opt-in:

* every *boot* test skips unless the environment variable ``PENGYOS_IT`` is
  exactly ``1``;
* a boot test additionally skips when there is no bootable image to try.

Images are discovered from a small, documented candidate set — "a tiny
prebuilt Alpine/hello image if present" in the task text. In this project the
"hello image" is whatever prebuilt disk the build pipeline left behind that
boots to a ``login:`` prompt: the reference build outputs and the dist USB
image (spec.md, "Reference image locations") are exactly that.

Candidate discovery (in priority order; a candidate is dropped when its
files/firmware are absent):

1. **explicit** — ``PENGYOS_IT_IMAGE`` (required), with optional
   ``PENGYOS_IT_KERNEL`` (bare-metal ``-kernel`` path) and ``PENGYOS_IT_UEFI=1``
   (firmware/disk boot). Always included when set — a missing file then fails
   the boot test with a clear error instead of silently picking a fallback.
2. **buildroot-bare-metal** — ``~/build/pengyos/output/pengyos-core/images/``:
   both ``bzImage`` and ``rootfs.ext4`` must exist → bare-metal boot
   (``-kernel`` + virtio rootfs).
3. **core-uefi-disk** — ``pengyos-core-x86_64.img`` in that same directory plus
   a locatable OVMF firmware → UEFI firmware/disk boot.

The per-boot timeout is ``PENGYOS_IT_TIMEOUT`` seconds (default 180 — a KVM
boot of Core takes ~7 s; 180 leaves headroom for a slow TCG host, where it may
still need raising).

The discovery logic is pure (``home``/``env``/``ovmf_present`` injectable),
so the *rules* are unit-tested here in every run; only the actual boots are
opt-in.

Run it with::

    PENGYOS_IT=1 pytest -q tests/test_integration.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import pytest

from pengyos_qemu_verify import assertions as assertions_mod
from pengyos_qemu_verify import argv as argv_mod
from pengyos_qemu_verify import reports
from pengyos_qemu_verify import tpm as tpm_mod
from pengyos_qemu_verify import tpm_state as tpm_state_mod
from pengyos_qemu_verify.boot import boot_image, find_qemu_bin

# --- opt-in gate -------------------------------------------------------------

IT_ENV = "PENGYOS_IT"
DEFAULT_IT_TIMEOUT: float = 180.0


def _sha256(path) -> str:
    """The sha256 of a file, streamed (images run to hundreds of MB)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def it_enabled(env: "dict | None" = None) -> bool:
    """True when integration tests are opted in: ``PENGYOS_IT`` == ``"1"``.

    Anything else (unset, ``0``, ``true``, ``yes``…) keeps them skipped — the
    gate is a strict, documented switch, not a truthy sniff.
    """
    if env is None:
        env = os.environ
    return env.get(IT_ENV) == "1"


def it_timeout(env: "dict | None" = None) -> float:
    """The per-boot timeout for integration boots, in seconds.

    ``PENGYOS_IT_TIMEOUT`` overrides :data:`DEFAULT_IT_TIMEOUT`; an unset,
    non-numeric, or non-positive value falls back to the default (a timeout
    of ``0``/negative would make ``boot_image`` raise ``ValueError``).
    """
    if env is None:
        env = os.environ
    raw = env.get("PENGYOS_IT_TIMEOUT")
    if raw is None:
        return DEFAULT_IT_TIMEOUT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_IT_TIMEOUT
    return value if value > 0 else DEFAULT_IT_TIMEOUT


# --- candidate discovery (pure) ----------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One bootable prebuilt image and how to boot it.

    Attributes:
        name: stable identifier (also the pytest ``id`` of the boot test).
        image: the disk image path (``-drive``).
        kernel: bare-metal ``-kernel`` image, or ``None`` for firmware/disk boot.
        uefi: boot through OVMF (``kernel`` must be ``None`` when set).
    """

    name: str
    image: str
    kernel: "str | None" = None
    uefi: bool = False


def _ovmf_available() -> bool:
    """True when UEFI firmware can be located on this host.

    :func:`argv_mod.locate_ovmf` is a *boot-path* helper and raises
    :class:`argv_mod.OvmfNotFound` when no firmware is installed. Discovery
    runs at import time and must stay non-raising on machines without OVMF
    (laptops, CI), so probe for a result and read a miss as "no UEFI here".
    """
    try:
        argv_mod.locate_ovmf()
    except argv_mod.OvmfNotFound:
        return False
    return True


def discover_candidates(
    home: "str | None" = None,
    env: "dict | None" = None,
    ovmf_present: "bool | None" = None,
) -> "list[Candidate]":
    """Discover the prebuilt images available for the opt-in boot tests.

    See the module docstring for the candidate set and priority order. Pure:
    only ``os.path.isfile`` probes on the paths derived from ``home``/``env``
    (plus the OVMF location when ``ovmf_present`` is not given).

    Args:
        home: the ``~`` the default image locations are resolved against
            (defaults to the real home; inject a temp dir in tests).
        env: the environment to read ``PENGYOS_IT_IMAGE``/``PENGYOS_IT_KERNEL``
            /``PENGYOS_IT_UEFI`` from (defaults to ``os.environ``).
        ovmf_present: whether UEFI firmware can be located for the ``--uefi``
            candidate; ``None`` (default) probes with :func:`_ovmf_available`
            (a missing firmware counts as *not* present, never an error).
            Inject ``True``/``False`` for hermetic unit tests.

    Returns:
        The candidates in priority order (may be empty).
    """
    if env is None:
        env = os.environ
    if home is None:
        home = os.path.expanduser("~")

    candidates: "list[Candidate]" = []

    # 1. Explicit operator override — always included when set.
    explicit_image = env.get("PENGYOS_IT_IMAGE")
    if explicit_image:
        candidates.append(
            Candidate(
                "explicit",
                explicit_image,
                kernel=env.get("PENGYOS_IT_KERNEL") or None,
                uefi=env.get("PENGYOS_IT_UEFI") == "1",
            )
        )

    # 2. Build pipeline outputs: bare-metal kernel + rootfs pair.
    images_dir = os.path.join(home, "build", "pengyos", "output", "pengyos-core", "images")
    bare_kernel = os.path.join(images_dir, "bzImage")
    bare_rootfs = os.path.join(images_dir, "rootfs.ext4")
    if os.path.isfile(bare_kernel) and os.path.isfile(bare_rootfs):
        candidates.append(Candidate("buildroot-bare-metal", bare_rootfs, kernel=bare_kernel))

    # 3. The Core disk image: UEFI firmware/disk boot (needs OVMF on this host).
    dist_image = os.path.join(images_dir, "pengyos-core-x86_64.img")
    if os.path.isfile(dist_image):
        if ovmf_present is None:
            ovmf_present = _ovmf_available()
        if ovmf_present:
            candidates.append(Candidate("core-uefi-disk", dist_image, uefi=True))

    return candidates


# Discovered at collection time (the environment is read-only during a run).
CANDIDATES: "list[Candidate]" = discover_candidates()


def _candidate_param(cand: "Candidate | None") -> "pytest.param":
    return pytest.param(cand, id=cand.name if cand is not None else "no-candidate-image")


# --- the opt-in integration tests ---------------------------------------------


class TestIntegrationBoots:
    """Real QEMU boots — skipped unless ``PENGYOS_IT=1`` (and an image is present)."""

    pytestmark = pytest.mark.skipif(
        not it_enabled(),
        reason=f"integration tests are opt-in: set {IT_ENV}=1 to run them",
    )

    @pytest.mark.parametrize("cand", [_candidate_param(c) for c in (CANDIDATES or [None])])
    def test_boot_reaches_login_and_emits_reports(self, cand: "Candidate | None", tmp_path):
        """Boot one prebuilt image headless; assert readiness, the default
        assertion set, and well-formed JSON + JUnit reports."""
        if cand is None:
            pytest.skip(
                f"{IT_ENV}=1 but no candidate image is present — set "
                "PENGYOS_IT_IMAGE (optionally PENGYOS_IT_KERNEL / PENGYOS_IT_UEFI=1) "
                "or build a PengyOS image"
            )
        if shutil.which("qemu-system-x86_64") is None:
            pytest.skip("qemu-system-x86_64 is not on PATH")

        result = boot_image(
            cand.image,
            kernel=cand.kernel,
            uefi=cand.uefi,
            timeout=it_timeout(),
        )

        assert result.error is None, f"boot failed: {result.error}"
        assert result.timed_out is False, (
            f"boot timed out after {result.duration:.1f}s "
            f"(raise PENGYOS_IT_TIMEOUT for a slow TCG host)"
        )
        assert result.ready is True, (
            "boot did not reach readiness (no 'login:' marker in the serial log)"
        )

        # The captured serial log (a file — BootResult.serial_log is its path).
        with open(result.serial_log, "r", encoding="utf-8", errors="replace") as fh:
            log_text = fh.read()

        # The item-6 runner is wired into the boot flow: the default set
        # ("it booted to a login without panicking") ran over the captured
        # log and its results are attached to the result.
        checks = result.checks
        expected_names = [a.name for a in assertions_mod.default_assertions()]
        assert [c.name for c in checks] == expected_names
        # They must agree with re-running the pure runner over the same log
        # (a real-boot oracle for the wiring itself).
        assert checks == assertions_mod.run_assertions(
            log_text, assertions_mod.default_assertions()
        )
        failed = [c for c in checks if not c.passed]
        assert not failed, f"default assertions failed: {[c.to_dict() for c in failed]}"

        # Reports: JSON verdict + shape, JUnit parse-back. checks=None — the
        # emitters fall back to the checks attached to the boot.
        json_path = reports.write_json(str(tmp_path / "report.json"), result)
        with open(json_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        assert doc["result"] == "pass", doc.get("error")
        assert doc["image"] == cand.image
        assert doc["ready"] is True and doc["timed_out"] is False
        assert doc["checks"] == [c.to_dict() for c in checks]

        junit_path = reports.write_junit(str(tmp_path / "report.xml"), result)
        with open(junit_path, "r", encoding="utf-8") as fh:
            root = ET.fromstring(fh.read())
        assert root.tag == "testsuites"
        assert root.get("name") == "pengyos-qemu-verify"
        suite = root.find("testsuite")
        assert suite is not None and suite.get("result") == "pass"
        testcases = list(suite.iter("testcase"))
        assert len(testcases) == 1 + len(checks)  # boot-reached-readiness + checks
        assert testcases[0].get("name") == "boot-reached-readiness"
        assert not list(root.iter("failure")), "a passing boot must have no <failure> nodes"


# --- unit tests for the opt-in machinery (always run, no QEMU needed) ----------


# --- opt-in real-swtpm state tests (the --tpm provisioning follow-up) ------


# --- PTM control-channel handshake (QEMU backends/tpm/tpm_emulator.c) -----
#
# The PTM protocol (QEMU's backends/tpm/tpm_ioctl.h) speaks over the unix
# socket the -tpmdev emulator chardev points at: a 4-byte big-endian command
# code followed by the command's request struct. QEMU's startup handshake is
# CMD_SET_DATAFD (it passes its own data-channel fd via SCM_RIGHTS), then
# CMD_GET_CAPABILITY and CMD_INIT. Only used by the opt-in real-swtpm test
# below.

_PTM_CMD_GET_CAPABILITY = 1   # 0x01
_PTM_CMD_INIT = 2             # 0x02
_PTM_CMD_SET_DATAFD = 0x10    # 0x10

_PTM_CAP_INIT = 1 << 0
_PTM_CAP_SHUTDOWN = 1 << 1
_PTM_CAP_GET_TPMESTABLISHED = 1 << 2
_PTM_CAP_SET_LOCALITY = 1 << 3
_PTM_CAP_RESET_TPMESTABLISHED = 1 << 7
_PTM_CAP_STOP = 1 << 10
_PTM_CAP_SET_DATAFD = 1 << 12
_PTM_CAP_SET_BUFFERSIZE = 1 << 13


def _ptm_read_exact(fd, n, timeout):
    """Read exactly n bytes from fd, bounded by timeout (raises on timeout/EOF)."""
    import select
    buf = b""
    deadline = time.monotonic() + timeout
    while len(buf) < n:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"PTM control channel timed out after {n - len(buf)} more byte(s)"
            )
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            raise TimeoutError(
                f"PTM control channel timed out after {n - len(buf)} more byte(s)"
            )
        chunk = fd.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("PTM control channel closed by the peer")
        buf += chunk
    return buf


def _ptm_control_handshake(sock_path, timeout=15.0):
    """Speak QEMU's control-channel handshake to a running swtpm.

    Connects to ``sock_path``, passes a fresh data-channel fd via
    CMD_SET_DATAFD, queries CMD_GET_CAPABILITY and issues CMD_INIT — the
    exact sequence QEMU's tpm_emulator does at startup (plus the capability
    check it performs). Returns the capability word; raises on any timeout,
    closed channel, or non-zero TPM result (which is exactly how a
    mis-wired / legacy-only swtpm would fail).
    """
    import struct
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(sock_path)

        def send_cmd(code, payload=b"", send_fd=None):
            msg = struct.pack(">I", code) + payload
            if send_fd is not None:
                client.sendmsg(
                    [msg],
                    [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                      struct.pack("i", send_fd))],
                )
            else:
                client.sendall(msg)

        # 1. CMD_SET_DATAFD: QEMU passes its data-channel fd and expects a
        #    4-byte ptm_res.
        data_pair = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            send_cmd(_PTM_CMD_SET_DATAFD, send_fd=data_pair[1].fileno())
            res = struct.unpack(
                ">I", _ptm_read_exact(client, 4, timeout)
            )[0]
            assert res == 0, f"CMD_SET_DATAFD failed with 0x{res:x}"
        finally:
            data_pair[1].close()

        # 2. CMD_GET_CAPABILITY: 4-byte tpm_result + 4-byte capability word.
        send_cmd(_PTM_CMD_GET_CAPABILITY)
        resp = _ptm_read_exact(client, 8, timeout)
        tpm_result, caps = struct.unpack(">II", resp)
        assert tpm_result == 0, f"CMD_GET_CAPABILITY failed with 0x{tpm_result:x}"

        # 3. CMD_INIT: 4-byte init_flags request, 4-byte ptm_res response.
        send_cmd(_PTM_CMD_INIT, payload=b"\x00\x00\x00\x00")
        res = struct.unpack(">I", _ptm_read_exact(client, 4, timeout))[0]
        assert res == 0, f"CMD_INIT failed with 0x{res:x}"
        return caps
    finally:
        try:
            data_pair[0].close()
        except Exception:
            pass
        client.close()


class TestIntegrationTpmState:
    """Real swtpm state checks — same strict opt-in gate as the boots;
    additionally skipped where no real ``swtpm`` binary is installed.

    These close the loop on the committed state fixture: the fixture is not
    just plausible bytes, it must be *loaded* by a genuine swtpm — and a full
    ``--tpm`` boot (provisioned state + real swtpm + real QEMU) must reach
    the login prompt.
    """

    pytestmark = pytest.mark.skipif(
        not it_enabled(),
        reason=f"integration tests are opt-in: set {IT_ENV}=1 to run them",
    )

    def test_committed_fixture_loads_in_real_swtpm(self, tmp_path):
        swtpm_bin = shutil.which("swtpm")
        if swtpm_bin is None:
            pytest.skip("swtpm is not installed")
        workdir = tmp_path / "wd"
        workdir.mkdir()
        # Seed the per-boot state dir exactly like a boot does (fixture path;
        # Swtpm's default state_subdir is 'tpmstate' inside the workdir).
        state_dir = workdir / "tpmstate"
        prov = tpm_state_mod.provision_tpm_state(str(state_dir), swtpm_setup_bin="")
        assert prov.method == "fixture"

        tpm = tpm_mod.Swtpm(str(workdir), swtpm_bin=swtpm_bin, ready_timeout=30.0)
        sock = tpm.start()  # raises SwtpmStartError if swtpm rejects the state
        try:
            # The datapath is a live listener, not just a file: connect to it.
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(sock)
            client.close()
        finally:
            tpm.stop()
        # The per-boot copy still holds valid state after the run.
        assert tpm_state_mod.has_tpm_state(str(state_dir))

    def test_real_tpm_boot_provisions_and_reaches_login(self):
        swtpm_bin = shutil.which("swtpm")
        if swtpm_bin is None:
            pytest.skip("swtpm is not installed")
        if not CANDIDATES:
            pytest.skip("no candidate image for a real --tpm boot")
        cand = CANDIDATES[0]
        # Real provisioning (auto-detects swtpm_setup, else the fixture), a
        # real swtpm process, and a real QEMU boot — the full --tpm path.
        result = boot_image(
            cand.image,
            kernel=cand.kernel,
            uefi=cand.uefi,
            tpm=True,
            timeout=it_timeout(),
        )
        assert result.error is None, f"boot failed: {result.error}"
        assert result.timed_out is False, f"boot timed out after {result.duration:.1f}s"
        assert result.ready is True, "--tpm boot did not reach readiness"
        assert result.tpm_state_method in ("existing", "swtpm_setup", "fixture")
        assert os.path.isdir(result.tpm_state)
        assert any(n.startswith("tpm2-") for n in os.listdir(result.tpm_state))
        # The emulated TPM device was actually wired into the launched argv.
        assert "-tpmdev" in result.argv

    def test_real_swtpm_speaks_ptm_control_channel(self, tmp_path):
        """A real swtpm started exactly like a ``--tpm`` boot must answer the
        QEMU control-channel handshake (``CMD_SET_DATAFD`` →
        ``CMD_GET_CAPABILITY`` → ``CMD_INIT``, the PTM protocol of QEMU's
        ``backends/tpm/tpm_emulator.c``). This is the protocol-level oracle
        for the version-adaptive wiring: a swtpm configured the legacy
        ``--server`` way would treat these control messages as (invalid) TPM
        data and never answer, exactly the deadlock that broke the real
        ``--tpm`` boot on QEMU >= 5.2."""
        swtpm_bin = shutil.which("swtpm")
        if swtpm_bin is None:
            pytest.skip("swtpm is not installed")
        protocol = tpm_mod.resolve_tpm_protocol("auto")
        if protocol != "control":
            pytest.skip(
                "QEMU on PATH is pre-5.2 (legacy data-channel wiring); the "
                "PTM control-channel handshake does not apply to it"
            )
        workdir = tmp_path / "wd"
        workdir.mkdir()
        # Seed the per-boot state dir exactly like a boot does (fixture path;
        # Swtpm's default state_subdir is 'tpmstate' inside the workdir).
        prov = tpm_state_mod.provision_tpm_state(
            str(workdir / "tpmstate"), swtpm_setup_bin=""
        )
        assert prov.method in ("existing", "swtpm_setup", "fixture")
        tpm = tpm_mod.Swtpm(
            str(workdir), swtpm_bin=swtpm_bin,
            ready_timeout=30.0, protocol=protocol,
        )
        sock = tpm.start()  # raises SwtpmStartError if swtpm rejects the state
        try:
            caps = _ptm_control_handshake(sock, timeout=15.0)
        finally:
            tpm.stop()
        # QEMU refuses a TPM2 emulator lacking any of the minimum capability
        # set (tpm_emulator_check_caps); a working swtpm must advertise all
        # of them, or the boot would fail right after this handshake.
        required = (
            _PTM_CAP_INIT | _PTM_CAP_SHUTDOWN | _PTM_CAP_GET_TPMESTABLISHED
            | _PTM_CAP_SET_LOCALITY | _PTM_CAP_RESET_TPMESTABLISHED
            | _PTM_CAP_STOP | _PTM_CAP_SET_DATAFD | _PTM_CAP_SET_BUFFERSIZE
        )
        assert (caps & required) == required, (
            f"swtpm capability set 0x{caps:x} is missing "
            f"0x{(required & ~caps):x}"
        )


def test_tpm_state_integration_is_opt_in():
    """The real-swtpm class carries the same PENGYOS_IT gate as the boots."""
    raw = getattr(TestIntegrationTpmState, "pytestmark", ())
    decorators = [raw] if hasattr(raw, "mark") else list(raw)
    marks = [d.mark if hasattr(d, "mark") else d for d in decorators]
    skipifs = [m for m in marks if m.name == "skipif"]
    assert skipifs, "TestIntegrationTpmState lost its PENGYOS_IT opt-in gate"
    assert skipifs[0].args[0] == (not it_enabled())


def test_it_enabled_requires_exactly_one():
    assert it_enabled({}) is False
    assert it_enabled({"PENGYOS_IT": "0"}) is False
    assert it_enabled({"PENGYOS_IT": "true"}) is False
    assert it_enabled({"PENGYOS_IT": ""}) is False
    assert it_enabled({"PENGYOS_IT": "1"}) is True


def test_it_enabled_reads_real_environment_by_default(monkeypatch):
    monkeypatch.delenv("PENGYOS_IT", raising=False)
    assert it_enabled() is False
    monkeypatch.setenv("PENGYOS_IT", "1")
    assert it_enabled() is True


def test_it_timeout_default_and_override():
    assert it_timeout({}) == DEFAULT_IT_TIMEOUT
    assert it_timeout({"PENGYOS_IT_TIMEOUT": "60"}) == 60.0
    assert it_timeout({"PENGYOS_IT_TIMEOUT": "60.5"}) == 60.5
    # Invalid values fall back to the default — a bad override must never make
    # boot_image raise ValueError(positive timeout) or hang with timeout 0.
    assert it_timeout({"PENGYOS_IT_TIMEOUT": "banana"}) == DEFAULT_IT_TIMEOUT
    assert it_timeout({"PENGYOS_IT_TIMEOUT": "0"}) == DEFAULT_IT_TIMEOUT
    assert it_timeout({"PENGYOS_IT_TIMEOUT": "-5"}) == DEFAULT_IT_TIMEOUT


def test_discover_explicit_candidate_wins_and_is_always_included(tmp_path):
    missing = str(tmp_path / "does-not-exist.img")
    env = {
        "PENGYOS_IT_IMAGE": missing,
        "PENGYOS_IT_KERNEL": str(tmp_path / "bzImage"),
        "PENGYOS_IT_UEFI": "1",
    }
    cands = discover_candidates(home=str(tmp_path), env=env, ovmf_present=False)
    assert [c.name for c in cands] == ["explicit"]
    cand = cands[0]
    assert cand.image == missing
    assert cand.kernel == str(tmp_path / "bzImage")
    assert cand.uefi is True


def test_discover_explicit_kernel_and_uefi_are_optional():
    cands = discover_candidates(home="/nonexistent-home", env={"PENGYOS_IT_IMAGE": "/x.img"})
    assert len(cands) == 1
    assert cands[0].kernel is None
    assert cands[0].uefi is False


def test_discover_buildroot_pair(tmp_path):
    images = tmp_path / "build" / "pengyos" / "output" / "pengyos-core" / "images"
    images.mkdir(parents=True)
    (images / "bzImage").write_bytes(b"k")
    (images / "rootfs.ext4").write_bytes(b"r")
    cands = discover_candidates(home=str(tmp_path), env={}, ovmf_present=False)
    assert [c.name for c in cands] == ["buildroot-bare-metal"]
    cand = cands[0]
    assert cand.image == str(images / "rootfs.ext4")
    assert cand.kernel == str(images / "bzImage")
    assert cand.uefi is False


def test_discover_buildroot_pair_incomplete_is_dropped(tmp_path):
    images = tmp_path / "build" / "pengyos" / "output" / "pengyos-core" / "images"
    images.mkdir(parents=True)
    (images / "rootfs.ext4").write_bytes(b"r")  # kernel missing
    cands = discover_candidates(home=str(tmp_path), env={}, ovmf_present=False)
    assert cands == []


def test_discover_uefi_core_image(tmp_path):
    dist = tmp_path / "build" / "pengyos" / "output" / "pengyos-core" / "images"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "pengyos-core-x86_64.img").write_bytes(b"i")
    cands = discover_candidates(home=str(tmp_path), env={}, ovmf_present=True)
    assert [c.name for c in cands] == ["core-uefi-disk"]
    assert cands[0].uefi is True
    assert cands[0].kernel is None


def test_discover_uefi_core_image_dropped_without_firmware(tmp_path):
    dist = tmp_path / "build" / "pengyos" / "output" / "pengyos-core" / "images"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "pengyos-core-x86_64.img").write_bytes(b"i")
    cands = discover_candidates(home=str(tmp_path), env={}, ovmf_present=False)
    assert cands == []


def test_discover_without_ovmf_does_not_raise(tmp_path, monkeypatch):
    """A host with no OVMF must not break discovery (it runs at import time).

    Regression: the default ``ovmf_present=None`` path called
    ``locate_ovmf()`` directly, whose ``OvmfNotFound`` escaped the module-level
    ``CANDIDATES = discover_candidates()`` and turned the whole suite into a
    collection error on any machine without firmware installed.
    """
    dist = tmp_path / "build" / "pengyos" / "output" / "pengyos-core" / "images"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "pengyos-core-x86_64.img").write_bytes(b"i")

    def _boom(*_args, **_kwargs):
        raise argv_mod.OvmfNotFound(["/usr/share/OVMF/OVMF_CODE.fd"])

    monkeypatch.setattr(argv_mod, "locate_ovmf", _boom)
    assert discover_candidates(home=str(tmp_path), env={}) == []


def test_discover_with_ovmf_probe_includes_uefi_candidate(tmp_path, monkeypatch):
    dist = tmp_path / "build" / "pengyos" / "output" / "pengyos-core" / "images"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "pengyos-core-x86_64.img").write_bytes(b"i")

    firmware = argv_mod.OvmfFirmware("/usr/share/OVMF/OVMF_CODE.fd")
    monkeypatch.setattr(argv_mod, "locate_ovmf", lambda *_a, **_k: firmware)
    cands = discover_candidates(home=str(tmp_path), env={})
    assert [c.name for c in cands] == ["core-uefi-disk"]
    assert cands[0].uefi is True


def test_discover_priority_and_empty(tmp_path):
    # Nothing present → no candidates.
    assert discover_candidates(home=str(tmp_path), env={}, ovmf_present=False) == []

    # All present → explicit first, then bare-metal, then the UEFI disk image.
    images = tmp_path / "build" / "pengyos" / "output" / "pengyos-core" / "images"
    images.mkdir(parents=True)
    (images / "bzImage").write_bytes(b"k")
    (images / "rootfs.ext4").write_bytes(b"r")
    dist = tmp_path / "build" / "pengyos" / "output" / "pengyos-core" / "images"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "pengyos-core-x86_64.img").write_bytes(b"i")
    cands = discover_candidates(
        home=str(tmp_path),
        env={"PENGYOS_IT_IMAGE": str(tmp_path / "other.img")},
        ovmf_present=True,
    )
    assert [c.name for c in cands] == ["explicit", "buildroot-bare-metal", "core-uefi-disk"]


def test_integration_boots_are_opt_in():
    """The boot tests must carry a PENGYOS_IT skipif marker (the module never
    gates with a module-level pytestmark, so the unit tests above always run)."""
    raw = getattr(TestIntegrationBoots, "pytestmark", ())
    # A single mark assigned to pytestmark is a MarkDecorator; several are a list.
    decorators = [raw] if hasattr(raw, "mark") else list(raw)
    marks = [d.mark if hasattr(d, "mark") else d for d in decorators]
    skipifs = [m for m in marks if m.name == "skipif"]
    assert skipifs, "TestIntegrationBoots lost its PENGYOS_IT opt-in gate"
    condition = skipifs[0].args[0]
    # The condition is evaluated once at import time, so compare it against the
    # same read — this must hold whether the suite runs with or without
    # PENGYOS_IT=1 (i.e. the gate is `skipif(not it_enabled(), ...)`).
    assert condition == (not it_enabled()), (
        "the opt-in gate no longer matches it_enabled() — it must skip "
        f"exactly when {IT_ENV} is not '1'"
    )


def test_parametrization_never_empty():
    """With or without images, the boot test must always be collectable:
    a candidate per discovered image, or one sentinel that skips."""
    params = [_candidate_param(c) for c in (CANDIDATES or [None])]
    assert len(params) >= 1
    if not CANDIDATES:
        assert params[0].id == "no-candidate-image"
        assert params[0].values == (None,)
    else:
        assert [p.id for p in params] == [c.name for c in CANDIDATES]


def test_discovered_candidates_are_consistent():
    """Whatever real images exist on this host, every UEFI candidate must have
    no kernel (they are mutually exclusive) and every name must be unique."""
    names = [c.name for c in CANDIDATES]
    assert len(names) == len(set(names))
    for cand in CANDIDATES:
        if cand.uefi:
            assert cand.kernel is None
        assert os.path.isabs(cand.image)
        assert find_qemu_bin(), "qemu-system-x86_64 should at least resolve to a name"


# --- artifact safety (the "verifying must not mutate the artifact" contract) --


def _snapshot_test_image() -> "Candidate | None":
    """The image the artifact-safety tests boot (prefer the UEFI disk image)."""
    for name in ("core-uefi-disk", "buildroot-bare-metal", "explicit"):
        for cand in CANDIDATES:
            if cand.name == name:
                return cand
    return CANDIDATES[0] if CANDIDATES else None


class TestArtifactSafety:
    """A verification boot must not mutate the image it verifies.

    Regression guard for 2026-09-14: the harness attached guest disks
    read-write, the guest re-mounts the ext4 root r/w (and writes a journal, a
    syslog and an RNG seed), so **every boot rewrote the artifact** —
    the published image went ``fa4423cb…`` → ``29cf366f…`` from our own
    verification runs, and the ``sha256sum -c`` users are told to run stopped
    working.

    The fix is a QEMU throwaway overlay (``snapshot=on``). These tests are the
    *behavioural* proof: they hash a real image, boot it for real, and hash it
    again. They boot a **copy**, so they stay safe even if the contract
    regresses — the real image is never at risk.
    """

    pytestmark = pytest.mark.skipif(
        not it_enabled(),
        reason=f"integration tests are opt-in: set {IT_ENV}=1 to run them",
    )

    def _boot_a_copy(self, tmp_path, **boot_kwargs):
        cand = _snapshot_test_image()
        if cand is None:
            pytest.skip(
                f"{IT_ENV}=1 but no candidate image is present — set "
                "PENGYOS_IT_IMAGE or build a PengyOS image"
            )
        if shutil.which("qemu-system-x86_64") is None:
            pytest.skip("qemu-system-x86_64 is not on PATH")
        work = tmp_path / "copy.img"
        shutil.copyfile(cand.image, work)
        before = _sha256(work)
        result = boot_image(
            str(work),
            kernel=cand.kernel,
            uefi=cand.uefi,
            timeout=it_timeout(),
            **boot_kwargs,
        )
        return work, before, result

    def test_default_boot_leaves_the_image_byte_identical(self, tmp_path):
        work, before, result = self._boot_a_copy(tmp_path)
        assert result.ok, f"boot failed: {result.error or result.status}"
        assert result.snapshot is True, "the harness must default to a snapshot overlay"
        assert _sha256(work) == before, (
            "the harness mutated the image it was verifying — the artifact's "
            "hash must be stable across a verification boot"
        )

    def test_write_through_boot_mutates_the_copy(self, tmp_path):
        """The control that keeps the test above honest.

        If a boot could never change the bytes, "the hash is unchanged" would
        prove nothing. With ``snapshot=False`` the guest's writes (rw remount,
        journal, RNG seed) must reach the image — and they do.
        """
        work, before, result = self._boot_a_copy(tmp_path, snapshot=False)
        assert result.ok, f"boot failed: {result.error or result.status}"
        assert result.snapshot is False
        assert _sha256(work) != before, (
            "a write-through boot should have modified the image; if it did "
            "not, the non-mutation test above is vacuous"
        )
