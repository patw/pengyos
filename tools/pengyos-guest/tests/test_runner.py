"""Tests for the mockable runner."""

from pengyos_guest.runner import FakeRunner, RunResult, SubprocessRunner


def test_fake_runner_records_calls():
    runner = FakeRunner()
    result = runner.run(["podman", "version"])
    assert runner.calls == [["podman", "version"]]
    assert result.returncode == 0


def test_fake_runner_scripted_response():
    runner = FakeRunner()
    runner.respond(["podman", "inspect"], RunResult(argv=["podman", "inspect"], returncode=1, stdout='{"exists":false}'))
    result = runner.run(["podman", "inspect", "guest"])
    assert result.returncode == 1
    assert result.stdout == '{"exists":false}'


def test_fake_runner_falls_back_to_default():
    runner = FakeRunner(default=RunResult(argv=[], returncode=3, stderr="boom"))
    result = runner.run(["anything"])
    assert result.returncode == 3
    assert result.stderr == "boom"


def test_fake_runner_prefix_match_is_anchored_at_start():
    runner = FakeRunner()
    runner.respond(["podman"], RunResult(argv=["podman"], returncode=0))
    runner.respond(["podman", "run"], RunResult(argv=["podman", "run"], returncode=0))
    runner.run(["podman", "run", "-d"])  # matches the run prefix
    runner.run(["podmansquat"])  # does NOT match "podman" prefix
    # podmansquat should have used the default (not the "podman" script)
    assert runner.calls[-1] == ["podmansquat"]


def test_subprocess_runner_executes_real_command():
    # A harmless stdlib-free command that needs no network/root/hardware.
    result = SubprocessRunner().run(["python3", "--version"])
    assert result.returncode == 0
    assert result.argv == ["python3", "--version"]
    assert "Python" in result.stdout


def test_subprocess_runner_missing_binary_returns_127(monkeypatch):
    """A missing binary (podman/distrobox not on PATH) is a clean, non-zero
    result — not a crash — so the CLI can report it as a normal failure."""
    import subprocess as _subprocess

    def _raise(*args, **kwargs):
        raise FileNotFoundError("podman")

    monkeypatch.setattr(_subprocess, "run", _raise)
    result = SubprocessRunner().run(["podman", "version"])
    assert result.returncode == 127
    assert "podman" in result.stderr


def test_runresult_ok_property():
    assert RunResult(argv=[], returncode=0).ok is True
    assert RunResult(argv=[], returncode=1).ok is False


def test_fake_runner_records_env_per_call():
    fake = FakeRunner()
    assert fake.last_env is None
    fake.run(["a"], env={"X": "1"})
    fake.run(["b"])  # env omitted -> None
    assert fake.envs == [{"X": "1"}, None]
    assert fake.last_env is None


def test_fake_runner_env_defaults_to_none():
    fake = FakeRunner()
    fake.run(["a"])
    assert fake.last_env is None
