"""Observers: what PengyOS pins, and what upstream has now.

Each ``parse_*`` / ``*_findings`` function is pure (text or JSON in, findings
out) so the tests run on recorded fixtures. Network access lives in the
``fetch_*`` helpers, which take an injectable ``get`` (url -> text) or ``run``
(argv -> stdout).

A finding is a dict:
    {"lane": "routine" | "major", "component": str, "current": str,
     "available": str, "note": str}
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable, Iterable

from pengyos_nightly import __version__
from pengyos_nightly.versions import bump_kind, is_prerelease, numeric, strip_prefix, vkey

Get = Callable[[str], str]
Run = Callable[[list], str]

# See edits.py: the User-Agent names the version actually running.
USER_AGENT = f"pengyos-nightly/{__version__} (+https://pengyos.catbee.ca)"


def http_get(url: str, timeout: float = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def run_cmd(argv: list, timeout: float = 120) -> str:
    return subprocess.run(argv, check=True, capture_output=True, text=True,
                          timeout=timeout).stdout


# --- pins read from the repository -------------------------------------------

def defconfig_value(text: str, symbol: str) -> str | None:
    m = re.search(rf'^{re.escape(symbol)}="?([^"\n]*)"?$', text, re.M)
    return m.group(1) if m else None


def mk_value(text: str, var: str) -> str | None:
    m = re.search(rf"^{re.escape(var)}\s*=\s*(\S+)\s*$", text, re.M)
    return m.group(1) if m else None


def read_cosmic_components(tsv: str) -> list[dict]:
    """scripts/cosmic-components.tsv: name, url, commit, build args."""
    rows = []
    for line in tsv.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) >= 3:
            rows.append({"name": cols[0], "url": cols[1], "commit": cols[2]})
    return rows


# --- git tags ----------------------------------------------------------------

def parse_ls_remote(text: str) -> dict[str, str]:
    """`git ls-remote --tags` -> {tag: commit}, preferring the peeled commit
    (`tag^{}`) of an annotated tag over the tag object."""
    tags: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].startswith("refs/tags/"):
            continue
        sha, ref = parts
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):
            peeled[name[:-3]] = sha
        else:
            tags[name] = sha
    tags.update(peeled)
    return tags


def buildroot_findings(tags: Iterable[str], current: str) -> list[dict]:
    """A newer point release of our series (2026.08.x) is routine; a newer
    release series is major, and an LTS one (YYYY.02) is called out."""
    releases = [t for t in tags if re.fullmatch(r"\d{4}\.\d{2}(\.\d+)?", t)]
    series = ".".join(current.split(".")[:2])
    out = []
    points = [t for t in releases if t.startswith(series + ".") and vkey(t) > vkey(current)]
    if points:
        best = max(points, key=vkey)
        out.append({"lane": "routine", "component": "buildroot", "current": current,
                    "available": best, "note": f"Buildroot {series} point release"})
    newer = [t for t in releases if vkey(".".join(t.split(".")[:2])) > vkey(series)]
    if newer:
        best_series = max({".".join(t.split(".")[:2]) for t in newer}, key=vkey)
        latest = max((t for t in newer if t.startswith(best_series)), key=vkey)
        lts = [s for s in {".".join(t.split(".")[:2]) for t in newer} if s.endswith(".02")]
        note = f"new Buildroot release series {best_series}"
        if lts:
            note += f" (long-term support: {max(lts, key=vkey)})"
        out.append({"lane": "major", "component": "buildroot", "current": current,
                    "available": latest, "note": note})
    return out


def nvidia_findings(tags: Iterable[str], current: str) -> list[dict]:
    """Every newer NVIDIA driver is a major finding: it needs real hardware."""
    versions = [t for t in tags if re.fullmatch(r"\d+\.\d+(\.\d+)?", t)]
    newer = [t for t in versions if vkey(t) > vkey(current)]
    if not newer:
        return []
    best = max(newer, key=vkey)
    same_branch = numeric(best)[0] == numeric(current)[0]
    note = ("newer driver in the same branch" if same_branch
            else f"new driver branch {numeric(best)[0]}")
    return [{"lane": "major", "component": "nvidia-open", "current": current,
             "available": best, "note": note}]


def pengyr_findings(tags: Iterable[str], current: str) -> list[dict]:
    """PengyR is the agent the images ship (`package/pengy`, pinned by
    `PENGY_VERSION` from github.com/patw/PengyR).

    It is an authored pin with its own upstream, so Buildroot's pkg-stats never
    sees it — this source is the only thing that can notice the shipped agent
    going stale. A patch bump inside the pinned minor is routine (the loop may
    bump it); a new minor is a major finding, because it changes the assistant
    every image carries and a human should decide that.
    """
    versions = [t for t in tags if re.fullmatch(r"v?\d+\.\d+\.\d+", t)]
    newer = [t for t in versions if vkey(t) > vkey(current) and not is_prerelease(t)]
    if not newer:
        return []
    best = max(newer, key=vkey)
    kind = bump_kind(current, best)
    note = ("PengyR patch release" if kind == "routine"
            else f"new PengyR minor ({'.'.join(map(str, numeric(current)[:2]))} -> "
                 f"{'.'.join(map(str, numeric(best)[:2]))})")
    return [{"lane": kind, "component": "pengyr", "current": current,
             # Report the version you would write into pengy.mk, not the git tag.
             "available": strip_prefix(best),
             "note": note}]


def cosmic_findings(components: list[dict], tags_by_repo: dict[str, dict[str, str]]) -> list[dict]:
    """For each COSMIC repo: the newest epoch-* tag, and whether the pinned
    commit is that tag. Repos behind the newest epoch are grouped into one
    major finding per epoch."""
    behind: dict[str, list[str]] = {}
    current_epochs: list[str] = []
    for comp in components:
        tags = tags_by_repo.get(comp["name"]) or {}
        epochs = {t: c for t, c in tags.items() if t.startswith("epoch-")}
        if not epochs:
            continue
        newest = max(epochs, key=lambda t: vkey(t[len("epoch-"):]))
        pinned_tag = next((t for t, c in epochs.items() if c == comp["commit"]), None)
        if pinned_tag:
            current_epochs.append(pinned_tag)
        if epochs[newest] != comp["commit"]:
            behind.setdefault(newest, []).append(comp["name"])
    out = []
    if current_epochs:
        common = max(set(current_epochs), key=lambda t: (current_epochs.count(t), vkey(t[6:])))
        n = current_epochs.count(common)
        current = common if n == len(current_epochs) else f"{common} ({n} of {len(current_epochs)} tagged repos)"
    else:
        current = "pinned commits"
    for epoch, names in sorted(behind.items(), key=lambda kv: vkey(kv[0][6:])):
        out.append({"lane": "major", "component": "cosmic", "current": current,
                    "available": epoch,
                    "note": f"{len(names)} components not at {epoch}: {', '.join(sorted(names))}"})
    return out


# --- kernel.org --------------------------------------------------------------

def kernel_findings(releases: dict, current: str) -> list[dict]:
    """Same series newer -> routine; series end of life or a newer stable
    series -> major."""
    series = ".".join(current.split(".")[:2])
    out = []
    rel = [r for r in releases.get("releases", [])
           if r.get("moniker") in ("stable", "longterm") and not is_prerelease(r["version"])]
    mine = [r for r in rel if r["version"].startswith(series + ".") or r["version"] == series]
    if mine:
        r = max(mine, key=lambda r: vkey(r["version"]))
        if bump_kind(current, r["version"]) == "routine":
            out.append({"lane": "routine", "component": "linux", "current": current,
                        "available": r["version"], "note": f"Linux {series} stable update"})
        if r.get("iseol"):
            out.append({"lane": "major", "component": "linux", "current": current,
                        "available": "", "note": f"Linux {series} is end of life"})
    else:
        out.append({"lane": "major", "component": "linux", "current": current, "available": "",
                    "note": f"Linux {series} is no longer listed on kernel.org (end of life)"})
    stable = [r for r in rel if r.get("moniker") == "stable"]
    if stable:
        top = max(stable, key=lambda r: vkey(r["version"]))
        top_series = ".".join(top["version"].split(".")[:2])
        if vkey(top_series) > vkey(series):
            out.append({"lane": "major", "component": "linux", "current": current,
                        "available": top["version"], "note": f"new stable series Linux {top_series}"})
    return out


# --- Buildroot pkg-stats -----------------------------------------------------

def parse_pkgstats(data: dict, edition: str) -> dict[str, dict]:
    """pkg-stats JSON -> {package: {...}} for one edition."""
    out = {}
    for name, p in data.get("packages", {}).items():
        infras = [i[0] for i in p.get("infras") or []]
        latest = p.get("latest_version") or {}
        out[name] = {
            "current": p.get("current_version") or "",
            "latest": latest.get("version") or "",
            "tree": p.get("tree", ""),
            # pkg-stats already knows where the recipe lives (a sub-package like
            # gst1-plugins-good has its own .mk under package/gstreamer1/) and what
            # CPE product it is, which is what the CVE evidence has to match.
            "pkg_path": p.get("pkg_path", ""),
            "mk_path": p.get("path", ""),
            "cpeid": p.get("cpeid", ""),
            "patch_files": list(p.get("patch_files") or []),
            "url": p.get("url", ""),
            "host_only": bool(infras) and "target" not in infras,
            "cves": list(p.get("cves") or []),
            "unsure_cves": list(p.get("unsure_cves") or []),
            "editions": [edition],
        }
    return out


def merge_packages(per_edition: Iterable[dict[str, dict]]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for pkgs in per_edition:
        for name, p in pkgs.items():
            if name not in merged:
                merged[name] = {**p, "editions": list(p["editions"])}
            else:
                m = merged[name]
                for e in p["editions"]:
                    if e not in m["editions"]:
                        m["editions"].append(e)
                m["cves"] = sorted(set(m["cves"]) | set(p["cves"]))
                m["unsure_cves"] = sorted(set(m["unsure_cves"]) | set(p["unsure_cves"]))
                m["host_only"] = m["host_only"] and p["host_only"]
                m["patch_files"] = sorted(set(m.get("patch_files", [])) | set(p.get("patch_files", [])))
    return merged


def package_findings(packages: dict[str, dict],
                     skip: Iterable[str] = ("linux", "nvidia-open")) -> list[dict]:
    """Version gaps worth reporting: our own recipes (BR2_EXTERNAL tree) only.
    Buildroot's packages move with Buildroot releases, not one by one; the
    kernel and the NVIDIA driver have their own observers."""
    out = []
    for name, p in sorted(packages.items()):
        if name in skip or not p["latest"] or p["tree"] == "BUILDROOT":
            continue
        kind = bump_kind(p["current"], p["latest"])
        if kind != "none":
            out.append({"lane": kind, "component": name, "current": p["current"],
                        "available": p["latest"], "note": "authored recipe"})
    return out


def run_pkgstats(buildroot: Path, output_dir: Path, json_out: Path, nvd_dir: Path,
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
    """Buildroot's pkg-stats in a configured output tree. aiohttp (and the
    setuptools distutils shim, ijson) come from uv, not the system."""
    env_cmd = ["env", "-u", "LD_LIBRARY_PATH", "uv", "run", "-q",
               "--with", "aiohttp", "--with", "setuptools", "--with", "ijson",
               "python3", str(buildroot / "support/scripts/pkg-stats"),
               "-c", "--json", str(json_out), "--nvd-path", str(nvd_dir)]
    run(env_cmd, cwd=output_dir, check=True, capture_output=True, text=True, timeout=3600)


# --- exploit signals ---------------------------------------------------------

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
EPSS_URL = "https://api.first.org/data/v1/epss?cve="


def parse_kev(data: dict) -> dict[str, dict]:
    return {v["cveID"]: {"date_added": v.get("dateAdded", ""),
                         "name": v.get("vulnerabilityName", ""),
                         "ransomware": v.get("knownRansomwareCampaignUse", "")}
            for v in data.get("vulnerabilities", [])}


def parse_epss(data: dict) -> dict[str, dict]:
    return {d["cve"]: {"epss": float(d["epss"]), "percentile": float(d["percentile"])}
            for d in data.get("data", [])}


def fetch_epss(cves: list[str], get: Get = http_get, batch: int = 80) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(cves), batch):
        out.update(parse_epss(json.loads(get(EPSS_URL + ",".join(cves[i:i + batch])))))
    return out


# --- NVD records (the fkie-cad clone pkg-stats maintains) ---------------------

def nvd_path(nvd_dir: Path, cve: str) -> Path:
    year, num = cve.split("-")[1:]
    return (Path(nvd_dir) / "git" / f"CVE-{year}"
            / f"CVE-{year}-{int(num) // 100}xx" / f"{cve}.json")


def nvd_raw(nvd_dir: Path, cve: str) -> dict:
    """The whole NVD record, including `configurations` - the affected ranges the act
    lane needs to prove a version closes a CVE. Missing record -> {}."""
    try:
        return json.loads(nvd_path(nvd_dir, cve).read_text())
    except (OSError, ValueError):
        return {}


def nvd_record(nvd_dir: Path, cve: str) -> dict:
    """Description, CVSS score/vector, status, published date for one CVE."""
    d = nvd_raw(nvd_dir, cve)
    if not d:
        return {}
    desc = next((x["value"] for x in d.get("descriptions", []) if x.get("lang") == "en"), "")
    score, vector = None, ""
    metrics = d.get("metrics", {})
    for key in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        entries = [m for m in metrics.get(key, []) if "cvssData" in m]
        if entries:
            primary = next((m for m in entries if m.get("type") == "Primary"), entries[0])
            score = primary["cvssData"].get("baseScore")
            vector = primary["cvssData"].get("vectorString", "")
            break
    return {"description": desc, "cvss": score, "vector": vector,
            "status": d.get("vulnStatus", ""), "published": d.get("published", "")[:10]}
