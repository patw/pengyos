"""The update feed, release manifests and their signatures (no system access).

Trust model: the feed (Atom, ``feed.xml``) only *announces* releases; nothing
in it is trusted. Each release has a ``manifest.json`` with a detached
``manifest.json.sig`` (``pengyos-sig-v1``, the format ``pengyos-sign`` writes:
ed25519 over the SHA-256 of the file). The manifest is checked against the
public keys the image ships (``/usr/share/pengyos/keys/*.pub``), and every file
it names is checked against the SHA-256 it records. So the web server, a
mirror or the network can withhold updates, but cannot change one.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ATOM = "http://www.w3.org/2005/Atom"
NS = "https://pengyos.catbee.ca/ns/update/1"
MANIFEST_FORMAT = "pengyos-update-1"
SIG_MAGIC = "pengyos-sig-v1"
KEY_MAGIC = "pengyos-key-v1"
# Which channels a machine on channel X accepts: testing also takes stable.
CHANNELS = {"stable": ("stable",), "testing": ("testing", "stable")}
# DER prefix of an Ed25519 SubjectPublicKeyInfo (RFC 8410) — the 32 raw key
# bytes follow it; openssl reads the result as a public key.
_ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


class UpdateError(Exception):
    """Anything that stops an update; the message is for the user."""


def parse_version(text: str) -> tuple[int, ...]:
    """``YY.MM.N`` (any dotted integers) -> comparable tuple."""
    try:
        parts = tuple(int(p) for p in text.strip().split("."))
    except ValueError:
        raise UpdateError(f"not a version: {text!r}") from None
    if not parts or any(p < 0 for p in parts):
        raise UpdateError(f"not a version: {text!r}")
    return parts


# --------------------------------------------------------------------------
# Feed
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class FeedEntry:
    image: str
    version: str
    channels: tuple[str, ...]
    manifest_url: str
    title: str = ""
    notes: str = ""


def parse_feed(xml_bytes: bytes) -> list[FeedEntry]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise UpdateError(f"the update feed is not valid XML ({exc})") from None
    entries = []
    for e in root.iter(f"{{{ATOM}}}entry"):
        image = (e.findtext(f"{{{NS}}}image") or "").strip()
        version = (e.findtext(f"{{{NS}}}version") or "").strip()
        manifest = (e.findtext(f"{{{NS}}}manifest") or "").strip()
        channels = tuple(c.text.strip() for c in e.findall(f"{{{NS}}}channel") if c.text)
        if not (image and version and manifest and channels):
            continue  # an entry for people only (news), or malformed: ignore
        try:
            parse_version(version)
        except UpdateError:
            continue
        entries.append(FeedEntry(image, version, channels, manifest,
                                 (e.findtext(f"{{{ATOM}}}title") or "").strip(),
                                 (e.findtext(f"{{{NS}}}notes") or "").strip()))
    return entries


def pick_update(entries: list[FeedEntry], image: str, channel: str,
                current: str) -> FeedEntry | None:
    """The newest release for ``image`` on ``channel`` newer than ``current``."""
    accept = CHANNELS.get(channel)
    if accept is None:
        raise UpdateError(f"unknown channel {channel!r} (use one of: {', '.join(CHANNELS)})")
    cur = parse_version(current)
    best = None
    for e in entries:
        if e.image != image or not set(e.channels) & set(accept):
            continue
        if parse_version(e.version) <= cur:
            continue
        if best is None or parse_version(e.version) > parse_version(best.version):
            best = e
    return best


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class FileRef:
    file: str
    size: int
    sha256: str
    raw_size: int = 0
    raw_sha256: str = ""


@dataclass(frozen=True)
class Manifest:
    image: str
    version: str
    build: str
    rootfs: FileRef
    kernel: FileRef


def parse_manifest(data: bytes) -> Manifest:
    try:
        m = json.loads(data)
        if m.get("format") != MANIFEST_FORMAT:
            raise UpdateError(f"unknown manifest format {m.get('format')!r}")
        files = m["files"]

        def ref(key: str, raw: bool) -> FileRef:
            f = files[key]
            name = str(f["file"])
            if "/" in name or name.startswith("."):
                raise UpdateError(f"manifest names a bad file: {name!r}")
            return FileRef(name, int(f["size"]), str(f["sha256"]).lower(),
                           int(f["raw_size"]) if raw else 0,
                           str(f["raw_sha256"]).lower() if raw else "")

        man = Manifest(str(m["image"]), str(m["version"]), str(m.get("build", "")),
                       ref("rootfs", True), ref("kernel", False))
    except (KeyError, TypeError, ValueError) as exc:
        raise UpdateError(f"the release manifest is malformed ({exc})") from None
    parse_version(man.version)
    return man


# --------------------------------------------------------------------------
# Keys and signatures (pengyos-sign's formats)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PublicKey:
    keyid: str
    raw: bytes
    path: str = ""


def parse_public_key(text: str, path: str = "") -> PublicKey:
    parts = text.split()
    if len(parts) != 4 or parts[0] != KEY_MAGIC or parts[1] != "ed25519":
        raise UpdateError(f"not a pengyos public key: {path or text[:40]!r}")
    raw = bytes.fromhex(parts[3])
    if len(raw) != 32 or hashlib.sha256(raw).hexdigest() != parts[2].lower():
        raise UpdateError(f"public key {path!r} is damaged (key id does not match)")
    return PublicKey(parts[2].lower(), raw, path)


def load_keys(directory: str | os.PathLike[str]) -> list[PublicKey]:
    keys = []
    for p in sorted(Path(directory).glob("*.pub")):
        keys.append(parse_public_key(p.read_text(encoding="ascii"), str(p)))
    return keys


def parse_signature(blob: bytes) -> tuple[str, bytes, bytes]:
    """``pengyos-sig-v1`` -> (keyid, sha256 digest, 64-byte signature)."""
    head, sep, sig = blob.partition(b"\n")
    fields = head.decode("ascii", "replace").split()
    if (not sep or len(fields) != 6 or fields[0] != SIG_MAGIC or fields[1] != "1"
            or fields[2] != "ed25519" or fields[4] != "sha256" or len(sig) != 64):
        raise UpdateError("the release signature is malformed")
    return fields[3].lower(), bytes.fromhex(fields[5]), sig


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes,
                   openssl: str = "openssl") -> bool:
    """Verify with the openssl CLI (the image's, so no crypto code of our own)."""
    der = _ED25519_SPKI_PREFIX + public_key
    with tempfile.TemporaryDirectory(prefix="pengyos-update-") as tmp:
        t = Path(tmp)
        (t / "key.der").write_bytes(der)
        (t / "msg").write_bytes(message)
        (t / "sig").write_bytes(signature)
        try:
            r = subprocess.run(
                [openssl, "pkeyutl", "-verify", "-pubin", "-keyform", "DER",
                 "-inkey", str(t / "key.der"), "-rawin", "-in", str(t / "msg"),
                 "-sigfile", str(t / "sig")],
                capture_output=True, text=True)
        except FileNotFoundError:
            raise UpdateError("openssl is missing — cannot check the release signature") from None
    return r.returncode == 0


def verify_signed(data: bytes, sig_blob: bytes, keys: list[PublicKey],
                  openssl: str = "openssl") -> PublicKey:
    """Check ``data`` against its detached signature; return the key that signed it."""
    keyid, digest, sig = parse_signature(sig_blob)
    if hashlib.sha256(data).digest() != digest:
        raise UpdateError("the release manifest does not match its signature (changed in transit?)")
    for key in keys:
        if key.keyid == keyid:
            if ed25519_verify(key.raw, digest, sig, openssl):
                return key
            raise UpdateError("the release signature is NOT valid — refusing this update")
    raise UpdateError(f"the release is signed by an unknown key ({keyid[:16]}…) — refusing it")


def sha256_file(path: str | os.PathLike[str], chunk: int = 4 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while b := fh.read(chunk):
            h.update(b)
    return h.hexdigest()
