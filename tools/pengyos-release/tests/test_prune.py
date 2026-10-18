"""prune: delete superseded testing releases from update/ and download/."""

from __future__ import annotations

import json

import pytest

from pengyos_release.cli import main
from pengyos_release.prune import PruneError, apply, plan


def release(site, image, version, channels=("testing",)):
    d = site / "update" / image / version
    d.mkdir(parents=True)
    (d / "manifest.json").write_text("{}")
    (d / "release.json").write_text(json.dumps({
        "image": image, "version": version, "name": image, "channels": list(channels),
        "published": f"2026-10-{int(version.rsplit('.', 1)[1]):02d}T00:00:00Z", "notes": ""}))


def download(site, version, images):
    d = site / "download" / version
    d.mkdir(parents=True)
    (d / "downloads.json").write_text(json.dumps({
        "version": version, "published": "2026-10-01T00:00:00Z",
        "images": [{"image": i, "name": i, "file": f"{i}-{version}-x86_64.img.xz", "size": 1,
                    "raw_size": 1, "sha256": "00", "kind": "disk", "build": "x"} for i in images]}))
    (site / "download" / "pengyos-release-abcd1234.pub").write_text("pub\n")


ALL = ["pengyos-desktop", "pengyos-server", "pengyos-core"]


@pytest.fixture
def site(tmp_path):
    s = tmp_path / "site"
    release(s, "pengyos-desktop", "26.10.4", ("testing", "stable"))
    release(s, "pengyos-desktop", "26.10.5")            # superseded testing
    release(s, "pengyos-desktop", "26.10.6", ("testing", "stable"))
    release(s, "pengyos-desktop", "26.10.7")            # superseded testing
    release(s, "pengyos-desktop", "26.10.8")            # newest: testing head
    release(s, "pengyos-server", "26.10.4", ("testing", "stable"))
    release(s, "pengyos-server", "26.10.7")             # newest for Server
    for v in ("26.10.4", "26.10.5", "26.10.6", "26.10.7", "26.10.8"):
        download(s, v, ALL)
    return s


def test_plan_keeps_stable_and_newest(site):
    p = plan(site)
    assert p.kept == {"pengyos-desktop": ["26.10.4", "26.10.6", "26.10.8"],
                      "pengyos-server": ["26.10.4", "26.10.7"]}
    assert p.pruned == {"pengyos-desktop": ["26.10.5", "26.10.7"]}
    # 26.10.7 downloads stay: Server 26.10.7 is still Server's newest.
    assert p.downloads_pruned == ["26.10.5"]
    assert p.downloads_kept == ["26.10.4", "26.10.6", "26.10.7", "26.10.8"]


def test_versions_sort_numerically(tmp_path):
    s = tmp_path / "site"
    release(s, "pengyos-desktop", "26.10.9")
    release(s, "pengyos-desktop", "26.10.10")
    assert plan(s).pruned == {"pengyos-desktop": ["26.10.9"]}


def test_keep_stable_caps_history_but_not_the_newest(site):
    p = plan(site, keep_stable=1)
    assert p.kept["pengyos-desktop"] == ["26.10.6", "26.10.8"]
    assert p.kept["pengyos-server"] == ["26.10.4", "26.10.7"]  # its only stable stays
    with pytest.raises(PruneError):
        plan(site, keep_stable=0)


def test_core_only_download_dir_follows_editions(tmp_path):
    s = tmp_path / "site"
    release(s, "pengyos-desktop", "26.10.2", ("testing", "stable"))
    release(s, "pengyos-desktop", "26.10.3")
    download(s, "26.10.1", ["pengyos-core"])   # nothing of 26.10.1 is kept
    download(s, "26.10.2", ["pengyos-core"])   # Core rides along with Desktop 26.10.2
    download(s, "26.10.3", ["pengyos-core"])   # newest download dir
    assert plan(s).downloads_pruned == ["26.10.1"]


def test_half_published_release_is_an_error(site):
    (site / "update/pengyos-server/26.10.7/release.json").unlink()
    with pytest.raises(PruneError, match="half published"):
        plan(site)


def test_apply_deletes_and_rewrites_listings(site):
    removed = apply(site, plan(site))
    assert sorted(p.name for p in removed) == ["26.10.5", "26.10.5", "26.10.7"]
    assert not (site / "update/pengyos-desktop/26.10.5").exists()
    assert (site / "update/pengyos-server/26.10.7").is_dir()
    assert not (site / "download/26.10.5").exists()
    feed = (site / "update/feed.xml").read_text()
    assert "pengyos-desktop:26.10.8" in feed and "pengyos-desktop:26.10.5" not in feed
    assert (site / "download/index.html").is_file()
    assert plan(site).pruned == {}  # idempotent


def test_cli_dry_run_changes_nothing(site, capsys):
    assert main(["prune", "--site", str(site)]) == 0
    out = capsys.readouterr().out
    assert "would remove" in out and "dry run" in out
    assert (site / "update/pengyos-desktop/26.10.5").is_dir()


def test_cli_apply_json(site, capsys):
    assert main(["prune", "--site", str(site), "--apply", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is False
    assert result["pruned"] == {"pengyos-desktop": ["26.10.5", "26.10.7"]}
    assert len(result["removed"]) == 3


def test_cli_empty_site_is_an_error(tmp_path, capsys):
    assert main(["prune", "--site", str(tmp_path)]) == 2
    assert "neither update/ nor download/" in capsys.readouterr().err
