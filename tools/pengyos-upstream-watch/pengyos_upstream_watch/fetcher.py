"""Upstream ``Fetcher`` abstraction — the only layer that touches the outside.

This module is the *observability* seam of pengyos-upstream-watch. The detection
pipeline (SPEC §3, OBSERVE stage) needs to know, for each pinned component, what
the latest upstream release is. That question is answered by a :class:`Fetcher`;
everything else — comparison, proposal emission — is pure and offline-testable.

Because the fetcher is the *only* thing that reaches the network, the rest of the
tool stays fully deterministic: tests swap the real :class:`HttpFetcher` for a
:class:`MockFetcher` and never leave the sandbox.

Design
------
* :class:`Component` is a small, self-contained description of a watched
  component (its ``name``, current ``version`` and an optional explicit
  ``url``). It is intentionally decoupled from the pins manifest schema, which
  the dedicated ``pengyos-pins`` tool owns — this tool only needs to *watch* a
  component, not author the manifest.
* :class:`Fetcher` is the interface: ``latest_version(component) -> str``.
* :class:`MockFetcher` answers from a plain ``{name: version}`` mapping. It is
  what tests use; it never touches the network.
* :class:`HttpFetcher` is the production implementation. Upstreams disagree on
  how "latest" is exposed, so dispatch is delegated to pluggable
  :class:`LatestVersionStrategy` objects. Every strategy is *pure* — it builds a
  request and parses a response, but never opens a socket itself. The single
  network entry point is :meth:`HttpFetcher.send`, which tests stub, so the
  whole fetcher is covered without ever hitting the network.

Standard library only.
"""

from __future__ import annotations

import abc
import contextlib
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

__all__ = [
    "Component",
    "Fetcher",
    "MockFetcher",
    "HttpFetcher",
    "Request",
    "Response",
    "LatestVersionStrategy",
    "PyPILatestVersionStrategy",
    "GitHubReleasesStrategy",
    "RedirectLatestStrategy",
    "FetcherError",
    "UnknownComponentError",
    "UpstreamError",
    "build_fetcher",
]


# --- Errors -----------------------------------------------------------------


class FetcherError(Exception):
    """Base class for every failure raised by a fetcher."""


class UnknownComponentError(FetcherError):
    """A :class:`MockFetcher` was asked about a component it has no answer for."""


class UpstreamError(FetcherError):
    """The upstream could not be reached, or its response could not be parsed."""


# --- Component --------------------------------------------------------------


@dataclass(frozen=True)
class Component:
    """A watched component: what to watch and, optionally, where to ask.

    Attributes
    ----------
    name:
        The component name, e.g. ``"busybox"``. This is the key the fetcher
        matches against and the label used in proposals.
    version:
        The currently pinned version, if known. The fetcher does not need it to
        answer ``latest_version``; it is carried along so callers (and the HTTP
        query) have full context. ``None`` is legal.
    url:
        An explicit upstream URL to query for the latest release. When omitted
        the :class:`HttpFetcher` falls back to ecosystem-aware defaults (see the
        concrete strategies).
    """

    name: str
    version: str | None = None
    url: str | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


# --- Fetcher interface ------------------------------------------------------


class Fetcher(abc.ABC):
    """Ask an upstream for the latest release of a component.

    Implementations MUST be pure with respect to everything but their data and,
    for :class:`HttpFetcher`, their transport. That keeps the detection pipeline
    offline-testable.
    """

    @abc.abstractmethod
    def latest_version(self, component: Component) -> str:
        """Return the latest upstream version string for ``component``.

        Raises :class:`FetcherError` (or a subclass) when the latest version
        cannot be determined.
        """
        raise NotImplementedError


# --- Mock implementation ----------------------------------------------------


class MockFetcher(Fetcher):
    """A :class:`Fetcher` that answers from a ``{name: version}`` mapping.

    This is the fetcher tests use; it never touches the network. Unknown
    components raise :class:`UnknownComponentError` so callers can distinguish
    "no answer yet" from "the upstream lied".

    Parameters
    ----------
    latest:
        Mapping of component name to the latest version string.
    """

    def __init__(self, latest: Mapping[str, str] | None = None) -> None:
        self._latest: dict[str, str] = dict(latest or {})

    def register(self, name: str, version: str) -> None:
        """Record (or overwrite) the mocked latest version for ``name``."""
        self._latest[name] = version

    def latest_version(self, component: Component) -> str:
        if component.name not in self._latest:
            raise UnknownComponentError(
                f"mock fetcher has no answer for component {component.name!r}; "
                f"known: {sorted(self._latest)}"
            )
        return self._latest[component.name]


# --- HTTP implementation ----------------------------------------------------


@dataclass(frozen=True)
class Response:
    """A raw HTTP response: the body bytes and the final (post-redirect) URL."""

    body: bytes
    url: str


@runtime_checkable
class LatestVersionStrategy(Protocol):
    """A strategy that knows how to read the latest version of one upstream.

    A strategy is *pure*: it builds requests from a :class:`Component` and
    parses a :class:`Response`, but never opens a socket. The network boundary
    is :meth:`HttpFetcher.send`; :class:`HttpFetcher.latest_version` calls that
    seam once per fetch and feeds the result to the strategy's ``parse``. That
    single seam is what tests stub.

    Concrete strategies answer three questions:

    * :meth:`supports` — "do I know how to query this component?"
    * :meth:`query` — "here is the request to make."
    * :meth:`parse` — "here is the latest version, given the response."
    """

    def supports(self, component: Component) -> bool: ...

    def query(self, component: Component) -> Request: ...

    def parse(self, response: Response) -> str: ...


@dataclass(frozen=True)
class Request:
    """A build-able HTTP request produced by a strategy."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)
    allow_redirects: bool = True


class PyPILatestVersionStrategy:
    """Fetch the latest version from the PyPI JSON API.

    Supports components whose ``url`` host is ``pypi.org`` (the common case for
    Python packages) — the ecosystem is inferred from the URL since a bare
    :class:`Component` carries no ecosystem field. Queries
    ``https://pypi.org/pypi/<name>/json`` and reads ``info.version``, which is
    the latest *stable* release.
    """

    SUPPORTED_HOSTS = ("pypi.org", "files.pythonhosted.org")

    def supports(self, component: Component) -> bool:
        host = self._host(component)
        return host in self.SUPPORTED_HOSTS

    def query(self, component: Component) -> Request:
        return Request(f"https://pypi.org/pypi/{component.name}/json")

    def parse(self, response: Response) -> str:
        try:
            data = json.loads(response.body)
        except (ValueError, TypeError) as exc:
            raise UpstreamError(
                f"could not decode PyPI response for {response.url!r}: {exc}"
            ) from exc
        version = data.get("info", {}).get("version")
        if not version:
            raise UpstreamError(f"PyPI returned no version for {response.url!r}")
        return str(version)

    @staticmethod
    def _host(component: Component) -> str | None:
        if not component.url:
            return None
        return urllib.parse.urlsplit(component.url).netloc.lower()


class GitHubReleasesStrategy:
    """Fetch the latest published release from the GitHub Releases API.

    Supports components whose ``url`` host is ``github.com``. Queries the public
    ``/repos/{owner}/{repo}/releases/latest`` endpoint and reads ``tag_name``.
    Set ``prereleases=True`` to query ``.../releases/latest?prereleases=true``
    instead.
    """

    SUPPORTED_HOST = "github.com"

    def __init__(self, prereleases: bool = False) -> None:
        self.prereleases = prereleases

    def supports(self, component: Component) -> bool:
        host = urllib.parse.urlsplit(component.url or "").netloc.lower()
        return host == self.SUPPORTED_HOST

    def query(self, component: Component) -> Request:
        repo = self._repo(component)
        url = f"https://api.github.com/repos/{repo}/releases/"
        url += "latest" if not self.prereleases else "latest?prereleases=true"
        return Request(url, headers={"Accept": "application/vnd.github+json"})

    def parse(self, response: Response) -> str:
        try:
            data = json.loads(response.body)
        except (ValueError, TypeError) as exc:
            raise UpstreamError(
                f"could not decode GitHub response for {response.url!r}: {exc}"
            ) from exc
        tag = data.get("tag_name")
        if not tag:
            raise UpstreamError(f"GitHub returned no release for {response.url!r}")
        return str(tag).lstrip("v")

    def _repo(self, component: Component) -> str:
        path = urllib.parse.urlsplit(component.url or "").path
        path = path.strip("/")
        parts = path.split("/")
        if len(parts) < 2:
            raise UpstreamError(
                f"github url for {component.name!r} must be "
                f"'https://github.com/<owner>/<repo>[...]', got {component.url!r}"
            )
        return f"{parts[0]}/{parts[1]}"


class RedirectLatestStrategy:
    """Generic fallback: follow redirects to the latest release.

    Used when no more specific strategy applies. The upstream is expected to
    answer at ``component.url`` with an HTTP redirect (``3xx``) whose final
    ``Location`` names the latest release. The version is extracted from the
    final URL's path, so a redirect to
    ``https://example.org/busybox/releases/v1.37.0.tar.gz`` yields ``1.37.0``.

    Deliberately forgiving: it raises only when no version can be found, never
    on a malformed body.
    """

    #: Patterns tried, in order, against the final URL path to find a version.
    #: Each allows an optional leading ``v`` (as in ``v1.37.0``) and captures
    #: the numeric core -- ``1.37.0`` or ``1.37``. The core is deliberately
    #: free of a trailing suffix so a file extension such as ``.tar.gz`` is not
    #: mistaken for a prerelease/build tag. Exact prerelease strings (e.g.
    #: ``1.37.0rc1``) come from the PyPI and GitHub strategies, which read the
    #: upstream's own version field rather than a redirect target.
    VERSION_PATTERNS = (
        re.compile(r"(?<![\w.])v?(\d+\.\d+(?:\.\d+)?)"),
    )

    def supports(self, component: Component) -> bool:
        # The generic strategy is the catch-all: it supports anything the HTTP
        # fetcher is asked to fetch.
        return True

    def query(self, component: Component) -> Request:
        url = component.url or self._default_url(component)
        return Request(url, allow_redirects=True)

    def parse(self, response: Response) -> str:
        version = self._extract(response.url)
        if not version:
            raise UpstreamError(
                "could not derive a version from redirect target "
                f"{response.url!r}"
            )
        return version

    def _default_url(self, component: Component) -> str:
        # A reasonable default for projects that expose a redirect at
        # <base>/releases/latest. Kept simple; callers should set url when they
        # know it.
        return f"https://{component.name}.example.org/latest/"

    def _extract(self, url: str) -> str | None:
        path = urllib.parse.urlsplit(url).path
        for pattern in self.VERSION_PATTERNS:
            match = pattern.search(path)
            if match:
                return match.group(1)
        return None


# --- HttpFetcher ------------------------------------------------------------


class HttpFetcher(Fetcher):
    """A :class:`Fetcher` that reads the latest version over HTTP(S).

    Dispatches to the first :class:`LatestVersionStrategy` whose
    :meth:`supports` returns ``True`` for the component. Strategies are tried in
    registration order; the generic :class:`RedirectLatestStrategy` is always
    last as the catch-all.

    The single network entry point is :meth:`send`. Production uses the default
    urllib implementation; tests replace it with a stub, so the whole class is
    covered without ever opening a socket.

    Parameters
    ----------
    strategies:
        Ordered strategies to try. Defaults to PyPI, then GitHub, then the
        generic redirect fallback.
    """

    def __init__(
        self,
        *,
        strategies: list[LatestVersionStrategy] | None = None,
    ) -> None:
        self.strategies: list[LatestVersionStrategy] = (
            list(strategies)
            if strategies is not None
            else [
                PyPILatestVersionStrategy(),
                GitHubReleasesStrategy(),
                RedirectLatestStrategy(),
            ]
        )

    def latest_version(self, component: Component) -> str:
        strategy = next(
            (s for s in self.strategies if s.supports(component)),
            None,
        )
        if strategy is None:  # pragma: no cover - defensive; RedirectLatest catches all
            raise UpstreamError(
                f"no fetcher strategy supports component {component.name!r}"
            )
        request = strategy.query(component)
        try:
            response = self.send(
                request.url,
                headers=request.headers or None,
                allow_redirects=request.allow_redirects,
            )
        except FetcherError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalise any transport error
            raise UpstreamError(
                f"failed to fetch latest version for {component.name!r}: {exc}"
            ) from exc
        try:
            return strategy.parse(response)
        except FetcherError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalise any parse error
            raise UpstreamError(
                f"failed to parse latest version for {component.name!r}: {exc}"
            ) from exc

    def send(self, url: str, *, headers: Mapping[str, str] | None = None,
             allow_redirects: bool = True) -> Response:
        """Transport seam: the single network entry point.

        Returns a :class:`Response` whose ``url`` is the *final* URL after any
        redirects (so redirect-following strategies can read the target).
        Production uses the default urllib implementation; tests replace this
        method with a stub (e.g. ``monkeypatch.setattr(fetcher, "send", stub)``)
        so the whole fetcher is covered without a socket.
        """
        raw = _default_send(
            url, headers=headers, allow_redirects=allow_redirects
        )
        return raw


# --- Transport ----------------------------------------------------------------


def _default_send(
    url: str, *, headers: Mapping[str, str] | None = None, allow_redirects: bool = True
) -> Response:  # pragma: no cover - network
    """Real transport: ``urllib.request.urlopen`` with a permissive UA.

    Returns a :class:`Response` whose ``url`` is the *final* URL after any
    redirects — needed by redirect-following strategies. This is the only
    function in the package that opens a socket.
    """
    hdrs = {"User-Agent": "pengyos-upstream-watch"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with _urlopen(req, timeout=15) as resp:  # pragma: no cover - network
        return Response(body=resp.read(), url=resp.geturl())


def _urlopen(req: urllib.request.Request, timeout: float = 15):  # pragma: no cover - network
    """Context-manager wrapper around ``urllib.request.urlopen``.

    One thin indirection so the socket boundary is a single, patchable entry
    point.
    """
    @contextlib.contextmanager
    def _open():
        resp = urllib.request.urlopen(req, timeout=timeout)
        try:
            yield resp
        finally:
            resp.close()

    return _open()


# --- Factory ------------------------------------------------------------------


def build_fetcher(kind: str, *, latest: Mapping[str, str] | None = None) -> Fetcher:
    """Construct the :class:`Fetcher` named by ``kind``.

    ``"mock"`` -> :class:`MockFetcher` (populated from ``latest``).
    ``"http"`` -> :class:`HttpFetcher` (real network on :meth:`send`).
    Any other value raises :class:`ValueError`.
    """
    if kind == "mock":
        return MockFetcher(latest=latest)
    if kind == "http":
        return HttpFetcher()  # pragma: no cover - network
    raise ValueError(f"unknown fetcher: {kind!r}")
