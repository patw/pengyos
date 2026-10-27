#!/usr/bin/env python3
"""Reproduce the pengyos-jev latency/memory numbers.

Starts a llama-server decision service, sends the same three-primitive request
N times, records steady-state latency + peak RSS, and prints JSON comparable
to results/measured.json. Needs a llama-server build with the /v1/systemone
route (llama.cpp >= b11318) and a decision-model GGUF.

  python3 measure.py --model /usr/share/pengyos/jev/Laya-Q8_0.gguf \
                     [--device Vulkan0] [--ngl 99] [--reps 8]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

STATE = "Customer message: I was charged twice for my order last week and nobody has replied."
QUESTIONS = {
    "route": {"type": "choice", "instructions": "Which team should handle this?",
              "criteria": {"billing": None, "shipping": None, "technical": None}},
    "angry": {"type": "noul", "instructions": "Is the customer angry?"},
    "urgency": {"type": "score", "instructions": "How urgent is this?",
                "criteria": ["can wait", "this week", "today", "right now"]},
}


def peak_rss_kb(pid: int) -> int:
    with open(f"/proc/{pid}/status") as fh:
        for line in fh:
            if line.startswith("VmHWM"):
                return int(line.split()[1])
    return -1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="llama-server")
    ap.add_argument("--model", required=True)
    ap.add_argument("--device", default="Vulkan0")
    ap.add_argument("--ngl", type=int, default=99)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--port", type=int, default=8199)
    ap.add_argument("--reps", type=int, default=8)
    ap.add_argument("--ctx", type=int, default=4096)
    args = ap.parse_args()

    cmd = [args.server, "-m", args.model, "-c", str(args.ctx),
           "-ngl", str(args.ngl), "-t", str(args.threads),
           "--port", str(args.port), "--host", "127.0.0.1"]
    if args.ngl > 0:
        cmd += ["--device", args.device]
    srv = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           start_new_session=True)

    url = f"http://127.0.0.1:{args.port}/v1/systemone"
    body = json.dumps({"state": STATE, "questions": QUESTIONS}).encode()
    ready = False
    for _ in range(120):
        time.sleep(1.0)
        try:
            with urllib.request.urlopen(url.replace("/systemone", "/models"), timeout=2) as r:
                ready = True
                break
        except Exception:
            if srv.poll() is not None:
                print("server exited early; check the model and device", file=sys.stderr)
                return 2
    if not ready:
        srv.terminate()
        print("server did not come up in 120 s", file=sys.stderr)
        return 2

    lat = []
    for i in range(args.reps):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=120) as resp:
            json.loads(resp.read().decode())
        lat.append((time.perf_counter() - t0) * 1000.0)

    out = {
        "model": os.path.basename(args.model),
        "device": args.device if args.ngl > 0 else "cpu",
        "threads": args.threads,
        "first_ms": round(lat[0], 1),
        "steady_ms": round(sorted(lat[1:])[len(lat[1:]) // 2], 1) if len(lat) > 1 else None,
        "peak_rss_mb": round(peak_rss_kb(srv.pid) / 1024, 1),
    }
    srv.terminate()
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
