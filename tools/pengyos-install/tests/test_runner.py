"""Tests for the injectable runner (spec: architecture seam)."""

from __future__ import annotations

import pytest

from pengyos_install.runner import (
    FakeRunner,
    RunResult,
    Runner,
    SubprocessRunner,
    WrapperRunner,
)


def test_result_ok_reflects_returncode():
    assert RunResult(argv=["x"], returncode=0).ok is True
    assert RunResult(argv=["x"], returncode=1).ok is False


def test_fake_records_calls_and_envs():
    fake = FakeRunner()
    fake.run(["a", "b"], env={"X": "1"})
    fake.run(["c"], env={"Y": "2"})
    assert fake.calls == [["a", "b"], ["c"]]
    # last_env / last_call reflect the most recent run.
    assert fake.last_env == {"Y": "2"}
    assert fake.last_call == ["c"]


def test_last_call_and_last_env_are_none_before_any_run():
    fake = FakeRunner()
    assert fake.last_call is None
    assert fake.last_env is None


def test_fake_responds_by_argv_prefix():
    fake = FakeRunner()
    fake.respond(["mkfs"], RunResult(argv=["mkfs"], returncode=0, stdout="ok"))
    fake.respond(["dd"], RunResult(argv=["dd"], returncode=1, stderr="boom"))

    assert fake.run(["mkfs", "-t", "ext4", "/dev/sda2"]).stdout == "ok"
    assert fake.run(["dd", "if=...", "of=/dev/sda2"]).returncode == 1
    assert fake.run(["unknown"]).ok is True  # default result


def test_fake_default_result_is_zero():
    fake = FakeRunner()
    assert fake.run(["anything"]).returncode == 0


def test_guard_is_invoked_before_run():
    class Guard:
        def __init__(self) -> None:
            self.checked: list[list[str]] = []

        def check(self, argv: list[str]) -> None:
            self.checked.append(argv)

    guard = Guard()
    fake = FakeRunner(guard=guard)
    fake.run(["mkfs", "/dev/sda2"])
    assert guard.checked == [["mkfs", "/dev/sda2"]]


def test_guard_violation_raises():
    class Refuse:
        def check(self, argv: list[str]) -> None:
            raise PermissionError(f"refused: {argv[0]}")

    fake = FakeRunner(guard=Refuse())
    with pytest.raises(PermissionError):
        fake.run(["rm", "-rf", "/"])


def test_subprocess_runner_reports_missing_binary_cleanly():
    # A nonexistent binary must not raise; it yields a clean non-zero result.
    result = SubprocessRunner().run(["definitely-not-a-real-binary-xyz"])
    assert result.returncode == 127
    assert "not found" in result.stderr


def test_runner_is_abstract():
    with pytest.raises(NotImplementedError):
        Runner().run(["x"])


def test_wrapper_runner_prefixes_argv():
    """WrapperRunner prefixes each argv with the given command (the --runner CLI)."""
    inner = FakeRunner()
    wrapped = WrapperRunner(["echo"], runner=inner)
    result = wrapped.run(["dd", "if=a", "of=/dev/sda2"])
    # the inner runner saw the prefixed argv
    assert inner.calls[0] == ["echo", "dd", "if=a", "of=/dev/sda2"]
    # the result is the inner runner's result
    assert result.returncode == inner.default.returncode


def test_wrapper_runner_defaults_to_subprocess_runner():
    """Without an explicit inner runner, WrapperRunner uses SubprocessRunner."""
    wrapped = WrapperRunner(["definitely-not-a-real-binary-xyz"])
    assert isinstance(wrapped.runner, SubprocessRunner)
    # a missing binary yields a clean 127 (not a raised exception)
    assert wrapped.run(["echo", "hi"]).returncode == 127


def test_wrapper_runner_passes_env():
    inner = FakeRunner()
    wrapped = WrapperRunner(["sudo"], runner=inner)
    wrapped.run(["dd", "if=a"], env={"X": "1"})
    assert inner.envs[0] == {"X": "1"}
    assert inner.calls[0] == ["sudo", "dd", "if=a"]
