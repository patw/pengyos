"""Opt-in (PENGYOS_IT=1, root) integration test for the dummy test link.

Brings up the offline
``pengyos-test0`` dummy link from ``networkd/20-pengyos-test.netdev`` +
``20-pengyos-test.network`` via systemd-networkd and check that it
carries the pinned ``10.99.0.1/24`` address.

Design constraints:
- The runtime path only executes when ``PENGYOS_IT=1`` AND the process
  runs as root on a systemd host where networkd + ip(8) are installed.
  Otherwise the integration test skips (conftest.py already skips the
  ``integration`` mark without ``PENGYOS_IT=1``; the guards inside the
  test are the second line of defense).
- The bring-up/verify state machine is factored out of the test body so
  it is unit-testable OFFLINE (no root, no network) against a fake
  command runner and a fake clock: the state machine is the only
  non-trivial logic here (wait loop, start-vs-reload choice, cleanup
  ordering) and it must be pinned by tests even on hosts where the real
  thing cannot run.
- Teardown is total: the test always deletes the link, removes the two
  unit files it installed, and reloads networkd — even when the body
  fails (``finally``). The test installs ONLY the two pengyos-test
  units, never the baseline default unit, and never modifies any
  pre-existing system config.
"""
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETDEV_UNIT = os.path.join(REPO_ROOT, "networkd", "20-pengyos-test.netdev")
NETWORK_UNIT = os.path.join(REPO_ROOT, "networkd", "20-pengyos-test.network")

LINK = "pengyos-test0"
EXPECTED_ADDRESS = ipaddress.IPv4Address("10.99.0.1")
EXPECTED_PREFIX = 24
NETWORKD_SERVICE = "systemd-networkd.service"
UNIT_DIR_DEFAULT = "/etc/systemd/network"

DEADLINE = 30.0  # s, worst-case wait for the dummy link to come up
POLL = 0.5  # s, wait-loop poll interval


class IntegrationError(RuntimeError):
    """The real bring-up did not reach the expected state."""


# ------------------------------------------------------------ command helpers


def _run(cmd, check=True, timeout=30):
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout
    )
    if check and proc.returncode != 0:
        raise IntegrationError(
            f"command failed: {' '.join(cmd)}\n"
            f"rc={proc.returncode}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return proc


def _has_systemd():
    # /run/systemd/system exists iff systemd is PID 1 on this host.
    return os.path.isdir("/run/systemd/system")


def _service_active(service):
    proc = subprocess.run(
        ["systemctl", "is-active", "--", service],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "active"


def _real_run(cmd, check=True, timeout=30):
    """The real command runner for bring_up/teardown.

    Understands two pseudo-commands (mirrored by FakeRunner in the
    offline tests) so the state machine has ONE runner vocabulary:
    `install-unit <src> <dst>` copies a shipped unit into the live
    config dir; `uninstall-unit <dst>` removes it. Everything else is
    a plain subprocess.
    """
    if cmd[0] == "install-unit":
        shutil.copyfile(cmd[1], cmd[2])
        os.chmod(cmd[2], 0o644)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    if cmd[0] == "uninstall-unit":
        try:
            os.unlink(cmd[1])
        except FileNotFoundError:
            pass
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    return _run(cmd, check=check, timeout=timeout)


# `ip -o link show` prints ONE physical line per device: continuation
# lines are joined with a literal backslash + whitespace (NOT a real
# newline) — e.g. "...qlen 1000\\    link/ether 2c:... brd ...\\
#     altname ...". Fixtures below use the same `\\` + newline spelling
# for readability; both are accepted by the parser.
# Header is `2: <dev>: <flags> ... state <STATE> ...` — note the SPACE
# between the `:` and the `<flags>` (ip(8) prints `enp34s0: <BROADCAST…`
#), and the optional `@parent` suffix on some interfaces.
LINK_RE = re.compile(
    r"^\d+:\s+\S+:\s*<[^>]*>(?:.*?\b)?"
    r"state (?P<state>\S+)\s.*\n"
    r"\s*link/(?P<kind>\S+)\s+[0-9a-f:]+\s+brd\s+\S+\s*$",
    re.M,
)


def _unescape_ip_one_line(output):
    """Turn ip -o's `\\`-joined continuations into real newlines."""
    return output.replace("\\\n", "\n").replace("\\ ", "\n")


def parse_ip_link_show(output):
    """Parse `ip -o link show <dev>` into (state, link_kind) or None.

    `ip -o` prints the header line
    ``2: pengyos-test0: <...> ... state UP ...`` and the continuation
    ``    link/dummy 02:00:00:00:00 brd ...`` (joined with a literal
    backslash in live output; see _unescape_ip_one_line) ->
    ``("UP", "dummy")``. Returns None when the device does not appear
    (device not created).
    """
    m = LINK_RE.search(_unescape_ip_one_line(output))
    if not m:
        return None
    return (m.group("state") or "UNKNOWN"), m.group("kind")


def parse_ip_addr_show(output):
    """Parse `ip -o -4 addr show dev <dev>` into [(ip, prefix), ...].

    One ``inet <addr>/<prefix> ...`` per line; a dummy link without the
    address configured yields [] (the link exists, the address does
    not).
    """
    result = []
    for line in output.splitlines():
        m = re.search(
            r"inet (\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})(?:\s|$)", line
        )
        if m:
            result.append(
                (ipaddress.IPv4Address(m.group(1)), int(m.group(2)))
            )
    return result


# ------------------------------------------------------------ bring-up state machine


def _install_units(run, unit_dir):
    for src, name in (
        (NETDEV_UNIT, "20-pengyos-test.netdev"),
        (NETWORK_UNIT, "20-pengyos-test.network"),
    ):
        if not os.path.isfile(src):
            raise IntegrationError(f"shipped unit missing: {src}")
        run(["install-unit", src, os.path.join(unit_dir, name)])


def _uninstall_units(run, unit_dir):
    for name in ("20-pengyos-test.netdev", "20-pengyos-test.network"):
        run(["uninstall-unit", os.path.join(unit_dir, name)])


def wait_for_link_ready(run, deadline=DEADLINE, poll=POLL,
                        sleep=time.sleep, clock=time.monotonic):
    """Poll until pengyos-test0 is UP with 10.99.0.1/24.

    ``run`` is a callable ``(cmd_list) -> CompletedProcess``; ``sleep``
    and ``clock`` are injectable so the loop is unit-testable without a
    live stack (and without burning real wall time). Raises
    IntegrationError when the deadline expires.
    """
    deadline_at = clock() + deadline
    last = None
    while True:
        link = parse_ip_link_show(
            run(["ip", "-o", "link", "show", LINK]).stdout
        )
        addrs = parse_ip_addr_show(
            run(["ip", "-o", "-4", "addr", "show", "dev", LINK]).stdout
        )
        last = (link, addrs)
        if (
            link is not None
            and link[0] == "UP"
            and link[1] == "dummy"
            and (EXPECTED_ADDRESS, EXPECTED_PREFIX) in addrs
        ):
            return
        if clock() >= deadline_at:
            raise IntegrationError(
                f"{LINK} not up with {EXPECTED_ADDRESS}/{EXPECTED_PREFIX} "
                f"within {deadline:.0f}s (last observed: {last!r})"
            )
        sleep(poll)


def bring_up(run, service_active, unit_dir, deadline=DEADLINE, poll=POLL,
             sleep=time.sleep, clock=time.monotonic):
    """Install the two units, get networkd running, wait for the link.

    start-vs-reload: if networkd was already active, ``systemctl reload``
    makes it pick up the installed units. If we start it ourselves it
    has read the units during startup and a reload would churn every
    other managed link — so no reload in that branch.
    """
    _install_units(run, unit_dir)
    if service_active(NETWORKD_SERVICE):
        run(["systemctl", "reload", NETWORKD_SERVICE])
    else:
        run(["systemctl", "start", NETWORKD_SERVICE])
    wait_for_link_ready(run, deadline=deadline, poll=poll, sleep=sleep,
                        clock=clock)


def teardown(run, service_active, unit_dir):
    """Total cleanup: link, then config, in a safe order.

    Delete the link first (so the address is released before networkd
    re-evaluates), remove the unit files, then reload networkd so it
    forgets the vanished config. ``run`` must tolerate failures (the
    real caller passes a check=False wrapper): deleting an already-gone
    link or reloading an inactive service is an expected no-op. This
    runs in a ``finally`` block where the test outcome must not be
    masked.
    """
    run(["ip", "link", "del", LINK])
    _uninstall_units(run, unit_dir)
    if service_active(NETWORKD_SERVICE):
        run(["systemctl", "reload", NETWORKD_SERVICE])


# ------------------------------------------------------------ the runtime test


@pytest.mark.integration
def test_pengyos_test0_dummy_link_came_up():
    if not _has_systemd():
        pytest.skip("not a systemd host (no /run/systemd/system)")
    if os.geteuid() != 0:
        pytest.skip("requires root (set PENGYOS_IT=1 and run as root)")
    if shutil.which("ip") is None:
        pytest.skip("ip(8) not available")
    if not shutil.which("systemctl"):
        pytest.skip("systemctl not available")
    if not shutil.which("systemd-networkd"):
        pytest.skip("systemd-networkd not installed")
    # The units are installed into the real config dir; that needs root
    # write access.
    if not os.access(UNIT_DIR_DEFAULT, os.W_OK):
        pytest.skip(f"no write access to {UNIT_DIR_DEFAULT}")
    # A crashed earlier run may have left the link behind; reclaim it
    # so the assertions below measure THIS run's bring-up.
    pre = _run(["ip", "-o", "link", "show", LINK], check=False)
    if pre.returncode == 0:
        _run(["ip", "link", "del", LINK], check=False)

    try:
        bring_up(_real_run, _service_active, UNIT_DIR_DEFAULT)
    finally:
        teardown(lambda cmd: _real_run(cmd, check=False), _service_active,
                 UNIT_DIR_DEFAULT)

    # The stack claim pinned by the spec: the address is not merely
    # configured but USABLE — binding a socket to it must succeed.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind((str(EXPECTED_ADDRESS), 0))
        assert s.getsockname()[0] == str(EXPECTED_ADDRESS)


# ------------------------------------------------------------ offline unit tests
#
# The state machine above is the only non-trivial logic in this module
# and it must be pinned by tests even on hosts that are not root/systemd
# (every dev box, CI). A fake command runner and a fake clock stand in
# for the live stack.


class FakeClock:
    """Monotonic clock + sleep that advance together (no real time)."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeRunner:
    """Scripted stand-in for _run.

    ``states`` is consumed one entry per `ip link` poll (each a string
    that looks like `ip -o link show` output; "" = device absent). Once
    the script is exhausted the LAST state repeats — so a single
    permanent "not ready" state plus a fake clock exercises the
    deadline path in microseconds. Every command is recorded in
    ``.calls`` for assertions.
    """

    def __init__(self, states, addrs=""):
        self.states = list(states)
        self.addrs = addrs
        self.calls = []
        self._current = states[0] if states else ""

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd[:3] == ["ip", "-o", "link"]:
            if self.states:
                self._current = self.states.pop(0)
            stdout = self._current
        elif cmd[:3] == ["ip", "-o", "-4"]:
            stdout = self.addrs
        elif cmd[0] == "uninstall-unit":
            # Mirror the real effect on disk (best-effort: the real
            # cleanup tolerates a missing file).
            try:
                os.unlink(cmd[1])
            except OSError:
                pass
            stdout = ""
        else:
            stdout = ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout,
                                           stderr="")


READY_ADDR = (
    "    inet 10.99.0.1/24 scope global pengyos-test0\n"
    "    valid_lft forever preferred_lft forever\n"
)


def _link_line(state="UP", kind="dummy"):
    return (
        f"2: {LINK}: <BROADCAST,NOARP,UP,LOWER_UP> mtu 1500 qdisc "
        f"noqueue state {state} mode DEFAULT group default qlen 1000\\\n"
        f"    link/{kind} 02:00:00:00:00:00 brd ff:ff:ff:ff:ff:ff\n"
    )


def test_wait_succeeds_once_link_up_with_address():
    clock = FakeClock()
    runner = FakeRunner(
        states=["", _link_line(state="DOWN"), _link_line()],
        addrs=READY_ADDR,
    )
    wait_for_link_ready(runner, deadline=DEADLINE, poll=POLL,
                        sleep=clock.sleep, clock=clock.monotonic)
    # Polled past the two "not ready" states before succeeding.
    assert clock.now >= 2 * POLL
    assert runner.calls


def test_wait_fails_when_address_never_appears():
    clock = FakeClock()
    runner = FakeRunner(
        states=[_link_line()],  # permanently UP, but...
        addrs="",  # ...the address is never there.
    )
    with pytest.raises(IntegrationError, match="10\\.99\\.0\\.1/24"):
        wait_for_link_ready(runner, deadline=DEADLINE, poll=POLL,
                            sleep=clock.sleep, clock=clock.monotonic)
    # The loop slept out the whole deadline before giving up.
    assert clock.now >= DEADLINE - POLL


def test_wait_fails_when_wrong_prefix():
    clock = FakeClock()
    runner = FakeRunner(
        states=[_link_line()],
        addrs="    inet 10.99.0.1/32 scope global pengyos-test0\n",
    )
    with pytest.raises(IntegrationError, match="10\\.99\\.0\\.1/24"):
        wait_for_link_ready(runner, deadline=DEADLINE, poll=POLL,
                            sleep=clock.sleep, clock=clock.monotonic)


def test_wait_fails_when_link_is_not_a_dummy():
    clock = FakeClock()
    runner = FakeRunner(
        states=[_link_line(kind="ether")],
        addrs=READY_ADDR,
    )
    with pytest.raises(IntegrationError, match="not up"):
        wait_for_link_ready(runner, deadline=DEADLINE, poll=POLL,
                            sleep=clock.sleep, clock=clock.monotonic)


def test_bring_up_starts_inactive_networkd_and_never_reloads_it():
    clock = FakeClock()
    runner = FakeRunner(
        states=["", _link_line()], addrs=READY_ADDR
    )
    bring_up(runner, lambda svc: False, "/tmp/net-units",
             sleep=clock.sleep, clock=clock.monotonic)
    svc_cmds = [
        c for c in runner.calls
        if c[:1] == ["systemctl"] and c[1] in ("start", "reload")
    ]
    # Inactive service: started exactly once, never reloaded (it read
    # the units at startup; a reload would churn other managed links).
    assert svc_cmds == [["systemctl", "start", NETWORKD_SERVICE]]


def test_bring_up_reloads_an_already_active_networkd():
    clock = FakeClock()
    runner = FakeRunner(
        states=["", _link_line()], addrs=READY_ADDR
    )
    bring_up(runner, lambda svc: True, "/tmp/net-units",
             sleep=clock.sleep, clock=clock.monotonic)
    svc_cmds = [
        c for c in runner.calls
        if c[:1] == ["systemctl"] and c[1] in ("start", "reload")
    ]
    # Active service: reloaded so it picks up the new units, never
    # started (starting an active service would be a restart = churn).
    assert svc_cmds == [["systemctl", "reload", NETWORKD_SERVICE]]


def test_bring_up_installs_exactly_the_two_test_units_first():
    clock = FakeClock()
    runner = FakeRunner(
        states=["", _link_line()], addrs=READY_ADDR
    )
    bring_up(runner, lambda svc: True, "/tmp/net-units",
             sleep=clock.sleep, clock=clock.monotonic)
    installs = [c for c in runner.calls if c[0] == "install-unit"]
    assert [c[1] for c in installs] == [NETDEV_UNIT, NETWORK_UNIT]
    assert [os.path.basename(c[2]) for c in installs] == [
        "20-pengyos-test.netdev", "20-pengyos-test.network"
    ]
    # The baseline default unit is NEVER touched by the integration
    # test — only the two pengyos-test units.
    assert not any("10-default" in c[1] for c in installs)
    # Units are installed before the service is acted on, else the
    # start/reload would run without the config in place.
    first_svc = next(i for i, c in enumerate(runner.calls)
                     if c[:1] == ["systemctl"])
    assert all(i < first_svc for i, c in enumerate(runner.calls)
               if c[0] == "install-unit")


def test_bring_up_propagates_failure_after_link_times_out():
    clock = FakeClock()
    runner = FakeRunner(states=[""], addrs="")  # never comes up
    with pytest.raises(IntegrationError):
        bring_up(runner, lambda svc: False, "/tmp/net-units",
                 deadline=DEADLINE, poll=POLL,
                 sleep=clock.sleep, clock=clock.monotonic)


def test_teardown_order_is_link_then_units_then_reload(tmp_path):
    runner = FakeRunner(states=[""], addrs="")
    unit_dir = tmp_path / "network"
    unit_dir.mkdir()
    for name in ("20-pengyos-test.netdev", "20-pengyos-test.network"):
        (unit_dir / name).write_text("[stub]\n")

    teardown(runner, lambda svc: True, str(unit_dir))

    calls = runner.calls
    assert calls[0] == ["ip", "link", "del", LINK]
    uninstalls = [c for c in calls if c[0] == "uninstall-unit"]
    assert [os.path.basename(c[1]) for c in uninstalls] == [
        "20-pengyos-test.netdev", "20-pengyos-test.network"
    ]
    assert calls[-1] == ["systemctl", "reload", NETWORKD_SERVICE]
    # Both unit files are actually gone from disk.
    assert list(unit_dir.iterdir()) == []


def test_teardown_is_total_even_with_absent_units(tmp_path):
    # No unit files installed (bring-up crashed before installing them)
    # and networkd inactive: teardown must still run and not raise.
    runner = FakeRunner(states=[""], addrs="")
    teardown(runner, lambda svc: False, str(tmp_path))
    assert runner.calls[0] == ["ip", "link", "del", LINK]
    assert not any(c[:1] == ["systemctl"] for c in runner.calls)


def test_teardown_does_not_reload_when_networkd_inactive(tmp_path):
    runner = FakeRunner(states=[""], addrs="")
    teardown(runner, lambda svc: False, str(tmp_path))
    assert not any(c[:1] == ["systemctl"] for c in runner.calls)


# ------------------------------------------------------------ parser tests


def test_parse_ip_link_show_real_newline_spellings():
    up = (
        "2: pengyos-test0: <BROADCAST,NOARP,UP,LOWER_UP> mtu 1500 qdisc "
        "noqueue state UP mode DEFAULT group default qlen 1000\\\n"
        "    link/dummy 02:00:00:00:00:00 brd ff:ff:ff:ff:ff:ff\n"
    )
    assert parse_ip_link_show(up) == ("UP", "dummy")
    down = (
        "2: pengyos-test0: <BROADCAST,NOARP> mtu 1500 qdisc noqueue "
        "state DOWN mode DEFAULT group default qlen 1000\\\n"
        "    link/dummy 02:00:00:00:00:00 brd ff:ff:ff:ff:ff:ff\n"
    )
    assert parse_ip_link_show(down) == ("DOWN", "dummy")


def test_parse_ip_link_show_backslash_continuation_spelling():
    # Live `ip -o link show` spells continuations as `\\` + whitespace
    # on ONE physical line, optionally with an altname tail.
    out = (
        "2: enp34s0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc "
        "mq state UP mode DEFAULT group default qlen 1000\\    "
        "link/ether 2c:f0:5d:71:52:31 brd ff:ff:ff:ff:ff:ff\\    "
        "altname enx2cf05d715231\n"
    )
    assert parse_ip_link_show(out) == ("UP", "ether")


def test_parse_ip_link_show_lo_unknown_state():
    out = (
        "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue "
        "state UNKNOWN mode DEFAULT group default qlen 1000\\    "
        "link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00\n"
    )
    assert parse_ip_link_show(out) == ("UNKNOWN", "loopback")


def test_parse_ip_link_show_absent_device():
    assert parse_ip_link_show("") is None


def test_parse_ip_addr_show_extracts_ipv4_and_prefix():
    out = (
        "2: pengyos-test0: <BROADCAST,NOARP,UP,LOWER_UP> mtu 1500 qdisc "
        "noqueue state UP group default qlen 1000\n"
        "    inet 10.99.0.1/24 scope global pengyos-test0\n"
    )
    assert parse_ip_addr_show(out) == [(EXPECTED_ADDRESS, EXPECTED_PREFIX)]


def test_parse_ip_addr_show_without_address():
    assert parse_ip_addr_show("") == []


def test_parse_ip_addr_show_multiple_addresses():
    out = (
        "    inet 10.99.0.1/24 scope global pengyos-test0\n"
        "    inet 10.99.0.9/24 secondary scope global pengyos-test0\n"
    )
    assert parse_ip_addr_show(out) == [
        (ipaddress.IPv4Address("10.99.0.1"), 24),
        (ipaddress.IPv4Address("10.99.0.9"), 24),
    ]


def test_real_run_install_and_uninstall_units(tmp_path):
    """The real runner's pseudo-command branch (offline, in a tmp dir)."""
    dst = tmp_path / "20-pengyos-test.netdev"
    proc = _real_run(["install-unit", NETDEV_UNIT, str(dst)])
    assert proc.returncode == 0
    assert dst.is_file()
    assert dst.read_text(encoding="utf-8") == open(
        NETDEV_UNIT, encoding="utf-8").read()
    assert (dst.stat().st_mode & 0o777) == 0o644
    proc = _real_run(["uninstall-unit", str(dst)])
    assert proc.returncode == 0
    assert not dst.exists()
    # Removing an already-gone unit is a tolerated no-op (teardown runs
    # in a finally block and must not raise).
    proc = _real_run(["uninstall-unit", str(dst)])
    assert proc.returncode == 0


def test_shipped_units_agree_with_test_expectations():
    """Offline pin: the shipped units match what the test asserts."""
    netdev = open(NETDEV_UNIT, encoding="utf-8").read()
    network = open(NETWORK_UNIT, encoding="utf-8").read()
    assert re.search(rf"^\s*Name\s*=\s*{LINK}\s*$", netdev, re.M)
    assert re.search(r"^\s*Kind\s*=\s*dummy\s*$", netdev, re.M)
    assert re.search(rf"^\s*Name\s*=\s*{LINK}\s*$", network, re.M)
    m = re.search(r"^\s*Address\s*=\s*(\S+)\s*$", network, re.M)
    assert m, "20-pengyos-test.network has no Address="
    addr = ipaddress.ip_interface(m.group(1))
    assert addr.ip == EXPECTED_ADDRESS
    assert addr.network.prefixlen == EXPECTED_PREFIX
