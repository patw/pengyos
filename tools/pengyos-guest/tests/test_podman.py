"""Tests for the podman wrapper (``pengyos_guest.podman``).

The wrapper is exercised entirely through the mockable :class:`FakeRunner`, so
these tests assert on the exact argv that would be executed and never touch a
real podman, network, root, or display.
"""

from pengyos_guest.podman import Podman
from pengyos_guest.runner import FakeRunner, RunResult, SubprocessRunner


def test_defaults_to_subprocess_runner():
    # No runner injected -> production gets a real subprocess runner.
    assert isinstance(Podman().runner, SubprocessRunner)


def test_injected_runner_is_used():
    fake = FakeRunner()
    podman = Podman(runner=fake)
    podman.inspect("wolfi")
    assert fake.calls == [["podman", "inspect", "wolfi"]]


def test_inspect_builds_argv():
    fake = FakeRunner()
    Podman(runner=fake).inspect("wolfi")
    assert fake.calls == [["podman", "inspect", "wolfi"]]


def test_container_exists_true_when_returncode_zero():
    fake = FakeRunner()
    fake.respond(["podman", "container", "exists"], RunResult(argv=["podman"], returncode=0))
    assert Podman(runner=fake).container_exists("wolfi") is True
    assert fake.calls == [["podman", "container", "exists", "wolfi"]]


def test_container_exists_false_when_nonzero():
    fake = FakeRunner()
    fake.respond(["podman", "container", "exists"], RunResult(argv=["podman"], returncode=1))
    assert Podman(runner=fake).container_exists("ghost") is False


def test_stop_builds_argv_with_ignore_flag():
    fake = FakeRunner()
    Podman(runner=fake).stop("wolfi")
    assert fake.calls == [["podman", "stop", "--ignore", "wolfi"]]


def test_remove_builds_argv_with_ignore_flag():
    fake = FakeRunner()
    Podman(runner=fake).remove("wolfi")
    assert fake.calls == [["podman", "rm", "--ignore", "wolfi"]]


def test_run_passthrough():
    fake = FakeRunner()
    Podman(runner=fake).run(["images", "--format", "{{.Repository}}"])
    assert fake.calls == [["podman", "images", "--format", "{{.Repository}}"]]


def test_env_passed_through_to_runner():
    fake = FakeRunner()
    Podman(runner=fake).run(["version"], env={"FOO": "bar"})
    # The runner sees the argv (with the podman prefix) and the env dict.
    assert fake.calls == [["podman", "version"]]
    assert fake.last_env == {"FOO": "bar"}


def test_default_result_is_used_when_unscripted():
    fake = FakeRunner(default=RunResult(argv=[], returncode=5))
    result = Podman(runner=fake).inspect("wolfi")
    assert result.returncode == 5
