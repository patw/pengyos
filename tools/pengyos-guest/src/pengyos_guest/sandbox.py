"""The no-host-writes sandbox guarantee.

PengyOS guests must never touch the host outside the guest's home mount and the
exports you declare (spec: "no host writes outside the guest's home mount and
the exports you declare"). This module turns that policy into an assertable
contract:

- :func:`declared_host_paths` collects the host paths a guest is *allowed* to
  reference — exactly the ``host:guest[:mode]`` bind-mount specs declared in
  ``guests.toml``. Everything the tool touches on the host (the container's bind
  mounts) comes from here, so this is the complete allowlist.
- :func:`undeclared_host_paths` scans a list of argv for absolute host-path
  tokens that are *not* in the allowlist.
- :class:`SandboxGuard` ties them together: it holds the allowlist and, before
  each command runs, asserts that the argv only references declared paths.

Why argv inspection? The only way a guest can write to the host is by pointing
``podman``/``distrobox`` at a host path — and the only host paths the tool ever
emits are the declared bind mounts. So scanning argv is both necessary and
sufficient: if no undeclared host path ever appears in the argv handed to the
runner, the guest cannot write outside its declared sandbox.

:class:`SandboxGuard` is opt-in: the runners default to *no* guard, so existing
callers are unaffected. When a guard is present it is checked *before* each
command executes, giving a real runtime guarantee (not just a test-time
assertion).
"""

from __future__ import annotations

from typing import Iterable, Sequence


class SandboxError(Exception):
    """A command referenced a host path outside the declared allowlist."""


def declared_host_paths(guests: Iterable[object]) -> set[str]:
    """The host paths a guest set is allowed to reference.

    These are exactly the ``host:guest[:mode]`` bind-mount specs declared in each
    guest's ``mounts``. The argument is duck-typed on ``.mounts`` so this stays
    decoupled from the config model (it works with :class:`~pengyos_guest.config.Guest`
    or any object exposing a ``mounts`` list).
    """
    paths: set[str] = set()
    for guest in guests:
        paths.update(guest.mounts)
    return paths


def undeclared_host_paths(
    argvs: Iterable[Sequence[str]],
    allowlist: Iterable[str],
) -> list[str]:
    """Absolute host-path tokens in *argvs* that are not in *allowlist*.

    Every token of every argv is scanned. A token is an "absolute host path"
    iff it starts with ``/`` (container image refs, guest names, package names,
    and flags never do). Any such token not in *allowlist* is undeclared.
    Returns the offending tokens — deduplicated, in first-seen order.
    """
    allowed = set(allowlist)
    found: list[str] = []
    seen: set[str] = set()
    for argv in argvs:
        for token in argv:
            if not token.startswith("/"):
                continue
            if token in allowed or token in seen:
                continue
            seen.add(token)
            found.append(token)
    return found


def is_sandboxed(argvs: Iterable[Sequence[str]], allowlist: Iterable[str]) -> bool:
    """True iff *argvs* only reference host paths in *allowlist*."""
    return not undeclared_host_paths(argvs, allowlist)


class SandboxGuard:
    """Pre-execution host-path allowlist.

    Holds the set of declared host paths. :meth:`check` raises
    :class:`SandboxError` if a command's argv references any absolute host path
    outside the allowlist. :meth:`set_allowlist` swaps the allowlist —
    :meth:`~pengyos_guest.engines.GuestManager.apply` calls it per guest so each
    guest is validated against its own declared mounts.
    """

    def __init__(self, allowlist: Iterable[str] = ()) -> None:
        self.allowlist: set[str] = set(allowlist)

    def set_allowlist(self, allowlist: Iterable[str]) -> None:
        """Replace the allowlist with *allowlist*."""
        self.allowlist = set(allowlist)

    def check(self, argv: Sequence[str]) -> None:
        """Raise :class:`SandboxError` if *argv* references an undeclared host path."""
        offenders = undeclared_host_paths([argv], self.allowlist)
        if offenders:
            raise SandboxError(
                "command references undeclared host path(s) "
                f"{offenders}; declared host paths: "
                f"{sorted(self.allowlist) or '(none)'}"
            )


__all__ = [
    "SandboxError",
    "SandboxGuard",
    "declared_host_paths",
    "is_sandboxed",
    "undeclared_host_paths",
]
