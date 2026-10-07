"""Validate a parsed :class:`Service` against the schema rules.

Two layers of validation are provided:

* :func:`validate` — the *structural* schema layer (item 2): required fields
  present, enums valid, and every list/map field shaped as the schema defines
  it. Shapes enforced here: ``ports`` / ``volumes`` are ``"<host>:<container>"``
  pairs (host side optional), ``env`` entries are ``"key=value"``. These catch
  typos such as ``--volume /a`` (no container side) at load time, before any
  unit is emitted.

* :func:`validate_semantic` — the *semantic policy* layer (item 4): returns
  every policy violation (the service is reachable, publishes at least one
  port; every persistent volume lives outside the image). These are the
  USE-CASES US-SRV-2 guarantees ("reachable on the LAN", "data persists across
  updates").

  Reachability is opt-out: a service with ``reachable: false`` (see
  :attr:`Service.reachable`) is exempt from the reachability rule — this is the
  "escape hatch" for non-network services (workers, cron, batch jobs) that
  legitimately publish no ports. Volume-persistence is never exempt.

* :func:`validate_policy` — raises :class:`PolicyError` for *fatal* policy
  violations. Reachability is **always** fatal; volume-persistence is
  **advisory** by default and only fatal when ``strict=True`` (the ``--strict``
  flag). :func:`policy_warnings` returns the advisory (non-fatal) violations.

The two layers are deliberately separate so each policy rule has its own
failing fixture and a distinct error type (policy vs. schema).
"""

from __future__ import annotations

import re
from typing import Optional

from .model import Service

# Valid systemd "Restart=" values. Anything else would make the emitted unit
# fail to load, so we reject it up front with a clean message.
VALID_RESTART = frozenset(
    {
        "no",
        "on-success",
        "on-failure",
        "on-abnormal",
        "on-watchdog",
        "on-abort",
        "always",
    }
)

# A published port is "[host:]<container>": the host side is optional (podman's
# --publish accepts a bare container port, mapping it to a random host port),
# but there must be at least one colon-free container side.
_PORT_RE = re.compile(r"^[^:]+(?::[^:]+)?$")

# A bind mount is "<host>:<container>" with an optional trailing options group
# (e.g. ":Z" SELinux label, ":ro" read-only). Both the host and container sides
# are mandatory and must be colon-free; the options group is optional.
_VOLUME_RE = re.compile(r"^[^:]+:[^:]+(?::[^:]+)?$")

# An env entry is "key=value". The key is a POSIX-ish identifier; the value may
# be empty (e.g. `FOO=`) but the '=' separator is mandatory.
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=(.*)$")


def _port_or_volume_error(item: str, kind: str) -> str:
    return f"{kind} must be a '<host>:<container>' pair, got {item!r}"


def _env_error(item: str) -> str:
    return f"env entry must be 'key=value', got {item!r}"


def validate_structure(service: Service) -> list[str]:
    """Check the *shape* of every list/map field per the schema.

    Returns a list of human-readable errors (empty == structurally valid).
    """

    errors: list[str] = []

    for port in service.ports:
        if not _PORT_RE.match(port):
            errors.append(_port_or_volume_error(port, "port"))

    for volume in service.volumes:
        if not _VOLUME_RE.match(volume):
            errors.append(_port_or_volume_error(volume, "volume"))

    for key, value in service.env.items():
        if not _ENV_RE.match(f"{key}={value}"):
            errors.append(_env_error(f"{key}={value}"))

    return errors


def validate(service: Service) -> list[str]:
    """Return a list of human-readable validation errors (empty == valid).

    Combines the structural checks (field shapes) with the required-field and
    enum checks. Semantic policy rules (reachability, volume persistence) live in
    :func:`validate_semantic` as a distinct layer.
    """

    errors: list[str] = []

    if not service.name or not service.name.strip():
        errors.append("name is required")

    if not service.image or not service.image.strip():
        errors.append("image is required")

    if service.restart not in VALID_RESTART:
        errors.append(
            f"restart must be one of {sorted(VALID_RESTART)}, got {service.restart!r}"
        )

    errors.extend(validate_structure(service))

    return errors


# The two semantic policy rules (item 4). Each is checked by its own helper so
# the CLI can treat them with different severity: reachability is always fatal,
# volume-persistence is advisory unless ``--strict`` is passed. See spec.md.
RULE_REACHABILITY = "reachability"
RULE_VOLUME_PERSISTENCE = "volume_persistence"


def _reachability_errors(service: Service) -> list[str]:
    """Policy errors for the *reachability* rule (empty == reachable).

    A reachable service (``service.reachable is not False``) must publish at
    least one port: without one it cannot be reached on the LAN, which is the
    whole point of US-SRV-2. The ``reachable: false`` escape hatch exempts
    non-network services (workers, cron, batch jobs) that legitimately publish
    no ports.
    """

    if service.reachable and not service.ports:
        return [
            "service publishes no ports; add at least one to be reachable on the "
            "LAN (or set reachable: false for a non-network service)"
        ]
    return []


def _volume_persistence_errors(service: Service) -> list[str]:
    """Policy errors for the *volume-persistence* rule (empty == all ok).

    The *host* side of every volume must be a real host data directory (where
    the data actually lives), not a path inside the image/container. A bind
    mount whose source is a known image/system directory (``/usr/share``,
    ``/etc``, ``/lib``, ``/bin``) is almost always a mistake — the user meant to
    reference a container path, and that source is not persistent (it is
    replaced when the image is updated). This rule is never exempt, not even by
    ``reachable: false``.
    """

    errors: list[str] = []
    for volume in service.volumes:
        host_path = _host_path(volume)
        if host_path is not None and _is_image_path(host_path):
            errors.append(
                f"volume {volume!r} uses {host_path!r} (an image/system path) as its "
                "host source; mount persistent data under a real host directory "
                "so it survives updates"
            )
    return errors


def validate_semantic(service: Service) -> list[str]:
    """Check the USE-CASES US-SRV-2 *policy* rules (empty == policy-valid).

    Returns *all* policy violations (both rules), each golden-testable and
    independently failing. The two rules are reported together here; the CLI
    classifies them by rule so it can treat reachability as fatal and
    volume-persistence as advisory (see :func:`validate_policy`).

    1. **Reachability** — a reachable service publishes at least one port. A
       service with no published ports cannot be reached on the LAN. **Opt-out**:
       a service with ``service.reachable is False`` is a non-network service
       (worker, cron, batch job) that legitimately publishes no ports, so this
       rule is skipped for it.
    2. **Volume persistence** — the *host* side of every volume must be a real
       host directory (where the data actually lives), not a path inside the
       image/container. This rule is never exempt.

    These are policy (not schema) rules: a service can be structurally valid yet
    semantically incomplete.
    """

    return (
        _reachability_errors(service) + _volume_persistence_errors(service)
    )


def validate_policy(service: Service, strict: bool = False) -> None:
    """Validate the semantic policy rules, raising :class:`PolicyError` if fatal.

    Reachability is **always** fatal. Volume-persistence is **advisory** by
    default (a warning, not an error) and only becomes fatal when ``strict`` is
    set (``--strict`` on the CLI). This matches the item: by default only
    reachability is fatal; ``--strict`` makes volume-persistence fatal too.
    """

    fatal = _reachability_errors(service)
    if strict:
        fatal.extend(_volume_persistence_errors(service))
    if fatal:
        raise PolicyError(fatal)


def policy_warnings(service: Service) -> list[str]:
    """Return the *advisory* policy violations (empty == none).

    A pure view of the service: every volume-persistence violation, regardless
    of ``strict``. The CLI decides whether to surface them as warnings
    (non-strict) or raise them as fatal (strict); this function itself reports
    all of them. Reachability is never advisory, so it is never included.
    """

    return _volume_persistence_errors(service)


# Known image/system directories. A bind mount whose *host source* is one of
# these is almost certainly a mistake (the user meant to reference a container
# path), and such a source is not persistent — it is replaced when the image is
# updated. Kept as a module constant so the rule is golden-testable and easy to
# extend.
IMAGE_SYSTEM_PATHS = (
    "/usr/share",
    "/usr/lib",
    "/lib",
    "/bin",
    "/sbin",
    "/etc",
)


def _is_image_path(path: str) -> bool:
    """True if ``path`` is a known image/system directory (not a host data dir)."""

    return any(path == p or path.startswith(p + "/") for p in IMAGE_SYSTEM_PATHS)


def _host_path(volume: str) -> Optional[str]:
    """Return the host (source) side of a ``"<host>:<container>"`` volume, or None.

    The structural check guarantees a valid shape, but this is defensive: callers
    that reach here with an unparseable volume get ``None`` and skip the policy
    rule rather than raising.
    """

    parts = volume.split(":")
    if parts and parts[0]:
        return parts[0]
    return None


def validate_strict(service: Service) -> None:
    """Validate ``service`` and raise :class:`SchemaError` if invalid.

    Convenience for the CLI, which wants to print a clean error and exit
    non-zero rather than raise an exception with a traceback.
    """

    errors = validate(service)
    if errors:
        raise SchemaError(errors)


class SchemaError(RuntimeError):
    """Raised when a service fails the structural schema. Carries every error."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class PolicyError(RuntimeError):
    """Raised when a service passes the schema but violates a semantic policy.

    Distinct from :class:`SchemaError` so the CLI can report policy failures
    (reachability, volume persistence) separately from schema failures.
    """

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


__all__ = [
    "IMAGE_SYSTEM_PATHS",
    "RULE_REACHABILITY",
    "RULE_VOLUME_PERSISTENCE",
    "VALID_RESTART",
    "PolicyError",
    "SchemaError",
    "policy_warnings",
    "validate",
    "validate_semantic",
    "validate_policy",
    "validate_structure",
    "validate_strict",
]
