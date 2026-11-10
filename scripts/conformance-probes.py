#!/usr/bin/env python3
"""Run the conformance probes (scripts/conformance-probes.toml).

    conformance-probes.py local                  on this machine; exit 1 on any mismatch
    conformance-probes.py boot IMAGE [ARGS...]   boot IMAGE under QEMU and run every
                                                 probe in it over SSH (pengyos-qemu-verify)
    conformance-probes.py commands               the in-guest commands, one per line

Each probe becomes one shell command that exits 0 when the image matches the
file: a convention that should hold holds, and one marked `absent` is still
absent. So `boot` reports one pass/fail per probe through qemu-verify's
--ssh-command, named after the probe. The image must be built with
PENGYOS_AUTHORIZED_KEYS_FILE (docs/qemu-testing.md, section 4).
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBES = HERE / "conformance-probes.toml"
QEMU_VERIFY = HERE.parent / "tools" / "pengyos-qemu-verify"


@dataclass(frozen=True)
class Probe:
    id: str
    run: str
    why: str
    when: str = ""
    absent: str = ""


def load(path: Path = PROBES) -> list[Probe]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    probes = []
    for entry in data.get("probe", []):
        unknown = set(entry) - {"id", "run", "why", "when", "absent"}
        if unknown:
            raise ValueError(f"probe {entry.get('id')}: unknown keys {sorted(unknown)}")
        probes.append(Probe(**entry))
    ids = [p.id for p in probes]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate probe ids: {sorted(dupes)}")
    return probes


def command(p: Probe) -> str:
    """One sh command, exit 0 when the machine matches the file.

    It starts with `: ID;` so qemu-verify's report name (derived from the
    command) begins with the probe's id.
    """
    head = f": {p.id}; "
    if p.absent:
        # Must still fail; passing means the absence entry is stale.
        return head + f"if sh -c {shlex.quote(p.run)} >/dev/null 2>&1; then " \
                      f"echo 'now present: drop absent= from {p.id}'; exit 1; fi"
    gate = f"sh -c {shlex.quote(p.when)} >/dev/null 2>&1 || {{ echo skipped; exit 0; }}; " if p.when else ""
    return head + gate + f"sh -c {shlex.quote(p.run)}"


def local(probes: list[Probe]) -> int:
    bad = 0
    for p in probes:
        r = subprocess.run(["sh", "-c", command(p)], capture_output=True, text=True)
        ok = r.returncode == 0
        bad += not ok
        tag = "skip" if ok and r.stdout.strip() == "skipped" else ("ok" if ok else "FAIL")
        line = f"[{tag:4}] {p.id:<24} {('absent: ' + p.absent) if p.absent else p.why}"
        print(line)
        if not ok:
            detail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [f"exit {r.returncode}"]
            print(f"         {detail[0]}")
    print(f"{len(probes) - bad}/{len(probes)} probes match")
    return 1 if bad else 0


def boot(probes: list[Probe], image: str, extra: list[str]) -> int:
    argv = [sys.executable, "-m", "pengyos_qemu_verify", "boot", image, "--uefi"]
    for p in probes:
        argv += ["--ssh-command", command(p)]
    argv += extra
    return subprocess.run(argv, cwd=QEMU_VERIFY).returncode


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2
    probes = load()
    if argv[0] == "local":
        return local(probes)
    if argv[0] == "commands":
        print("\n".join(command(p) for p in probes))
        return 0
    if argv[0] == "boot" and len(argv) >= 2:
        return boot(probes, argv[1], argv[2:])
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
