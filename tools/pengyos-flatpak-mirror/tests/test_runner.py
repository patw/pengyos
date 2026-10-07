"""Runner tests.

Covers :mod:`pengyos_flatpak_mirror.runner`: the mockable execution boundary and
the golden argv each operation constructs. No real flatpak/ostree, network,
root, or display — every external call goes through an injected fake runner
(plus a few safe real-subprocess checks for the production default).
"""

import subprocess
from pathlib import Path

import pytest

from pengyos_flatpak_mirror import runner
from pengyos_flatpak_mirror.runner import CommandError, Result, Runner


# --- a fake runner that records argv and returns canned results -------------


class FakeRunner:
    """Records argv and returns a canned :class:`Result` (or raises)."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return Result(argv=list(argv), returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


# --- golden argv -------------------------------------------------------------


def test_init_repo_golden_argv():
    fake = FakeRunner()
    r = Runner(runner=fake).init_repo("/tmp/repo")
    assert r.argv == ["flatpak", "build-update-repo", "--init", "/tmp/repo"]
    assert r.returncode == 0


def test_add_remote_golden_argv():
    fake = FakeRunner()
    Runner(runner=fake).add_remote("pengyos", "https://example.com/remote")
    assert fake.calls[-1] == [
        "flatpak",
        "remote-add",
        "--if-not-exists",
        "pengyos",
        "https://example.com/remote",
    ]


def test_mirror_ref_golden_argv():
    fake = FakeRunner()
    r = Runner(runner=fake).mirror_ref("org.example.App", "aarch64", "edge", "deadbeef")
    assert r.argv == [
        "flatpak",
        "update",
        "--user",
        "--noninteractive",
        "--commit=deadbeef",
        "org.example.App@aarch64/edge",
    ]


def test_pull_collection_golden_argv():
    fake = FakeRunner()
    Runner(runner=fake).pull_collection("org.example", "pengyos")
    assert fake.calls[-1] == [
        "flatpak",
        "update",
        "--user",
        "--noninteractive",
        "--collection=org.example",
        "pengyos",
    ]


def test_list_refs_golden_argv():
    fake = FakeRunner()
    Runner(runner=fake).list_refs("/tmp/repo")
    assert fake.calls[-1] == ["ostree", "--repo=/tmp/repo", "refs"]


def test_verify_golden_argv():
    fake = FakeRunner()
    Runner(runner=fake).verify("/tmp/repo", "/tmp/key.asc")
    assert fake.calls[-1] == [
        "ostree",
        "--repo=/tmp/repo",
        "verify",
        "--gpg-key=/tmp/key.asc",
    ]


def test_prune_golden_argv_and_keep_recorded():
    fake = FakeRunner()
    r = Runner(runner=fake).prune("/tmp/repo", keep=5)
    assert r.argv == ["flatpak", "update", "--prune"]
    # The requested keep count is recorded on the result (a per-ref --keep flag
    # is a follow-up; see the module docstring).
    assert r.keep == 5


def test_prune_keep_zero_is_allowed():
    fake = FakeRunner()
    r = Runner(runner=fake).prune("/tmp/repo", keep=0)
    assert r.keep == 0


def test_prune_negative_keep_rejected():
    with pytest.raises(ValueError):
        Runner(runner=FakeRunner()).prune("/tmp/repo", keep=-1)


# --- mockability: the injected runner is the single execution boundary -------


def test_operations_pass_argv_through_injected_runner():
    fake = FakeRunner()
    run = Runner(runner=fake)
    run.init_repo("/a")
    run.add_remote("r", "https://x")
    run.mirror_ref("id", "x86_64", "stable", "0a1b2c3d")
    run.list_refs("/a")
    run.verify("/a", "/k")
    run.prune("/a", 3)
    # Every invocation of the external tool went through the fake, in order.
    assert fake.calls == [
        ["flatpak", "build-update-repo", "--init", "/a"],
        ["flatpak", "remote-add", "--if-not-exists", "r", "https://x"],
        ["flatpak", "update", "--user", "--noninteractive", "--commit=0a1b2c3d", "id@x86_64/stable"],
        ["ostree", "--repo=/a", "refs"],
        ["ostree", "--repo=/a", "verify", "--gpg-key=/k"],
        ["flatpak", "update", "--prune"],
    ]


def test_injected_runner_can_return_completed_process():
    """The runner may return a subprocess.CompletedProcess — the production
    default. Normalisation must still yield a Result."""
    cp = subprocess.run(["true"], capture_output=True, text=True)
    r = Runner(runner=lambda argv: cp).init_repo("/a")
    assert isinstance(r, Result)
    assert r.returncode == cp.returncode
    assert r.argv == ["flatpak", "build-update-repo", "--init", "/a"]


def test_injected_runner_can_return_int():
    assert Runner(runner=lambda argv: 0).init_repo("/a").returncode == 0


def test_injected_runner_can_return_tuple():
    res = Runner(runner=lambda argv: (0, "out", "")).init_repo("/a")
    assert res.returncode == 0
    assert res.stdout == "out"
    assert res.stderr == ""


def test_injected_runner_tuple_failure_raises():
    with pytest.raises(CommandError):
        Runner(runner=lambda argv: (1, "", "boom")).init_repo("/a")


def test_injected_runner_rejects_unsupported_return():
    with pytest.raises(TypeError):
        Runner(runner=lambda argv: object()).init_repo("/a")


# --- failure handling --------------------------------------------------------


def test_nonzero_exit_raises_command_error():
    fake = FakeRunner(returncode=127, stderr="flatpak: command not found")
    with pytest.raises(CommandError) as exc:
        Runner(runner=fake).init_repo("/a")
    assert exc.value.result.returncode == 127
    assert "flatpak: command not found" in str(exc.value)


def test_command_error_carries_argv(capsys):
    fake = FakeRunner(returncode=1, stderr="boom")
    with pytest.raises(CommandError) as exc:
        Runner(runner=fake).verify("/a", "/k")
    assert exc.value.result.argv == ["ostree", "--repo=/a", "verify", "--gpg-key=/k"]


def test_successful_result_does_not_raise():
    Runner(runner=FakeRunner(returncode=0)).init_repo("/a")  # must not raise


# --- logger injection --------------------------------------------------------


def test_log_goes_through_injected_logger():
    seen: list[str] = []
    r = Runner(runner=FakeRunner(), logger=seen.append)
    r.init_repo("/a")
    assert len(seen) == 1
    assert "build-update-repo" in seen[0]
    assert "flatpak" in seen[0]


def test_no_logger_is_noop():
    # Default logger is None; log() must not raise.
    Runner(runner=FakeRunner()).log("anything")


# --- custom executable names -------------------------------------------------


def test_custom_executable_names():
    fake = FakeRunner()
    Runner(runner=fake, flatpak="fb", ostree="ob").list_refs("/a")
    assert fake.calls[-1] == ["ob", "--repo=/a", "refs"]


# --- production default path (safe real subprocesses, no root/network) --------


def test_run_helper_executes_real_command():
    # The default runner streams to the terminal (does not capture), so we
    # assert on the exit code and argv, not on captured stdout.
    res = runner.run(["python3", "-c", "print('hello runner')"])
    assert res.returncode == 0
    assert res.argv == ["python3", "-c", "print('hello runner')"]


def test_run_helper_raises_on_failure():
    with pytest.raises(CommandError):
        runner.run(["false"])


def test_default_runner_is_subprocess_run():
    """A bare Runner() (no injected runner) talks to the real subprocess path."""
    res = Runner()._run(["true"])
    assert res.returncode == 0


def test_runner_is_importable_from_package():
    # Ensures the module is reachable and the public surface is stable.
    assert Runner is runner.Runner
    assert CommandError is runner.CommandError
    assert Result is runner.Result


def test_result_fields_frozen():
    res = Result(argv=["a"], returncode=0)
    with pytest.raises(Exception):
        res.returncode = 1  # type: ignore[misc]
