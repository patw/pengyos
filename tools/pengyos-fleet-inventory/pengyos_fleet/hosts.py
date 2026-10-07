"""The fleet host list and its ``hosts.toml`` schema.

``pengyos-fleet collect`` reads a *host list* — a small TOML document naming the
machines to profile. Each entry is a :class:`Host`: a machine identity plus the
information a :class:`~pengyos_fleet.runner.Runner` needs to reach it.

Schema (``hosts.toml``)::

    [[hosts]]
    name = "n1"                 # required: used as the output directory name
    address = "n1.home.lab"     # required: SSH target (bare host, or user@host)
    fixtures = "tests/fixtures/hosts/n1"   # optional: mock-runner fixture dir

``name`` is the machine's *logical* name and also the directory ``collect``
writes its ``profile.toml`` under. ``address`` is what the SSH runner dials; the
mock runner ignores it. ``fixtures`` is only consulted by the mock runner and,
when relative, is resolved against the directory containing ``hosts.toml`` (so
the file is portable regardless of the current working directory).

The loader is deliberately strict: a missing ``name``/``address``, a malformed
``hosts`` array, or unreadable/invalid TOML all raise a typed
:class:`HostsError` with an actionable message. Standard library only.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


class HostsError(RuntimeError):
    """Raised when a host list is missing, unreadable, or does not conform.

    The message names the offending file (and, where possible, the offending
    entry) so a bad ``hosts.toml`` is easy to fix.
    """


@dataclass(frozen=True)
class Host:
    """One machine in the fleet.

    Attributes:
        name: Logical machine name; also the ``collect`` output directory.
        address: SSH target — a bare host name/address, or ``user@host``.
        fixtures: Absolute path to a directory of command-output fixtures, or
            ``None`` when the host is only reachable over SSH (the mock runner
            rejects it without this). Resolved to an absolute path by
            :func:`load_hosts`.
    """

    name: str
    address: str
    fixtures: str | None = None


def _resolve_fixtures(fixtures: str, base_dir: Path) -> str:
    """Return ``fixtures`` as an absolute path, relative to ``base_dir``.

    ``base_dir`` is the directory containing ``hosts.toml``; fixing relative
    fixture paths against it keeps the host list portable across working
    directories.
    """

    path = Path(fixtures)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return str(path)


def load_hosts(path: str | Path) -> list[Host]:
    """Load and validate the host list at ``path``.

    Reads the TOML document, expects a top-level ``[[hosts]]`` array of tables,
    and returns one :class:`Host` per entry. Relative ``fixtures`` paths are
    resolved against the directory containing the file.

    Raises:
        HostsError: if the file is missing, not valid TOML, does not define a
            ``hosts`` array, contains a malformed entry, or omits a required
            field.
    """

    file_path = Path(path)
    if not file_path.is_file():
        raise HostsError(f"host list not found: {file_path}")

    try:
        with file_path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise HostsError(f"invalid TOML in {file_path}: {exc}") from exc
    except OSError as exc:
        raise HostsError(f"cannot read host list {file_path}: {exc}") from exc

    entries = data.get("hosts", [])
    if not isinstance(entries, list):
        raise HostsError(
            f"{file_path}: 'hosts' must be an array of tables ([[hosts]]), "
            f"got {type(entries).__name__}"
        )

    base_dir = file_path.parent
    hosts: list[Host] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise HostsError(f"{file_path}[{index}]: expected a table")

        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HostsError(f"{file_path}[{index}]: missing required string field 'name'")

        address = entry.get("address")
        if not isinstance(address, str) or not address.strip():
            raise HostsError(
                f"{file_path}[{index}] ({name!r}): missing required string field 'address'"
            )

        fixtures = entry.get("fixtures")
        if fixtures is not None:
            if not isinstance(fixtures, str):
                raise HostsError(
                    f"{file_path}[{index}] ({name!r}): 'fixtures' must be a string path"
                )
            fixtures = _resolve_fixtures(fixtures, base_dir)

        hosts.append(Host(name=name, address=address, fixtures=fixtures))

    if not hosts:
        raise HostsError(f"{file_path}: defines no '[[hosts]]' entries")

    return hosts
