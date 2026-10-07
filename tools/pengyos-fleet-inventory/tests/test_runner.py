"""Tests for the injectable runner (``pengyos_fleet.runner``).

Covers the runner factory, the deterministic mock runner (driven by committed
fixtures), and the SSH runner's argument construction and subprocess plumbing
(via a tiny fake ``ssh`` script — no real network needed).
"""

from __future__ import annotations

import os
import subprocess

import pytest

from pengyos_fleet.hosts import Host
from pengyos_fleet.runner import (
    MockRunner,
    RunResult,
    Runner,
    RunnerError,
    SSHRunner,
    make_runner,
)


def _host(name="n1", address="n1.home.lab", fixtures=None):
    return Host(name=name, address=address, fixtures=fixtures)


# ---------------------------------------------------------------------------
# Factory.
# ---------------------------------------------------------------------------


def test_make_runner_mock():
    runner = make_runner("mock")
    assert isinstance(runner, MockRunner)
    assert runner.name == "mock"


def test_make_runner_ssh():
    runner = make_runner("ssh")
    assert isinstance(runner, SSHRunner)
    assert runner.name == "ssh"


def test_make_runner_is_case_insensitive():
    assert isinstance(make_runner("MOCK"), MockRunner)
    assert isinstance(make_runner("Ssh"), SSHRunner)


def test_make_runner_unknown_raises():
    with pytest.raises(RunnerError, match="unknown runner"):
        make_runner("telnet")


# ---------------------------------------------------------------------------
# Mock runner.
# ---------------------------------------------------------------------------


def test_mock_runner_returns_fixture_output():
    here = __import__("pathlib").Path(__file__).parent
    host = _host(fixtures=str(here / "fixtures" / "hosts" / "n1"))
    runner = MockRunner()
    result = runner.run(host, ["lscpu"])
    assert isinstance(result, RunResult)
    assert result.args == ["lscpu"]
    assert result.returncode == 0
    assert "Model name:" in result.stdout
    assert result.stderr == ""


def test_mock_runner_maps_command_basename():
    here = __import__("pathlib").Path(__file__).parent
    host = _host(fixtures=str(here / "fixtures" / "hosts" / "n1"))
    runner = MockRunner()
    # A command with flags still maps by basename (free -b -> free).
    result = runner.run(host, ["free", "-b"])
    assert "total" in result.stdout


def test_mock_runner_missing_fixture_raises():
    here = __import__("pathlib").Path(__file__).parent
    host = _host(fixtures=str(here / "fixtures" / "hosts" / "n1"))
    runner = MockRunner()
    with pytest.raises(RunnerError, match="no fixture"):
        runner.run(host, ["lsof"])  # no such fixture file


def test_mock_runner_empty_command_raises():
    host = _host(fixtures="/tmp/whatever")
    with pytest.raises(RunnerError, match="empty command"):
        MockRunner().fixture_path(host, [])


def test_mock_runner_requires_fixtures_path():
    # A host with no fixtures and no runner-level root.
    host = _host(fixtures=None)
    with pytest.raises(RunnerError, match="no fixtures path"):
        MockRunner().run(host, ["lscpu"])


def test_mock_runner_falls_back_to_root(tmp_path):
    (tmp_path / "n1").mkdir()
    (tmp_path / "n1" / "lscpu").write_text("fake cpu\n", encoding="utf-8")
    host = _host(fixtures=None)  # no per-host fixtures -> use fixtures_root
    runner = MockRunner(fixtures_root=str(tmp_path / "n1"))
    result = runner.run(host, ["lscpu"])
    assert result.stdout == "fake cpu\n"


def test_mock_runner_is_abstract_subclass():
    class Concrete(Runner):
        def run(self, host, args):  # pragma: no cover - trivial
            return RunResult(args, 0)

    assert issubclass(Concrete, Runner)
    assert Concrete().run(_host(), ["x"]).returncode == 0


# ---------------------------------------------------------------------------
# SSH runner — argument construction.
# ---------------------------------------------------------------------------


def test_ssh_build_argv_no_user():
    argv = SSHRunner().build_argv(_host(address="n1.home.lab"), ["lscpu"])
    assert argv == ["ssh", "--", "n1.home.lab", "lscpu"]


def test_ssh_build_argv_with_user():
    argv = SSHRunner(user="admin").build_argv(
        _host(address="n1.home.lab"), ["lscpu"]
    )
    assert argv == ["ssh", "--", "admin@n1.home.lab", "lscpu"]


def test_ssh_build_argv_forwards_options():
    argv = SSHRunner(ssh_options=["-o", "ConnectTimeout=5"]).build_argv(
        _host(address="n1.home.lab"), ["uname", "-s"]
    )
    assert argv == ["ssh", "-o", "ConnectTimeout=5", "--", "n1.home.lab", "uname", "-s"]


def test_ssh_build_argv_guards_leading_dash():
    # The `--` separator precedes the target, so a remote command that begins
    # with a dash (here `-n`) is never parsed as a local ssh option.
    argv = SSHRunner().build_argv(_host(address="n1.home.lab"), ["-n", "x"])
    assert argv == ["ssh", "--", "n1.home.lab", "-n", "x"]


# ---------------------------------------------------------------------------
# SSH runner — subprocess plumbing (via a fake ssh script, no network).
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_ssh(tmp_path):
    """A stand-in `ssh` that echoes its remote args and exits with a code."""

    script = tmp_path / "fake-ssh"
    script.write_text(
        "#!/bin/sh\n"
        'echo "remote args: $*"\n'
        'echo "remote stderr" >&2\n'
        "exit 3\n",
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    return str(script)


def test_ssh_run_captures_output(fake_ssh):
    runner = SSHRunner(ssh_bin=fake_ssh)
    result = runner.run(_host(address="n1.home.lab"), ["lscpu"])
    assert result.returncode == 3
    assert "lscpu" in result.stdout
    assert "remote stderr" in result.stderr


def test_ssh_run_missing_bin_raises():
    with pytest.raises(RunnerError, match="not found"):
        SSHRunner(ssh_bin=str(__import__("pathlib").Path("/no/such/ssh-bin"))).run(
            _host(), ["lscpu"]
        )


def test_ssh_run_timeout_raises(monkeypatch):
    def boom(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["ssh"], timeout=30)

    monkeypatch.setattr("pengyos_fleet.runner.subprocess.run", boom)
    with pytest.raises(RunnerError, match="timed out"):
        SSHRunner(timeout=30).run(_host(address="n1"), ["lscpu"])
