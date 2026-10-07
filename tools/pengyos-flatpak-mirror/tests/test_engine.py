"""Engine tests.

Covers :mod:`pengyos_flatpak_mirror.engine`: the sync flow (register the remote
once, mirror every pinned ref), its idempotency, the ``ostree refs`` parser, and
the structured list report. No real flatpak/ostree, network, or root — every
external call goes through an injected fake runner, and manifests are written to
``tmp_path`` fixtures.
"""

import json
from pathlib import Path

import pytest

from pengyos_flatpak_mirror.engine import (
    ListResult,
    SyncResult,
    list_report,
    list_report_dict,
    parse_refs,
    sync,
    sync_from_manifest,
)
from pengyos_flatpak_mirror.manifest import ManifestError, load_manifest
from pengyos_flatpak_mirror.runner import Result, Runner

# A two-app manifest used across the sync tests.
TWO_APP_MANIFEST = """
remote = "flathub"
remote-name = "pengyos"
apps = [
  { id = "org.gnome.Calculator", commit = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef" },
  { id = "org.mozilla.firefox", commit = "f7e6d5c4b3a291807162534324354657", arch = "aarch64", branch = "edge" },
]
collections = ["org.gnome", "org.mozilla"]
"""


def _write(tmp_path, text: str) -> str:
    path = tmp_path / "mirror.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


# --- a fake runner that records argv and returns canned results -------------


class FakeRunner:
    """Records argv and returns a canned :class:`Result`."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return Result(argv=list(argv), returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


# --- sync: the golden flow --------------------------------------------------


def test_sync_adds_remote_then_mirrors_each_app_in_order(tmp_path):
    fake = FakeRunner()
    m = load_manifest(_write(tmp_path, TWO_APP_MANIFEST))
    result = sync(m, Runner(runner=fake))

    assert isinstance(result, SyncResult)
    assert result.remote_name == "pengyos"
    assert result.remote == "flathub"
    # Manifest order preserved, with the app's arch/branch applied.
    assert result.refs_synced == (
        "org.gnome.Calculator/x86_64/stable",
        "org.mozilla.firefox/aarch64/edge",
    )

    # First call registers the remote once; the rest mirror each ref in order.
    assert fake.calls[0] == [
        "flatpak", "remote-add", "--if-not-exists", "pengyos", "flathub",
    ]
    # mirror_ref pins the ref as ``<id>@<arch>/<branch>`` (see runner golden argv).
    assert fake.calls[1] == [
        "flatpak", "update", "--user", "--noninteractive",
        "--commit=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "org.gnome.Calculator@x86_64/stable",
    ]
    assert fake.calls[2] == [
        "flatpak", "update", "--user", "--noninteractive",
        "--commit=f7e6d5c4b3a291807162534324354657",
        "org.mozilla.firefox@aarch64/edge",
    ]


def test_sync_with_no_apps_still_adds_remote(tmp_path):
    fake = FakeRunner()
    m = load_manifest(_write(tmp_path, 'remote = "flathub"\n'))
    result = sync(m, Runner(runner=fake))
    assert result.refs_synced == ()
    assert result.remote_name == "flathub"
    # Only the (idempotent) remote-add call — nothing else ran.
    assert fake.calls == [
        ["flatpak", "remote-add", "--if-not-exists", "flathub", "flathub"],
    ]


def test_sync_passes_logger_lines(tmp_path):
    seen: list[str] = []
    fake = FakeRunner()
    m = load_manifest(_write(tmp_path, TWO_APP_MANIFEST))
    sync(m, Runner(runner=fake, logger=seen.append))
    # One line per call: remote-add + two mirror_refs + two collection pulls.
    assert len(seen) == 5
    assert any("remote-add" in line for line in seen)
    assert any("Calculator" in line for line in seen)
    assert any("firefox" in line for line in seen)
    assert any("collection" in line for line in seen)


# --- sync: idempotency ------------------------------------------------------


def test_sync_is_idempotent_second_run_issues_same_calls(tmp_path):
    fake = FakeRunner()
    runner = Runner(runner=fake)
    m = load_manifest(_write(tmp_path, TWO_APP_MANIFEST))

    sync(m, runner)
    first_run = list(fake.calls)

    fake.calls.clear()
    sync(m, runner)
    second_run = list(fake.calls)

    # Re-running the pinned flow issues the exact same argv sequence — no growth,
    # no branching on prior repo state. That is the idempotency the spec tests.
    assert second_run == first_run
    # remote-add + two mirror_refs + two collection pulls (manifest has 2).
    assert len(first_run) == 5


# --- sync: collections ------------------------------------------------------


def test_sync_pulls_collections_after_apps(tmp_path):
    fake = FakeRunner()
    m = load_manifest(_write(tmp_path, TWO_APP_MANIFEST))
    result = sync(m, Runner(runner=fake))

    # The manifest declares two collections; they are pulled in manifest order,
    # after the two mirror_refs.
    assert result.collections_pulled == ("org.gnome", "org.mozilla")
    assert result.refs_synced == (
        "org.gnome.Calculator/x86_64/stable",
        "org.mozilla.firefox/aarch64/edge",
    )

    assert fake.calls[0] == [
        "flatpak", "remote-add", "--if-not-exists", "pengyos", "flathub",
    ]
    assert fake.calls[1][5] == "org.gnome.Calculator@x86_64/stable"
    assert fake.calls[2][5] == "org.mozilla.firefox@aarch64/edge"
    # pull_collection pins the collection id and the remote name (golden argv).
    assert fake.calls[3] == [
        "flatpak", "update", "--user", "--noninteractive",
        "--collection=org.gnome", "pengyos",
    ]
    assert fake.calls[4] == [
        "flatpak", "update", "--user", "--noninteractive",
        "--collection=org.mozilla", "pengyos",
    ]


def test_sync_with_no_collections_pulls_nothing(tmp_path):
    fake = FakeRunner()
    m = load_manifest(_write(tmp_path, 'remote = "flathub"\n'))
    result = sync(m, Runner(runner=fake))
    assert result.collections_pulled == ()
    # Only the (idempotent) remote-add call — no apps, no collections.
    assert fake.calls == [
        ["flatpak", "remote-add", "--if-not-exists", "flathub", "flathub"],
    ]


def test_sync_from_manifest_pulls_collections(tmp_path):
    path = _write(tmp_path, TWO_APP_MANIFEST)
    fake = FakeRunner()
    result = sync_from_manifest(path, Runner(runner=fake))
    assert result.collections_pulled == ("org.gnome", "org.mozilla")
    # The last two calls are the collection pulls.
    assert fake.calls[-1][4] == "--collection=org.mozilla"


def test_sync_is_idempotent_with_collections(tmp_path):
    fake = FakeRunner()
    runner = Runner(runner=fake)
    m = load_manifest(_write(tmp_path, TWO_APP_MANIFEST))

    sync(m, runner)
    first_run = list(fake.calls)

    fake.calls.clear()
    sync(m, runner)
    second_run = list(fake.calls)

    # Same argv sequence on the second run — including the collection pulls.
    assert second_run == first_run
    assert len(first_run) == 5


def test_sync_from_manifest_rejects_bad_collection(tmp_path):
    # A non-string collection entry is a manifest validation error, raised
    # before any command runs.
    path = _write(tmp_path, 'remote = "flathub"\ncollections = [1, 2]\n')
    with pytest.raises(ManifestError):
        sync_from_manifest(path, Runner(runner=FakeRunner()))


# --- sync: via manifest path (CLI entry) ------------------------------------


def test_sync_from_manifest_loads_and_syncs(tmp_path):
    path = _write(tmp_path, TWO_APP_MANIFEST)
    fake = FakeRunner()
    result = sync_from_manifest(path, Runner(runner=fake))
    assert result.remote_name == "pengyos"
    assert result.refs_synced == (
        "org.gnome.Calculator/x86_64/stable",
        "org.mozilla.firefox/aarch64/edge",
    )
    assert fake.calls[0][1] == "remote-add"


def test_sync_from_manifest_raises_manifest_error(tmp_path):
    with pytest.raises(ManifestError):
        sync_from_manifest(str(tmp_path / "nope.toml"), Runner(runner=FakeRunner()))


def test_sync_from_manifest_rejects_bad_commit(tmp_path):
    path = _write(tmp_path, 'remote = "flathub"\napps = [{ id = "org.x", commit = "not-hex" }]\n')
    with pytest.raises(ManifestError):
        sync_from_manifest(path, Runner(runner=FakeRunner()))


# --- sync: derived collections (manifest omits `collections`) ----------------


# A manifest that omits `collections` entirely — the sync flow must derive the
# collection IDs to pull from each app's `collection-id`.
DERIVED_COLLECTIONS_MANIFEST = """
remote = "flathub"
remote-name = "pengyos"
apps = [
  { id = "org.gnome.Calculator", commit = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", collection-id = "org.gnome" },
  { id = "org.mozilla.firefox", commit = "f7e6d5c4b3a291807162534324354657", arch = "aarch64", branch = "edge", collection-id = "org.mozilla" },
]
"""


def test_sync_pulls_derived_collections(tmp_path):
    fake = FakeRunner()
    m = load_manifest(_write(tmp_path, DERIVED_COLLECTIONS_MANIFEST))
    result = sync(m, Runner(runner=fake))

    # Derived from the apps' collection-id, in manifest order.
    assert result.collections_pulled == ("org.gnome", "org.mozilla")
    # remote-add + two mirror_refs + two derived collection pulls.
    assert fake.calls[0] == [
        "flatpak", "remote-add", "--if-not-exists", "pengyos", "flathub",
    ]
    assert fake.calls[1][5] == "org.gnome.Calculator@x86_64/stable"
    assert fake.calls[2][5] == "org.mozilla.firefox@aarch64/edge"
    assert fake.calls[3] == [
        "flatpak", "update", "--user", "--noninteractive",
        "--collection=org.gnome", "pengyos",
    ]
    assert fake.calls[4] == [
        "flatpak", "update", "--user", "--noninteractive",
        "--collection=org.mozilla", "pengyos",
    ]


def test_sync_derived_collections_idempotent(tmp_path):
    fake = FakeRunner()
    runner = Runner(runner=fake)
    m = load_manifest(_write(tmp_path, DERIVED_COLLECTIONS_MANIFEST))

    sync(m, runner)
    first_run = list(fake.calls)

    fake.calls.clear()
    sync(m, runner)
    second_run = list(fake.calls)

    # Derivation is deterministic, so the second run issues the same argv.
    assert second_run == first_run
    # remote-add + two mirror_refs + two derived collection pulls.
    assert len(first_run) == 5


def test_sync_from_manifest_pulls_derived_collections(tmp_path):
    path = _write(tmp_path, DERIVED_COLLECTIONS_MANIFEST)
    fake = FakeRunner()
    result = sync_from_manifest(path, Runner(runner=fake))
    assert result.collections_pulled == ("org.gnome", "org.mozilla")
    assert fake.calls[-1] == [
        "flatpak", "update", "--user", "--noninteractive",
        "--collection=org.mozilla", "pengyos",
    ]


def test_sync_derives_no_collections_when_apps_have_none(tmp_path):
    # Apps without a `collection-id` and an omitted `collections` key: nothing.
    text = """
remote = "flathub"
remote-name = "pengyos"
apps = [
  { id = "org.x.App", commit = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef" },
]
"""
    fake = FakeRunner()
    m = load_manifest(_write(tmp_path, text))
    result = sync(m, Runner(runner=fake))
    assert result.collections_pulled == ()
    # remote-add + one mirror_ref only.
    assert fake.calls == [
        ["flatpak", "remote-add", "--if-not-exists", "pengyos", "flathub"],
        [
            "flatpak", "update", "--user", "--noninteractive",
            "--commit=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "org.x.App@x86_64/stable",
        ],
    ]


# --- parse_refs -------------------------------------------------------------


def test_parse_refs_tab_separated():
    out = (
        "org.gnome.Calculator/x86_64/stable\t0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n"
        "org.mozilla.firefox/x86_64/stable\tf7e6d5c4b3a291807162534324354657\n"
    )
    refs = parse_refs(out)
    assert refs == [
        ("org.gnome.Calculator/x86_64/stable",
         "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"),
        ("org.mozilla.firefox/x86_64/stable", "f7e6d5c4b3a291807162534324354657"),
    ]


def test_parse_refs_ignores_comments_and_blank_lines():
    out = "# a header comment\n\norg.x/x86_64/stable\t0a1b2c3d4e5f6a7b\n   \n"
    assert parse_refs(out) == [("org.x/x86_64/stable", "0a1b2c3d4e5f6a7b")]


def test_parse_refs_handles_space_separator():
    # ``ostree refs`` tab-separates in practice, but a space run must also work.
    assert parse_refs("org.x/x86_64/stable 0a1b2c3d") == [
        ("org.x/x86_64/stable", "0a1b2c3d"),
    ]


def test_parse_refs_skips_malformed_lines():
    # A line without a commit is dropped rather than raising.
    out = "only-one-field\norg.x/x86_64/stable\t0a1b2c3d\n"
    assert parse_refs(out) == [("org.x/x86_64/stable", "0a1b2c3d")]


def test_parse_refs_empty_string():
    assert parse_refs("") == []


# --- list_report ------------------------------------------------------------


def test_list_report_wraps_parse_refs():
    lr = list_report("org.x/x86_64/stable\t0a1b2c3d4e5f6a7b\n")
    assert isinstance(lr, ListResult)
    assert lr.refs == (("org.x/x86_64/stable", "0a1b2c3d4e5f6a7b"),)


def test_list_report_empty():
    assert list_report("").refs == ()


# --- list_report_dict: the deterministic machine report --------------------


MACHINE_STDOUT = (
    "org.mozilla.firefox/x86_64/stable\tf7e6d5c4b3a29180\n"
    "org.gnome.Calculator/x86_64/stable\t0123456789abcdef\n"
    "# a header comment\n"
    "\n"
)


def test_list_report_dict_structure():
    report = list_report_dict(MACHINE_STDOUT)
    assert report == {
        "refs": [
            {"ref": "org.gnome.Calculator/x86_64/stable", "commit": "0123456789abcdef"},
            {"ref": "org.mozilla.firefox/x86_64/stable", "commit": "f7e6d5c4b3a29180"},
        ]
    }


def test_list_report_dict_includes_repo_when_given():
    report = list_report_dict(MACHINE_STDOUT, repo="/tmp/repo")
    assert report["repo"] == "/tmp/repo"


def test_list_report_dict_sorts_refs_by_ref():
    # Input is deliberately out of order; the report must be sorted by (ref, commit).
    out = (
        "org.zzz.App/x86_64/stable\t00000000\n"
        "org.aaa.App/x86_64/stable\t11111111\n"
    )
    report = list_report_dict(out)
    assert [r["ref"] for r in report["refs"]] == [
        "org.aaa.App/x86_64/stable",
        "org.zzz.App/x86_64/stable",
    ]


def test_list_report_dict_is_deterministic():
    # Two runs — even against shuffled input — produce a byte-identical report.
    a = list_report_dict(MACHINE_STDOUT)
    b = list_report_dict(
        "org.gnome.Calculator/x86_64/stable\t0123456789abcdef\n"
        "org.mozilla.firefox/x86_64/stable\tf7e6d5c4b3a29180\n"
    )
    assert a == b


def test_list_report_dict_json_is_stable():
    # Serialising with sort_keys=True is the machine contract — stable output.
    report = list_report_dict(MACHINE_STDOUT, repo="/tmp/repo")
    first = json.dumps(report, sort_keys=True, indent=2)
    # Re-run with shuffled input; the JSON must be identical.
    report2 = list_report_dict(
        "org.mozilla.firefox/x86_64/stable\tf7e6d5c4b3a29180\n"
        "org.gnome.Calculator/x86_64/stable\t0123456789abcdef\n",
        repo="/tmp/repo",
    )
    assert json.dumps(report2, sort_keys=True, indent=2) == first


def test_list_report_dict_empty():
    assert list_report_dict("") == {"refs": []}


def test_list_report_dict_ignores_comments_and_blanks():
    out = "# header\n\norg.x/x86_64/stable\t0a1b2c3d\n   \n"
    assert list_report_dict(out) == {
        "refs": [{"ref": "org.x/x86_64/stable", "commit": "0a1b2c3d"}]
    }
