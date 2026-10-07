"""PengyOS fleet-inventory — hardware profiles for the fleet.

SSH into the home-lab machines, gather hardware facts, and emit declarative,
version-controllable per-machine profiles (fixture-driven).

PengyOS morphs around the hardware it runs on (SPEC §1.5); the first step is
knowing *what* the fleet machines are. This package collects hardware facts from
a list of hosts — through an injectable *runner* (an SSH transport in production,
a deterministic mock in tests) — and writes one hwprofile-schema
``profile.toml`` per host.

The package is standard-library only at runtime. ``pytest`` is an optional dev
dependency (see ``pyproject.toml``).
"""

__version__ = "0.1.0"

from .diff import (
    DiffError,
    diff_documents,
    load_profile,
    render_diff,
)
from .emit import (
    collect_all,
    emit_profile,
    emit_profile_if_changed,
    profile_written,
    render,
)
from .facts import (
    FactsError,
    collect_facts,
    commands,
    parse_cpu,
    parse_gpu,
    parse_memory,
    parse_network,
    parse_storage,
)
from .hosts import Host, HostsError, load_hosts
from .runner import (
    MockRunner,
    RunResult,
    Runner,
    RunnerError,
    SSHRunner,
    make_runner,
)

__all__ = [
    "__version__",
    "DiffError",
    "FactsError",
    "Host",
    "HostsError",
    "MockRunner",
    "RunResult",
    "Runner",
    "RunnerError",
    "SSHRunner",
    "collect_all",
    "collect_facts",
    "commands",
    "diff_documents",
    "emit_profile",
    "emit_profile_if_changed",
    "load_hosts",
    "load_profile",
    "make_runner",
    "parse_cpu",
    "parse_gpu",
    "parse_memory",
    "parse_network",
    "parse_storage",
    "profile_written",
    "render",
    "render_diff",
]
