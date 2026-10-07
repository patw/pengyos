"""The public update feed: publish (signed), promote, feed + index."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET

import pytest

from pengyos_release.cli import main
from pengyos_release.osupdate import (
    NS,
    UpdatePublishError,
    promote,
    publish_update,
    render_feed,
    sign_bytes,
    write_feed,
)

crypto = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ATOM = "http://www.w3.org/2005/Atom"


@pytest.fixture
def key(tmp_path):
    k = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    keyid = hashlib.sha256(pub).hexdigest()
    path = tmp_path / "release.key"
    path.write_text(f"pengyos-key-v1 ed25519 {keyid} {bytes(range(32)).hex()}\n")
    return path, pub, keyid


def make_build(root, version="26.09.1", image="pengyos-desktop"):
    (root / "target/usr/lib").mkdir(parents=True)
    (root / "target/usr/lib/os-release").write_text(
        f'NAME="PengyOS"\nPRETTY_NAME="PengyOS Desktop 26.09"\nVARIANT="Desktop"\nIMAGE_ID={image}\n'
        f'IMAGE_VERSION="{version}"\nBUILD_ID="abc123"\n')
    pl = root / "images/payload"
    pl.mkdir(parents=True)
    rootfs = b"zstd-compressed-rootfs"
    (pl / "rootfs.ext4.zst").write_bytes(rootfs)
    (root / "images/bzImage").write_bytes(b"kernel")
    (pl / "manifest.json").write_text(json.dumps({
        "format": "pengyos-layout-1", "version": version, "build": "abc123",
        "files": {"rootfs": {"file": "rootfs.ext4.zst", "sha256": hashlib.sha256(rootfs).hexdigest(),
                             "raw_size": 1024, "raw_sha256": "ab" * 32}}}))
    return root


def test_sign_bytes_is_pengyos_sig_v1(key):
    path, pub, keyid = key
    blob = sign_bytes(b"hello", path)
    head, _, sig = blob.partition(b"\n")
    digest = hashlib.sha256(b"hello").digest()
    assert head.decode() == f"pengyos-sig-v1 1 ed25519 {keyid} sha256 {digest.hex()}"
    Ed25519PublicKey.from_public_bytes(pub).verify(sig, digest)  # raises if bad


def test_sign_rejects_mismatched_keyid(tmp_path):
    p = tmp_path / "bad.key"
    p.write_text(f"pengyos-key-v1 ed25519 {'00' * 32} {bytes(range(32)).hex()}\n")
    with pytest.raises(UpdatePublishError, match="key id"):
        sign_bytes(b"x", p)


def test_publish_promote_and_feed(tmp_path, key):
    path, pub, _ = key
    build = make_build(tmp_path / "out")
    site = tmp_path / "site"
    p = publish_update(build, site, path, channel="testing", notes="First update.\n\nSecond para.")
    d = site / "update/pengyos-desktop/26.09.1"
    assert p.path == d
    assert {f.name for f in d.iterdir()} == {"rootfs.ext4.zst", "bzImage", "manifest.json",
                                            "manifest.json.sig", "release.json"}
    data = (d / "manifest.json").read_bytes()
    man = json.loads(data)
    assert man["format"] == "pengyos-update-1" and man["image"] == "pengyos-desktop"
    assert man["name"] == "PengyOS Desktop"
    assert man["files"]["kernel"]["sha256"] == hashlib.sha256(b"kernel").hexdigest()
    assert man["files"]["rootfs"]["raw_size"] == 1024
    sig = (d / "manifest.json.sig").read_bytes().partition(b"\n")[2]
    Ed25519PublicKey.from_public_bytes(pub).verify(sig, hashlib.sha256(data).digest())

    with pytest.raises(UpdatePublishError, match="already published"):
        publish_update(build, site, path)

    root = ET.fromstring(render_feed(site))
    (entry,) = root.findall(f"{{{ATOM}}}entry")
    assert entry.findtext(f"{{{NS}}}image") == "pengyos-desktop"
    assert entry.findtext(f"{{{NS}}}version") == "26.09.1"
    assert [c.text for c in entry.findall(f"{{{NS}}}channel")] == ["testing"]
    assert entry.findtext(f"{{{NS}}}manifest") == "pengyos-desktop/26.09.1/manifest.json"
    assert entry.findtext(f"{{{NS}}}notes") == "First update.\n\nSecond para."

    promote(site, "pengyos-desktop", "26.09.1", "stable")
    root = ET.fromstring(render_feed(site))
    assert [c.text for c in root.iter(f"{{{NS}}}channel")] == ["testing", "stable"]
    written = write_feed(site)
    assert [w.name for w in written] == ["feed.xml", "index.html"]
    assert "Second para." in (site / "update/index.html").read_text()


def test_publish_refuses_payload_version_mismatch(tmp_path, key):
    build = make_build(tmp_path / "out")
    m = json.loads((build / "images/payload/manifest.json").read_text())
    m["version"] = "26.09.0"
    (build / "images/payload/manifest.json").write_text(json.dumps(m))
    with pytest.raises(UpdatePublishError, match="rebuild"):
        publish_update(build, tmp_path / "site", key[0])


def test_cli_update_publish(tmp_path, key, capsys):
    build = make_build(tmp_path / "out")
    rc = main(["update-publish", "--build", str(build), "--site", str(tmp_path / "site"),
               "--key", str(key[0]), "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["version"] == "26.09.1"
    assert (tmp_path / "site/update/feed.xml").is_file()
    rc = main(["update-promote", "--site", str(tmp_path / "site"), "--image", "pengyos-desktop",
               "--release", "26.09.1", "--channel", "stable"])
    assert rc == 0


def test_download_publish(tmp_path, key):
    from pengyos_release.downloads import publish_downloads, _stick

    def build(name, image, img_name, variant):
        b = make_build(tmp_path / name, image=image)
        (b / "target/usr/lib/os-release").write_text(
            f'NAME="PengyOS"\nVARIANT="{variant}"\nIMAGE_ID={image}\nIMAGE_VERSION="26.09.1"\nBUILD_ID="x"\n')
        (b / "images" / img_name).write_bytes(b"disk image " * 100)
        return b
    desk = build("d", "pengyos-desktop", "pengyos-desktop-x86_64-installer.img", "Desktop")
    (desk / "images/pengyos-desktop-x86_64.img").write_bytes(b"live only")
    core = build("c", "pengyos-core", "pengyos-core-x86_64.img", "Core")
    site = tmp_path / "site"
    dest = publish_downloads([core, desk], site, key[0])
    listing = json.loads((dest / "downloads.json").read_text())
    assert [e["image"] for e in listing["images"]] == ["pengyos-desktop", "pengyos-core"]
    assert listing["images"][0]["kind"] == "installer" and listing["images"][1]["kind"] == "disk"
    import lzma
    assert lzma.decompress((dest / "pengyos-desktop-26.09.1-x86_64.img.xz").read_bytes()) == b"disk image " * 100
    sums = (dest / "SHA256SUMS").read_text()
    assert "pengyos-core-26.09.1-x86_64.img.xz" in sums
    sig = (dest / "SHA256SUMS.sig").read_bytes().partition(b"\n")[2]
    Ed25519PublicKey.from_public_bytes(key[1]).verify(sig, hashlib.sha256(sums.encode()).digest())
    page = (site / "download/index.html").read_text()
    assert "Secure Boot" in page
    assert "pengyos-desktop-26.09.1-x86_64.img.xz" not in page  # testing until promoted
    publish_update(desk, site, key[0], channel="testing")
    promote(site, "pengyos-desktop", "26.09.1", "stable")
    from pengyos_release.downloads import refresh_index
    refresh_index(site)
    page = (site / "download/index.html").read_text()
    assert "26.09.1/pengyos-desktop-26.09.1-x86_64.img.xz" in page
    assert "26.09.1/pengyos-core-26.09.1-x86_64.img.xz" in page  # Core rides with its version
    assert list((site / "download").glob("pengyos-release-*.pub"))
    assert _stick(6 * 10**9) == 8 and _stick(1_400_000_000) == 4
    # a mixed-version set is refused
    other = build("s", "pengyos-server", "pengyos-server-x86_64-installer.img", "Server")
    (other / "target/usr/lib/os-release").write_text('IMAGE_ID=pengyos-server\nIMAGE_VERSION="26.09.2"\n')
    with pytest.raises(UpdatePublishError, match="one release at a time"):
        publish_downloads([desk, other], site, key[0])


def test_download_page_lists_newest_stable_per_edition(tmp_path, key):
    """A testing release stays off the page; each edition shows its own newest stable."""
    from pengyos_release.downloads import publish_downloads

    site = tmp_path / "site"

    def release(image, version, channel):
        b = make_build(tmp_path / f"{image}-{version}", version=version, image=image)
        (b / "images" / f"{image}-x86_64-installer.img").write_bytes(b"img")
        publish_update(b, site, key[0], channel="testing")
        if channel == "stable":
            promote(site, image, version, "stable")
        publish_downloads([b], site, key[0])

    release("pengyos-server", "26.09.1", "stable")
    release("pengyos-desktop", "26.09.1", "stable")
    release("pengyos-desktop", "26.09.2", "testing")
    page = (site / "download/index.html").read_text()
    assert "26.09.1/pengyos-desktop-26.09.1-x86_64.img.xz" in page
    assert "26.09.2" not in page
    assert "26.09.1/pengyos-server-26.09.1-x86_64.img.xz" in page

    assert main(["update-promote", "--site", str(site), "--image", "pengyos-desktop",
                 "--release", "26.09.2", "--channel", "stable"]) == 0
    page = (site / "download/index.html").read_text()
    assert "26.09.2/pengyos-desktop-26.09.2-x86_64.img.xz" in page
    assert "26.09.1/pengyos-server-26.09.1-x86_64.img.xz" in page  # Server keeps its own newest
    assert "26.09.1/pengyos-desktop-26.09.1-x86_64.img.xz" not in page
    # both cards lead with the release line; the build is small print
    assert page.count('<span class="v">26.09</span>') == 2
    assert "build 26.09.2" in page and "build 26.09.1" in page
