"""The change schema — a structured description of a proposed change.

A *change* is what the policy engine classifies. It carries exactly three
fields, mirroring the CLI surface in ``spec.md``:

- ``action`` — what is being done (``modify``, ``create``, ``delete``,
  ``install``, ``remove``, …). Lowercase identifier.
- ``path`` — the primary filesystem path being touched (always absolute,
  POSIX-style). Required; drives glob matching against the rules file.
- ``target`` — an optional named target (a package, service, key, or other
  named artifact) that a rule may filter on.

The schema is deliberately small and strict: validation happens up front so
the classifier (task 3) only ever reasons about well-formed changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ChangeError

# action: lowercase identifier, e.g. "modify", "install", "remove-key".
_ACTION_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

# path: a non-empty string with no whitespace or NUL (globs match against it).
_PATH_RE = re.compile(r"^\S+$")

# target: a non-empty identifier-ish label (package/service/key name).
_TARGET_RE = re.compile(r"^[^\s/]+$")


@dataclass(frozen=True)
class Change:
    """An immutable, validated proposed change."""

    action: str
    path: str
    target: str | None = None

    @classmethod
    def from_dict(cls, data: object) -> "Change":
        """Build a :class:`Change` from a dict (e.g. parsed JSON).

        Raises :class:`ChangeError` on any schema violation so callers can
        surface a precise message and exit non-zero.
        """

        if not isinstance(data, dict):
            raise ChangeError("change must be a JSON object, got %r" % type(data).__name__)

        action = data.get("action")
        if not isinstance(action, str) or not _ACTION_RE.match(action):
            raise ChangeError(
                "action is required and must be a lowercase identifier "
                "(e.g. 'modify'), got %r" % (action,)
            )

        path = data.get("path")
        if not isinstance(path, str) or not _PATH_RE.match(path):
            raise ChangeError(
                "path is required and must be a non-empty string with no "
                "whitespace, got %r" % (path,)
            )

        target = data.get("target")
        if target is not None:
            if not isinstance(target, str) or not _TARGET_RE.match(target):
                raise ChangeError(
                    "target must be a non-empty string with no slashes when "
                    "present, got %r" % (target,)
                )

        return cls(action=action, path=path, target=target)

    def to_dict(self) -> dict:
        """Serialise back to a plain dict (used by ``--json`` output)."""

        return {"action": self.action, "path": self.path, "target": self.target}

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.action} {self.path}" + (
            f" (target={self.target})" if self.target is not None else ""
        )
