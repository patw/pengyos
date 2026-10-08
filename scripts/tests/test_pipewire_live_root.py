"""Tests for the Desktop's live-root PipeWire drop-ins.

The four drop-ins under
``buildroot-external/board/desktop/rootfs-overlay/etc/systemd/user/*/10-pengyos-live-root.conf``
allow root's PipeWire only on a live boot (upstream sets ``ConditionUser=!root``,
and the live desktop *is* root's session). They used to decide "live" from the
``PENGYOS-PAYLOAD`` partition label, which only the *installer* stick has — so the
plain live image (``pengyos-desktop-x86_64.img``), a root session with no payload,
started no PipeWire at all: COSMIC's Sound settings showed no devices and the box
had no audio. Same wrong proxy the login screen used (see
``test_greetd_autologin.py``); found on behotv 2026-10-08.

The fix decides "live" by the boot, like ``greetd-config`` and ``agetty-autologin``:
an installed system boots with ``pengyos.data=`` on the kernel line, a live image
does not. ``ConditionKernelCommandLine=`` matches a whole word or a ``word=value``
pair, so ``pengyos.data`` covers ``pengyos.data=4``.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
USER_UNITS = (REPO / "buildroot-external/board/desktop/rootfs-overlay"
              / "etc/systemd/user")
DROPINS = sorted(USER_UNITS.glob("*.d/10-pengyos-live-root.conf"))

EXPECTED_DIRS = ["pipewire-pulse.service.d", "pipewire-pulse.socket.d",
                 "pipewire.service.d", "pipewire.socket.d"]


def test_the_four_dropins_are_present():
    """The service *and* socket of both PipeWire and its Pulse shim must be gated,
    or one of them still refuses to start for root."""
    assert [p.parent.name for p in DROPINS] == EXPECTED_DIRS


def test_live_root_is_gated_by_the_kernel_line_not_the_payload_label():
    """Regression guard on the *shape* of the decision: "the installer is available"
    and "this boot is live" are different facts, and only the kernel line tells them
    apart."""
    for p in DROPINS:
        text = p.read_text()
        assert "ConditionKernelCommandLine=|!pengyos.data" in text, p
        assert "/dev/disk/by-label" not in text, p


def test_the_dropins_are_identical():
    """All four are the same policy; drift between them is how one output silently
    stops starting."""
    texts = {p.read_text() for p in DROPINS}
    assert len(texts) == 1, sorted(texts)


@pytest.mark.skipif(shutil.which("systemd-analyze") is None,
                    reason="systemd-analyze not available")
def test_the_condition_agrees_with_this_hosts_cmdline():
    """systemd-analyze condition reads the real /proc/cmdline, so this checks the
    rule on whatever host runs it: ``|!pengyos.data`` holds exactly when the boot is
    not an installed PengyOS (no ``pengyos.data=`` on the line)."""
    cmdline = Path("/proc/cmdline").read_text()
    live = "pengyos.data=" not in cmdline
    r = subprocess.run(["systemd-analyze", "condition",
                        "ConditionKernelCommandLine=|!pengyos.data"],
                       capture_output=True, text=True)
    assert (r.returncode == 0) == live, r.stdout + r.stderr


@pytest.mark.skipif(shutil.which("systemd-analyze") is None,
                    reason="systemd-analyze not available")
def test_the_positive_form_is_the_complement():
    """On a live boot the *installed* form must not hold — that is what makes the
    negated form safe to OR in."""
    cmdline = Path("/proc/cmdline").read_text()
    live = "pengyos.data=" not in cmdline
    r = subprocess.run(["systemd-analyze", "condition",
                        "ConditionKernelCommandLine=pengyos.data"],
                       capture_output=True, text=True)
    assert (r.returncode == 0) == (not live), r.stdout + r.stderr
