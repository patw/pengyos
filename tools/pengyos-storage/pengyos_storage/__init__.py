"""pengyos-storage — see where the disk went.

Standard library only, read-only, no network, no root. See spec.md.
"""
from __future__ import annotations

#: The tool version, kept in lockstep with pyproject.toml.
VERSION = "0.1.0"
#: The canonical command name shown in help / JSON documents.
TOOL = "pengyos-storage"
#: The version of the JSON document schema.
SCHEMA = 1

from .model import FileEntry, Node, ScanResult, ScanError, human, parse_size
from .scan import scan_tree
from .report import render_text, render_du
from .html import render_html
from .chart import PALETTE, pie, treemap

__all__ = [
    "VERSION",
    "TOOL",
    "SCHEMA",
    "FileEntry",
    "Node",
    "ScanResult",
    "ScanError",
    "scan_tree",
    "render_text",
    "render_du",
    "render_html",
    "pie",
    "treemap",
    "PALETTE",
    "human",
    "parse_size",
]
