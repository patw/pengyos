"""Helpers for parsing Buildroot recipe files.

Pure, dependency-free. The functions operate on *text* so they can be unit
tested against fixtures without a real recipe on disk, and are shared by the
scaffold checks (``tests/test_scaffold.py``) and the recipe checks
(``tests/test_recipe.py``).

Buildroot ``.mk`` recipes are GNU makefiles. Variables are assigned at column
zero with ``NAME = value``; a value that spans several lines ends each
continuation line with a trailing backslash. ``Config.in`` files are kconfig
(``config`` / ``depends on`` / ``select`` / ``help`` blocks) — see
``parse_kconfig``.
"""

from __future__ import annotations

import re


def mk_variable(text: str, name: str) -> str:
    """Return the value of ``NAME = ...`` in a Buildroot ``.mk`` body.

    Backslash line-continuations are joined (with the trailing backslash and
    the newline dropped, collapsing the run of whitespace that make would keep
    into a single space) and the assembled right-hand side is stripped. Returns
    ``""`` when the variable is not defined. Assignment must start at column
    zero, as Buildroot convention requires (``NAME = value`` with optional
    surrounding whitespace around the ``=``).
    """
    pattern = re.compile(r"^\s*" + re.escape(name) + r"\s*=")
    for line in _join_continuations(text):
        match = pattern.match(line)
        if match:
            return line[match.end() :].strip()
    return ""


def mk_defined(text: str, name: str) -> bool:
    """True when ``NAME = ...`` appears in the ``.mk`` body."""
    return bool(mk_variable(text, name))


def _join_continuations(text: str) -> list[str]:
    """Join GNU-make backslash continuations into logical lines.

    A line whose stripped form ends in ``\\`` continues onto the next physical
    line. Comment-only and blank physical lines are dropped. The trailing
    backslash is removed and the joined run of whitespace collapses to a single
    space (the value is re-stripped by the caller).
    """
    logical = []
    buffer = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            # A comment/blank breaks an in-progress continuation cleanly.
            if buffer:
                logical.append(buffer)
                buffer = ""
            continue
        if line.rstrip().endswith("\\"):
            buffer += line.rstrip()[:-1]
        else:
            buffer += line
            logical.append(buffer)
            buffer = ""
    if buffer:
        logical.append(buffer)
    # Collapse the whitespace that the backslash-newlines left running
    # together (tabs, multiple spaces) into single spaces so the value is
    # clean for downstream splitting/stripping.
    return [re.sub(r"\s+", " ", piece).strip() for piece in logical]


def mk_license_values(text: str) -> list[str]:
    """Return the license tokens of a package ``_LICENSE`` (split on ``/``).

    The license variable is matched by suffix (``_LICENSE``) so this works for
    any package name (``PENGY_LICENSE`` / ``DBUS_BROKER_LICENSE`` / ...).
    Buildroot expresses compound licenses as ``Apache-2.0 and/or LGPL-2.1+``;
    the parser splits on ``and/or`` and ``and`` and returns the individual
    SPDX-ish tokens. Returns ``[]`` when the variable is undefined.
    """
    value = _mk_variable_by_suffix(text, "_LICENSE")
    if not value:
        return []
    for sep in (" and/or ", " and ", " or "):
        if sep in value:
            return [part.strip() for part in value.split(sep)]
    return [value]


def _mk_variable_by_suffix(text: str, suffix: str) -> str:
    """Return the value of the first variable whose name ends with *suffix*.

    Unlike :func:`mk_variable` (which matches a literal name at column zero),
    this matches by suffix so it works for any package name
    (``PENGY_LICENSE`` / ``DBUS_BROKER_LICENSE`` ...). Continuation lines are
    joined first, mirroring how make would resolve the assignment.
    """
    name_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
    for line in _join_continuations(text):
        match = name_re.match(line)
        if match is not None and match.group(1).endswith(suffix):
            return line[match.end() :].strip()
    return ""


def installed_systemd_units(text: str) -> list[str]:
    """Return the systemd unit install destinations named in the ``.mk``.

    Scans for ``$(TARGET_DIR)/usr/lib/systemd/<kind>/<unit>`` install paths and
    returns the ``<kind>/<unit>`` portion of each.
    """
    units = []
    for line in text.splitlines():
        if "/usr/lib/systemd/" not in line:
            continue
        marker = "/usr/lib/"
        idx = line.index(marker) + len(marker)
        rest = line[idx:].strip()
        # keep only the leading path token (the destination file)
        unit_path = rest.split()[0] if rest.split() else ""
        unit_path = unit_path.replace('"', "").strip()
        if unit_path and unit_path.endswith((".service", ".socket", ".target", ".timer", ".path")):
            units.append(unit_path)
    return units


def installed_init_files(text: str) -> list[str]:
    """Return every ``$(TARGET_DIR)/<dest>`` install destination named in the ``.mk``.

    Scans for the literal ``$(TARGET_DIR)/`` marker and returns the leading
    destination path token that follows it on each physical line (surrounding
    quotes stripped). This is the general form of
    :func:`installed_systemd_units`: it catches every install destination — the
    systemd units *and* any config files the recipe ships — so a recipe that
    installs to the wrong prefix fails loudly.
    """
    dests = []
    marker = "$(TARGET_DIR)/"
    for line in text.splitlines():
        if marker not in line:
            continue
        idx = line.index(marker) + len(marker)
        rest = line[idx:].strip()
        dest = rest.split()[0] if rest.split() else ""
        dest = dest.replace('"', "").strip()
        if dest and "/" in dest:
            dests.append(dest)
    return dests


def hash_entries(text: str) -> list[dict[str, str]]:
    """Return the ``<algorithm> <digest> <filename>`` rows of a ``.hash`` body.

    Buildroot ``.hash`` files are whitespace-separated rows of the form
    ``<algorithm>  <digest>  <filename>`` (see the Buildroot manual,
    "Package hash files"). Blank lines and ``#`` comments are ignored, and the
    algorithm is lower-cased so ``SHA256`` and ``sha256`` are treated the same.
    Returns ``[]`` for an empty / comment-only file.
    """
    entries: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 3:
            # Not a well-formed "<algo> <hash> <file>" row; ignore it.
            continue
        entries.append(
            {
                "algorithm": parts[0].lower(),
                "digest": parts[1],
                "filename": parts[-1],
            }
        )
    return entries


def hash_sha256_entries(text: str) -> list[dict[str, str]]:
    """Return only the ``sha256`` rows of a ``.hash`` body (see :func:`hash_entries`)."""
    return [entry for entry in hash_entries(text) if entry["algorithm"] == "sha256"]


def kconfig_block(text: str, name: str) -> str:
    """Return the kconfig block for ``config NAME`` (its body up to the next block).

    Used to inspect ``depends on`` / ``select`` lines for a package's
    ``Config.in`` entry. Returns ``""`` when the entry is absent.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if _is_config_header(line, name):
            start = i
            break
    if start is None:
        return ""
    body = [lines[start]]
    for line in lines[start + 1 :]:
        # a new top-level block starts with an unindented non-blank line
        if line and not line[0].isspace():
            break
        body.append(line)
    return "\n".join(body)


def _is_config_header(line: str, name: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("config ") and stripped[len("config ") :] .split()[0] == name


def kconfig_depends_on(block: str) -> list[str]:
    """Return the operands of every ``depends on`` line in a kconfig block."""
    deps = []
    for line in block.splitlines():
        s = line.strip()
        if s.startswith("depends on"):
            operand = s[len("depends on") :].strip()
            # split "A || B" style operands on '||'
            for part in operand.split("||"):
                token = part.strip()
                if token:
                    deps.append(token)
    return deps
