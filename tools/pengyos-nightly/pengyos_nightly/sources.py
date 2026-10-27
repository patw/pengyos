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
    # The component name is the Buildroot package name, `pengy`, not "pengyr":
    # the planner joins a finding to `obs["packages"]` by that key, and pkg-stats
    # lists our authored recipe under its package name. A finding named after the
    # upstream repo finds no package entry, so the candidate is built from the
    # empty fallback — no editions, no current version — and every guard that
    # reads the package then refuses it for a reason that is not the real one.
    return [{"lane": kind, "component": "pengy", "current": current,
             # Report the version you would write into pengy.mk, not the git tag.
             "available": strip_prefix(best),
             "note": note}]


def openssh_fork_findings(tags: Iterable[str], current: str) -> list[dict]:
    """OpenSSH portable, observed for the fork that ships it (`openssh-pengyos`).

    A fork is invisible to Buildroot's pkg-stats: it scans the stock recipe, and the
    stock recipe is switched off. So nothing in the toolchain notices our pin going
    stale - the PengyR case (images shipped 1.10.0 while upstream was at 1.11.1 and
    nothing said so), which is exactly the failure `divergence.toml` requires an
    `observe_source` to prevent. For OpenSSH the cost of that silence is a remote
    unauthenticated sshd CVE sitting in every image with no signal.

    Upstream tags portable releases `V_10_6_P1`; the recipe pins `10.6p1`, so the tag
    is normalized to the string the recipe would carry. A new patch release (10.6p2)
    is routine - the loop may bump it; a new minor is major, because sshd is the
    exposed service of every edition and a series change is a human decision.
    """
    def normalize(tag: str) -> str | None:
        m = re.fullmatch(r"V_(\d+)_(\d+)_P(\d+)", tag)
        return f"{m.group(1)}.{m.group(2)}p{m.group(3)}" if m else None

    versions = [v for v in (normalize(t) for t in tags) if v]
    newer = [v for v in versions if vkey(v) > vkey(current)]
    if not newer:
        return []
    best = max(newer, key=vkey)
    kind = bump_kind(current, best)
    return [{"lane": kind, "component": "openssh-pengyos", "current": current,
             "available": best,
             "note": (f"OpenSSH patch release (fork: {current} -> {best})"
                      if kind == "routine"
                      else f"new OpenSSH series ({current} -> {best})")}]


def ffmpeg_fork_findings(html: str, current: str) -> list[dict]:
    """ffmpeg, observed for the fork that ships it (`ffmpeg-pengyos`).

    Same reason as `openssh_fork_findings`: pkg-stats scans the stock recipe, the
    stock recipe is switched off, so nothing in the toolchain notices our 9.0.2 pin
    going stale while ffmpeg publishes a release that closes a CVE we carry the fork
    for. The ledger names this as the fork's `observe_source`.

    ffmpeg publishes no tag list a `git ls-remote` can read; the release directory at
    https://ffmpeg.org/releases/ is the index, so the source is the directory listing
    and the parse is of `ffmpeg-<version>.tar.xz` hrefs. Signatures (`.asc`, `.sig`),
    snapshots and the `.tar.gz`/`.tar.bz2` alternates are not releases of a version,
    so only the exact `.tar.xz` name counts.

    A point release (9.0.2 -> 9.0.3) is routine: it is the version the fork's own
    expiry names, and it closes the three CVEs NVD says are affected *through* 9.0.2.
    A new series (9.0 -> 9.1) is major: ffmpeg is the Desktop's media path, and a
    series change is a human decision.
    """
    # The negative lookahead is not decoration: without it `ffmpeg-9.0.2.tar.xz.asc`
    # matches, and a signature file is read as a release - the fork would report a
    # bump to a version that does not exist, every night. ffmpeg publishes .asc and
    # .pq.asc next to every tarball, so this is the normal shape of the page.
    versions = re.findall(r"ffmpeg-([0-9]+(?:\.[0-9]+)*)\.tar\.xz(?![A-Za-z0-9.])", html)
    versions = {v for v in versions if not is_prerelease(v)}
    newer = [v for v in versions if vkey(v) > vkey(current)]
    if not newer:
        return []
    best = max(newer, key=vkey)
    kind = bump_kind(current, best)
    return [{"lane": kind, "component": "ffmpeg-pengyos", "current": current,
             "available": best,
             "note": (f"ffmpeg point release (fork: {current} -> {best})"
                      if kind == "routine"
                      else f"new ffmpeg series ({current} -> {best})")}]


def hplip_fork_findings(html: str, current: str) -> list[dict]:
    """hplip, observed for the fork that ships it (`hplip-pengyos`).

    Same reason as `openssh_fork_findings` and `ffmpeg_fork_findings`: pkg-stats
    scans the stock recipe, the stock recipe is switched off, so nothing in the
    toolchain notices our 3.26.6 pin going stale while hplip publishes a release
    that closes a CVE we carry the fork for. divergence.toml -> hplip-3.26.6-fork
    names this as the fork's `observe_source`.

    hplip has no git tags and no plain-text checksum index; the SourceForge files
    page for the project is the index, and it lists the current release. Only
    `hplip-<version>.tar.gz` counts - the `.asc` signatures, the `.run`/`.sh`
    self-extracters and the `.sig` files are not releases of a version.

    Every hplip release here is routine: hplip ships point releases on a stable
    3.x line, and this fork exists to be deleted when Buildroot catches up, not to
    be carried forward through a redesign. A new 3.2x series is therefore routine
    for the loop, and the ledger's expiry - not the lane - is what ends the fork.
    """
    # The negative lookahead is not decoration. Without it `hplip-3.99.0.tar.gz.asc`
    # matches and the fork reports a release that does not exist - a signature file
    # read as a version, every night, forever.
    versions = re.findall(r"hplip-([0-9]+(?:\.[0-9]+)*)\.tar\.gz(?![A-Za-z0-9.])", html)
    versions = {v for v in versions if not is_prerelease(v)}
    newer = [v for v in versions if vkey(v) > vkey(current)]
    if not newer:
        return []
    best = max(newer, key=vkey)
    return [{"lane": "routine", "component": "hplip-pengyos", "current": current,
             "available": best,
             "note": f"hplip release newer than the fork (hplip-pengyos: {current} -> {best})"}]


def upower_fork_findings(tags: Iterable[str], current: str) -> list[dict]:
    """UPower, observed for the fork that ships it (`upower-pengyos`).

    Same reason as `openssh_fork_findings`: pkg-stats scans the stock recipe, the
    stock recipe is switched off, so nothing in the toolchain notices our 1.91.5 pin
    going stale. For upower the stock recipe is no help even when it is on - Buildroot
    has not touched it since 2022 - and NVD lists no CPE for upower, so there is no
    CVE feed either. This source is the only thing that sees the pin age.
    divergence.toml -> upower-1.91.5-fork names it as the fork's `observe_source`.

    Upstream tags releases `v1.91.5`. The `UPOWER_0_99_x` and `DeviceKit-power-*`
    tags are pre-2022 history in the other naming scheme and are all older than any
    pin this fork can carry, so only `vX.Y.Z` counts. A point release (1.91.6) is
    routine; a new series (1.92) is major, because upowerd's D-Bus API is what the
    panel, Settings and power-profiles-daemon read.
    """
    versions = [t[1:] for t in tags if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    newer = [v for v in versions if vkey(v) > vkey(current)]
    if not newer:
        return []
    best = max(newer, key=vkey)
    kind = bump_kind(current, best)
    return [{"lane": kind, "component": "upower-pengyos", "current": current,
             "available": best,
             "note": (f"UPower point release (fork: {current} -> {best})"
                      if kind == "routine"
                      else f"new UPower series ({current} -> {best})")}]


def libdisplay_info_fork_findings(tags: Iterable[str], current: str) -> list[dict]:
    """libdisplay-info, observed for the fork that ships it (`libdisplay-info-pengyos`).

    The fork pins 0.3.0, BELOW the 0.4.0 Buildroot ships: cosmic-comp links
    libdisplay-info.so.3 and its libdisplay-info-sys crate accepts only < 0.4.0.
    pkg-stats scans the stock recipe, which is switched off, and NVD lists no CPE for
    libdisplay-info, so its CVEs never match either. divergence.toml ->
    libdisplay-info-0.3.0-fork names this as the fork's `observe_source`.

    Upstream tags releases `0.3.0`, with no `v`. A point release in the pinned series
    (0.3.1) is routine and wins over a newer series: it is the one the fork can take
    without moving cosmic-comp, and the likeliest carrier of a security fix. Without
    one, the newest series is major - a newer series needs cosmic-comp's crate to move
    first, so it is reported, never something the loop can bump on its own.
    """
    versions = [t for t in tags if re.fullmatch(r"\d+\.\d+\.\d+", t)]
    newer = [v for v in versions if vkey(v) > vkey(current)]
    if not newer:
        return []
    series = [v for v in newer if bump_kind(current, v) == "routine"]
    best = max(series or newer, key=vkey)
    kind = bump_kind(current, best)
    return [{"lane": kind, "component": "libdisplay-info-pengyos", "current": current,
             "available": best,
             "note": (f"libdisplay-info point release (fork: {current} -> {best})"
                      if kind == "routine"
                      else f"new libdisplay-info series ({current} -> {best}); "
                           "cosmic-comp's libdisplay-info-sys must accept it first")}]


def uutils_findutils_findings(tags: Iterable[str], current: str) -> list[dict]:
    """uutils/findutils, the authored recipe that ships find and xargs
    (`package/uutils-findutils`, Core).

    pkg-stats can see the recipe but not its upstream: release-monitoring.org has
    no uutils-findutils project, so the "latest" column stays empty and the pin
    would age with nothing noticing. This source reads the upstream git tags.

    Upstream tags releases bare (`0.10.0`) and keeps a moving `latest-commit` tag,
    which is not a release. It is still 0.x, where a new minor is a breaking
    release, so `bump_kind` makes 0.10.0 -> 0.10.1 routine and 0.11.0 major: the
    find every script runs is a human's call when its behaviour may change.
    """
    versions = [strip_prefix(t) for t in tags if re.fullmatch(r"v?\d+\.\d+\.\d+", t)]
    newer = [v for v in versions if vkey(v) > vkey(current) and not is_prerelease(v)]
    if not newer:
        return []
    best = max(newer, key=vkey)
    kind = bump_kind(current, best)
    # Named after the Buildroot package, which is the key the planner joins on.
    return [{"lane": kind, "component": "uutils-findutils", "current": current,
             "available": best,
             "note": (f"uutils/findutils patch release ({current} -> {best})"
                      if kind == "routine"
                      else f"new uutils/findutils series ({current} -> {best})")}]


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
