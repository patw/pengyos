"""Validated data models for the PengyOS install engine.

This module is the *data layer* of the engine.
It turns the three plain-text inputs the engine consumes into validated, typed
objects:

- :class:`Release` — the signed release manifest (JSON): the artifact the
  updater consumes. Carries the rootfs + UKI artifact descriptors and the
  signature record.
- :class:`Layout` — the target layout (JSON): where slot A/B, the ESP, and the
  data partition live on the target device.
- :class:`Step` / :class:`Plan` — the install plan itself: an ordered list of
  shell-quotable argv steps (golden / deterministic).
- :class:`ProvisionConfig` — the first-boot provisioning config (``install.toml``):
  hostname, timezone, network, and users (with SSH keys).

Validation is strict but not hostile: missing keys, wrong types, malformed
SHA-256 digests, unknown SSH key types, and invalid timezones all raise a
typed :class:`ValidationError` (with a specific subclass per input) rather than
a confusing ``KeyError``/``TypeError`` downstream. The models are ``frozen``
dataclasses so a validated release/layout/config is immutable — the plan and the
engine can rely on their shape.

Everything here is pure Python (stdlib only). Timezone validity is checked via
``zoneinfo`` when it is available (Linux/Windows ships tzdata; on other platforms
``tzdata`` may need to be installed) — an unavailable tz database raises a clear
:class:`ConfigError` rather than silently accepting an unknown zone.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import shlex
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from pengyos_install._toml_compat import loads as _toml_loads

__all__ = [
    "ValidationError",
    "ReleaseError",
    "LayoutError",
    "ConfigError",
    "Artifact",
    "Signature",
    "Release",
    "Slot",
    "Layout",
    "Step",
    "Plan",
    "User",
    "Network",
    "Timezone",
    "ProvisionConfig",
]


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class ValidationError(Exception):
    """Base class for all validation failures."""


class ReleaseError(ValidationError):
    """The release manifest is malformed."""


class LayoutError(ValidationError):
    """The target layout is malformed."""


class ConfigError(ValidationError):
    """The provisioning config (install.toml) is malformed."""


# --------------------------------------------------------------------------- #
# Small validation helpers
# --------------------------------------------------------------------------- #
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# ssh-ed25519, ssh-rsa, ecdsa-sha2-nistp256/384/521, ssh-dss, sk-*@openssh.com.
_KNOWN_SSH_KEY_TYPES = frozenset(
    {
        "ssh-ed25519",
        "ssh-rsa",
        "ssh-dss",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "sk-ssh-ed25519@openssh.com",
        "sk-ecdsa-sha2-nistp256@openssh.com",
    }
)


def _require(mapping: Any, key: str, *, where: str) -> Any:
    """Return ``mapping[key]`` or raise ``{where}Error`` if absent/not a mapping."""
    if not isinstance(mapping, dict):
        raise ValidationError(f"{where}: expected an object, got {type(mapping).__name__}")
    if key not in mapping:
        raise ValidationError(f"{where}: missing required field {key!r}")
    return mapping[key]


def _require_str(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{where}: expected a non-empty string")
    return value


def _require_int(value: Any, *, where: str) -> int:
    # bool is a subclass of int; reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{where}: expected an integer")
    return value


def _require_positive_int(value: Any, *, where: str) -> int:
    n = _require_int(value, where=where)
    if n <= 0:
        raise ValidationError(f"{where}: expected a positive integer, got {n}")
    return n


def _require_non_empty_list(value: Any, *, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValidationError(f"{where}: expected a list of strings")
    return list(value)


def _require_sub(value: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{where}: expected an object, got {type(value).__name__}")
    return value


def _validate_sha256(value: Any, *, where: str) -> str:
    digest = _require_str(value, where=where)
    if not _SHA256_RE.match(digest):
        raise ValidationError(
            f"{where}: sha256 must be 64 lowercase hex characters, got {digest!r}"
        )
    return digest


def _validate_ssh_key(value: str, *, where: str) -> str:
    """Validate a single OpenSSH public-key line.

    Accepts ``<type> <base64> [comment]`` where *type* is a known algorithm and
    *base64* decodes cleanly. Comment is optional.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{where}: expected a non-empty SSH public key")
    parts = value.split()
    if len(parts) < 2:
        raise ValidationError(f"{where}: malformed SSH key {value!r}")
    key_type, encoded = parts[0], parts[1]
    if key_type not in _KNOWN_SSH_KEY_TYPES:
        raise ValidationError(
            f"{where}: unknown SSH key type {key_type!r} "
            f"(known: {', '.join(sorted(_KNOWN_SSH_KEY_TYPES))})"
        )
    try:
        base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValidationError(f"{where}: SSH key payload is not valid base64")
    return value.strip()


def _validate_timezone(value: str, *, where: str) -> str:
    zone = _require_str(value, where=where)
    if not zone:
        raise ValidationError(f"{where}: timezone must be non-empty")
    # Reject obviously-invalid zone names cheaply before touching the tz database.
    if zone.startswith("/") or "\x00" in zone or zone in {"", ".."}:
        raise ValidationError(f"{where}: invalid timezone {zone!r}")
    # zoneinfo ships on Linux/Windows; on other platforms tzdata may be needed.
    if sys.version_info >= (3, 9):
        try:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            ZoneInfo(zone)
        except ZoneInfoNotFoundError:
            raise ValidationError(f"{where}: unknown timezone {zone!r}")
        except ModuleNotFoundError:  # pragma: no cover - platform dependent
            raise ConfigError(
                f"{where}: zoneinfo/tzdata unavailable; install the 'tzdata' "
                f"package to validate timezones on this platform"
            )
    return zone


# --------------------------------------------------------------------------- #
# Release manifest
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Artifact:
    """A downloadable/uploadable artifact carried by the release manifest."""

    path: str
    sha256: str
    size: int

    @classmethod
    def _from_dict(cls, data: dict[str, Any], *, where: str) -> "Artifact":
        sub = _require_sub(data, where=where)
        return cls(
            path=_require_str(sub.get("path", ""), where=f"{where}.path"),
            sha256=_validate_sha256(sub.get("sha256"), where=f"{where}.sha256"),
            size=_require_positive_int(sub.get("size"), where=f"{where}.size"),
        )


@dataclass(frozen=True)
class Signature:
    """The signature record attached to the release.

    ``verified`` is authoritative: the engine refuses to install a release whose
    signature is not verified (see the ``install`` unit).
    """

    alg: str
    cert: str
    data: str
    verified: bool

    @classmethod
    def _from_dict(cls, data: dict[str, Any], *, where: str) -> "Signature":
        sub = _require_sub(data, where=where)
        alg = _require_str(sub.get("alg", ""), where=f"{where}.alg")
        if not alg:
            raise ValidationError(f"{where}: signature algorithm must be non-empty")
        return cls(
            alg=alg,
            cert=_require_str(sub.get("cert", ""), where=f"{where}.cert"),
            data=_require_str(sub.get("data", ""), where=f"{where}.data"),
            verified=bool(sub.get("verified", False)),
        )


@dataclass(frozen=True)
class Release:
    """The signed release manifest: the artifact the updater consumes."""

    name: str
    edition: str
    version: str
    slot: str
    rootfs: Artifact
    uki: Artifact
    signature: Signature

    @classmethod
    def from_json(cls, text: str) -> "Release":
        """Parse and validate a release manifest from *text* (JSON)."""
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ReleaseError(f"release manifest is not valid JSON: {exc}") from exc
        return cls._from_raw(raw)

    @classmethod
    def from_json_file(cls, path: str) -> "Release":
        with open(path, encoding="utf-8") as handle:
            return cls.from_json(handle.read())

    @classmethod
    def _from_raw(cls, raw: Any) -> "Release":
        if not isinstance(raw, dict):
            raise ReleaseError("release manifest must be a JSON object")
        try:
            return cls(
                name=_require_str(raw.get("name", ""), where="release.name"),
                edition=_require_str(raw.get("edition", ""), where="release.edition"),
                version=_require_str(raw.get("version", ""), where="release.version"),
                slot=_require_str(raw.get("slot", ""), where="release.slot"),
                rootfs=Artifact._from_dict(
                    _require(raw, "rootfs", where="release.rootfs"), where="release.rootfs"
                ),
                uki=Artifact._from_dict(
                    _require(raw, "uki", where="release.uki"), where="release.uki"
                ),
                signature=Signature._from_dict(
                    _require(raw, "signature", where="release.signature"),
                    where="release.signature",
                ),
            )
        except ValidationError as exc:
            raise ReleaseError(str(exc)) from exc

    def to_json(self) -> str:
        """Serialize back to a stable, deterministic JSON string."""
        return json.dumps(
            {
                "name": self.name,
                "edition": self.edition,
                "version": self.version,
                "slot": self.slot,
                "rootfs": {
                    "path": self.rootfs.path,
                    "sha256": self.rootfs.sha256,
                    "size": self.rootfs.size,
                },
                "uki": {
                    "path": self.uki.path,
                    "sha256": self.uki.sha256,
                    "size": self.uki.size,
                },
                "signature": {
                    "alg": self.signature.alg,
                    "cert": self.signature.cert,
                    "data": self.signature.data,
                    "verified": self.signature.verified,
                },
            },
            indent=2,
            sort_keys=True,
        )

    @property
    def is_signed(self) -> bool:
        """True when the release signature has been verified."""
        return self.signature.verified


# --------------------------------------------------------------------------- #
# Target layout
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Slot:
    """A named partition on the target device."""

    partition: str
    type: str = ""
    index: int = 0

    @classmethod
    def _from_dict(cls, data: dict[str, Any], *, where: str) -> "Slot":
        sub = _require_sub(data, where=where)
        return cls(
            partition=_require_str(sub.get("partition", ""), where=f"{where}.partition"),
            type=str(sub.get("type", "") or ""),
            index=_require_int(sub.get("index"), where=f"{where}.index"),
        )


@dataclass(frozen=True)
class Layout:
    """The target layout: where slots, the ESP, and data live.

    The layout describes the *physical* partitioning — which slots exist, plus
    the ESP and data partitions. It does **not** decide which slot is active; the
    release's ``slot`` is authoritative for that (cross-checked against the
    declared slots during planning). Slot indices must be unique and the target
    must be a non-empty device path.
    """

    target: str
    table: str
    slots: dict[str, Slot]
    esp: Slot
    data: Slot

    @classmethod
    def from_json(cls, text: str) -> "Layout":
        """Parse and validate a target layout from *text* (JSON)."""
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LayoutError(f"layout is not valid JSON: {exc}") from exc
        return cls._from_raw(raw)

    @classmethod
    def from_json_file(cls, path: str) -> "Layout":
        with open(path, encoding="utf-8") as handle:
            return cls.from_json(handle.read())

    @classmethod
    def _from_raw(cls, raw: Any) -> "Layout":
        if not isinstance(raw, dict):
            raise LayoutError("layout must be a JSON object")
        try:
            target = _require_str(raw.get("target", ""), where="layout.target")
            table = _require_str(raw.get("table", ""), where="layout.table")
            if table not in {"gpt", "msdos"}:
                raise LayoutError(
                    f"layout.table must be 'gpt' or 'msdos', got {table!r}"
                )

            slots_raw = _require(raw, "slots", where="layout.slots")
            if not isinstance(slots_raw, dict) or not slots_raw:
                raise LayoutError("layout.slots must be a non-empty object")
            slots: dict[str, Slot] = {}
            for name, slot_data in slots_raw.items():
                slots[name] = Slot._from_dict(
                    slot_data, where=f"layout.slots.{name}"
                )
            esp = Slot._from_dict(
                _require(raw, "esp", where="layout.esp"), where="layout.esp"
            )
            data = Slot._from_dict(
                _require(raw, "data", where="layout.data"), where="layout.data"
            )

            indices = [slot.index for slot in slots.values()] + [esp.index, data.index]
            if len(set(indices)) != len(indices):
                seen: set[int] = set()
                duplicates = {
                    i for i in indices if i in seen or seen.add(i)
                }
                raise LayoutError(
                    "layout partition indices must be unique; "
                    f"duplicates: {duplicates}"
                )

            return cls(
                target=target,
                table=table,
                slots=slots,
                esp=esp,
                data=data,
            )
        except ValidationError as exc:
            raise LayoutError(str(exc)) from exc

    def to_json(self) -> str:
        """Serialize back to a stable, deterministic JSON string."""
        return json.dumps(
            {
                "target": self.target,
                "table": self.table,
                "slots": {name: slot.__dict__ for name, slot in self.slots.items()},
                "esp": self.esp.__dict__,
                "data": self.data.__dict__,
            },
            indent=2,
            sort_keys=True,
        )

    def slot(self, name: str) -> Slot:
        """Return the slot *name* (raises :class:`KeyError` if unknown)."""
        return self.slots[name]

    def assert_has_slot(self, name: str) -> None:
        """Raise :class:`LayoutError` if *name* is not a declared slot.

        The release's ``slot`` is authoritative for the write target; this is the
        cross-check the planning step performs against the declared slots.
        """
        if name not in self.slots:
            raise LayoutError(
                f"release slot {name!r} is not one of the declared slots "
                f"{sorted(self.slots)}"
            )


# --------------------------------------------------------------------------- #
# Install plan (ordered, shell-quotable steps)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Step:
    """A single ordered install step: a shell-quotable argv.

    A step is the atomic unit the :mod:`pengyos_install.install` engine executes.
    ``argv`` is a list of arguments (never a single shell string) so the engine
    can run it without a shell; :meth:`shlex_join` renders it for humans/scripts.
    ``description`` is optional human context; ``env`` is an optional per-step
    environment overlay.

    ``redirect`` is an optional *shell-script* concern: when set, the shell
    rendering appends ``> '<redirect>'`` after the argv. It is **not** part of
    ``argv`` (a ``>`` is a shell metacharacter, not an argument — passing it to
    the runner would make it a literal argument), so the engine still runs a
    clean argv. File-content writers (see
    :mod:`pengyos_install.provision`) emit their content to **stdout** via
    ``printf`` and set ``redirect`` so the rendered shell script writes the file.
    """

    argv: tuple[str, ...]
    description: str = ""
    env: tuple[tuple[str, str], ...] = ()
    redirect: str = ""

    @classmethod
    def from_argv(cls, argv: Sequence[str], *, description: str = "",
                  env: Mapping[str, str] | None = None,
                  redirect: str = "") -> "Step":
        parts = [str(a) for a in argv]
        if not parts:
            raise ValidationError("a step must have a non-empty argv")
        env_items = tuple(sorted((k, str(v)) for k, v in (env or {}).items()))
        return cls(argv=tuple(parts), description=description, env=env_items,
                   redirect=redirect)

    def to_argv(self) -> list[str]:
        """The argv as a plain list (the executable form the runner consumes).

        The ``redirect`` is deliberately excluded — it is a shell-script concern,
        not an argument. The runner executes a clean argv (content to stdout).
        """
        return list(self.argv)

    def to_json(self) -> dict[str, Any]:
        node: dict[str, Any] = {"argv": list(self.argv)}
        if self.description:
            node["description"] = self.description
        if self.env:
            node["env"] = {k: v for k, v in self.env}
        if self.redirect:
            node["redirect"] = self.redirect
        return node

    def shlex_join(self) -> str:
        """Render the argv as a single shell-quotable string (for humans)."""
        return shlex.join(self.argv)

    def __str__(self) -> str:
        return self.shlex_join()


@dataclass(frozen=True)
class Plan:
    """An ordered, validated list of :class:`Step`s — the install plan.

    Deterministic: steps are stored in construction order and the JSON/shell
    renderings are stable, so a plan is golden/reproducible.
    """

    steps: tuple[Step, ...]

    @classmethod
    def from_steps(cls, steps: Iterable[Step]) -> "Plan":
        return cls(steps=tuple(steps))

    @classmethod
    def from_json(cls, text: str) -> "Plan":
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"plan is not valid JSON: {exc}") from exc
        if not isinstance(raw, list):
            raise ValidationError("plan JSON must be an array of step objects")
        try:
            return cls(steps=tuple(
                Step.from_argv(
                    s["argv"],
                    description=s.get("description", ""),
                    env=s.get("env"),
                    redirect=s.get("redirect", ""),
                )
                for s in raw
            ))
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValidationError(f"plan step must be an object with an 'argv' field: {exc}") from exc

    def to_json(self) -> str:
        """Serialize back to a stable, deterministic JSON string."""
        return json.dumps([s.to_json() for s in self.steps], indent=2, sort_keys=True)

    def to_shell_script(self) -> str:
        """Render the plan as a shell script (one command per step).

        A step with a ``redirect`` has ``> '<redirect>'`` appended after its
        argv — the shell performs the redirect (a ``>`` is never an argument).
        """
        lines = ["#!/usr/bin/env bash", "set -euo pipefail", ""]
        for i, step in enumerate(self.steps, 1):
            if step.description:
                lines.append(f"# {i}. {step.description}")
            rendered = step.shlex_join()
            if step.redirect:
                rendered += f" > {shlex.quote(step.redirect)}"
            lines.append(rendered)
        return "\n".join(lines) + "\n"

    def __len__(self) -> int:
        return len(self.steps)

    def __getitem__(self, index: int) -> Step:
        return self.steps[index]


# --------------------------------------------------------------------------- #
# Provisioning config (install.toml)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class User:
    """A first-boot user account with optional authorized SSH keys."""

    name: str
    shell: str
    groups: list[str]
    ssh_keys: list[str]

    @classmethod
    def _from_dict(cls, data: dict[str, Any], *, where: str) -> "User":
        sub = _require_sub(data, where=where)
        name = _require_str(sub.get("name", ""), where=f"{where}.name")
        shell = _require_str(sub.get("shell", ""), where=f"{where}.shell")
        groups = _require_non_empty_list(sub.get("groups", []), where=f"{where}.groups")
        raw_keys = sub.get("ssh_keys", [])
        if raw_keys is None:
            raw_keys = []
        if not isinstance(raw_keys, list) or not all(
            isinstance(k, str) for k in raw_keys
        ):
            raise ValidationError(f"{where}.ssh_keys: expected a list of key strings")
        ssh_keys = [_validate_ssh_key(k, where=f"{where}.ssh_keys") for k in raw_keys]
        return cls(name=name, shell=shell, groups=groups, ssh_keys=ssh_keys)


@dataclass(frozen=True)
class Network:
    """First-boot network configuration."""

    manager: str

    @classmethod
    def _from_dict(cls, data: dict[str, Any], *, where: str) -> "Network":
        sub = _require_sub(data, where=where)
        manager = _require_str(sub.get("manager", ""), where=f"{where}.manager")
        if not manager:
            raise ValidationError(f"{where}.manager: must be non-empty")
        return cls(manager=manager)


@dataclass(frozen=True)
class Timezone:
    """First-boot timezone configuration."""

    zone: str

    @classmethod
    def _from_dict(cls, data: dict[str, Any], *, where: str) -> "Timezone":
        sub = _require_sub(data, where=where)
        zone = _require_str(sub.get("zone", ""), where=f"{where}.zone")
        _validate_timezone(zone, where=f"{where}.zone")
        return cls(zone=zone)


@dataclass(frozen=True)
class ProvisionConfig:
    """The first-boot provisioning config (``install.toml``).

    Renders the host's first-boot state: hostname, timezone, network manager, and
    users (each with authorized SSH keys). All fields are validated on load.
    """

    hostname: str
    timezone: Timezone
    network: Network
    users: list[User]
    # Extra, un-modeled keys are preserved (verbatim) so the config is lossless
    # and future fields survive a round-trip even before the engine models them.
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_toml(cls, text: str) -> "ProvisionConfig":
        """Parse and validate a provisioning config from *text* (TOML)."""
        try:
            raw = _toml_loads(text)
        except Exception as exc:  # tomllib/tomli raise TOMLDecodeError (a ValueError)
            raise ConfigError(f"install.toml is not valid TOML: {exc}") from exc
        return cls._from_raw(raw)

    @classmethod
    def from_toml_file(cls, path: str) -> "ProvisionConfig":
        with open(path, encoding="utf-8") as handle:
            return cls.from_toml(handle.read())

    @classmethod
    def _from_raw(cls, raw: Any) -> "ProvisionConfig":
        if not isinstance(raw, dict):
            raise ConfigError("install.toml must contain a top-level table")
        try:
            hostname = _require_str(raw.get("hostname", ""), where="hostname")
            if not hostname or any(c.isspace() for c in hostname):
                raise ConfigError(
                    "hostname must be a non-empty, whitespace-free string"
                )

            tz_raw = _require(raw, "timezone", where="timezone")
            net_raw = _require(raw, "network", where="network")
            users_raw = _require(raw, "users", where="users")
            if not isinstance(users_raw, list) or not users_raw:
                raise ConfigError("users must be a non-empty array of tables")

            names: set[str] = set()
            users: list[User] = []
            for entry in users_raw:
                user = User._from_dict(entry, where="users")
                if user.name in names:
                    raise ConfigError(
                        f"duplicate user name {user.name!r} in users"
                    )
                names.add(user.name)
                users.append(user)

            known = {"hostname", "timezone", "network", "users"}
            extra = {k: v for k, v in raw.items() if k not in known}
            return cls(
                hostname=hostname,
                timezone=Timezone._from_dict(tz_raw, where="timezone"),
                network=Network._from_dict(net_raw, where="network"),
                users=users,
                extra=extra,
            )
        except ValidationError as exc:
            raise ConfigError(str(exc)) from exc

    def to_toml(self) -> str:
        """Serialize back to a stable, deterministic TOML string.

        Scalars are emitted via :func:`json.dumps` (valid TOML for str/bool/int;
        ``true``/``false`` and quoted strings), arrays as TOML arrays. Kept
        explicit so the rendered content is golden and reviewable.
        """
        lines: list[str] = [f'hostname = {json.dumps(self.hostname)}', ""]
        lines.append("[timezone]")
        lines.append(f'zone = {json.dumps(self.timezone.zone)}')
        lines.append("")
        lines.append("[network]")
        lines.append(f'manager = {json.dumps(self.network.manager)}')
        lines.append("")
        for user in self.users:
            lines.append("[[users]]")
            lines.append(f'name = {json.dumps(user.name)}')
            lines.append(f'shell = {json.dumps(user.shell)}')
            lines.append(f"groups = {json.dumps(user.groups)}")
            lines.append(f"ssh_keys = {json.dumps(user.ssh_keys)}")
            lines.append("")
        for key, value in sorted(self.extra.items()):
            lines.append(f"{key} = {json.dumps(value)}")
            lines.append("")
        return "\n".join(lines).rstrip("\n") + "\n"

    @property
    def primary_user(self) -> User | None:
        """The first declared user, or ``None`` if there are none."""
        return self.users[0] if self.users else None
