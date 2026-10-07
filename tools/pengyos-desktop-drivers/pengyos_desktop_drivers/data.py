"""Vendored reference-data loaders.

The package ships two committed fixtures under ``pengyos_desktop_drivers/data/``
:

* ``symbols.2026.08.txt`` -- every ``CONFIG_*`` symbol in the pinned 2026.08
  kernel build (the sibling Buildroot checkout's ``linux-7.2.5/.config``).
* ``firmware.upstream.txt`` -- every upstream ``linux-firmware`` loadable path
  (the subset oracle, derived from the pinned ``linux-firmware-20260810``
  release tarball).

These loaders read them via :mod:`importlib.resources` so the package works
in-place (repo root on ``sys.path``) and when installed as a wheel. They are the
oracle :mod:`pengyos_desktop_drivers.lint` checks its fragment/firmware against,
and the grounding tests in :mod:`tests.test_profile` assert the driver profile
against them.

The files are plain newline-delimited lists with a trailing newline; blank lines
are skipped so a stray empty entry can never slip in.
"""

from __future__ import annotations

import importlib.resources as ir
from pathlib import Path
from typing import Iterable


def _package_text(name: str) -> str:
    """Return the text of a committed package-data file under ``data/``."""
    return ir.files("pengyos_desktop_drivers").joinpath("data", name).read_text(
        encoding="utf-8"
    )


def _iter_lines(text: str) -> Iterable[str]:
    """Yield non-empty, stripped lines from a newline-delimited list file."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            yield stripped


def load_symbols() -> set[str]:
    """Return the set of every ``CONFIG_*`` symbol in ``symbols.2026.08.txt``."""
    return set(_iter_lines(_package_text("symbols.2026.08.txt")))


def load_firmware_names() -> list[str]:
    """Return the ordered list of upstream ``linux-firmware`` names."""
    return list(_iter_lines(_package_text("firmware.upstream.txt")))


def symbol_path(name: str = "symbols.2026.08.txt") -> Path:
    """Return the on-disk path of a committed data file (for tooling/tests)."""
    return Path(__file__).resolve().parent / "data" / name
