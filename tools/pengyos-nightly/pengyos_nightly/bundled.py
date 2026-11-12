"""Bundled dependencies: advisories against the Rust crates and Go modules compiled
into the images, from OSV (osv.dev: RustSec, the Go vulnerability database, GHSA).

pkg-stats sees Buildroot packages by CPE. COSMIC, Pengy, podman, uv and the rest
are one package each to it, but each links tens to thousands of third-party crates
or modules that no CPE names. The SBOM lists them (pengyos-sbom 0.2.0, nested
CycloneDX components with pkg:cargo / pkg:golang purls); this reads them out of
each edition's images/<edition>.cdx.json and asks OSV about every one.

Only names and versions of public open-source packages leave the machine.
"""
from __future__ import annotations

import json
import math
import urllib.request
from pathlib import Path
from typing import Callable

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/"
OSV_ECOSYSTEM = {"cargo": "crates.io", "golang": "Go"}
SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3, "UNRATED": 4}
# The id shown first: the database the ecosystem's own maintainers keep.
_ID_PREFERENCE = ("RUSTSEC-", "GO-", "GHSA-", "CVE-")

Post = Callable[[str, dict], dict]
Get = Callable[[str], str]


def http_post_json(url: str, body: dict, timeout: float = 120) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "pengyos-nightly"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def deps_from_cdx(doc: dict, edition: str, acc: dict) -> int:
    """Add every nested pkg:cargo / pkg:golang component of a CycloneDX SBOM to
    ``acc`` ({(eco, name, version): {"in": set, "editions": set}}). Returns how many
    the SBOM carried (0 means an SBOM older than pengyos-sbom 0.2.0)."""
    n = 0
    for comp in doc.get("components", []):
        for dep in comp.get("components", []) or []:
            purl = dep.get("purl", "")
            eco = purl[4:].split("/", 1)[0] if purl.startswith("pkg:") else ""
            if eco not in OSV_ECOSYSTEM or not dep.get("version"):
                continue
            rec = acc.setdefault((eco, dep["name"], dep["version"]), {"in": set(), "editions": set()})
            rec["in"].add(comp.get("name", "?"))
            rec["editions"].add(edition)
            n += 1
    return n


_W = {"AV": {"N": .85, "A": .62, "L": .55, "P": .2}, "AC": {"L": .77, "H": .44},
      "UI": {"N": .85, "R": .62}, "CIA": {"H": .56, "L": .22, "N": 0.0}}


def cvss3_score(vector: str) -> float | None:
    """CVSS 3.x base score from its vector (the FIRST spec's formula). RustSec
    records carry only the vector, no severity word; without this, 189 of the 310
    advisories against the Desktop's crates came back unrated."""
    try:
        m = dict(part.split(":", 1) for part in vector.split("/")[1:])
        scope = m["S"]
        pr = {"N": .85, "L": .68 if scope == "C" else .62, "H": .5 if scope == "C" else .27}[m["PR"]]
        iss = 1 - (1 - _W["CIA"][m["C"]]) * (1 - _W["CIA"][m["I"]]) * (1 - _W["CIA"][m["A"]])
        impact = 6.42 * iss if scope == "U" else 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
        exploit = 8.22 * _W["AV"][m["AV"]] * _W["AC"][m["AC"]] * pr * _W["UI"][m["UI"]]
    except (KeyError, ValueError):
        return None
    if impact <= 0:
        return 0.0
    raw = impact + exploit if scope == "U" else 1.08 * (impact + exploit)
    return math.ceil(min(raw, 10) * 10 - 1e-9) / 10


def _band(score: float) -> str:
    return ("CRITICAL" if score >= 9 else "HIGH" if score >= 7 else "MODERATE" if score >= 4
            else "LOW" if score > 0 else "")


def _severity(record: dict) -> str:
    s = str((record.get("database_specific") or {}).get("severity") or "").upper()
    if s == "MEDIUM":
        return "MODERATE"
    if s in SEVERITY_ORDER:
        return s
    for sev in record.get("severity", []) or []:
        if str(sev.get("type", "")).startswith("CVSS_V3"):
            score = cvss3_score(str(sev.get("score", "")))
            if score is not None:
                return _band(score)
    return ""


def _informational(record: dict) -> bool:
    """RustSec's notices that are not a vulnerability: "unmaintained", "unsound",
    "notice". OSV keeps the flag per affected package, not at the top."""
    if (record.get("database_specific") or {}).get("informational"):
        return True
    return any((a.get("database_specific") or {}).get("informational")
               for a in record.get("affected", []))


def _fixed(record: dict, eco: str, name: str) -> str:
    out = []
    for aff in record.get("affected", []):
        pkg = aff.get("package") or {}
        if pkg.get("ecosystem") != OSV_ECOSYSTEM[eco] or pkg.get("name") != name:
            continue
        for rng in aff.get("ranges", []):
            out += [e["fixed"] for e in rng.get("events", []) if "fixed" in e]
    return ", ".join(dict.fromkeys(out))


def _vuln(vid: str, modified: str, get: Get, cache: dict) -> dict:
    hit = cache.get(vid)
    if hit and hit.get("modified") == modified:
        return hit["record"]
    rec = json.loads(get(OSV_VULN + vid))
    cache[vid] = {"modified": modified or rec.get("modified", ""), "record": rec}
    return rec


def scan(deps: dict, post: Post = http_post_json, get: Get | None = None,
         cache: dict | None = None, batch: int = 500) -> list[dict]:
    """One record per (dependency, advisory): aliases folded together (RUSTSEC, GHSA,
    GO and CVE ids of one flaw are one advisory), withdrawn and informational
    (unmaintained / unsound) entries dropped, worst first."""
    from pengyos_nightly import sources
    get = get or sources.http_get
    cache = cache if cache is not None else {}
    keys = sorted(deps)
    out = []
    for i in range(0, len(keys), batch):
        chunk = keys[i:i + batch]
        q = {"queries": [{"package": {"ecosystem": OSV_ECOSYSTEM[e], "name": n},
                          "version": v.lstrip("v") if e == "cargo" else v}
                         for e, n, v in chunk]}
        results = post(OSV_BATCH, q).get("results", [])
        for (eco, name, ver), res in zip(chunk, results):
            records = [_vuln(v["id"], v.get("modified", ""), get, cache)
                       for v in res.get("vulns", []) or []]
            records = [r for r in records if not r.get("withdrawn") and not _informational(r)]
            groups: list[dict] = []
            for r in records:
                ids = {r["id"], *r.get("aliases", [])}
                g = next((g for g in groups if g["ids"] & ids), None)
                if g is None:
                    groups.append({"ids": ids, "records": [r]})
                else:
                    g["ids"] |= ids
                    g["records"].append(r)
            for g in groups:
                ids = sorted(g["ids"], key=lambda x: (next(
                    (k for k, p in enumerate(_ID_PREFERENCE) if x.startswith(p)), 9), x))
                sevs = [s for s in (_severity(r) for r in g["records"]) if s]
                sev = min(sevs, key=SEVERITY_ORDER.get) if sevs else "UNRATED"
                summary = next((r.get("summary") for r in g["records"] if r.get("summary")), "")
                fixed = next((f for f in (_fixed(r, eco, name) for r in g["records"]) if f), "")
                d = deps[(eco, name, ver)]
                out.append({"key": f"{ids[0]}|{eco}:{name}@{ver}", "id": ids[0], "aliases": ids[1:],
                            "ecosystem": eco, "name": name, "version": ver,
                            "in": sorted(d["in"]), "editions": sorted(d["editions"]),
                            "severity": sev, "summary": summary[:200], "fixed": fixed})
    out.sort(key=lambda b: (SEVERITY_ORDER[b["severity"]], b["name"], b["version"], b["id"]))
    return out


def observe(build_root: Path, targets: list[dict], state_dir: Path,
            post: Post = http_post_json, get: Get | None = None) -> tuple[list[dict], dict]:
    """(advisories, errors) for every edition with a configured output tree."""
    errors: dict[str, str] = {}
    deps: dict = {}
    for t in targets:
        out = Path(build_root) / "output" / t["name"]
        if not (out / ".config").exists():
            continue
        cdx = out / "images" / f"{t['name']}.cdx.json"
        try:
            n = deps_from_cdx(json.loads(cdx.read_text()), t["name"], deps)
        except (OSError, ValueError) as e:
            errors[f"sbom {t['name']}"] = f"cannot read {cdx}: {e}"[:300]
            continue
        if n == 0:
            errors[f"sbom {t['name']}"] = (f"{cdx.name} lists no bundled crates or Go modules "
                                          "(built before pengyos-sbom 0.2.0?)")
    if not deps:
        return [], errors
    cache_path = Path(state_dir) / "osv-cache.json"
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    found = scan(deps, post=post, get=get, cache=cache)
    tmp = cache_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, sort_keys=True))
    tmp.replace(cache_path)
    return found, errors
