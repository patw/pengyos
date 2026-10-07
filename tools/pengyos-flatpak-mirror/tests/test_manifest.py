"""Manifest loader tests.

Covers :mod:`pengyos_flatpak_mirror.manifest`: the schema, validation errors,
and the data model (``App.ref``, ``MirrorManifest.refs``). No network/root —
manifests are written to ``tmp_path`` fixtures.
"""

from pathlib import Path

import pytest

from pengyos_flatpak_mirror.manifest import (
    DEFAULT_ARCH,
    DEFAULT_BRANCH,
    MIN_COMMIT_LEN,
    App,
    ManifestError,
    _derive_collections,
    load_manifest,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_MANIFEST = REPO_ROOT / "examples" / "mirror.example.toml"


def _write(tmp_path, text: str) -> str:
    path = tmp_path / "mirror.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


# --- happy path -------------------------------------------------------------


def test_load_manifest_minimal(tmp_path):
    path = _write(tmp_path, 'remote = "flathub"\n')
    m = load_manifest(path)
    assert m.remote == "flathub"
    assert m.apps == ()
    assert m.collections == ()
    assert m.refs == []
    # remote-name defaults to remote.
    assert m.remote_name == "flathub"
    assert m.source == Path(path)


def test_load_manifest_full(tmp_path):
    text = """
remote = "flathub"
remote-name = "pengyos"
apps = [
  { id = "org.example.App", commit = "0a1b2c3d4e5f6a7b", collection-id = "org.example" },
  { id = "org.example.Tool", commit = "f7e6d5c4b3a291807162534324354657" },
]
collections = ["org.example"]
"""
    m = load_manifest(_write(tmp_path, text))
    assert m.remote == "flathub"
    assert m.remote_name == "pengyos"
    assert m.collections == ("org.example",)
    assert len(m.apps) == 2

    a0 = m.apps[0]
    assert a0.id == "org.example.App"
    assert a0.commit == "0a1b2c3d4e5f6a7b"
    assert a0.collection_id == "org.example"
    # Defaults applied.
    assert a0.arch == DEFAULT_ARCH
    assert a0.branch == DEFAULT_BRANCH

    a1 = m.apps[1]
    assert a1.collection_id is None
    assert a1.arch == DEFAULT_ARCH
    assert a1.branch == DEFAULT_BRANCH


def test_app_ref_format():
    app = App(id="org.example.App", commit="0a1b2c3d4e5f6a7b", arch="aarch64", branch="edge")
    assert app.ref == "org.example.App/aarch64/edge"


def test_manifest_refs_property(tmp_path):
    text = """
remote = "flathub"
apps = [
  { id = "org.example.App", commit = "0a1b2c3d4e5f6a7b" },
  { id = "org.example.App", commit = "f7e6d5c4b3a291807162534324354657", arch = "aarch64", branch = "edge" },
]
"""
    m = load_manifest(_write(tmp_path, text))
    assert m.refs == [
        "org.example.App/x86_64/stable",
        "org.example.App/aarch64/edge",
    ]


def test_remote_name_defaults_and_overrides(tmp_path):
    defaults = load_manifest(_write(tmp_path, 'remote = "flathub"\n'))
    assert defaults.remote_name == "flathub"

    override = load_manifest(_write(tmp_path, 'remote = "flathub"\nremote-name = "pengyos"\n'))
    assert override.remote_name == "pengyos"


# --- data model -------------------------------------------------------------


def test_app_defaults():
    app = App(id="x", commit="0a1b2c3d4e5f6a7b")
    assert app.arch == DEFAULT_ARCH
    assert app.branch == DEFAULT_BRANCH
    assert app.collection_id is None


def test_manifest_is_immutable(tmp_path):
    """The manifest is a frozen dataclass — its fields cannot be mutated."""
    m = load_manifest(_write(tmp_path, 'remote = "flathub"\n'))
    with pytest.raises(Exception):
        m.remote = "other"  # type: ignore[misc]


# --- validation errors ------------------------------------------------------


def test_missing_file_raises(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(tmp_path / "nope.toml")
    assert "not found" in str(exc.value)


def test_missing_remote_raises(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, 'apps = []\n'))
    assert "remote" in str(exc.value)


def test_empty_remote_raises(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, 'remote = "   "\n'))
    assert "remote" in str(exc.value)


def test_apps_must_be_list(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, 'remote = "flathub"\napps = "x"\n'))
    assert "apps" in str(exc.value)


def test_app_must_be_table(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, 'remote = "flathub"\napps = ["org.x"]\n'))
    assert "apps[0]" in str(exc.value)


def test_app_missing_id_raises(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, 'remote = "flathub"\napps = [{ commit = "0a1b2c3d4e5f6a7b" }]\n'))
    assert "id" in str(exc.value)


def test_app_missing_commit_raises(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, 'remote = "flathub"\napps = [{ id = "org.x" }]\n'))
    assert "commit" in str(exc.value)


def test_app_non_hex_commit_raises(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, 'remote = "flathub"\napps = [{ id = "org.x", commit = "not-a-commit" }]\n'))
    assert "commit" in str(exc.value)


def test_app_short_commit_raises(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, f'remote = "flathub"\napps = [{{ id = "org.x", commit = "abc" }}]\n'))
    assert "too short" in str(exc.value)


def test_app_short_commit_boundary_ok(tmp_path):
    # Exactly MIN_COMMIT_LEN hex chars is accepted.
    good = "0" * MIN_COMMIT_LEN
    m = load_manifest(_write(tmp_path, f'remote = "flathub"\napps = [{{ id = "org.x", commit = "{good}" }}]\n'))
    assert m.apps[0].commit == good


def test_app_uppercase_commit_ok(tmp_path):
    good = "A" * MIN_COMMIT_LEN
    m = load_manifest(_write(tmp_path, f'remote = "flathub"\napps = [{{ id = "org.x", commit = "{good}" }}]\n'))
    assert m.apps[0].commit == good


def test_app_bad_arch_raises(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, 'remote = "flathub"\napps = [{ id = "org.x", commit = "0a1b2c3d4e5f6a7b", arch = "" }]\n'))
    assert "arch" in str(exc.value)


def test_app_bad_collection_id_raises(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, 'remote = "flathub"\napps = [{ id = "org.x", commit = "0a1b2c3d4e5f6a7b", collection-id = "" }]\n'))
    assert "collection-id" in str(exc.value)


def test_collections_must_be_list(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, 'remote = "flathub"\ncollections = "org.x"\n'))
    assert "collections" in str(exc.value)


def test_collections_must_be_strings(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, 'remote = "flathub"\ncollections = [1, 2]\n'))
    assert "collections[0]" in str(exc.value)


def test_remote_name_must_be_string(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, 'remote = "flathub"\nremote-name = 5\n'))
    assert "remote-name" in str(exc.value)


def test_invalid_toml_raises(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, 'remote = "flathub\n'))  # unterminated string
    assert "TOML" in str(exc.value)


def test_non_table_top_level_raises(tmp_path):
    """A non-table top level is rejected. (tomllib always yields a dict for
    valid TOML, so this also guards malformed input such as a bare array.)"""
    with pytest.raises(ManifestError):
        load_manifest(_write(tmp_path, '["a", "b"]\n'))


# --- derive collections from apps (omitted) ---------------------------------
# An *omitted* `collections` key is derived from the
# apps' `collection-id`; an explicit `collections = []` pulls nothing.


def test_derive_collections_from_apps_when_omitted(tmp_path):
    text = """
remote = "flathub"
apps = [
  { id = "org.gnome.Calculator", commit = "0a1b2c3d4e5f6a7b", collection-id = "org.gnome" },
  { id = "org.mozilla.firefox", commit = "f7e6d5c4b3a29180", collection-id = "org.mozilla" },
]
"""
    m = load_manifest(_write(tmp_path, text))
    assert m.collections == ("org.gnome", "org.mozilla")


def test_derive_collections_preserves_order_and_dedupes(tmp_path):
    # Manifest order is preserved and a repeated collection-id appears once
    # (first appearance wins).
    text = """
remote = "flathub"
apps = [
  { id = "a", commit = "0a1b2c3d4e5f6a7b", collection-id = "org.b" },
  { id = "b", commit = "f7e6d5c4b3a29180", collection-id = "org.a" },
  { id = "c", commit = "1111111122222222", collection-id = "org.b" },
]
"""
    m = load_manifest(_write(tmp_path, text))
    assert m.collections == ("org.b", "org.a")


def test_derive_collections_skips_apps_without_collection_id(tmp_path):
    # Apps without a `collection-id` contribute nothing.
    text = """
remote = "flathub"
apps = [
  { id = "a", commit = "0a1b2c3d4e5f6a7b" },
  { id = "b", commit = "f7e6d5c4b3a29180", collection-id = "org.a" },
  { id = "c", commit = "1111111122222222" },
]
"""
    m = load_manifest(_write(tmp_path, text))
    assert m.collections == ("org.a",)


def test_derive_collections_no_apps_yields_empty(tmp_path):
    # No apps and no `collections` key: nothing to derive.
    m = load_manifest(_write(tmp_path, 'remote = "flathub"\n'))
    assert m.collections == ()


def test_explicit_empty_collections_overrides_derivation(tmp_path):
    # An explicit `collections = []` pulls nothing, even though the apps carry a
    # `collection-id`. Only an *omitted* key derives from apps.
    text = """
remote = "flathub"
apps = [
  { id = "a", commit = "0a1b2c3d4e5f6a7b", collection-id = "org.a" },
]
collections = []
"""
    m = load_manifest(_write(tmp_path, text))
    assert m.collections == ()


def test_explicit_collections_used_verbatim(tmp_path):
    # A present `collections` key wins over derivation (even if it omits a
    # collection the apps declare).
    text = """
remote = "flathub"
apps = [
  { id = "a", commit = "0a1b2c3d4e5f6a7b", collection-id = "org.a" },
]
collections = ["org.explicit"]
"""
    m = load_manifest(_write(tmp_path, text))
    assert m.collections == ("org.explicit",)


def test_derive_collections_helper_direct():
    apps = (
        App(id="a", commit="0a1b2c3d4e5f6a7b", collection_id="org.b"),
        App(id="b", commit="f7e6d5c4b3a29180", collection_id="org.a"),
        App(id="c", commit="1111111122222222", collection_id="org.b"),
        App(id="d", commit="2222222233333333"),  # no collection-id
    )
    assert _derive_collections(apps) == ("org.b", "org.a")


def test_derive_collections_helper_empty():
    assert _derive_collections(()) == ()


# --- committed example fixture ----------------------------------------------


def test_example_manifest_loads():
    """The committed example manifest must parse — a real, tested example."""
    assert EXAMPLE_MANIFEST.is_file()
    m = load_manifest(EXAMPLE_MANIFEST)
    assert m.remote == "flathub"
    assert m.remote_name == "pengyos"
    assert m.refs == [
        "org.gnome.Calculator/x86_64/stable",
        "org.mozilla.firefox/x86_64/stable",
    ]
    assert m.collections == ("org.gnome", "org.mozilla")
