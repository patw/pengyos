"""Command-line interface for pengyos-sbom.

The argument contract for every subcommand is defined here (and therefore
locked in by the scaffold tests). All five subcommands — ``generate``,
``validate``, ``sign``, ``verify`` and ``keygen`` — are fully implemented.

Usage:
    pengyos-sbom generate --legal-info DIR --show-info FILE --out OUT [--cdx CDX]
    pengyos-sbom validate FILE [--format {auto,spdx,cyclonedx}]
    pengyos-sbom sign FILE --key KEY [--out SIG]
    pengyos-sbom verify FILE --key KEY [--sig SIG]
    pengyos-sbom keygen [--out SEED_FILE] [--pub PUB_FILE]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from . import __version__
from . import constants as c
from . import keys
from . import signing
from .errors import SBOMError
from .cyclonedx import build_cyclonedx_document
from .ingest import ingest
from .spdx import build_spdx_document
from .validate import validate_file

PROG = "pengyos-sbom"


# --- Argument parser construction ----------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Turn Buildroot legal-info/show-info into SPDX 2.3 (and CycloneDX) "
            "SBOMs, with ed25519 signing/verification."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"{PROG} {__version__}"
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True  # a subcommand is always required

    g = sub.add_parser(
        "generate",
        help="Build SPDX 2.3 (and optional CycloneDX) from Buildroot manifests.",
    )
    g.add_argument(
        "--legal-info",
        required=True,
        metavar="DIR",
        help="Directory produced by Buildroot `make legal-info` "
        "(contains manifest.csv + package dirs).",
    )
    g.add_argument(
        "--show-info",
        required=True,
        metavar="FILE",
        help="JSON file produced by Buildroot `make show-info`.",
    )
    g.add_argument(
        "--out",
        required=True,
        metavar="FILE",
        help="Path to write the SPDX 2.3 JSON SBOM.",
    )
    g.add_argument(
        "--cdx",
        default=None,
        metavar="FILE",
        help="Also write a CycloneDX 1.5 JSON SBOM to this path.",
    )

    v = sub.add_parser(
        "validate",
        help="Validate an SPDX or CycloneDX SBOM file for required fields.",
    )
    v.add_argument("file", metavar="FILE", help="SBOM file to validate.")
    v.add_argument(
        "--format",
        dest="fmt",
        default="auto",
        choices=["auto", "spdx", "cyclonedx"],
        help="SBOM format to validate (default: auto-detect).",
    )

    s = sub.add_parser(
        "sign",
        help="Write a detached ed25519 signature over the canonicalized SBOM.",
    )
    s.add_argument("file", metavar="FILE", help="SBOM file to sign.")
    s.add_argument(
        "--key",
        required=True,
        metavar="KEY",
        help="Path to the ed25519 signing key.",
    )
    s.add_argument(
        "--out",
        default=None,
        metavar="SIG",
        help="Signature output path (default: FILE + '.sig').",
    )

    y = sub.add_parser(
        "verify",
        help="Verify a detached ed25519 signature against an SBOM file.",
    )
    y.add_argument("file", metavar="FILE", help="SBOM file to verify.")
    y.add_argument(
        "--key",
        required=True,
        metavar="KEY",
        help="Path to the ed25519 public key.",
    )
    y.add_argument(
        "--sig",
        default=None,
        metavar="SIG",
        help="Signature input path (default: FILE + '.sig').",
    )

    k = sub.add_parser(
        "keygen",
        help="Generate an ed25519 keypair (seed + public key files).",
    )
    k.add_argument(
        "--out",
        default="seed.key",
        metavar="SEED_FILE",
        help="Where to write the 32-byte secret-key seed (default: seed.key).",
    )
    k.add_argument(
        "--pub",
        default=None,
        metavar="PUB_FILE",
        help="Where to write the 32-byte public key (default: SEED_FILE + '.pub').",
    )

    return parser


# --- Subcommand handlers -------------------------------------------------
# Each handler returns a process exit code (see constants).
def _write_json(path: Path, doc: Dict) -> None:
    """Serialize *doc* deterministically and write it, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _created_timestamp() -> str:
    """The documents' creation time: ``$SOURCE_DATE_EPOCH`` when set, else now.

    Two builds of one commit must embed byte-identical SBOMs, so a release
    build pins the timestamp to the commit (reproducible-builds.org convention).
    """
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if epoch:
        try:
            return datetime.fromtimestamp(int(epoch), timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            print(f"{PROG}: warning: ignoring invalid SOURCE_DATE_EPOCH {epoch!r}", file=sys.stderr)
    return datetime.now(timezone.utc).isoformat()


def cmd_generate(args: argparse.Namespace) -> int:
    """Merge Buildroot legal-info + show-info into SPDX 2.3 (and CycloneDX).

    Always writes the SPDX 2.3 document to ``--out``; when ``--cdx`` is given it
    additionally writes a CycloneDX 1.5 BOM to that path, derived from the same
    internal model.
    """
    try:
        result = ingest(Path(args.legal_info), Path(args.show_info))
    except SBOMError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return c.EXIT_ERROR

    created = _created_timestamp()
    doc = build_spdx_document(result, created=created)
    try:
        _write_json(Path(args.out), doc)
    except OSError as exc:
        print(f"{PROG}: error: cannot write {args.out}: {exc}", file=sys.stderr)
        return c.EXIT_ERROR
    print(
        f"{PROG}: wrote SPDX {c.SPDX_VERSION} SBOM to {args.out} "
        f"({len(doc['packages'])} packages)",
    )

    if args.cdx:
        cdx = build_cyclonedx_document(result, created=created)
        try:
            _write_json(Path(args.cdx), cdx)
        except OSError as exc:
            print(f"{PROG}: error: cannot write {args.cdx}: {exc}", file=sys.stderr)
            return c.EXIT_ERROR
        print(
            f"{PROG}: wrote CycloneDX {c.CYCLONEDX_SPEC_VERSION} BOM to {args.cdx} "
            f"({len(cdx['components'])} components)",
        )
    return c.EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate an SPDX 2.3 or CycloneDX 1.5 SBOM file.

    Auto-detects the format unless ``--format`` is given. Prints one line per
    problem to stderr and exits 1 when any problem is found; exits 0 with an
    ``OK`` line on stdout when the document is valid.
    """
    try:
        fmt, problems = validate_file(Path(args.file), fmt=args.fmt)
    except SBOMError as exc:
        print(f"{PROG}: error: {args.file}: {exc}", file=sys.stderr)
        return c.EXIT_ERROR

    if problems:
        for problem in problems:
            print(f"{PROG}: {args.file}: {problem}", file=sys.stderr)
        print(
            f"{PROG}: {args.file}: invalid: {len(problems)} problem(s) found",
            file=sys.stderr,
        )
        return c.EXIT_ERROR

    label = {
        "spdx": f"SPDX {c.SPDX_VERSION}",
        "cyclonedx": f"CycloneDX {c.CYCLONEDX_SPEC_VERSION}",
    }[fmt]
    print(f"{PROG}: {args.file}: OK (valid {label} SBOM)")
    return c.EXIT_OK


def cmd_sign(args: argparse.Namespace) -> int:
    """Write a detached ed25519 signature over the canonicalized SBOM.

    The file is validated first: only well-formed documents are signed. The
    signature (64 raw bytes) is written to ``--out`` or ``FILE + '.sig'``.
    """
    path = Path(args.file)
    try:
        fmt, problems = validate_file(path, fmt="auto")
    except FileNotFoundError:
        print(f"{PROG}: error: {args.file}: no such file", file=sys.stderr)
        return c.EXIT_ERROR
    except SBOMError as exc:
        print(f"{PROG}: error: {args.file}: {exc}", file=sys.stderr)
        return c.EXIT_ERROR
    if problems:
        for problem in problems:
            print(f"{PROG}: {args.file}: {problem}", file=sys.stderr)
        print(
            f"{PROG}: {args.file}: refusing to sign: {len(problems)} problem(s) "
            f"found (run 'validate' for details)",
            file=sys.stderr,
        )
        return c.EXIT_ERROR

    try:
        seed = signing.load_signing_key(Path(args.key))
        signature = signing.sign_file(path, seed)
    except FileNotFoundError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return c.EXIT_ERROR
    except SBOMError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return c.EXIT_ERROR

    out = Path(args.out) if args.out else Path(str(path) + c.SIG_EXT)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(signature)
    except OSError as exc:
        print(f"{PROG}: error: cannot write {out}: {exc}", file=sys.stderr)
        return c.EXIT_ERROR
    print(f"{PROG}: wrote ed25519 signature for {args.file} to {out}")
    return c.EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify a detached ed25519 signature, then re-validate the document.

    Both checks must pass for exit code 0: the signature must verify against
    the public key (any tampering — a flipped byte, a moved file, a wrong
    key — fails) and the document must still be structurally valid SBOM.
    """
    path = Path(args.file)
    sig_path = Path(args.sig) if args.sig else Path(str(path) + c.SIG_EXT)

    try:
        # A --key file may hold either a public key or a signing seed, and
        # the two are ambiguous (~50% of seeds are valid curve points), so
        # resolve it to candidate public keys and try every one.
        pub_candidates = signing.verification_key_candidates(Path(args.key))
    except FileNotFoundError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return c.EXIT_ERROR
    except SBOMError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return c.EXIT_ERROR

    try:
        signature = sig_path.read_bytes()
    except FileNotFoundError:
        print(f"{PROG}: error: {sig_path}: no such file", file=sys.stderr)
        return c.EXIT_ERROR
    except OSError as exc:
        print(f"{PROG}: error: cannot read {sig_path}: {exc}", file=sys.stderr)
        return c.EXIT_ERROR

    try:
        sig_ok = signing.verify_file(path, pub_candidates, signature)
    except FileNotFoundError:
        print(f"{PROG}: error: {args.file}: no such file", file=sys.stderr)
        return c.EXIT_ERROR
    except OSError as exc:
        print(f"{PROG}: error: cannot read {args.file}: {exc}", file=sys.stderr)
        return c.EXIT_ERROR

    problems: list = []
    try:
        fmt, problems = validate_file(path, fmt="auto")
    except SBOMError as exc:  # unparseable JSON => document is not valid
        problems = [str(exc)]

    ok = sig_ok and not problems
    if sig_ok:
        print(f"{PROG}: {args.file}: signature OK (ed25519, key {args.key})")
    else:
        print(
            f"{PROG}: {args.file}: signature INVALID "
            f"(tampered document, wrong key, or bad signature)",
            file=sys.stderr,
        )
    if problems:
        for problem in problems:
            print(f"{PROG}: {args.file}: {problem}", file=sys.stderr)
        print(
            f"{PROG}: {args.file}: invalid: {len(problems)} problem(s) found",
            file=sys.stderr,
        )
        return c.EXIT_ERROR
    if ok:
        label = {
            "spdx": f"SPDX {c.SPDX_VERSION}",
            "cyclonedx": f"CycloneDX {c.CYCLONEDX_SPEC_VERSION}",
        }[fmt]
        print(f"{PROG}: {args.file}: OK (verified {label} SBOM)")
    return c.EXIT_OK if ok else c.EXIT_ERROR


def cmd_keygen(args: argparse.Namespace) -> int:
    """Generate an ed25519 keypair (seed + public key files).

    Writes the 32-byte secret-key seed (mode 0600) and its derived 32-byte
    public key (mode 0644). Existing files are never overwritten.
    """
    try:
        seed, pub, seed_path, pub_path = keys.generate_keypair(
            Path(args.out), args.pub
        )
    except (keys.KeygenError, OSError) as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return c.EXIT_ERROR
    assert len(seed) == 32 and len(pub) == 32
    print(f"{PROG}: generated ed25519 keypair")
    print(f"{PROG}:   signing key (seed): {seed_path} (mode 600)")
    print(f"{PROG}:   public key:         {pub_path} (mode 644)")
    return c.EXIT_OK


_HANDLERS = {
    "generate": cmd_generate,
    "validate": cmd_validate,
    "sign": cmd_sign,
    "verify": cmd_verify,
    "keygen": cmd_keygen,
}


# --- Entry point ---------------------------------------------------------
def main(argv=None) -> int:
    """Run the CLI.

    Returns a process exit code (see :mod:`pengyos_sbom.constants`) rather than
    calling ``sys.exit`` directly, so tests can invoke it without a subprocess.
    ``python -m pengyos_sbom`` and the console script wrap this with
    ``sys.exit(main(...))``.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --help / --version / usage errors
        # argparse has already printed; normalize the exit code.
        return exc.code if isinstance(exc.code, int) else 0

    try:
        return _HANDLERS[args.command](args)
    except FileNotFoundError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return c.EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
