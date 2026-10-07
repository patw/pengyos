"""Tests for the declarative config loader."""

from pengyos_guest.config import Guest, Guestbook, load, parse


def test_parse_single_guest(sample_guests_toml):
    book = parse(sample_guests_toml)
    assert isinstance(book, Guestbook)
    assert book.names() == ["wolfi", "nixos"]


def test_parse_populates_fields(sample_guests_toml):
    book = parse(sample_guests_toml)
    wolfi = book.get("wolfi")
    assert isinstance(wolfi, Guest)
    assert wolfi.image == "quay.io/aliyunos/wolfi:latest"
    assert wolfi.packages == ["git", "curl"]
    assert wolfi.exports == {"git-editor": "/var/lib/distrobox/bin/git-editor"}
    assert wolfi.mounts == ["/home/alex/projects:/projects:rw"]


def test_parse_preserves_declaration_order(sample_guests_toml):
    assert parse(sample_guests_toml).names() == ["wolfi", "nixos"]


def test_parse_empty_exports_and_mounts():
    body = '[guests]\n  [[guests.empty]]\n  image = "img:latest"\n'
    book = parse(body)
    empty = book.get("empty")
    assert empty == Guest(name="empty", image="img:latest")
    assert empty.packages == []
    assert empty.exports == {}
    assert empty.mounts == []


def test_parse_missing_guests_table_raises():
    try:
        parse("[other]\nx = 1\n")
    except ValueError as exc:
        assert "guests" in str(exc)
    else:  # pragma: no cover - must raise
        raise AssertionError("expected ValueError for missing [guests] table")


def test_parse_non_dict_guest_raises():
    try:
        parse("[guests]\nfoo = 1\n")
    except ValueError as exc:
        assert "foo" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for non-table guest")


def test_load_reads_file(tmp_path, sample_guests_toml):
    path = tmp_path / "guests.toml"
    path.write_text(sample_guests_toml, encoding="utf-8")
    book = load(str(path))
    assert book.get("wolfi") is not None


def test_load_missing_file_raises():
    try:
        load("/no/such/guests.toml")
    except FileNotFoundError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected FileNotFoundError")


def test_get_unknown_returns_none(sample_guests_toml):
    assert parse(sample_guests_toml).get("nope") is None


def test_guest_to_dict_roundtrip():
    g = Guest(name="wolfi", image="img", packages=["a"], exports={"x": "/x"}, mounts=["h:g:rw"])
    d = g.to_dict()
    assert d == {
        "image": "img",
        "packages": ["a"],
        "exports": {"x": "/x"},
        "mounts": ["h:g:rw"],
        "setup": [],
        "nvidia": False,
        "description": "",
    }
    # name is represented by the key, not the body
    assert "name" not in d


# -- default search path ----------------------------------------------------------

from pengyos_guest.config import load_default


def test_load_default_merges_image_and_user_guests(tmp_path):
    system = tmp_path / "guests.d"
    system.mkdir()
    (system / "10-dev.toml").write_text(
        '[guests.dev]\nimage = "ubuntu:24.04"\nsetup = ["true"]\n', encoding="utf-8")
    (system / "20-cuda.toml").write_text(
        '[guests.cuda]\nimage = "nvidia/cuda:13.1.0-devel-ubuntu24.04"\nnvidia = true\n',
        encoding="utf-8")
    (system / "notes.txt").write_text("ignored", encoding="utf-8")
    user = tmp_path / "guests.toml"
    user.write_text('[guests.dev]\nimage = "fedora:42"\n', encoding="utf-8")

    book = load_default(system, user)

    assert book.names() == ["dev", "cuda"]
    dev, cuda = book.guests
    assert dev.image == "fedora:42" and dev.setup == []  # the user's replaces the image's
    assert cuda.nvidia is True


def test_load_default_with_nothing_installed(tmp_path):
    assert load_default(tmp_path / "none", tmp_path / "none.toml").guests == []
