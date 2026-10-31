#!/usr/bin/env python3
"""The sandbox's stand-ins for systemctl, qemu-img, networkctl, ip, ssh,
journalctl and loginctl, picked by the name they are installed as.

State lives under the sandbox root (the directory two levels up from this
copy, DIR/usr/bin/NAME): units/<unit> holds active|inactive, units/<unit>.enabled
marks an enabled one, and every call is appended to calls.log. A fake qcow2 is
a JSON file {"virtual-size": N}.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = Path(sys.argv[0]).name
ARGS = sys.argv[1:]
with open(ROOT / "calls.log", "a") as log:
    log.write(json.dumps([NAME] + ARGS) + "\n")


def size_of(path: str) -> int:
    p = Path(path)
    try:
        return int(json.loads(p.read_text())["virtual-size"])
    except (ValueError, KeyError, UnicodeDecodeError):
        return p.stat().st_size


def parse(text: str) -> int:
    units = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}
    return int(text[:-1]) * units[text[-1]] if text[-1] in units else int(text)


if NAME == "systemctl":
    assert ARGS[0] == "--user", ARGS
    verb, rest = ARGS[1], ARGS[2:]
    units = ROOT / "units"
    units.mkdir(exist_ok=True)
    unit = rest[-1] if rest else ""
    state = units / unit
    if verb == "is-active":
        now = state.read_text() if state.exists() else "inactive"
        print(now)
        sys.exit(0 if now == "active" else 3)
    if verb == "is-enabled":
        on = (units / (unit + ".enabled")).exists()
        print("enabled" if on else "disabled")
        sys.exit(0 if on else 1)
    if verb == "start":
        if (ROOT / "fail-start").exists():
            state.write_text("failed")
        else:
            state.write_text("active")
    elif verb in ("stop", "kill"):
        state.write_text("inactive")
    elif verb == "enable":
        (units / (unit + ".enabled")).touch()
    elif verb == "disable":
        (units / (unit + ".enabled")).unlink(missing_ok=True)
    sys.exit(0)

if NAME == "qemu-img":
    verb = ARGS[0]
    if verb == "create":
        Path(ARGS[-2]).write_text(json.dumps({"virtual-size": parse(ARGS[-1])}))
    elif verb == "convert":
        Path(ARGS[-1]).write_text(json.dumps({"virtual-size": size_of(ARGS[-2])}))
    elif verb == "resize":
        Path(ARGS[-2]).write_text(json.dumps({"virtual-size": parse(ARGS[-1])}))
    elif verb == "info":
        print(json.dumps({"virtual-size": size_of(ARGS[-1])}))
    sys.exit(0)

if NAME == "ip" and ARGS[:3] == ["-j", "-4", "neigh"]:
    neigh = ROOT / "neigh.json"
    print(neigh.read_text() if neigh.exists() else "[]")
    sys.exit(0)

if NAME == "ssh":
    print(json.dumps(ARGS))
    sys.exit(0)

if NAME == "loginctl":
    sys.exit(1)

sys.exit(0)
