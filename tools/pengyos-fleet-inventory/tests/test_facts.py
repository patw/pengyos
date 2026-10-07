"""Tests for fact collection (``pengyos_fleet.facts``).

Covers the command registry, each command parser (pure functions over command
output), and the :func:`collect_facts` orchestration driven by the deterministic
mock runner against the committed fixtures under ``tests/fixtures/hosts``.

No network, no root, no real hardware — every parser is tested against inline
strings and the committed fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_fleet import __version__, facts
from pengyos_fleet.facts import FactsError, collect_facts, commands
from pengyos_fleet.hosts import Host
from pengyos_fleet.runner import MockRunner, RunResult, Runner


def _host(name="n1"):
    here = Path(__file__).parent
    return Host(
        name=name,
        address=f"{name}.home.lab",
        fixtures=str(here / "fixtures" / "hosts" / name),
    )


def _read(*parts):
    return (Path(__file__).parent / "fixtures" / "hosts").joinpath(*parts).read_text(
        encoding="utf-8"
    )


class _FailingRunner(Runner):
    """A runner whose named command exits non-zero — for the error-path test.

    Every other command succeeds (empty stdout) so only the targeted command's
    failure surfaces.
    """

    name = "failing"

    def __init__(self, fail_on, returncode=1, stderr="boom"):
        self.fail_on = fail_on
        self.returncode = returncode
        self.stderr = stderr

    def run(self, host, args):
        if args and Path(args[0]).name == self.fail_on:
            return RunResult(args, self.returncode, "", self.stderr)
        return RunResult(args, 0, "", "")


# ---------------------------------------------------------------------------
# Command registry.
# ---------------------------------------------------------------------------


def test_commands_registry_has_all_sections():
    sections = [section for section, _argv in commands()]
    assert sections == ["metadata", "cpu", "memory", "gpu", "storage", "network"]


def test_commands_argv_uses_command_basenames():
    argvs = [argv for _section, argv in commands()]
    assert argvs[0][:1] == ("uname",)  # uname -a
    assert argvs[1][:1] == ("lscpu",)
    assert argvs[2][:1] == ("free",)  # free -b maps by basename 'free'
    assert argvs[3][:1] == ("lspci",)
    assert argvs[4][:1] == ("lsblk",)  # lsblk -P maps by basename 'lsblk'
    assert argvs[5][:1] == ("ip",)  # ip -o link show maps by basename 'ip'


# ---------------------------------------------------------------------------
# metadata — uname -a
# ---------------------------------------------------------------------------


def test_parse_metadata_uname_a():
    text = _read("n1", "uname")
    assert facts._parse_metadata(text) == {
        "hostname": "n1",
        "kernel": "5.15.0-91-generic",
    }


def test_parse_metadata_bare_uname_s():
    # A bare `uname -s` (just "Linux") yields a hostname and no kernel.
    assert facts._parse_metadata("Linux") == {"hostname": "linux"}


def test_parse_metadata_empty():
    assert facts._parse_metadata("") == {}


# ---------------------------------------------------------------------------
# cpu — lscpu
# ---------------------------------------------------------------------------


def test_parse_cpu_n1():
    cpu = facts.parse_cpu(_read("n1", "lscpu"))
    assert cpu == {
        "vendor": "Intel",
        "model": "Intel(R) Core(TM) i7-1068G7 @ 2.30GHz",
        "cores": 4,  # 2 cores/socket x 2 sockets
        "threads": 8,
        "mhz": 2300.0,
    }


def test_parse_cpu_n2():
    cpu = facts.parse_cpu(_read("n2", "lscpu"))
    assert cpu["vendor"] == "AuthenticAMD"
    assert cpu["cores"] == 8  # 8 cores/socket x 1 socket
    assert cpu["threads"] == 16
    assert cpu["mhz"] == 3800.0


def test_parse_cpu_fallback_cores_to_cpus():
    # No per-socket breakdown -> cores falls back to the CPU(s) count.
    text = "CPU(s):                  4\nModel name:              Test CPU\nCPU MHz:                 1000.000\n"
    cpu = facts.parse_cpu(text)
    assert cpu["cores"] == 4
    assert cpu["threads"] == 4
    assert cpu["model"] == "Test CPU"
    assert cpu["vendor"] == "unknown"


def test_parse_cpu_missing_cpu_count_raises():
    with pytest.raises(FactsError, match="cpu"):
        facts.parse_cpu("Model name: Test\n")


# ---------------------------------------------------------------------------
# memory — free -b
# ---------------------------------------------------------------------------


def test_parse_memory_n1():
    (module,) = facts.parse_memory(_read("n1", "free"))
    assert module["locator"] == "System"
    assert module["size_bytes"] == 16226921472


def test_parse_memory_n2():
    (module,) = facts.parse_memory(_read("n2", "free"))
    assert module["size_bytes"] == 32523644928


def test_parse_memory_no_mem_row():
    assert facts.parse_memory("no memory info here\n") == []


def test_parse_memory_unparseable_raises():
    with pytest.raises(FactsError, match="memory"):
        facts.parse_memory("Mem: notanumber\n")


# ---------------------------------------------------------------------------
# gpu — lspci
# ---------------------------------------------------------------------------


def test_parse_gpu_n1_names_iris_xe():
    (gpu,) = facts.parse_gpu(_read("n1", "lspci"))
    assert gpu == {"vendor": "Intel Corporation", "model": "Iris Xe Graphics", "bus": "00:02.0"}


def test_parse_gpu_n2_names_amd():
    (gpu,) = facts.parse_gpu(_read("n2", "lspci"))
    assert gpu["vendor"] == "Advanced Micro Devices, Inc. [AMD]"
    assert gpu["model"] == "Radeon RX Vega"
    assert gpu["bus"] == "00:02.0"


def test_parse_gpu_ignores_non_display():
    text = (
        "00:00.0 Host bridge: Intel Corporation host bridge (rev 0c)\n"
        "00:17.0 Storage controller: Intel Corporation SATA (rev 10)\n"
    )
    assert facts.parse_gpu(text) == []


def test_parse_gpu_no_devices():
    assert facts.parse_gpu("") == []


def test_parse_gpu_without_nn_ids_falls_back_to_text():
    # Without [vendor:device] ids, the model falls back to the description text.
    text = "00:02.0 Display controller: Some Vendor Thing (rev 05)\n"
    (gpu,) = facts.parse_gpu(text)
    assert gpu["vendor"] == "unknown"
    assert gpu["model"] == "Some Vendor Thing"


def test_pci_ids_normalises_without_and_with_0x():
    assert facts._pci_ids("Thing [8086:9a49]") == ("0x8086", "0x9a49")
    assert facts._pci_ids("Thing [0x8086:0x9a49]") == ("0x8086", "0x9a49")


def test_pci_ids_no_ids():
    assert facts._pci_ids("Thing (rev 05)") == (None, None)


def test_lspci_line_splits_multiword_class_and_ignores_nonsense():
    assert facts._lspci_line("00:02.0 Display controller: X Corp Y (rev 05)") == (
        "00:02.0",
        "Display controller",
        "X Corp Y (rev 05)",
    )
    assert facts._lspci_line("this line has no colon or address") is None


# ---------------------------------------------------------------------------
# storage — lsblk -P
# ---------------------------------------------------------------------------


def test_parse_storage_n1_single_nvme():
    (dev,) = facts.parse_storage(_read("n1", "lsblk"))
    assert dev == {
        "bus": "nvme",
        "model": "Samsung SSD 970 EVO Plus 500GB",
        "size_bytes": 500107862016,
        "serial": "S123N0M2162001",
        "transport": "nvme",
    }


def test_parse_storage_n2_two_devices():
    devices = facts.parse_storage(_read("n2", "lsblk"))
    assert {d["bus"] for d in devices} == {"nvme", "sata"}
    assert len(devices) == 2


def test_parse_storage_skips_device_without_size():
    text = 'NAME="sr0" MODEL="DVDRW" SIZE="" TRAN="\n'
    assert facts.parse_storage(text) == []


def test_parse_storage_empty():
    assert facts.parse_storage("") == []


# ---------------------------------------------------------------------------
# network — ip -o link show
# ---------------------------------------------------------------------------


def test_parse_network_n1():
    interfaces = facts.parse_network(_read("n1", "ip"))
    assert [i["interface"] for i in interfaces] == ["enp3s0"]
    assert interfaces[0]["mac"] == "aa:bb:cc:dd:ee:01"


def test_parse_network_n2_two_interfaces():
    interfaces = facts.parse_network(_read("n2", "ip"))
    assert [i["interface"] for i in interfaces] == ["enp4s0", "wlan0"]


def test_parse_network_skips_loopback():
    text = (
        "1: lo: <LOOPBACK,UP> MTU:65536 link/loopback 00:00:00:00:00:00\n"
        "2: enp0: <BROADCAST> MTU:1500 link/ether de:ad:be:ef:00:01\n"
    )
    assert [i["interface"] for i in facts.parse_network(text)] == ["enp0"]


def test_parse_network_no_mac():
    text = "2: enp0: <BROADCAST> MTU:1500 qdisc noqueue\n"
    assert facts.parse_network(text)[0]["mac"] == ""


# ---------------------------------------------------------------------------
# collect_facts — full document via the mock runner.
# ---------------------------------------------------------------------------


def test_collect_facts_n1_shape():
    doc = collect_facts(MockRunner(), _host("n1"))
    assert set(doc) == {"metadata", "cpu", "memory", "gpu", "storage", "network"}
    assert doc["metadata"]["hostname"] == "n1"
    assert doc["metadata"]["kernel"] == "5.15.0-91-generic"
    assert doc["metadata"]["tool_version"] == __version__
    assert doc["cpu"]["cores"] == 4
    assert doc["memory"][0]["size_bytes"] == 16226921472
    assert len(doc["gpu"]) == 1
    assert len(doc["storage"]) == 1
    assert [n["interface"] for n in doc["network"]] == ["enp3s0"]


def test_collect_facts_n2_shape():
    doc = collect_facts(MockRunner(), _host("n2"))
    assert doc["metadata"]["hostname"] == "n2"
    assert doc["cpu"]["vendor"] == "AuthenticAMD"
    assert len(doc["storage"]) == 2
    assert {n["interface"] for n in doc["network"]} == {"enp4s0", "wlan0"}


def test_collect_facts_runs_all_six_commands():
    # The mock runner must read one fixture per command basename. A host with a
    # missing fixture would raise RunnerError; if every fixture resolves, all six
    # commands ran.
    collect_facts(MockRunner(), _host("n1"))


def test_collect_facts_nonzero_exit_raises():
    # The mock runner reads fixtures by basename, so `lscpu` maps to the lscpu
    # fixture; a failing runner for that command surfaces the stderr.
    runner = _FailingRunner("lscpu", returncode=1, stderr="lscpu: command not found")
    with pytest.raises(FactsError, match="lscpu: command not found"):
        collect_facts(runner, _host("n1"))


def test_collect_facts_reports_section_on_failure():
    runner = _FailingRunner("lsblk", returncode=2)
    with pytest.raises(FactsError, match="storage command .* exited 2"):
        collect_facts(runner, _host("n1"))


# ---------------------------------------------------------------------------
# Schema conformance — the collected document must fit the hwprofile schema.
# ---------------------------------------------------------------------------


def test_collect_facts_document_validates_against_hwprofile_schema():
    try:
        from pengyos_hwprofile.schema import validate  # type: ignore
    except Exception:  # pragma: no cover - hwprofile optional
        pytest.skip("pengyos_hwprofile not importable")
    for name in ("n1", "n2"):
        doc = collect_facts(MockRunner(), _host(name))
        validate(doc)  # raises ProfileError on any violation


# ---------------------------------------------------------------------------
# Integration: the whole flow over the mock runner is opt-in.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_collect_facts_integration_over_mock():
    # Opt-in: prove collect_facts drives the real MockRunner end to end.
    for name in ("n1", "n2"):
        doc = collect_facts(MockRunner(), _host(name))
        assert doc["metadata"]["hostname"] == name
