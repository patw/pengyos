"""Does a candidate version actually close a CVE? NVD's own ranges decide.

An NVD record states the affected range as CPE match constraints. Two shapes
matter here, and they mean different things:

    versionEndExcluding 2.15.4   affected <  2.15.4  -> 2.15.4 closes it
    versionEndIncluding 2.15.3   affected <= 2.15.3  -> anything above closes it

A record that pins the exact version and says nothing about a fix
(`cpe:2.3:a:gnu:tar:1.35:*:*:*:*:*:*:*`) carries no evidence at all: it tells you
the version is affected, not what closes it. That is the difference between a
CVE the loop may act on and one it may only report — so `bounds()` returns None
and the planner refuses rather than guessing.

Buildroot's package names do not always match the CPE product name
(`gst1-plugins-good` vs `gst-plugins-good`), so the product match goes through
an explicit alias table. An unmatched product is *no evidence*, not a guess.
"""
from __future__ import annotations

import re

from pengyos_nightly.versions import vkey

# Buildroot recipe name -> CPE product name, for the ones that differ. Extend it
# when a real package is refused for the wrong reason, never to force one through.
CPE_ALIASES = {
    "gst1-plugins-good": "gst-plugins-good",
    "gst1-plugins-base": "gst-plugins-base",
    "gst1-plugins-bad": "gst-plugins-bad",
    "gst1-plugins-ugly": "gst-plugins-ugly",
    "gst1-libav": "gst-libav",
    "gstreamer1": "gstreamer",
    "gstreamer1-vaapi": "gstreamer-vaapi",
    "gst1-rtsp-server": "gst-rtsp-server",
    "libjpeg": "libjpeg-turbo",
    "boost": "boost",
    "openssl": "openssl",
}

_CPE = re.compile(r"^cpe:/?2\.3:[a-z]:([^:]+):([^:]+)")


def product_of(criteria: str) -> str | None:
    """cpe:2.3:a:xmlsoft:libxml2:*... -> 'libxml2'."""
    m = _CPE.match(criteria or "")
    return m.group(2) if m else None


def matches_package(criteria: str, package: str, cpeid: str = "") -> bool:
    """Is this CPE match about the package we ship?

    pkg-stats already reports the package's own CPE id, and its product is the
    authoritative answer (`gst1-plugins-good` is `gst-plugins-good`); the alias
    table is only the fallback for a package with no cpeid.
    """
    product = product_of(criteria)
    if not product:
        return False
    wanted = product_of(cpeid) or CPE_ALIASES.get(package, package)
    return product.lower() == wanted.lower()


def bounds(record: dict, package: str, cpeid: str = "") -> dict | None:
    """The affected-range constraints NVD states for `package`.

    Returns {"constraints": [{"end_excluding": "2.15.4"}, ...], "products": [...]},
    or None when the record says nothing about this package at all. A constraint
    with no end field carries the exact version the CPE names (`exact`), which
    proves exposure but names no fix.
    """
    constraints: list[dict] = []
    products: set[str] = set()

    def walk(node: dict) -> None:
        for match in node.get("cpeMatch") or []:
            criteria = match.get("criteria", "")
            if not matches_package(criteria, package, cpeid):
                continue
            products.add(product_of(criteria) or "")
            c: dict = {}
            for key, field in (("end_excluding", "versionEndExcluding"),
                              ("end_including", "versionEndIncluding"),
                              ("start_including", "versionStartIncluding"),
                              ("start_excluding", "versionStartExcluding")):
                if match.get(field):
                    c[key] = match[field]
            if not any(k.startswith("end_") for k in c):
                exact = cpe_version(criteria)
                if exact:
                    c["exact"] = exact
            constraints.append(c)
        for child in node.get("nodes") or []:
            walk(child)

    for config in record.get("configurations") or []:
        walk(config)
    if not constraints:
        return None
    return {"constraints": constraints, "products": sorted(products)}


def cpe_version(criteria: str) -> str:
    """The version an exact CPE match names: cpe:2.3:a:gnu:tar:1.35:*... -> 1.35."""
    parts = (criteria or "").split(":")
    return parts[5] if len(parts) > 5 and parts[5] not in ("*", "-") else ""


def affected(bound: dict, version: str) -> tuple[bool, str]:
    """Is the version we ship inside the affected range? That is the exposure proof
    the patch lane needs: it does not move the version, so it has to be sure the
    hole is real before carrying a fix into the image."""
    if not bound:
        return False, "no NVD affected-range for this package"
    reasons = []
    for c in bound["constraints"]:
        if "end_excluding" in c:
            if vkey(version) < vkey(c["end_excluding"]):
                reasons.append(f"{version} < {c['end_excluding']}, where NVD says the range stops")
            else:
                return False, f"{version} is at or past {c['end_excluding']}: NVD does not list it as affected"
        elif "end_including" in c:
            if vkey(version) <= vkey(c["end_including"]):
                reasons.append(f"{version} <= {c['end_including']}, the last affected release")
            else:
                return False, f"{version} is above {c['end_including']}: NVD does not list it as affected"
        elif "exact" in c:
            if vkey(version) == vkey(c["exact"]):
                reasons.append(f"NVD names {c['exact']} exactly, which is what we ship")
            else:
                return False, f"NVD names {c['exact']}, we ship {version}"
        else:
            return False, "NVD gives a range this tool cannot read"
    return True, "; ".join(reasons)


def covers(bound: dict, target: str) -> tuple[bool, str]:
    """Does `target` close every constraint in this range? (bool, why).

    Every constraint must be satisfied: a record that lists two affected ranges
    is not closed by a target that clears only one of them.
    """
    if not bound:
        return False, "no NVD affected-range for this package"
    reasons = []
    for c in bound["constraints"]:
        if "end_excluding" in c:
            if vkey(target) >= vkey(c["end_excluding"]):
                reasons.append(f"affected < {c['end_excluding']}; {target} is at or past it")
            else:
                return False, f"{target} is still below {c['end_excluding']}, where NVD says the CVE stops"
        elif "end_including" in c:
            if vkey(target) > vkey(c["end_including"]):
                reasons.append(f"affected <= {c['end_including']}; {target} is above it")
            else:
                return False, f"{target} is still inside the affected range (<= {c['end_including']})"
        else:
            # An exact-version match with no end constraint names no fix.
            return False, "NVD names an affected version but no version that closes it"
    return True, "; ".join(reasons)


def evidence(cve: str, package: str, record: dict, current: str, target: str,
             cpeid: str = "") -> dict:
    """The evidence entry a security action must carry.

    Two questions, both answered, because the two shapes need different ones:
    `affected` is what makes the hole real in the version we ship (the patch lane
    needs this), `covered` is what makes a version bump worth doing (the recipe
    lane needs this).
    """
    bound = bounds(record or {}, package, cpeid)
    if not bound:
        return {"kind": "none", "affected": False, "covered": False,
                "note": f"NVD has no affected-range for {package} in {cve}; nothing proves a fix"}
    aff, aff_why = affected(bound, current)
    cov, cov_why = covers(bound, target) if target else (False, "no newer version to move to")
    return {"kind": "nvd_range", "affected": aff, "covered": cov,
            "affected_note": aff_why, "covered_note": cov_why, "note": aff_why,
            "constraints": bound["constraints"], "products": bound["products"],
            "current": current, "target": target}


_COMMIT = re.compile(r"/commit/([0-9a-f]{7,40})")
# A fix published as a review request rather than a bare commit: GitHub `pull/N`,
# FFmpeg/cgit-style `pulls/N`, GitLab `-/merge_requests/N`. Each form was verified
# against a real NVD reference on 2026-10-06: code.ffmpeg.org/FFmpeg/FFmpeg/pulls/22348
# and gitlab.freedesktop.org/gstreamer/gstreamer/-/merge_requests/11243 both serve the
# fix as git format-patch (see edits.patch_url).
_PULL = re.compile(r"/(?:pull|pulls|merge_requests|merge-requests)/(\d+)")


def host_of(url: str) -> str:
    return url.split("//", 1)[-1].split("/", 1)[0]


def fix_commit(record: dict) -> dict | None:
    """The upstream fix NVD points at, if it names one: a commit, or the pull /
    merge request that carries it.

    For a stock package this is the whole difference between "carry the fix" and
    "report it": a record that points at a vendor erratum (tar's CVEs reference
    Red Hat errata) or at an advisory (gstreamer's ZDI entries) has nothing to
    fetch, so the loop must not pretend otherwise. A review request counts because
    the hosts we carry patches from serve the request itself as a format-patch,
    which is how FFmpeg and GStreamer actually publish a fix; the sha is then read
    back out of the patch (edits.patch_sha), so the record still names a commit.
    """
    refs = [r.get("url", "") for r in (record or {}).get("references", [])]
    # A named commit first: it is the exact change, and it is what the patch lane
    # has always carried. A request is the fallback, not the preference.
    for url in refs:
        m = _COMMIT.search(url)
        if m:
            return {"url": url, "sha": m.group(1), "host": host_of(url), "kind": "commit"}
    for url in refs:
        m = _PULL.search(url)
        if m:
            return {"url": url, "sha": "", "host": host_of(url), "kind": "pull",
                    "request": m.group(1)}
    return None


def fix_refs(record: dict, limit: int = 3) -> list[str]:
    """Upstream references worth putting in the release notes."""
    refs = [r.get("url", "") for r in (record or {}).get("references", [])]
    keep = [r for r in refs if re.search(r"(commit|compare|advisory|security|bulletin|CVE-)", r, re.I)]
    return (keep or refs)[:limit]
