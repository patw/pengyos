"""Load and validate a ``pengyos-hwprofile`` profile.

This is the single entry point the rest of the tool uses to read a hardware
profile off disk. It leans on the *shared* hwprofile schema
(:mod:`pengyos_hwprofile.schema`) rather than reimplementing validation: a
profile is a TOML document that must conform to the hwprofile schema, so loading
is "read the TOML, then validate it with the schema's ``validate``".

The sibling ``pengyos-hwprofile`` tool emits exactly this artifact as
``profile.toml``; that is the format this tool consumes. The spec's loose
``profile.json`` wording refers to this same profile document — the on-disk
shape is TOML.

Two failure modes are surfaced *cleanly* (a single :class:`ProfileError` with a
human message, never a traceback):

* the file is missing / unreadable, or the bytes are not valid TOML; and
* the bytes are valid TOML but violate the hwprofile schema (a missing required
  field, a wrong type, an unknown field, an unknown top-level section, …).

Standard library only at runtime.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

# Imported lazily inside :func:`load_profile` so this module stays importable
# (and importable-in-tests) even when the sibling tool is absent from
# ``sys.path``. The runtime contract is "hwprofile is present"; the lazy import
# just keeps the import graph honest and the scaffold testable in isolation.


class ProfileError(Exception):
    """Raised when a profile file cannot be loaded or violates the schema.

    The message is always human-facing and names the offending file (or, for a
    schema violation, the hwprofile path that failed). Callers should catch this
    and print it to stderr with a non-zero exit — never let it escape as a
    traceback.
    """


def load_profile(path: str | Path) -> dict:
    """Read and validate a profile file, returning the parsed document.

    Parameters:
        path: Path to a ``profile.toml`` (the hwprofile artifact).

    Returns:
        The parsed document as a plain ``dict`` (already validated against the
        hwprofile schema).

    Raises:
        ProfileError: if the file is missing/unreadable, is not valid TOML, or
            violates the hwprofile schema.
    """

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"cannot read {path!r}: {exc}") from exc

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{path!r} is not valid TOML: {exc}") from exc

    # Validate against the shared hwprofile schema. Its ``ProfileError`` already
    # carries a path-qualified message; wrap it so callers only ever see this
    # tool's single, well-defined error type.
    from pengyos_hwprofile.schema import ProfileError as _HwProfileError
    from pengyos_hwprofile.schema import validate

    try:
        validate(data)
    except _HwProfileError as exc:
        raise ProfileError(f"{path!r}: {exc}") from exc

    return data
