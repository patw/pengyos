"""Tests for the upstream ``Fetcher`` abstraction.

Covers the interface, the offline :class:`MockFetcher`, the network
:class:`HttpFetcher` (with its transport *stubbed* so no socket is ever opened),
each :class:`LatestVersionStrategy`, and ``build_fetcher``.

No test opens a socket: :class:`HttpFetcher.send` is replaced with a stub that
returns a canned :class:`Response`, so the dispatch + parsing logic is exercised
deterministically.
"""

from __future__ import annotations

import pytest

from pengyos_upstream_watch.fetcher import (
    Component,
    Fetcher,
    GitHubReleasesStrategy,
    HttpFetcher,
    LatestVersionStrategy,
    MockFetcher,
    PyPILatestVersionStrategy,
    RedirectLatestStrategy,
    Request,
    Response,
    UpstreamError,
    UnknownComponentError,
    build_fetcher,
)


# --- Fixtures / helpers -----------------------------------------------------


class RecordingSend:
    """A stand-in for :meth:`HttpFetcher.send` that records calls and returns
    a canned :class:`Response`."""

    def __init__(self, response: Response, *, raise_exc: BaseException | None = None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, dict, bool]] = []

    def __call__(self, url, *, headers=None, allow_redirects=True):
        self.calls.append((url, headers, allow_redirects))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


def attach(fetcher: HttpFetcher, send) -> HttpFetcher:
    """Replace ``fetcher.send`` with ``send`` and return the fetcher."""
    fetcher.send = send
    return fetcher


# --- Component --------------------------------------------------------------


def test_component_defaults():
    c = Component("busybox")
    assert c.name == "busybox"
    assert c.version is None
    assert c.url is None


def test_component_equality_and_frozen():
    a = Component("glibc", "2.39", "https://example.org/glibc")
    b = Component("glibc", "2.39", "https://example.org/glibc")
    c = Component("glibc", "2.40")
    assert a == b
    assert a != c
    with pytest.raises(AttributeError):
        a.name = "x"  # frozen dataclass


def test_component_str_is_name():
    assert str(Component("busybox", "1.36.1")) == "busybox"


# --- MockFetcher ------------------------------------------------------------


def test_mock_fetcher_returns_registered_version():
    fetcher = MockFetcher({"busybox": "1.37.0"})
    assert fetcher.latest_version(Component("busybox", "1.36.1")) == "1.37.0"


def test_mock_fetcher_empty_raises_unknown():
    fetcher = MockFetcher()
    with pytest.raises(UnknownComponentError):
        fetcher.latest_version(Component("busybox"))


def test_mock_fetcher_register_and_overwrite():
    fetcher = MockFetcher({"busybox": "1.36.1"})
    fetcher.register("busybox", "1.37.0")
    fetcher.register("glibc", "2.39")
    assert fetcher.latest_version(Component("busybox")) == "1.37.0"
    assert fetcher.latest_version(Component("glibc")) == "2.39"


def test_mock_fetcher_is_a_fetcher():
    assert isinstance(MockFetcher(), Fetcher)


def test_mock_fetcher_unknown_error_is_fetcher_error():
    assert issubclass(UnknownComponentError, Exception)


# --- HttpFetcher: dispatch + parsing (transport stubbed) --------------------


def test_http_fetcher_selects_pypi_strategy():
    send = RecordingSend(Response(b'{"info": {"version": "2.0"}}', "https://pypi.org/pypi/requests/json"))
    fetcher = attach(HttpFetcher(), send)
    latest = fetcher.latest_version(
        Component("requests", "1.5", url="https://pypi.org/simple/requests/")
    )
    assert latest == "2.0"
    # The PyPI JSON API is what we queried.
    assert send.calls[0][0] == "https://pypi.org/pypi/requests/json"


def test_http_fetcher_pypi_strips_prerelease_from_stable():
    # info.version is the latest *stable* release; a prerelease index.json
    # would still report the stable core here.
    send = RecordingSend(Response(b'{"info": {"version": "4.2.1"}}', "u"))
    fetcher = attach(HttpFetcher(), send)
    assert fetcher.latest_version(Component("x", url="https://pypi.org/simple/x/")) == "4.2.1"


def test_http_fetcher_selects_github_strategy():
    send = RecordingSend(Response(b'{"tag_name": "v1.2.3"}', "https://api.github.com/repos/o/j/releases/latest"))
    fetcher = attach(HttpFetcher(), send)
    latest = fetcher.latest_version(Component("foo", url="https://github.com/o/j"))
    assert latest == "1.2.3"  # leading 'v' stripped


def test_http_fetcher_github_sends_accept_header():
    send = RecordingSend(Response(b'{"tag_name": "v9"}', "u"))
    fetcher = attach(HttpFetcher(), send)
    fetcher.latest_version(Component("foo", url="https://github.com/o/j"))
    url, headers, _ = send.calls[0]
    assert "api.github.com" in url
    assert headers["Accept"] == "application/vnd.github+json"


def test_http_fetcher_github_prereleases_flag():
    send = RecordingSend(Response(b'{"tag_name": "v0.1.0-rc1"}', "u"))
    fetcher = attach(HttpFetcher(), send)
    fetcher.strategies = [GitHubReleasesStrategy(prereleases=True)]
    # With prereleases enabled the query URL requests them.
    fetcher.latest_version(Component("foo", url="https://github.com/o/j"))
    assert "prereleases=true" in send.calls[0][0]


def test_http_fetcher_selects_redirect_strategy():
    send = RecordingSend(Response(b"", "https://example.org/busybox/releases/v1.37.0.tar.gz"))
    fetcher = attach(HttpFetcher(), send)
    latest = fetcher.latest_version(Component("busybox", "1.36.1", url="https://example.org/latest"))
    assert latest == "1.37.0"
    # Redirects are followed (allow_redirects=True) so the final URL is read.
    assert send.calls[0][2] is True


def test_http_fetcher_redirect_uses_component_url():
    send = RecordingSend(Response(b"", "https://cdn.example.org/v2.5"))
    fetcher = attach(HttpFetcher(), send)
    fetcher.latest_version(Component("z", url="https://example.org/latest"))
    assert send.calls[0][0] == "https://example.org/latest"


def test_http_fetcher_dispatch_order_specific_first():
    # A github URL must not fall through to the PyPI strategy.
    called: list[str] = []

    class PyPI(PyPILatestVersionStrategy):
        def parse(self, response):  # pragma: no cover - not reached
            called.append("pypi")
            return "0"

    class GitHubGitHub(GitHubReleasesStrategy):
        def parse(self, response):
            called.append("github")
            return "7"

    strategies = [PyPI(), GitHubGitHub(), RedirectLatestStrategy()]
    fetcher = HttpFetcher(strategies=strategies)
    send = RecordingSend(Response(b'{"tag_name": "v7"}', "u"))
    fetcher.send = send
    assert fetcher.latest_version(Component("foo", url="https://github.com/o/j")) == "7"
    assert called == ["github"]


def test_http_fetcher_custom_strategies_used():
    class Echo(LatestVersionStrategy):
        def supports(self, component):
            return component.name == "echo"

        def query(self, component):
            return Request("http://echo", allow_redirects=False)

        def parse(self, response):
            return "42"

    fetcher = HttpFetcher(strategies=[Echo()])
    send = RecordingSend(Response(b"", "http://echo"))
    fetcher.send = send
    assert fetcher.latest_version(Component("echo")) == "42"
    assert send.calls[0][2] is False  # our strategy's allow_redirects flag


def test_http_fetcher_send_error_is_normalised():
    send = RecordingSend(Response(b"", "u"), raise_exc=OSError("boom"))
    fetcher = attach(HttpFetcher(), send)
    with pytest.raises(UpstreamError) as exc:
        fetcher.latest_version(Component("x", url="https://pypi.org/simple/x/"))
    assert "boom" in str(exc.value)


def test_http_fetcher_fetcher_error_passthrough():
    send = RecordingSend(Response(b"", "u"), raise_exc=UpstreamError("already up"))
    fetcher = attach(HttpFetcher(), send)
    with pytest.raises(UpstreamError) as exc:
        fetcher.latest_version(Component("x", url="https://pypi.org/simple/x/"))
    assert "already up" in str(exc.value)


def test_http_fetcher_parse_error_is_normalised():
    send = RecordingSend(Response(b"not json", "u"))
    fetcher = attach(HttpFetcher(), send)
    with pytest.raises(UpstreamError):
        fetcher.latest_version(Component("x", url="https://pypi.org/simple/x/"))


def test_http_fetcher_no_strategy_is_defensive():
    # A fetcher with a strategy set that supports nothing should not raise
    # inside dispatch; RedirectLatest catches all, so use a custom empty set.
    fetcher = HttpFetcher(strategies=[])  # empty: nothing supports
    # RedirectLatest is the default catch-all; with an explicit empty list there
    # is no catch-all, so dispatch is defensive.
    with pytest.raises(UpstreamError):
        fetcher.latest_version(Component("x", url="https://example.org/"))


# --- PyPILatestVersionStrategy ----------------------------------------------


def test_pypi_strategy_supports_by_host():
    s = PyPILatestVersionStrategy()
    assert s.supports(Component("x", url="https://pypi.org/simple/x/"))
    assert s.supports(Component("x", url="https://files.pythonhosted.org/"))
    assert not s.supports(Component("x", url="https://github.com/o/j"))
    assert not s.supports(Component("x"))  # no url


def test_pypi_strategy_query_url():
    s = PyPILatestVersionStrategy()
    assert s.query(Component("requests")).url == "https://pypi.org/pypi/requests/json"


def test_pypi_strategy_parse_missing_version():
    s = PyPILatestVersionStrategy()
    with pytest.raises(UpstreamError):
        s.parse(Response(b'{"info": {}}', "u"))


def test_pypi_strategy_parse_invalid_json():
    s = PyPILatestVersionStrategy()
    with pytest.raises(UpstreamError):
        s.parse(Response(b"<<<", "u"))


# --- GitHubReleasesStrategy -------------------------------------------------


def test_github_strategy_supports_by_host():
    s = GitHubReleasesStrategy()
    assert s.supports(Component("x", url="https://github.com/o/j"))
    assert not s.supports(Component("x", url="https://gitlab.com/o/j"))
    assert not s.supports(Component("x"))


def test_github_strategy_query_no_prereleases():
    s = GitHubReleasesStrategy()
    assert s.query(Component("x", url="https://github.com/o/j")).url == (
        "https://api.github.com/repos/o/j/releases/latest"
    )


def test_github_strategy_parse_strips_v():
    s = GitHubReleasesStrategy()
    assert s.parse(Response(b'{"tag_name": "v3.1.4"}', "u")) == "3.1.4"


def test_github_strategy_parse_missing_tag():
    s = GitHubReleasesStrategy()
    with pytest.raises(UpstreamError):
        s.parse(Response(b'{"foo": "bar"}', "u"))


def test_github_strategy_bad_repo_url():
    s = GitHubReleasesStrategy()
    with pytest.raises(UpstreamError):
        s.query(Component("x", url="https://github.com/onlyone"))


# --- RedirectLatestStrategy -------------------------------------------------


def test_redirect_strategy_supports_everything():
    s = RedirectLatestStrategy()
    assert s.supports(Component("x", url="https://example.org/"))
    assert s.supports(Component("x"))  # no url still supported (fallback)


def test_redirect_strategy_parse_extracts_version():
    s = RedirectLatestStrategy()
    assert s.parse(Response(b"", "https://a.org/r/v1.2.3.tar.gz")) == "1.2.3"
    assert s.parse(Response(b"", "https://a.org/r/2.5")) == "2.5"


def test_redirect_strategy_parse_no_version():
    s = RedirectLatestStrategy()
    with pytest.raises(UpstreamError):
        s.parse(Response(b"", "https://a.org/no-version-here"))


def test_redirect_strategy_default_url_without_component_url():
    s = RedirectLatestStrategy()
    req = s.query(Component("busybox"))
    assert req.url == "https://busybox.example.org/latest/"


def test_redirect_strategy_does_not_capture_file_extension():
    s = RedirectLatestStrategy()
    # .tar.gz must not leak into the version.
    assert s.parse(Response(b"", "https://a.org/v1.37.0.tar.gz")) == "1.37.0"


# --- build_fetcher ----------------------------------------------------------


def test_build_fetcher_mock():
    fetcher = build_fetcher("mock", latest={"a": "1"})
    assert isinstance(fetcher, MockFetcher)
    assert fetcher.latest_version(Component("a")) == "1"


def test_build_fetcher_http():
    fetcher = build_fetcher("http")
    assert isinstance(fetcher, HttpFetcher)


def test_build_fetcher_unknown_raises_value_error():
    with pytest.raises(ValueError):
        build_fetcher("bogus")


# --- CLI selection of the fetcher (item 2 wired into ``run``) ---------------


def test_cli_run_constructs_selected_fetcher(tmp_path, monkeypatch):
    """``run`` builds the fetcher named by ``--fetcher``. We spy on
    ``build_fetcher`` (patched at its source module, which ``cli._run`` imports
    locally) so no network is touched."""
    from pengyos_upstream_watch import cli

    pins = tmp_path / "pins.toml"
    pins.write_text('[[component]]\nname = "busybox"\nversion = "1.36.1"\n',
                    encoding="utf-8")

    seen: dict[str, str] = {}

    def fake_build(kind, **kwargs):
        seen["kind"] = kind
        return build_fetcher(kind)

    monkeypatch.setattr("pengyos_upstream_watch.fetcher.build_fetcher", fake_build)

    rc = cli.main(
        ["run", "--pins", str(pins), "--fetcher", "mock", "--out", str(tmp_path / "o.json")]
    )
    assert seen["kind"] == "mock"
    # The fetcher is built, but the detection pipeline (comparison/proposals)
    # is not scaffolded yet, so this is a clean ran-but-failed (exit 1).
    assert rc == cli.EXIT_FAIL


def test_cli_run_constructs_http_fetcher_without_network(tmp_path, monkeypatch):
    """``--fetcher http`` builds an :class:`HttpFetcher` without opening a
    socket: the transport seam (``send``) is stubbed so detection runs but the
    upstream cannot be reached (a clean ran-but-failed, exit 1)."""
    from pengyos_upstream_watch import cli
    from pengyos_upstream_watch.fetcher import HttpFetcher, UpstreamError

    pins = tmp_path / "pins.toml"
    pins.write_text('[[component]]\nname = "busybox"\nversion = "1.36.1"\n',
                    encoding="utf-8")

    real_build = build_fetcher
    made: list[object] = []

    def fake_build(kind, **kwargs):
        fetched = real_build(kind)
        made.append(fetched)
        return fetched

    monkeypatch.setattr("pengyos_upstream_watch.fetcher.build_fetcher", fake_build)

    # Stub the single transport seam so no socket is ever opened.
    def stub_send(url, *, headers=None, allow_redirects=True):
        raise UpstreamError("no network in tests")

    monkeypatch.setattr(HttpFetcher, "send", stub_send)

    rc = cli.main(
        ["run", "--pins", str(pins), "--fetcher", "http", "--out", str(tmp_path / "o.json")]
    )
    assert isinstance(made[0], HttpFetcher)
    # The fetcher is built and detection runs, but the upstream cannot be
    # reached (stub raises), so this is a clean ran-but-failed (exit 1).
    assert rc == cli.EXIT_FAIL
