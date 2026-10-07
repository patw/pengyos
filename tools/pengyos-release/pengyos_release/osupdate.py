"""The public update feed that installed machines read (``pengyos-update``).

Layout (``<site>`` is the web root; ``/update`` is what pengyos.catbee.ca serves)::

    <site>/update/
        feed.xml                      Atom feed: one entry per release (people + machines)
        index.html                    the same, for a browser
        <image>/<version>/            image = os-release IMAGE_ID, e.g. pengyos-desktop
            manifest.json             format pengyos-update-1: files + sha256s (signed)
            manifest.json.sig         pengyos-sig-v1 (ed25519 over sha256), pengyos-sign's format
            rootfs.ext4.zst           the root filesystem, for a root slot
            bzImage                   its kernel, for ESP/slot-X/
            release.json              channels, date, notes (NOT signed: presentation only)

The feed only announces; machines trust ``manifest.json`` alone, checked with
the public key their image ships. Channels (stable, testing) live in
``release.json`` and the feed, so promoting testing -> stable never re-signs.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import html
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from .errors import PengyOsError
from .manifest import parse_version

MANIFEST_FORMAT = "pengyos-update-1"
NS = "https://pengyos.catbee.ca/ns/update/1"
CHANNELS = ("stable", "testing")
DEFAULT_BASE_URL = "https://pengyos.catbee.ca/update/"


class UpdatePublishError(PengyOsError):
    """A release could not be published to the update feed."""


# --------------------------------------------------------------------------
# Signing: pengyos-sign's key and signature formats, ed25519 via `cryptography`
# --------------------------------------------------------------------------

def _private_key(path: Path):
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError:  # pragma: no cover - host dependency
        raise UpdatePublishError("signing needs the Python 'cryptography' package on the build host")
    parts = path.read_text(encoding="ascii").split()
    if len(parts) != 4 or parts[0] != "pengyos-key-v1" or parts[1] != "ed25519":
        raise UpdatePublishError(f"{path} is not a pengyos-sign private key")
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(parts[3]))
    from cryptography.hazmat.primitives import serialization
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    keyid = hashlib.sha256(pub).hexdigest()
    if keyid != parts[2].lower():
        raise UpdatePublishError(f"{path}: key id does not match the key (damaged file?)")
    return key, keyid


def sign_bytes(data: bytes, key_path: Path) -> bytes:
    """A ``pengyos-sig-v1`` detached signature, byte-identical to ``pengyos-sign sign``."""
    key, keyid = _private_key(key_path)
    digest = hashlib.sha256(data).digest()
    header = f"pengyos-sig-v1 1 ed25519 {keyid} sha256 {digest.hex()}\n".encode("ascii")
    return header + key.sign(digest)


# --------------------------------------------------------------------------
# Publish / promote
# --------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while b := fh.read(4 << 20):
            h.update(b)
    return h.hexdigest()


def _os_release(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v.strip().strip('"')
    return out


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Published:
    image: str
    version: str
    path: Path


def publish_update(build: Path, site: Path, key: Path, channel: str = "testing",
                   notes: str = "", force: bool = False) -> Published:
    """Turn a Buildroot output dir (``target/`` + ``images/payload``) into a release."""
    if channel not in CHANNELS:
        raise UpdatePublishError(f"unknown channel {channel!r}")
    osr = _os_release(build / "target" / "usr" / "lib" / "os-release")
    image, version = osr.get("IMAGE_ID", ""), osr.get("IMAGE_VERSION", "")
    if not image or not version:
        raise UpdatePublishError(f"{build}: os-release has no IMAGE_ID / IMAGE_VERSION")
    parse_version(version)
    images = build / "images"
    payload = json.loads((images / "payload" / "manifest.json").read_text())
    if payload.get("version") != version:
        raise UpdatePublishError(
            f"the install payload is {payload.get('version')!r} but the image says {version!r} — rebuild")
    rootfs_src = images / "payload" / payload["files"]["rootfs"]["file"]
    kernel_src = images / "bzImage"
    dest = site / "update" / image / version
    if dest.exists() and not force:
        raise UpdatePublishError(f"{image} {version} is already published ({dest}); bump VERSION")
    dest.mkdir(parents=True, exist_ok=True)
    for src in (rootfs_src, kernel_src):
        shutil.copyfile(src, dest / src.name)
    rootfs = payload["files"]["rootfs"]
    if _sha256(dest / rootfs_src.name) != rootfs["sha256"]:
        raise UpdatePublishError("the copied rootfs does not match the payload manifest")
    manifest = {
        "format": MANIFEST_FORMAT,
        "image": image,
        "version": version,
        "build": osr.get("BUILD_ID", payload.get("build", "")),
        "name": " ".join(filter(None, (osr.get("NAME", "PengyOS"), osr.get("VARIANT")))),
        "files": {
            "rootfs": {"file": rootfs_src.name, "size": (dest / rootfs_src.name).stat().st_size,
                       "sha256": rootfs["sha256"], "raw_size": rootfs["raw_size"],
                       "raw_sha256": rootfs["raw_sha256"]},
            "kernel": {"file": kernel_src.name, "size": (dest / kernel_src.name).stat().st_size,
                       "sha256": _sha256(dest / kernel_src.name)},
        },
    }
    data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (dest / "manifest.json").write_bytes(data)
    (dest / "manifest.json.sig").write_bytes(sign_bytes(data, key))
    release = {"image": image, "version": version, "name": manifest["name"],
               "channels": [channel], "published": _now(), "notes": notes}
    (dest / "release.json").write_text(json.dumps(release, indent=2) + "\n")
    return Published(image, version, dest)


def promote(site: Path, image: str, version: str, channel: str) -> None:
    if channel not in CHANNELS:
        raise UpdatePublishError(f"unknown channel {channel!r}")
    rj = site / "update" / image / version / "release.json"
    if not rj.is_file():
        raise UpdatePublishError(f"{image} {version} is not published")
    rel = json.loads(rj.read_text())
    if channel not in rel["channels"]:
        rel["channels"].append(channel)
        rel.setdefault("promoted", {})[channel] = _now()
    rj.write_text(json.dumps(rel, indent=2) + "\n")


# --------------------------------------------------------------------------
# Feed + index page
# --------------------------------------------------------------------------

def _releases(site: Path) -> list[dict]:
    rels = []
    for rj in (site / "update").glob("*/*/release.json"):
        rel = json.loads(rj.read_text())
        rels.append(rel)
    rels.sort(key=lambda r: (r.get("published", ""), parse_version(r["version"])), reverse=True)
    return rels


def _anchor(r: dict) -> str:
    return f"{r['image']}-{r['version']}"


def render_feed(site: Path, base_url: str = DEFAULT_BASE_URL) -> str:
    rels = _releases(site)
    updated = rels[0]["published"] if rels else _now()
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:pengyos="{NS}">',
        "  <title>PengyOS releases</title>",
        "  <subtitle>New PengyOS system images. Installed machines read this feed; "
        "every release is signed.</subtitle>",
        f"  <id>{escape(base_url)}</id>",
        f'  <link rel="self" type="application/atom+xml" href={quoteattr(base_url + "feed.xml")}/>',
        f'  <link rel="alternate" type="text/html" href={quoteattr(base_url)}/>',
        f"  <updated>{updated}</updated>",
        "  <author><name>PengyOS</name><uri>https://pengyos.catbee.ca/</uri></author>",
    ]
    for r in rels:
        rel_dir = f"{r['image']}/{r['version']}/"
        url = base_url + rel_dir
        chans = ", ".join(r["channels"])
        body = f"<p><strong>{html.escape(r.get('name', r['image']))}</strong> — channels: {html.escape(chans)}</p>"
        if r.get("notes"):
            body += "".join(f"<p>{html.escape(p.strip())}</p>" for p in r["notes"].split("\n\n") if p.strip())
        out += [
            "  <entry>",
            f"    <title>{escape(r.get('name', r['image']))} {escape(r['version'])}</title>",
            f"    <id>urn:pengyos:{escape(r['image'])}:{escape(r['version'])}</id>",
            f"    <updated>{r.get('promoted', {}).get('stable', r['published'])}</updated>",
            f"    <published>{r['published']}</published>",
            f'    <link rel="alternate" type="text/html" href={quoteattr(base_url + "#" + _anchor(r))}/>',
            f'    <link rel="enclosure" type="application/json" href={quoteattr(url + "manifest.json")}/>',
            *[f"    <category term={quoteattr(t)}/>" for t in [r["image"], *r["channels"]]],
            f"    <pengyos:image>{escape(r['image'])}</pengyos:image>",
            f"    <pengyos:version>{escape(r['version'])}</pengyos:version>",
            *[f"    <pengyos:channel>{escape(c)}</pengyos:channel>" for c in r["channels"]],
            f"    <pengyos:manifest>{escape(rel_dir + 'manifest.json')}</pengyos:manifest>",
            *([f"    <pengyos:notes>{escape(r['notes'].strip())}</pengyos:notes>"] if r.get("notes") else []),
            f'    <content type="html">{escape(body)}</content>',
            "  </entry>",
        ]
    out.append("</feed>")
    return "\n".join(out) + "\n"


def render_index(site: Path, base_url: str = DEFAULT_BASE_URL) -> str:
    rows = []
    for r in _releases(site):
        d = f"{r['image']}/{r['version']}/"
        badges = " ".join(f'<span class="b {c}">{c}</span>' for c in r["channels"])
        notes = "".join(f"<p>{html.escape(p.strip())}</p>" for p in r.get("notes", "").split("\n\n") if p.strip())
        rows.append(
            f'<article id="{_anchor(r)}"><h2>{html.escape(r.get("name", r["image"]))} {html.escape(r["version"])} {badges}</h2>'
            f'<p class="m"><code>{html.escape(r["image"])}</code> · published {html.escape(r["published"][:10])} · '
            f'<a href="{d}manifest.json">manifest</a> · <a href="{d}manifest.json.sig">signature</a></p>{notes}</article>')
    body = "\n".join(rows) or "<p>No releases yet.</p>"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PengyOS updates</title>
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="alternate" type="application/atom+xml" title="PengyOS releases" href="feed.xml">
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; color: #1f2328;
  background: #fff; max-width: 760px; margin: 0 auto; padding: 32px 16px; line-height: 1.6; }}
a {{ color: #0f766e; }} header {{ display: flex; align-items: center; gap: 14px; margin-bottom: 8px; }}
header img {{ width: 56px; height: 56px; }} h1 {{ font-size: 1.6rem; margin: 0; }}
.lead {{ color: #656d76; }} article {{ border: 1px solid #d0d7de; border-radius: 8px; padding: 14px 18px; margin: 14px 0; background: #f6f8fa; }}
h2 {{ font-size: 1.1rem; margin: 0 0 4px; }} .m {{ color: #656d76; font-size: .85rem; margin: 0 0 6px; }}
.b {{ font-size: .7rem; font-weight: 700; border-radius: 10px; padding: 2px 8px; vertical-align: middle; }}
.stable {{ background: #dafbe1; color: #116329; }} .testing {{ background: #fff8c5; color: #7d4e00; }}
code {{ font-size: .85em; }} pre {{ background: #f0f2f4; padding: 10px 14px; border-radius: 6px; overflow-x: auto; }}
</style></head><body>
<header><a href="/"><img src="/pengy.png" alt="PengyOS"></a><h1>PengyOS updates</h1></header>
<p class="lead">Every PengyOS release, newest first. Subscribe to <a href="feed.xml">the feed</a> in any feed reader.
Installed machines read the same feed and check each release's signature before installing it.</p>
<pre>pengyos-update check            # is there something new?
sudo pengyos-update apply --reboot
sudo pengyos-update channel testing   # follow testing builds too</pre>
{body}
</body></html>
"""


def write_feed(site: Path, base_url: str = DEFAULT_BASE_URL) -> list[Path]:
    upd = site / "update"
    upd.mkdir(parents=True, exist_ok=True)
    out = []
    for name, text in (("feed.xml", render_feed(site, base_url)), ("index.html", render_index(site, base_url))):
        tmp = upd / (name + ".new")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, upd / name)
        out.append(upd / name)
    return out
