#!/usr/bin/env python3
"""Stand-ins for systemctl --user, openssl and ip, picked by the name they are
installed as (DIR/usr/bin/NAME). Unit state in DIR/units/; calls in DIR/calls.log.
DIR/no-session makes `start`/`restart` fail as Requisite=graphical-session.target does.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = Path(sys.argv[0]).name
ARGS = sys.argv[1:]
with open(ROOT / "calls.log", "a") as log:
    log.write(json.dumps([NAME] + ARGS) + "\n")

if NAME == "systemctl":
    assert ARGS[0] == "--user", ARGS
    verb, rest = ARGS[1], [a for a in ARGS[2:] if not a.startswith("--")]
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
    if verb in ("start", "restart"):
        if (ROOT / "no-session").exists():
            print("Job failed: dependency", file=sys.stderr)
            sys.exit(1)
        state.write_text("active")
    elif verb == "enable":
        (units / (unit + ".enabled")).touch()
    elif verb == "disable":
        (units / (unit + ".enabled")).unlink(missing_ok=True)
        if "--now" in ARGS:
            state.write_text("inactive")
    sys.exit(0)

if NAME == "openssl":
    if ARGS[0] == "req":
        Path(ARGS[ARGS.index("-keyout") + 1]).write_text("KEY\n")
        Path(ARGS[ARGS.index("-out") + 1]).write_text("CERT\n")
    elif ARGS[0] == "x509":
        print("sha256 Fingerprint=AB:CD:EF")
    sys.exit(0)

if NAME == "ip":
    print(json.dumps([
        {"ifname": "enp3s0", "addr_info": [{"family": "inet", "local": "10.0.23.4"},
                                           {"family": "inet6", "local": "fd00::4"},
                                           {"family": "inet6", "local": "fd00::99", "temporary": True}]},
        {"ifname": "podman0", "addr_info": [{"family": "inet", "local": "10.88.0.1"}]},
        {"ifname": "wlan0", "addr_info": [{"family": "inet", "local": "192.168.1.20"}]},
    ]))
    sys.exit(0)
sys.exit(0)
