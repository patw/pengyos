"""Tests for the transport abstraction (``pengyos_fleet_rollout.transport``).

These lock the transport subtask: the side-effect-free :class:`NoopTransport`
(the default, and the path the suite asserts on), the subprocess-backed
:class:`SubprocessTransport` (driven by a fake executable — no real machine),
and the dry-run ``build_argv`` that renders the exact argv a transport would run
without invoking anything. No network, no root, no hardware — the subprocess
fake is a tiny script under ``tmp_path`` (on disk here).
"""

from __future__ import annotations

import stat
import sys

import pytest

import pengyos_fleet_rollout
from pengyos_fleet_rollout.errors import RolloutTransportError
from pengyos_fleet_rollout.transport import (
    ACTION_APPLY,
    ACTION_ROLLED_BACK,
    DEFAULT_TRANSPORT_CMD,
    NoopTransport,
    SubprocessTransport,
    Transport,
    TransportStep,
    TransportResult,
    build_argv,
    make_transport,
)


# ---------------------------------------------------------------------------
# TransportStep.
# ---------------------------------------------------------------------------


def test_transport_step_defaults_address_to_none():
    step = TransportStep(ACTION_APPLY, "canary-1", "img-0", "img-1")
    assert step.action == ACTION_APPLY
    assert step.host == "canary-1"
    assert step.prior == "img-0"
    assert step.new == "img-1"
    assert step.address is None


def test_transport_step_is_frozen():
    step = TransportStep(ACTION_APPLY, "canary-1", "img-0", "img-1")
    with pytest.raises(AttributeError):
        step.host = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_argv — the exact argv the transport renders.
# ---------------------------------------------------------------------------


def test_build_argv_has_command_action_host_prior_new():
    step = TransportStep(ACTION_APPLY, "canary-1", "img-0", "img-1")
    assert build_argv(step) == (
        DEFAULT_TRANSPORT_CMD,
        "apply",
        "canary-1",
        "img-0",
        "img-1",
    )


def test_build_argv_appends_address_when_present():
    step = TransportStep(
        ACTION_APPLY, "canary-1", "img-0", "img-1", address="canary-1.home.lab"
    )
    assert build_argv(step) == (
        DEFAULT_TRANSPORT_CMD,
        "apply",
        "canary-1",
        "img-0",
        "img-1",
        "--address",
        "canary-1.home.lab",
    )


def test_build_argv_omits_address_when_absent():
    step = TransportStep(ACTION_ROLLED_BACK, "core-1", "img-1", "img-0")
    assert "--address" not in build_argv(step)


def test_build_argv_omits_address_when_explicitly_disabled():
    step = TransportStep(
        ACTION_APPLY, "canary-1", "img-0", "img-1", address="canary-1.home.lab"
    )
    assert "--address" not in build_argv(step, include_address=False)


def test_build_argv_respects_a_custom_command():
    step = TransportStep(ACTION_APPLY, "canary-1", "img-0", "img-1")
    argv = build_argv(step, command="/usr/local/bin/my-transport")
    assert argv[0] == "/usr/local/bin/my-transport"


def test_build_argv_is_deterministic():
    step = TransportStep(
        ACTION_APPLY, "canary-1", "img-0", "img-1", address="canary-1.home.lab"
    )
    assert build_argv(step) == build_argv(step)


# ---------------------------------------------------------------------------
# NoopTransport — the side-effect-free path the suite asserts on.
# ---------------------------------------------------------------------------


def test_noop_transport_renders_argv_and_reports_success():
    step = TransportStep(
        ACTION_APPLY, "canary-1", "img-0", "img-1", address="canary-1.home.lab"
    )
    result = NoopTransport().run(step)
    assert result.ok is True
    assert result.argv == build_argv(step)
    assert result.returncode is None  # no subprocess was run
    assert result.output == ""


def test_noop_transport_performs_no_transport_call():
    # The dry-run contract: the noop transport never invokes anything, so the
    # only observable effect is the rendered argv. A fake subprocess that would
    # raise if executed is never called.
    step = TransportStep(ACTION_APPLY, "canary-1", "img-0", "img-1")
    result = NoopTransport().run(step)
    assert result.ok is True
    assert result.returncode is None


def test_noop_transport_rollback_renders_the_rollback_argv():
    step = TransportStep(ACTION_ROLLED_BACK, "core-1", "img-1", "img-0")
    result = NoopTransport().run(step)
    assert result.argv == (
        DEFAULT_TRANSPORT_CMD,
        "rollback",
        "core-1",
        "img-1",
        "img-0",
    )


def test_noop_transport_reports_the_step_prior():
    # The noop transport has no host to read, so it reports the step's own prior
    # — apply falls back to this (the UNKNOWN placeholder on a fresh plan).
    step = TransportStep(ACTION_APPLY, "canary-1", "img-2026.09.18", "img-2026.09.25")
    result = NoopTransport().run(step)
    assert result.prior == "img-2026.09.18"


def test_transport_result_prior_defaults_to_none():
    result = TransportResult(ok=True, argv=("cmd",))
    assert result.prior is None


# ---------------------------------------------------------------------------
# SubprocessTransport — driven by a fake executable (no real machine).
# ---------------------------------------------------------------------------


def _write_fake_transport(tmp_path, name, body=""):
    """Write a tiny executable script that reports the argv it was run with.

    The script is written under ``tmp_path`` (on disk here — ``/tmp`` is tmpfs
    on this host, so a build there would OOM the box). The shebang points at
    ``sys.executable`` — the exact interpreter running the test — so the fake
    needs no network, no root, and no real hardware, and survives the host's
    ``env`` sandbox (which rejects a ``#!/usr/bin/env`` shebang). The script
    echoes the argv it was invoked with on stdout; ``body`` is appended so a
    test can make it exit non-zero.
    """

    script = tmp_path / name
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "sys.stdout.write('INVOKED: ' + ' '.join(sys.argv[1:]) + '\\n')\n"
        f"{body}",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(script)


def test_subprocess_transport_succeeds_on_zero_exit(tmp_path):
    script = _write_fake_transport(tmp_path, "ok.sh")
    step = TransportStep(ACTION_APPLY, "canary-1", "img-0", "img-1")
    result = SubprocessTransport(env={"PENGYOS_ROLLOUT_TRANSPORT_CMD": script}).run(step)
    assert result.ok is True
    assert result.returncode == 0
    assert result.argv == (script, "apply", "canary-1", "img-0", "img-1")
    assert "INVOKED: apply canary-1 img-0 img-1" in result.output


def test_subprocess_transport_fails_on_nonzero_exit(tmp_path):
    script = _write_fake_transport(tmp_path, "fail.sh", body="sys.exit(3)\n")
    step = TransportStep(ACTION_ROLLED_BACK, "core-1", "img-1", "img-0")
    result = SubprocessTransport(env={"PENGYOS_ROLLOUT_TRANSPORT_CMD": script}).run(step)
    assert result.ok is False  # a failed step is what a gate failure surfaces as
    assert result.returncode == 3


def test_subprocess_transport_uses_the_env_command(tmp_path):
    script = _write_fake_transport(tmp_path, "env.sh")
    # With the env var unset the transport falls back to the default command,
    # which is not an executable on this box — it must fail loudly, proving the
    # env var is what selects the command.
    step = TransportStep(ACTION_APPLY, "canary-1", "img-0", "img-1")
    with pytest.raises(RolloutTransportError):
        SubprocessTransport(env={}).run(step)
    # And with the env var set it runs the fake.
    result = SubprocessTransport(env={"PENGYOS_ROLLOUT_TRANSPORT_CMD": script}).run(step)
    assert result.ok is True


def test_subprocess_transport_raises_when_command_absent():
    # A command that does not exist (and no env override) is a transport error,
    # not a swallowed exception.
    script = "/no/such/transport/binary"
    step = TransportStep(ACTION_APPLY, "canary-1", "img-0", "img-1")
    with pytest.raises(RolloutTransportError):
        SubprocessTransport(env={"PENGYOS_ROLLOUT_TRANSPORT_CMD": script}).run(step)


def test_subprocess_transport_captures_stderr(tmp_path):
    script = tmp_path / "err.sh"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "sys.stderr.write('boom\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    step = TransportStep(ACTION_APPLY, "canary-1", "img-0", "img-1")
    result = SubprocessTransport(env={"PENGYOS_ROLLOUT_TRANSPORT_CMD": str(script)}).run(step)
    assert result.ok is True
    assert "boom" in result.output


# ---------------------------------------------------------------------------
# make_transport — the factory the CLI dispatches on.
# ---------------------------------------------------------------------------


def test_make_transport_noop_returns_noop_transport():
    transport = make_transport("noop")
    assert isinstance(transport, NoopTransport)
    assert isinstance(transport, Transport)


def test_make_transport_subprocess_returns_subprocess_transport():
    transport = make_transport("subprocess")
    assert isinstance(transport, SubprocessTransport)


def test_make_transport_unknown_raises():
    with pytest.raises(RolloutTransportError):
        make_transport("bogus")


# ---------------------------------------------------------------------------
# The Transport protocol is the injectable contract.
# ---------------------------------------------------------------------------


def test_transport_protocol_runtime_checkable():
    # The protocol is runtime-checkable, so a concrete transport satisfies it.
    assert isinstance(NoopTransport(), Transport)
    assert isinstance(SubprocessTransport(), Transport)
    assert not isinstance("not a transport", Transport)


def test_public_api_exports_the_transport():
    # The transport is the injectable seam apply/rollback drive; it is importable
    # from the package root so later subtasks (and tests) can reach for it.
    import pengyos_fleet_rollout.transport as t

    for name in (
        "TransportStep",
        "TransportResult",
        "NoopTransport",
        "SubprocessTransport",
        "build_argv",
        "make_transport",
    ):
        assert hasattr(t, name), f"missing transport export {name}"
