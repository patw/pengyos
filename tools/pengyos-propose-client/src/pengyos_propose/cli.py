"""pengyos-propose — command line for the PengyOS propose-client.

Subcommands
-----------
* ``submit`` — format a proposal and POST it to the propose/approve gate.
* ``render`` — print a human-readable view of a proposal (Markdown).

The gate is human-gated and its HTTP layer is mockable; see
:mod:`pengyos_propose.gate`.  Tier gating (refusing a Tier 2 without an explicit
human-approval flag) is enforced by :mod:`pengyos_propose.gating`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional, Sequence

from . import __version__
from .gate import GateClient, build_payload
from .gating import GateError
from .proposal import Proposal, load_proposal_file
from .render import render_markdown


class CliError(Exception):
    """A user-facing error; printed to stderr and mapped to exit code 1."""

# Default gate base when not given explicitly.  The `propose` gate listens on
# the LAN; this is only a fallback and is overridable by the caller.
DEFAULT_GATE_URL = "http://127.0.0.1:8071"
GATE_URL_ENV = "PENGYOS_GATE_URL"


def _load(path: str) -> Proposal:
    try:
        return load_proposal_file(path)
    except FileNotFoundError:
        raise CliError(f"no such proposal file: {path}")
    except ValueError as e:
        raise CliError(str(e))


def _cmd_render(args: argparse.Namespace) -> int:
    p = _load(args.path)
    fmt = args.format or "md"
    if fmt in ("md", "markdown"):
        sys.stdout.write(render_markdown(p))
    elif fmt in ("html", "html-page"):
        raise CliError("HTML rendering is not implemented yet (use --format md)")
    else:
        raise CliError(f"unknown --format {fmt!r}")
    return 0


def _cmd_submit(args: argparse.Namespace) -> int:
    p = _load(args.proposal)

    if args.dry_run:
        payload = build_payload(p, human_approved=args.human_approved)
        print(json.dumps(payload, indent=2))
        return 0

    base_url = args.base_url or os.environ.get(GATE_URL_ENV, DEFAULT_GATE_URL)
    client = GateClient(base_url)
    try:
        resp = client.submit(p, human_approved=args.human_approved)
    except GateError as e:
        # Human gate refused: a Tier 2 without explicit approval never leaves
        # the client. Surface a clear, actionable message and refuse.
        raise CliError(str(e)) from e

    if not resp.ok:
        sys.stderr.write(
            f"pengyos-propose: submit failed (HTTP {resp.status_code})\n"
        )
        sys.stderr.write(json.dumps(resp.body, indent=2) + "\n")
        return 1

    if resp.proposal_id:
        print(f"proposal created: {resp.proposal_id}")
    if resp.review_url:
        print(f"review link: {resp.review_url}")
    print("submitted to gate")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    ap = argparse.ArgumentParser(
        prog="pengyos-propose",
        description="Format change proposals and route them to the human approve gate.",
    )
    ap.add_argument("--version", action="version", version=f"pengyos-propose {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="Print a human-readable view of a proposal")
    r.add_argument("path", help="Path to the proposal JSON file")
    r.add_argument("--format", "-f", default="md", choices=["md", "markdown", "html"],
                   help="Output format (default: md)")
    r.set_defaults(fn=_cmd_render)

    s = sub.add_parser("submit", help="Submit a proposal to the gate")
    s.add_argument("--proposal", "-p", required=True, help="Path to the proposal JSON file")
    s.add_argument("--dry-run", action="store_true",
                   help="Print the request body instead of sending it")
    s.add_argument("--human-approved", action="store_true",
                   help="Declare this proposal has explicit human approval (required for Tier 2)")
    s.add_argument("--base-url", default=None,
                   help=f"Gate base URL (default: --base-url or ${GATE_URL_ENV})")
    s.set_defaults(fn=_cmd_submit)

    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except CliError as e:
        sys.stderr.write(f"pengyos-propose: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
