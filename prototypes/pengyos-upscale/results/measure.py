#!/usr/bin/env python3
"""Measure the Real-ESRGAN ncnn-vulkan engine the way PengyOS would call it.

Run:  python3 results/measure.py [ENGINE] [MODELS_DIR]

Writes results/measured.json. Every number in README.md and spec.md comes from
this file; nothing here is a guess. Wall time and peak RSS come from
/usr/bin/time -v wrapping the engine directly (not the wrapper), so they are
the engine's own cost.

The point of the measurement is the model/device matrix: the fast model runs
on the GPU, the big models do not allocate on the NVIDIA devices on this box,
and the engine reports every failure with exit status 0.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    "~/Personal/skills/upscale/realesrgan-ncnn-vulkan")
MODELS = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser(
    "~/Personal/skills/upscale/models")
WORK = "/tmp/pengyos-upscale-measure"
WALL = "/data/PengyOS/art/wallpapers/jpeg/christmas-lights-16x9.jpg"
os.makedirs(WORK, exist_ok=True)


def sh(args):
    return subprocess.run(args, capture_output=True, text=True)


def make_photo(name, w, h):
    p = f"{WORK}/{name}.png"
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", WALL, "-vf",
        f"scale={w}:{h}:flags=lanczos", "-frames:v", "1", p])
    return p


def make_art(name, w, h):
    p = f"{WORK}/{name}.png"
    sh(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        f"mandelbrot=size={w}x{h}:rate=1", "-frames:v", "1", p])
    return p


def probe(path):
    r = sh(["ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", path])
    return r.stdout.strip()


TIME = "/usr/bin/time"

CASES = [
    # label, model, scale, gpu (None = the engine's own auto pick), tile, input
    ("fast x2 512 art",      "realesr-animevideov3",   2, None, 0, "art512"),
    ("fast x4 512 art",      "realesr-animevideov3",   4, None, 0, "art512"),
    ("fast x2 1080p photo",  "realesr-animevideov3",   2, None, 0, "hd1080"),
    ("fast x4 1080p photo",  "realesr-animevideov3",   4, None, 0, "hd1080"),
    ("fast x4 4K photo",     "realesr-animevideov3",   4, None, 0, "uhd2160"),
    ("anime x4 512 art",     "realesrgan-x4plus-anime", 4, None, 0, "art512"),
    ("photo x4 512 art",     "realesrgan-x4plus",       4, None, 0, "art512"),
    ("photo x4 512 llvmpipe", "realesrgan-x4plus",      4, 2, 0, "art512"),
    ("fast x4 512 llvmpipe", "realesr-animevideov3",    4, 2, 0, "art512"),
    ("photo x4 1080p llvmpipe", "realesrgan-x4plus",    4, 2, 0, "hd1080"),
]

inputs = {
    "art512": make_art("art512", 512, 512),
    "hd1080": make_photo("hd1080", 1920, 1080),
    "uhd2160": make_photo("uhd2160", 3840, 2160),
}

results = {"box": {}, "cases": []}
uname = sh(["uname", "-r"]).stdout.strip()
results["box"] = {
    "kernel": uname,
    "cpus": os.cpu_count(),
    "gpus": [l for l in sh(["nvidia-smi", "-L"]).stdout.splitlines() if l.strip()],
    "note": ("Vulkan devices as the engine enumerates them: 0 and 1 are the two "
             "RTX 5060 Ti, 2 is Mesa llvmpipe (CPU)."),
}

for label, model, scale, gpu, tile, key in CASES:
    src = inputs[key]
    out = f"{WORK}/out-{label.replace(' ', '_')}.png"
    if os.path.exists(out):
        os.unlink(out)
    cmd = [BIN, "-i", src, "-o", out, "-n", model, "-s", str(scale),
           "-m", MODELS, "-f", "png", "-t", str(tile)]
    if gpu is not None:
        cmd += ["-g", str(gpu)]
    r = sh([TIME, "-v", "-o", "/dev/stdout"] + cmd)
    text = r.stdout + r.stderr
    wall = re.search(r"Elapsed \(wall clock\) time \([hm]:mm:ss or m:ss\):\s+(\d+):([\d.]+)", text)
    rss = re.search(r"Maximum resident set size \([^)]*\):\s+(\d+)", text)
    row = {
        "label": label,
        "model": model,
        "scale": scale,
        "gpu": "auto" if gpu is None else gpu,
        "tile": tile,
        "input": key,
        "input_dims": probe(src),
        "wall_s": (round(int(wall.group(1)) * 60 + float(wall.group(2)), 2)
                   if wall else None),
        "max_rss_kb": int(rss.group(1)) if rss else None,
        "engine_exit": r.returncode,
        "out_bytes": os.path.getsize(out) if os.path.exists(out) else None,
        "out_dims": probe(out) if os.path.exists(out) else None,
        "failure": None,
    }
    sig = re.search(r"(vkAllocateMemory failed[^\n]*|decode image[^\n]*failed)", text)
    if sig:
        row["failure"] = sig.group(1).strip()
    results["cases"].append(row)
    print(json.dumps(row), flush=True)
    with open(os.path.join(HERE, "measured.json"), "w") as f:
        json.dump(results, f, indent=2)

print("wrote", os.path.join(HERE, "measured.json"))
