"""CVE triage: rules that cannot be argued with, then the local model.

The model gets one CVE at a time as a plain completion (no tools) and answers
JSON. Its answer can raise a priority above the rules' floor, never lower it.
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Callable

PRIORITIES = ("P0", "P1", "P2")
REACH = ("remote", "local", "file", "none", "unknown")

# What each edition exposes, for the model's reachability call. Keep in step
# with the board overlays (board/*/rootfs-overlay; overlays accumulate
# Core -> Server -> Desktop).
EXPOSURE = """\
PengyOS editions and what they expose:
- Core: minimal systemd base. sshd on TCP 22 (key-only, no passwords), no
  firewall; chrony as an NTP client. No other listening services.
- Server: Core plus podman and admin CLIs. nftables default-deny inbound;
  only SSH (22, key-only) and mDNS (UDP 5353 multicast) are open. Containers
  may publish ports the operator chooses.
- Desktop (and Desktop NVIDIA): Server's firewall, plus the COSMIC desktop,
  NetworkManager, avahi (mDNS), CUPS (localhost only), PipeWire, GStreamer and
  image/media libraries that parse files the user opens or downloads.
  Browsers come from Flathub (not part of the image).
- Packages marked host-only are build tools on the build machine and are not
  in any image."""

Complete = Callable[[list[dict]], str]


def rules(cve: str, pkg: dict, kev: dict) -> dict | None:
    """A final answer from rules alone, or None if the model should look."""
    if pkg.get("host_only"):
        return {"priority": "P2", "reachable": "none", "source": "rule",
                "rationale": "Build-host tool only; not in any PengyOS image."}
    return None


def floor(cve: str, pkg: dict, kev: dict) -> str:
    """The lowest priority the model may assign."""
    if cve in kev and not pkg.get("host_only"):
        return "P1"
    return "P2"


def fallback(cve: str, pkg: dict, kev: dict, nvd: dict) -> dict:
    if cve in kev:
        pri = "P1"
    elif (nvd.get("cvss") or 0) >= 9.0:
        pri = "P1"
    else:
        pri = "P2"
    return {"priority": pri, "reachable": "unknown", "source": "fallback",
            "rationale": "Model answer unavailable; priority from KEV/CVSS rules only."}


def build_messages(cve: str, package: str, pkg: dict, nvd: dict, kev: dict,
                   epss: dict) -> list[dict]:
    facts = {
        "cve": cve,
        "package": package,
        "package_version": pkg.get("current", ""),
        "editions_shipping_it": pkg.get("editions", []),
        "nvd_description": nvd.get("description", "(no NVD record found)"),
        "cvss": nvd.get("cvss"),
        "cvss_vector": nvd.get("vector", ""),
        "nvd_status": nvd.get("status", ""),
        "published": nvd.get("published", ""),
        "in_cisa_kev_actively_exploited": cve in kev,
        "epss_probability": (epss.get(cve) or {}).get("epss"),
        "matched_by": "name/version heuristic (may be a false match)"
                      if pkg.get("unsure") else "CPE match",
    }
    system = (
        "You triage CVEs for PengyOS, a small Linux distribution. Decide how "
        "urgently this CVE matters to PengyOS users. Many CVE matches are "
        "noise: very old CVEs already fixed in this version, issues in features "
        "PengyOS does not use, or disputed reports. Judge from the facts given.\n\n"
        + EXPOSURE + "\n\n"
        "Priorities:\n"
        "- P0: actively exploited (CISA KEV), or remotely reachable without "
        "authentication through something an edition exposes.\n"
        "- P1: serious and plausibly reachable (remote, or by opening a "
        "malicious file, or local privilege escalation) in this version.\n"
        "- P2: not reachable in PengyOS, already fixed in this version, "
        "disputed, or low impact.\n\n"
        "Answer with ONLY a JSON object, no prose before or after:\n"
        '{"priority": "P0|P1|P2", "reachable": "remote|local|file|none|unknown", '
        '"rationale": "one or two plain sentences"}'
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(facts, indent=1)}]


_THINK = re.compile(r"<think>.*?</think>", re.S)


def parse_answer(text: str) -> dict | None:
    """The first JSON object in the reply, validated; None if unusable."""
    text = _THINK.sub("", text or "")
    candidates = []
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{"):text.rindex("}") + 1])
    candidates += [m.group(0) for m in re.finditer(r"\{.*?\}", text, re.S)]
    for c in candidates:
        try:
            d = json.loads(c)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        pri = str(d.get("priority", "")).upper()
        reach = str(d.get("reachable", "unknown")).lower()
        rationale = str(d.get("rationale", "")).strip()
        if pri in PRIORITIES and rationale:
            return {"priority": pri, "reachable": reach if reach in REACH else "unknown",
                    "rationale": rationale[:600], "source": "model"}
    return None


def triage_cve(cve: str, package: str, pkg: dict, nvd: dict, kev: dict, epss: dict,
               complete: Complete | None) -> dict:
    ruled = rules(cve, pkg, kev)
    if ruled:
        return ruled
    answer = None
    if complete is not None:
        messages = build_messages(cve, package, pkg, nvd, kev, epss)
        for _ in range(2):
            try:
                answer = parse_answer(complete(messages))
            except Exception:  # network, timeout, HTTP error: retry, then fall back
                answer = None
            if answer:
                break
    if not answer:
        answer = fallback(cve, pkg, kev, nvd)
    low = floor(cve, pkg, kev)
    if PRIORITIES.index(answer["priority"]) > PRIORITIES.index(low):
        answer = {**answer, "priority": low,
                  "rationale": answer["rationale"] + f" (raised to {low}: in CISA KEV)"}
    return answer


# --- the model gateway Pengy uses ----------------------------------------------

def pengy_settings(path: Path = Path.home() / ".config/pengy/settings.json") -> dict:
    return json.loads(Path(path).read_text())


def make_complete(base_url: str, api_key: str, model: str = "local",
                  timeout: float = 600) -> Complete:
    """An OpenAI-compatible chat completion, no tools, temperature 0."""
    url = base_url.rstrip("/") + "/chat/completions"

    def complete(messages: list[dict]) -> str:
        body = json.dumps({"model": model, "messages": messages,
                           "temperature": 0}).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"].get("content") or ""

    return complete
