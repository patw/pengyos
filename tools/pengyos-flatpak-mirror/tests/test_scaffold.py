"""Scaffold-level tests.

Covers the scaffold deliverable:

* package metadata is coherent and matches the build metadata,
* the ``main`` entry point works (``--version``, help, not-yet-implemented
  subcommands, bad usage),
* the CLI argument surface (``init`` / ``sync`` / ``list`` / ``verify`` /
  ``prune``) is registered with the documented flags and defaults,
* the console-script entry point is declared in ``pyproject.toml``.
"""

import importlib.metadata as importlib_metadata
from pathlib import Path

import pytest

import pengyos_flatpak_mirror as pkg
from pengyos_flatpak_mirror import __version__
from pengyos_flatpak_mirror import cli
from pengyos_flatpak_mirror.cli import (
    EXIT_FAIL,
    EXIT_OK,
    EXIT_USAGE,
    SUBCOMMANDS,
    build_parser,
    main,
)
from pengyos_flatpak_mirror.runner import Result, Runner

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- package metadata -------------------------------------------------------


def test_package_exposes_semver_string():
    assert isinstance(pkg.__version__, str)
    parts = pkg.__version__.split(".")
    # At least a numeric MAJOR.MINOR.PATCH core.
    assert len(parts) >= 3
    assert parts[0].isdigit()
    assert parts[1].isdigit()
    assert parts[2][:1].isdigit()


def test_version_matches_pyproject():
    """__version__ must not drift from the declared build metadata."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in text
    assert 'name = "pengyos-flatpak-mirror"' in text
    # The license table form should name MIT.
    assert "MIT" in text


def test_version_matches_installed_metadata():
    """If the package is installed (loop does `pip install -e .`), the
    importlib metadata must agree with ``__version__``. Skipped when not
    installed so the suite is green either way."""
    try:
        installed = importlib_metadata.version("pengyos-flatpak-mirror")
    except importlib_metadata.PackageNotFoundError:
        pytest.skip("pengyos-flatpak-mirror is not installed (run: pip install -e .)")
    assert installed == __version__


def test_entry_point_declared_in_pyproject():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'pengyos-flatpak-mirror = "pengyos_flatpak_mirror.cli:main"' in text


def test_python_module_entry_point_exists():
    """``python -m pengyos_flatpak_mirror`` is wired to the same entry point."""
    text = (REPO_ROOT / "pengyos_flatpak_mirror" / "__main__.py").read_text(
        encoding="utf-8"
    )
    assert "from .cli import main" in text
    assert "sys.exit(main())" in text


def test_subcommands_match_documented_surface():
    """The documented CLI surface (spec Interface line) is exactly what the
    parser advertises, in help order."""
    text = (REPO_ROOT / "spec.md").read_text(encoding="utf-8")
    for token in ("init", "sync", "list", "verify", "prune"):
        assert token in text
    assert SUBCOMMANDS == ("init", "sync", "list", "verify", "prune")


# --- entry point behaviour --------------------------------------------------


def test_main_version_prints_and_returns_zero(capsys):
    rc = main(["--version"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert __version__ in out
    assert "pengyos-flatpak-mirror" in out


def test_main_no_args_prints_help_returns_zero(capsys):
    rc = main([])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "pengyos-flatpak-mirror" in out
    # All documented subcommands are advertised.
    for token in SUBCOMMANDS:
        assert token in out


def test_main_init_is_implemented(tmp_path, monkeypatch, capsys):
    """``init`` is now wired through the runner: it runs the golden argv
    ``flatpak build-update-repo --init <repo>`` and reports a clean success
    (exit 0), not a "not implemented" error."""
    fake = _FakeRunner()
    _patch_runner(monkeypatch, fake)
    capsys.readouterr()
    rc = main(["init", "--repo", str(tmp_path)])
    assert rc == EXIT_OK
    assert fake.calls[-1] == ["flatpak", "build-update-repo", "--init", str(tmp_path)]
    out = capsys.readouterr().out
    assert f"initialised {tmp_path}" in out


def test_main_init_command_error_returns_fail(tmp_path, monkeypatch, capsys):
    """A non-zero external exit from ``init`` (e.g. the repo dir could not be
    created) is a clean exit 1, not a crash."""

    class _FailRunner:
        def __call__(self, argv):
            return Result(argv=list(argv), returncode=1, stderr="cannot create repo")

    _patch_runner(monkeypatch, _FailRunner())
    capsys.readouterr()
    rc = main(["init", "--repo", "/root/needs/root"])
    assert rc == EXIT_FAIL
    err = capsys.readouterr().err
    assert "cannot create repo" in err


def _patch_runner(monkeypatch, fake):
    """Point ``cli.Runner`` at a fake so the CLI dispatch exercises no real
    flatpak/ostree. The CLI constructs ``Runner()`` (no args), so the factory
    ignores any args and returns a Runner bound to ``fake``."""
    monkeypatch.setattr(
        "pengyos_flatpak_mirror.cli.Runner",
        lambda *a, **k: Runner(runner=fake),
    )


class _FakeRunner:
    """Records argv and returns a canned :class:`Result` (see test_runner)."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return Result(argv=list(argv), returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


def test_main_sync_is_implemented(tmp_path, monkeypatch, capsys):
    """``sync`` loads the manifest, registers the remote once, then mirrors
    every pinned ref — through the injected fake runner."""
    fake = _FakeRunner()
    _patch_runner(monkeypatch, fake)
    manifest = tmp_path / "m.toml"
    manifest.write_text(
        'remote = "flathub"\nremote-name = "pengyos"\napps = [\n'
        '  { id = "org.x.App", commit = "0a1b2c3d4e5f6a7b" },\n'
        ']\n',
        encoding="utf-8",
    )
    capsys.readouterr()
    rc = main(["sync", "--manifest", str(manifest)])
    assert rc == EXIT_OK
    # remote-add first, then one mirror per app (pinned ref uses <id>@<arch>/<branch>).
    assert fake.calls[0] == ["flatpak", "remote-add", "--if-not-exists", "pengyos", "flathub"]
    assert fake.calls[1][5] == "org.x.App@x86_64/stable"
    out = capsys.readouterr().out
    assert "synced 1 ref(s)" in out


def test_main_sync_reports_collections(tmp_path, monkeypatch, capsys):
    """``sync`` mirrors each pinned ref then pulls each manifest collection, and
    reports both counts."""
    fake = _FakeRunner()
    _patch_runner(monkeypatch, fake)
    manifest = tmp_path / "m.toml"
    manifest.write_text(
        'remote = "flathub"\nremote-name = "pengyos"\n'
        'apps = [{ id = "org.x.App", commit = "0a1b2c3d4e5f6a7b" }]\n'
        'collections = ["org.x", "org.y"]\n',
        encoding="utf-8",
    )
    capsys.readouterr()
    rc = main(["sync", "--manifest", str(manifest)])
    assert rc == EXIT_OK
    # remote-add, one mirror, then two collection pulls (manifest order).
    assert fake.calls[0] == ["flatpak", "remote-add", "--if-not-exists", "pengyos", "flathub"]
    assert fake.calls[1][5] == "org.x.App@x86_64/stable"
    assert fake.calls[2] == ["flatpak", "update", "--user", "--noninteractive", "--collection=org.x", "pengyos"]
    assert fake.calls[3] == ["flatpak", "update", "--user", "--noninteractive", "--collection=org.y", "pengyos"]
    out = capsys.readouterr().out
    assert "synced 1 ref(s) and 2 collection(s)" in out
    assert "collection: org.x" in out
    assert "collection: org.y" in out


def test_main_sync_pulls_derived_collections(tmp_path, monkeypatch, capsys):
    """``sync`` derives the collections to pull from the apps' collection-id when
    the manifest omits ``collections``, and reports both counts."""
    fake = _FakeRunner()
    _patch_runner(monkeypatch, fake)
    manifest = tmp_path / "m.toml"
    manifest.write_text(
        'remote = "flathub"\nremote-name = "pengyos"\n'
        'apps = [\n'
        '  { id = "org.x.App", commit = "0a1b2c3d4e5f6a7b", collection-id = "org.x" },\n'
        '  { id = "org.y.Tool", commit = "f7e6d5c4b3a29180", collection-id = "org.y" },\n'
        ']\n',
        encoding="utf-8",
    )
    capsys.readouterr()
    rc = main(["sync", "--manifest", str(manifest)])
    assert rc == EXIT_OK
    # remote-add, two mirror_refs, then two derived collection pulls (manifest
    # order).
    assert fake.calls[0] == ["flatpak", "remote-add", "--if-not-exists", "pengyos", "flathub"]
    assert fake.calls[1][5] == "org.x.App@x86_64/stable"
    assert fake.calls[2][5] == "org.y.Tool@x86_64/stable"
    assert fake.calls[3] == ["flatpak", "update", "--user", "--noninteractive", "--collection=org.x", "pengyos"]
    assert fake.calls[4] == ["flatpak", "update", "--user", "--noninteractive", "--collection=org.y", "pengyos"]
    out = capsys.readouterr().out
    assert "synced 2 ref(s) and 2 collection(s)" in out
    assert "collection: org.x" in out
    assert "collection: org.y" in out


def test_main_sync_bad_manifest_returns_fail_without_running(tmp_path, monkeypatch, capsys):
    """A manifest validation error is a clean exit 1, and no external command
    runs (the manifest is validated before anything is executed)."""
    fake = _FakeRunner()
    _patch_runner(monkeypatch, fake)
    capsys.readouterr()
    rc = main(["sync", "--manifest", str(tmp_path / "nope.toml")])
    assert rc == EXIT_FAIL
    err = capsys.readouterr().err
    assert "not found" in err
    assert fake.calls == []


def test_main_list_is_implemented(tmp_path, monkeypatch, capsys):
    """``list`` runs ``ostree refs`` and prints each ref + commit."""
    fake = _FakeRunner(stdout="org.x/x86_64/stable\t0a1b2c3d4e5f6a7b\n")
    _patch_runner(monkeypatch, fake)
    capsys.readouterr()
    rc = main(["list", "--repo", "/tmp/repo"])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "1 ref(s) in /tmp/repo" in out
    assert "org.x/x86_64/stable 0a1b2c3d4e5f6a7b" in out


def test_main_list_json_emits_deterministic_report(tmp_path, monkeypatch, capsys):
    """``list --json`` emits a deterministic machine report: refs sorted by
    (ref, commit), keys sorted, valid JSON — regardless of the input order."""
    import json as _json

    fake = _FakeRunner(
        stdout=(
            "org.mozilla.firefox/x86_64/stable\tf7e6d5c4b3a29180\n"
            "org.gnome.Calculator/x86_64/stable\t0123456789abcdef\n"
        )
    )
    _patch_runner(monkeypatch, fake)
    capsys.readouterr()
    rc = main(["list", "--repo", "/tmp/repo", "--json"])
    assert rc == EXIT_OK
    assert fake.calls[-1] == ["ostree", "--repo=/tmp/repo", "refs"]
    out = capsys.readouterr().out
    report = _json.loads(out)  # must be valid JSON
    assert report == {
        "repo": "/tmp/repo",
        "refs": [
            {"ref": "org.gnome.Calculator/x86_64/stable", "commit": "0123456789abcdef"},
            {"ref": "org.mozilla.firefox/x86_64/stable", "commit": "f7e6d5c4b3a29180"},
        ],
    }
    # Keys are sorted ("commit" before "ref", "refs" before "repo").
    assert '"commit"' in out and out.index('"commit"') < out.index('"ref"')


def test_main_list_json_is_order_independent(tmp_path, monkeypatch, capsys):
    """Shuffling the underlying ``ostree refs`` output does not change the
    machine report — it is deterministic."""
    import json as _json

    def _run_list(monkeypatch, stdout):
        fake = _FakeRunner(stdout=stdout)
        _patch_runner(monkeypatch, fake)
        capsys.readouterr()
        return main(["list", "--repo", "/tmp/repo", "--json"])

    a = _run_list(monkeypatch, "org.zzz.App/x86_64/stable\t00000000\norg.aaa.App/x86_64/stable\t11111111\n")
    b = _run_list(monkeypatch, "org.aaa.App/x86_64/stable\t11111111\norg.zzz.App/x86_64/stable\t00000000\n")
    assert a == EXIT_OK and b == EXIT_OK
    first = capsys.readouterr().out
    _run_list(monkeypatch, "org.aaa.App/x86_64/stable\t11111111\norg.zzz.App/x86_64/stable\t00000000\n")
    second = capsys.readouterr().out
    assert _json.loads(first) == _json.loads(second)


def test_main_list_json_flag_defaults_false(tmp_path, monkeypatch, capsys):
    """Without ``--json`` the output is the human summary, not JSON."""
    fake = _FakeRunner(stdout="org.x/x86_64/stable\t0a1b2c3d4e5f6a7b\n")
    _patch_runner(monkeypatch, fake)
    capsys.readouterr()
    rc = main(["list", "--repo", "/tmp/repo"])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "1 ref(s) in /tmp/repo" in out
    assert "org.x/x86_64/stable 0a1b2c3d4e5f6a7b" in out
    assert out.strip().startswith("{") is False


def test_main_verify_is_implemented(tmp_path, monkeypatch, capsys):
    """``verify`` runs its golden argv and reports."""
    fake = _FakeRunner()
    _patch_runner(monkeypatch, fake)
    capsys.readouterr()
    rc = main(["verify", "--gpg-key", "/tmp/k.asc", "--repo", "/tmp/repo"])
    assert rc == EXIT_OK
    assert fake.calls[-1] == ["ostree", "--repo=/tmp/repo", "verify", "--gpg-key=/tmp/k.asc"]


def test_main_prune_is_implemented(tmp_path, monkeypatch, capsys):
    """``prune`` runs its golden argv and reports the requested keep count."""
    fake = _FakeRunner()
    _patch_runner(monkeypatch, fake)
    capsys.readouterr()
    rc = main(["prune", "--keep", "3", "--repo", "/tmp/repo"])
    assert rc == EXIT_OK
    assert fake.calls[-1] == ["flatpak", "update", "--prune"]
    out = capsys.readouterr().out
    assert "keep=3" in out


def test_main_command_error_returns_fail(tmp_path, monkeypatch, capsys):
    """A non-zero external exit (CommandError) is a clean exit 1, not a crash."""
    class _FailRunner:
        def __call__(self, argv):
            return Result(argv=list(argv), returncode=127, stderr="flatpak: not found")

    _patch_runner(monkeypatch, _FailRunner())
    capsys.readouterr()
    rc = main(["list", "--repo", "/tmp/repo"])
    assert rc == EXIT_FAIL
    err = capsys.readouterr().err
    assert "127" in err or "not found" in err


def test_bad_subcommand_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["frobnicate"])
    assert exc.value.code == EXIT_USAGE


def test_version_flag_beats_subcommand(capsys):
    # ``--version`` on the top-level parser short-circuits before dispatch.
    rc = main(["--version", "sync", "--manifest", "m"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert __version__ in out


# --- CLI argument surface ---------------------------------------------------


def _require(exc_type, argv):
    """Assert ``parse_args(argv)`` raises ``exc_type``; return the ExceptionInfo."""
    with pytest.raises(exc_type) as exc:
        build_parser().parse_args(argv)
    return exc


def test_init_parser_requires_repo():
    exc = _require(SystemExit, ["init"])
    assert exc.value.code == EXIT_USAGE


def test_init_parser_takes_repo(tmp_path):
    args = build_parser().parse_args(["init", "--repo", str(tmp_path)])
    assert args.command == "init"
    assert args.repo == str(tmp_path)


def test_sync_parser_requires_manifest():
    exc = _require(SystemExit, ["sync"])
    assert exc.value.code == EXIT_USAGE


def test_sync_parser_takes_manifest_and_optional_repo(tmp_path):
    args = build_parser().parse_args(
        ["sync", "--manifest", str(tmp_path / "m.toml"), "--repo", str(tmp_path)]
    )
    assert args.command == "sync"
    assert args.manifest == str(tmp_path / "m.toml")
    assert args.repo == str(tmp_path)


def test_list_parser_requires_repo():
    exc = _require(SystemExit, ["list"])
    assert exc.value.code == EXIT_USAGE


def test_list_parser_takes_repo(tmp_path):
    args = build_parser().parse_args(["list", "--repo", str(tmp_path)])
    assert args.command == "list"
    assert args.repo == str(tmp_path)


def test_list_parser_takes_json_flag(tmp_path):
    args = build_parser().parse_args(["list", "--repo", str(tmp_path), "--json"])
    assert args.command == "list"
    assert args.repo == str(tmp_path)
    assert args.json is True


def test_list_parser_json_defaults_false(tmp_path):
    args = build_parser().parse_args(["list", "--repo", str(tmp_path)])
    assert args.json is False


def test_verify_parser_requires_key_and_repo():
    # Missing --repo.
    assert (
        _require(SystemExit, ["verify", "--gpg-key", "k"]).value.code == EXIT_USAGE
    )
    # Missing --gpg-key.
    assert (
        _require(SystemExit, ["verify", "--repo", "r"]).value.code == EXIT_USAGE
    )


def test_verify_parser_takes_key_and_repo(tmp_path):
    args = build_parser().parse_args(
        ["verify", "--gpg-key", str(tmp_path / "k.asc"), "--repo", str(tmp_path)]
    )
    assert args.command == "verify"
    assert args.gpg_key == str(tmp_path / "k.asc")
    assert args.repo == str(tmp_path)


def test_prune_parser_requires_keep_and_repo():
    exc = _require(SystemExit, ["prune", "--repo", "r"])
    assert exc.value.code == EXIT_USAGE


def test_prune_parser_takes_keep_and_repo(tmp_path):
    args = build_parser().parse_args(["prune", "--keep", "5", "--repo", str(tmp_path)])
    assert args.command == "prune"
    assert args.keep == 5
    assert args.repo == str(tmp_path)


def test_prune_keep_must_be_integer():
    # ``--keep`` is typed int; a non-integer is a usage error (exit 2).
    exc = _require(SystemExit, ["prune", "--keep", "abc", "--repo", "r"])
    assert exc.value.code == EXIT_USAGE
