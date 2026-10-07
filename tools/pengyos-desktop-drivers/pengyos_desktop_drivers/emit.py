"""``emit`` -- render the Desktop kernel fragment and firmware list.

Responsibility: given the driver
profile, deterministically produce two artifacts under an output directory:

* ``linux.fragment`` -- a Linux kernel Kconfig fragment (``CONFIG_X=y`` lines),
  with the vendored generic-driver header prepended.
* ``firmware.list`` -- the curated ``linux-firmware`` subset, one upstream
  firmware name per line.

The output is byte-for-byte deterministic: the fragment symbols and the firmware
names are both sorted, and the firmware selection is exactly the set of upstream
names under the profile's curated prefixes. That determinism is what lets the
golden tests pin the artifacts exactly and what lets ``lint`` validate a produced
profile against the same oracle ``emit`` used.

Rendering is factored into two pure functions -- :func:`render_fragment` and
:func:`render_firmware_list` -- so they can be unit-tested in isolation and so
:func:`emit_desktop_profile` is a thin "render + write + hash" wrapper.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import data, profile

# The two artifacts this module owns. Kept as module constants so the CLI and
# tests agree on the canonical filenames.
FRAGMENT_FILENAME = "linux.fragment"
FIRMWARE_FILENAME = "firmware.list"


def render_fragment(symbols: Iterable[str], header: str = profile.FRAGMENT_HEADER) -> str:
    """Render the Kconfig fragment body from a symbol iterable.

    The vendored generic-driver ``header`` is prepended, then the symbols are
    emitted as ``CONFIG_X=y`` lines in sorted, de-duplicated order and the text
    ends with a single trailing newline. Sorting + de-duplication make the output
    canonical and stable across runs, which is what the golden tests rely on.
    (``profile.all_symbols`` already de-duplicates; the set here is a defensive
    guard so a duplicate symbol can never produce a duplicate fragment line.)
    """
    lines = sorted({f"{sym}=y" for sym in symbols})
    return header + "\n".join(lines) + "\n"


def render_firmware_list(
    names: Sequence[str], prefixes: Sequence[str]
) -> str:
    """Render the curated firmware list: upstream names under the prefixes.

    Returns the upstream ``names`` filtered to those starting with any of the
    curated ``prefixes``, sorted, and newline-terminated. Determinism (sorted +
    deduped-by-input) is what lets the golden test pin the bytes exactly.
    """
    matched = {n for n in names if any(n.startswith(p) for p in prefixes)}
    return "\n".join(sorted(matched)) + "\n"


def _sha256_text(text: str) -> str:
    """Return the hex SHA-256 of ``text`` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def emit_desktop_profile(
    out_dir: str | Path, as_json: bool = False
) -> dict[str, Any]:
    """Emit the Desktop ``linux.fragment`` and ``firmware.list`` into ``out_dir``.

    The fragment is rendered from :func:`profile.all_symbols` and the firmware
    list from :func:`profile.firmware_prefixes` filtered against the vendored
    upstream oracle in :func:`data.load_firmware_names`. Both are written under
    ``out_dir`` (see :data:`FRAGMENT_FILENAME` / :data:`FIRMWARE_FILENAME`) and
    hashed so the caller can assert on byte-stability.

    Returns a dict describing what was written::

        {
            "fragment": {"path": str, "bytes": int, "sha256": str},
            "firmware": {"path": str, "lines": int, "sha256": str},
        }

    ``as_json`` is accepted for API symmetry with the CLI; it has no effect on the
    returned structure (the CLI is responsible for JSON-serializing it).
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    symbols = profile.all_symbols()
    prefixes = profile.firmware_prefixes()
    upstream = data.load_firmware_names()

    fragment_text = render_fragment(symbols)
    firmware_text = render_firmware_list(upstream, prefixes)

    frag_path = out / FRAGMENT_FILENAME
    fw_path = out / FIRMWARE_FILENAME
    frag_path.write_text(fragment_text, encoding="utf-8")
    fw_path.write_text(firmware_text, encoding="utf-8")

    report: dict[str, Any] = {
        "fragment": {
            "path": str(frag_path),
            "bytes": len(fragment_text.encode("utf-8")),
            "sha256": _sha256_text(fragment_text),
        },
        "firmware": {
            "path": str(fw_path),
            "lines": firmware_text.count("\n"),
            "sha256": _sha256_text(firmware_text),
        },
    }
    return report
