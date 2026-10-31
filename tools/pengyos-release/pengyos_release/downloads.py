"""Install images for people: ``<site>/download/``.

    <site>/download/
        index.html                         what to pick, how to write it, how to boot (generated)
        pengyos-release-2026.pub           the release signing key (pengyos-key-v1, public)
        <YY.MM.N>/
            pengyos-<edition>-<YY.MM.N>-x86_64.img.xz   one per edition
            SHA256SUMS + SHA256SUMS.sig    sha256sum format; ed25519 (pengyos-sig-v1)
            downloads.json                 what index.html lists

Desktop, Desktop NVIDIA, Server and Server NVIDIA ship their *installer* stick (live system +
installer). Core is not offered (2026-10-31): it is the base the other editions are
built on and is still built and smoke-tested with every release, but as a download it
had no installer, no way in over SSH, no updates and a 1 GB root on any disk. Older
releases' Core files stay on the site, unlisted.

``download-publish`` runs with every release, testing included, because the
images must come from the build that was boot-checked. ``index.html`` lists
only **stable** ones: per edition, the newest version whose update release
(``<site>/update/<image>/<version>/release.json``) is on stable, so a testing
release stays off the page until ``update-promote --channel stable``, which
regenerates it.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import html
import json
import os
import subprocess
from pathlib import Path

from .manifest import parse_version
from .osupdate import UpdatePublishError, _os_release, _private_key, _sha256, sign_bytes

# What each image is for, in the order the page lists them.
EDITIONS = {
    "pengyos-desktop": ("Desktop", "The COSMIC desktop for Intel and AMD graphics (and most laptops). "
                        "Boots into a live desktop you can try, with the installer one click away."),
    "pengyos-desktop-nvidia": ("Desktop NVIDIA", "The same desktop with NVIDIA's driver, for GeForce GTX 16 / "
                               "RTX cards and newer. Pick this if your computer has an NVIDIA graphics card."),
    "pengyos-server": ("Server", "Headless: SSH, containers (podman), monitoring, backups and a firewall. "
                       "Boots into a console installer."),
    "pengyos-server-nvidia": ("Server NVIDIA", "The Server with NVIDIA's driver for GPU computing: CUDA, "
                              "<code>nvidia-smi</code> and GPUs in containers, for GeForce RTX 20 cards "
                              "and newer. No graphics; boots into a console installer."),
}
ORDER = list(EDITIONS)


def _image_file(build: Path) -> Path:
    images = build / "images"
    inst = sorted(images.glob("*-installer.img"))
    if inst:
        return inst[0]
    plain = sorted(p for p in images.glob("pengyos-*.img"))
    if not plain:
        raise UpdatePublishError(f"{images}: no pengyos-*.img to publish")
    return plain[0]


def public_key_line(key: Path) -> str:
    from cryptography.hazmat.primitives import serialization

    priv, keyid = _private_key(key)
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return f"pengyos-key-v1 ed25519 {keyid} {pub.hex()}\n"


def publish_downloads(builds: list[Path], site: Path, key: Path, *, force: bool = False) -> Path:
    """Compress + checksum + sign one release's install images (all one version)."""
    entries, version = [], None
    for build in builds:
        osr = _os_release(build / "target" / "usr" / "lib" / "os-release")
        image, ver = osr.get("IMAGE_ID", ""), osr.get("IMAGE_VERSION", "")
        if image not in EDITIONS:
            raise UpdatePublishError(f"{build}: unknown image {image!r}")
        if version and ver != version:
            raise UpdatePublishError(f"{build} is {ver}, the others are {version}: publish one release at a time")
        version = ver
        entries.append((build, image, osr))
    if not version:
        raise UpdatePublishError("no builds given")
    parse_version(version)
    dest = site / "download" / version
    dest.mkdir(parents=True, exist_ok=True)

    listing = []
    for build, image, osr in entries:
        src = _image_file(build)
        name = f"{image}-{version}-x86_64.img.xz"
        out = dest / name
        if force or not out.exists():
            tmp = out.with_name(out.name + ".part")
            with open(tmp, "wb") as fh:
                subprocess.run(["xz", "-T0", "-6", "-c", str(src)], stdout=fh, check=True)
            os.replace(tmp, out)
        listing.append({
            "image": image, "name": EDITIONS[image][0], "file": name, "size": out.stat().st_size,
            "raw_size": src.stat().st_size, "sha256": _sha256(out),
            "kind": "installer" if src.name.endswith("-installer.img") else "disk",
            "build": osr.get("BUILD_ID", ""),
        })
    old = dest / "downloads.json"
    if old.exists():  # keep editions published earlier for this version
        have = {e["image"] for e in listing}
        listing += [e for e in json.loads(old.read_text())["images"]
                    if e["image"] not in have and e["image"] in EDITIONS]
    listing.sort(key=lambda e: ORDER.index(e["image"]))

    sums = "".join(f"{e['sha256']}  {e['file']}\n" for e in listing).encode()
    (dest / "SHA256SUMS").write_bytes(sums)
    (dest / "SHA256SUMS.sig").write_bytes(sign_bytes(sums, key))
    published = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    (dest / "downloads.json").write_text(json.dumps(
        {"version": version, "published": published, "images": listing}, indent=2) + "\n")
    _, keyid = _private_key(key)
    (site / "download" / f"pengyos-release-{keyid[:8]}.pub").write_text(public_key_line(key))
    write_index(site, key_file=f"pengyos-release-{keyid[:8]}.pub")
    return dest


def _stable(site: Path) -> set[tuple[str, str]]:
    """(image, version) pairs on the stable update channel."""
    pairs = set()
    for rj in (site / "update").glob("*/*/release.json"):
        rel = json.loads(rj.read_text())
        if "stable" in rel.get("channels", []):
            pairs.add((rel["image"], rel["version"]))
    return pairs


def listed(site: Path) -> tuple[list[dict], list[dict]]:
    """What the page offers: (one entry per edition, its newest stable; older stable releases)."""
    releases = [json.loads(dj.read_text()) for dj in (site / "download").glob("*/downloads.json")]
    releases.sort(key=lambda r: parse_version(r["version"]), reverse=True)
    stable = _stable(site)
    current = {}
    for r in releases:
        for e in r["images"]:
            if e["image"] not in EDITIONS:  # no longer offered (Core)
                continue
            if (e["image"], r["version"]) in stable and e["image"] not in current:
                current[e["image"]] = {**e, "version": r["version"]}
    shown = {e["version"] for e in current.values()}
    older = [r for r in releases if r["version"] not in shown
             and any((e["image"], r["version"]) in stable and e["image"] in EDITIONS for e in r["images"])]
    return sorted(current.values(), key=lambda e: ORDER.index(e["image"])), older


def _line(version: str) -> str:
    """The release line people compare (``26.10``); the build (``26.10.7``) is small print."""
    return version.rsplit(".", 1)[0]


def _stick(raw: int) -> int:
    """The smallest common USB stick size (GB) the image fits on (sticks hold ~93%)."""
    return next(g for g in (4, 8, 16, 32, 64, 128) if g * 10**9 * 0.93 >= raw)


def _mib(n: int) -> str:
    return f"{n / (1 << 30):.1f} GB" if n >= 1 << 30 else f"{n >> 20} MB"


def render_index(site: Path, key_file: str) -> str:
    current, past = listed(site)
    if not current:
        cards = "<p>No images published yet.</p>"
    else:
        cards = "\n".join(
            f'''<div class="card"><h3>{html.escape(e["name"])} <span class="v">{_line(e["version"])}</span></h3>
<p>{EDITIONS[e["image"]][1]}</p>
<p><a class="btn" href="{e["version"]}/{e["file"]}">Download {html.escape(e["name"])}</a>
<span class="m">{_mib(e["size"])} download · {_mib(e["raw_size"])} written · needs {"an" if _stick(e["raw_size"]) == 8 else "a"} {_stick(e["raw_size"])} GB+ stick</span></p>
<p class="m mono">build {e["version"]} · sha256 {e["sha256"]} · <a href="{e["version"]}/SHA256SUMS">SHA256SUMS</a></p></div>''' for e in current)
    older = "".join(
        f'<li><a href="{r["version"]}/">{r["version"]}</a> ({r["published"][:10]})</li>' for r in past)
    v = current[0]["version"] if current else "YY.MM.N"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Download PengyOS</title>
<link rel="icon" type="image/png" href="/favicon.png">
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; color: #1f2328;
  background: #fff; max-width: 820px; margin: 0 auto; padding: 32px 16px 64px; line-height: 1.6; }}
a {{ color: #0f766e; }} header {{ display: flex; align-items: center; gap: 14px; }}
header img {{ width: 64px; height: 64px; }} h1 {{ font-size: 1.7rem; margin: 0; }}
h2 {{ font-size: 1.25rem; margin: 36px 0 8px; border-top: 1px solid #d0d7de; padding-top: 24px; }}
.lead {{ color: #656d76; }} .card {{ border: 1px solid #d0d7de; border-radius: 8px; padding: 14px 18px; margin: 12px 0; background: #f6f8fa; }}
.card h3 {{ margin: 0 0 4px; font-size: 1.1rem; }} .card p {{ margin: 6px 0; }} .v {{ color: #656d76; font-weight: 400; }}
.btn {{ display: inline-block; background: #0f766e; color: #fff; padding: 6px 14px; border-radius: 6px; font-weight: 600; text-decoration: none; margin-right: 10px; }}
.m {{ color: #656d76; font-size: .85rem; }} .mono {{ font-family: monospace; font-size: .75rem; word-break: break-all; }}
pre {{ background: #f0f2f4; padding: 10px 14px; border-radius: 6px; overflow-x: auto; font-size: .85rem; }}
code {{ font-size: .9em; }} .warn {{ border-left: 4px solid #9a6700; background: #fff8c5; padding: 8px 14px; border-radius: 4px; }}
li {{ margin: 4px 0; }}
</style></head><body>
<header><a href="/"><img src="/pengy.png" alt="PengyOS"></a><h1>Download PengyOS</h1></header>
<p class="lead">PengyOS is early, unfinished software. Try it on a spare computer or disk first —
installing <strong>erases the whole disk</strong> you install to.</p>

<h2>1. Pick an edition</h2>
{cards}
<p class="m">Each edition is updated on its own, and each image here is the newest for its edition: a fix
that only touches the Desktop does not make a new Server, so build numbers can differ.</p>
<p class="m">Each release lists its images in a SHA256SUMS file, signed by the PengyOS release key
(<code>SHA256SUMS.sig</code> beside it; <a href="{key_file}">public key</a>). Installed machines get
updates from <a href="/update/">the update feed</a>.</p>
{f'<div class="m">Older releases: <ul>{older}</ul></div>' if older else ''}

<h2>2. What you need</h2>
<ul>
<li>A 64-bit PC (x86-64) that starts in <strong>UEFI</strong> mode — anything from the last ten years.</li>
<li><strong>Secure Boot turned off</strong> in the firmware settings. PengyOS is not signed for Secure Boot yet.</li>
<li>A USB stick at least as big as the edition says (everything on it is replaced).</li>
<li>For installing: a disk of 20 GB or more that may be erased.</li>
</ul>

<h2>3. Write it to a USB stick</h2>
<p><strong>Windows, macOS or Linux:</strong> <a href="https://etcher.balena.io/">balenaEtcher</a> or
<a href="https://www.raspberrypi.com/software/">Raspberry Pi Imager</a> (“Use custom”) — choose the
downloaded <code>.img.xz</code> as it is (no need to unpack it), choose the stick, write.
<strong>Windows:</strong> <a href="https://rufus.ie/">Rufus</a> works too (write in DD image mode).
<strong>Linux:</strong> GNOME Disks → the stick → ⋮ → <em>Restore Disk Image…</em>.</p>
<p>From a Linux terminal (replace <code>/dev/sdX</code> with the stick — check with <code>lsblk</code>; the wrong
letter erases the wrong disk):</p>
<pre>sha256sum -c SHA256SUMS --ignore-missing          # check the download
sudo wipefs -a /dev/sdX                            # clear what was on the stick
xz -dc pengyos-desktop-{v}-x86_64.img.xz | sudo dd of=/dev/sdX bs=4M conv=fsync status=progress
sudo sgdisk -e /dev/sdX                            # move the backup partition table to the stick's end</pre>
<p class="warn">If the computer does not list the stick in its boot menu, the stick probably still carries
an old partition table from earlier use, which some firmware refuses. Clear it first (the
<code>wipefs</code> and <code>sgdisk -e</code> lines above, or delete all its partitions in your
disk tool) and write it again.</p>

<h2>4. Start from the stick</h2>
<ol>
<li>Plug the stick in and switch the computer on.</li>
<li>Open the one-time boot menu straight away — usually <kbd>F12</kbd> (Dell, Lenovo, Acer),
<kbd>F11</kbd> (MSI, ASRock), <kbd>F8</kbd> (ASUS), <kbd>F9</kbd> (HP), <kbd>Esc</kbd> or <kbd>F7</kbd>
on others — and pick the entry that starts with <strong>UEFI</strong> and names the stick.</li>
<li><strong>Desktop</strong> starts a live desktop: try it, then click <strong>Install PengyOS</strong>,
pick the disk, choose a computer name, user name and password, and restart when it says so (take the stick
out). <strong>Server</strong> shows its installer on screen and asks the same questions.
<strong>Core</strong> simply runs from the stick — write it to an internal disk the same way to keep it.</li>
</ol>

<h2>5. After installing</h2>
<p>PengyOS checks for updates every day. On the Desktop, <strong>PengyOS Updates</strong> tells you when one
is ready (you choose whether and when); on a Server, <code>sudo pengyos-update apply</code> installs it.
An update goes into a second system slot and only becomes the default once it has started cleanly —
if it does not, the computer goes back to the previous version by itself.</p>
<p class="m"><a href="/">← pengyos.catbee.ca</a></p>
</body></html>
"""


def refresh_index(site: Path) -> Path | None:
    """Rewrite index.html after a promotion, with the key file download-publish left there."""
    keys = sorted((site / "download").glob("pengyos-release-*.pub"))
    return write_index(site, key_file=keys[-1].name) if keys else None


def write_index(site: Path, key_file: str) -> Path:
    out = site / "download" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name("index.html.new")
    tmp.write_text(render_index(site, key_file), encoding="utf-8")
    os.replace(tmp, out)
    return out
