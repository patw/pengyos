"""Structural nftables validator (US-SRV-5).

A small, dependency-free validator for the default-deny ``nftables``
rulesets that :mod:`pengyos_netctl.cli` renders with the ``rules``
command. It answers three questions about a rendered ruleset:

1. Does it start with ``flush ruleset`` (so nothing pre-existing leaks in)?
2. Are ``input`` and ``forward`` chains ``policy drop`` (default-deny)?
3. Is the inbound allow list *exactly* the declared ports from a
   :class:`~pengyos_netctl.config.Port` config — no more, no less?
4. Does the ``input`` chain carry an sshguard/fail2ban-style
   rate-limit/ban (a ``limit rate`` … ``drop`` statement) mitigating SSH
   brute-force attempts?

Everything runs offline with the standard library only: the ``nft``
binary is not required (``nft -c`` needs privileges anyway), and real
application of the ruleset is a non-goal (see spec.md).

## Parser origin

The *structural* nft parser below is a documented local copy of the
parser in the sibling ``pengyos-net`` deliverable
(``pengyos_net/nft_validate.py``). The task brief for this subtask says
to "reuse pengyos-net's rules if importable, else a documented local
copy". The ``pengyos_net`` package *is* importable, but its *policy*
layer is hard to reuse: it enforces the *baseline* posture (exactly
SSH/tcp/22, a pinned statement set, a single ``inet pengyos`` table),
whereas this deliverable must validate a *declared-ports* posture
(SSH + WireGuard + the ports named in ``ports.yaml``).

So we vendor the reusable, policy-neutral *parser* verbatim (with the
attribution header kept intact) and layer our own declared-ports policy
on top. Keeping the parser in-repo also keeps this suite self-contained
and independent of the sibling repo's location on ``sys.path``.

The parser relies on these nft grammar facts (see the source for detail):

* a statement ends at ``;`` **or a newline** (real-world ``.nft`` files
  and the man-page examples both omit the ``;`` before a newline);
* blocks are ``{ header { … } }`` keyed by the LAST word of the header
  (``table inet filter`` → ``filter``, ``chain input`` → ``input``);
* comments are ``#``, ``//``, and ``/* … */`` (double-quoted strings are
  preserved verbatim so a ``#`` inside a value cannot hide a statement).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from .config import PortsConfig

__all__ = [
    "NftSyntaxError",
    "Port",
    "ValidationResult",
    "chain_policy",
    "chain_statements",
    "dport_accepts",
    "find_rate_limit_drop",
    "parse_nft_ruleset",
    "strip_comments",
    "validate_ports_ruleset",
    "validate_ports_ruleset_text",
]


# ------------------------------------------------------------- nft parsing
#
# The following functions are a verbatim local copy of the parser in
# pengyos-net's pengyos_net/nft_validate.py (same license, Copyright (c)
# 2026 Pat Wendorf). Attribution retained per that file's provenance.
# -------------------------------------------------------------


class NftSyntaxError(ValueError):
    """The ruleset text is not structurally valid nft syntax."""


def strip_comments(text: str) -> str:
    """Remove ``#``/``//`` line comments and ``/* … ``*``*/`` block comments.

    Double-quoted strings are preserved verbatim so a ``#`` inside a
    quoted value cannot hide a statement. Newlines outside strings are
    converted to ``"; `` up-front because nft treats a newline as a
    statement terminator, exactly like ``;``.

    Raises :class:`NftSyntaxError` on an unterminated comment or string.
    """
    out = []
    i, n = 0, len(text)
    in_string = False
    in_block_comment = False
    while i < n:
        ch = text[i]
        if in_block_comment:
            if ch == "*" and i + 1 < n and text[i + 1] == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            in_block_comment = True
            i += 2
            continue
        out.append("; " if ch == "\n" else ch)
        i += 1
    if in_block_comment:
        raise NftSyntaxError("unterminated /* comment")
    if in_string:
        raise NftSyntaxError("unterminated string")
    return "".join(out)


_TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|[{};]|[^\s{};]+')


def tokenize(text: str):
    r"""Tokenise nft text.

    Words are ``[^\s{};]+`` — NOT ``\\S+``: ``\\S+`` would swallow a
    glued ``;``/``{``/``}`` (e.g. ``0;`` → one token) and silently
    corrupt statements. Double-quoted strings are single tokens.
    """
    return [m.group(0) for m in _TOKEN_RE.finditer(text)]


def parse_nft_ruleset(text: str):
    """Parse nftables ruleset text into a nested block tree.

    Returns a node::

        {"header": str | None,
         "stmts":  [str, ...],          # top-to-bottom order
         "blocks": {name: node, ...},   # insertion (source) order
         "items":  [("stmt", s) | ("block", name), ...]}

    ``items`` preserves the full source order of statements and blocks
    (needed for ordering policy checks such as "no accept before the
    allow table"); ``stmts``/``blocks`` are convenience views over it.

    The top-level node has ``header=None``; table/chain blocks are keyed
    by the LAST word of their header (the table/chain name). A block's
    header is everything between the previous ``;``/``}`` and its
    opening ``{``.

    Raises :class:`NftSyntaxError` on unbalanced braces, a headerless
    block, a duplicate block name, a statement missing its terminator
    before ``}``/EOF, or malformed comments/strings.
    """
    root = {"header": None, "stmts": [], "blocks": {}, "items": []}
    stack = [root]
    buf = []
    for token in tokenize(strip_comments(text)):
        if token == "{":
            if not buf:
                raise NftSyntaxError("block opener without a header")
            header = " ".join(buf)
            # Key by the last word: 'table inet pengyos' -> 'pengyos',
            # 'chain input' -> 'input'.
            name = header.split()[-1]
            if name in stack[-1]["blocks"]:
                raise NftSyntaxError(f"duplicate block {name!r}")
            node = {"header": header, "stmts": [], "blocks": {}, "items": []}
            stack[-1]["blocks"][name] = node
            stack[-1]["items"].append(("block", name))
            stack.append(node)
            buf = []
        elif token == "}":
            if len(stack) == 1:
                raise NftSyntaxError("unbalanced '}'")
            if buf:
                raise NftSyntaxError(
                    f"statement {buf!r} missing ';' before '}}'"
                )
            stack.pop()
        elif token == ";":
            if buf:
                stmt = " ".join(buf)
                stack[-1]["stmts"].append(stmt)
                stack[-1]["items"].append(("stmt", stmt))
            buf = []
        else:
            buf.append(token)
    if len(stack) != 1:
        raise NftSyntaxError("unbalanced '{'")
    if buf:
        raise NftSyntaxError(f"statement {buf!r} missing trailing ';'")
    return root


def chain_statements(root: dict, table: str, chain: str):
    """Return ``(header, [stmt, ...])`` for the named table/chain blocks.

    Raises :class:`KeyError` if the table or chain is missing.
    """
    tables = root["blocks"]
    if table not in tables:
        raise KeyError(f"table {table!r} not found")
    chains = tables[table]["blocks"]
    if chain not in chains:
        raise KeyError(f"chain {chain!r} not found in table {table!r}")
    node = chains[chain]
    return node["header"], node["stmts"]


def chain_policy(stmts: List[str]) -> str:
    """Extract the ``policy <action>`` of a chain from its statements.

    Raises :class:`NftSyntaxError` if no (or an unrecognised) policy
    statement is present.
    """
    for stmt in stmts:
        m = re.fullmatch(r"policy (drop|accept|reject)\b", stmt)
        if m:
            return m.group(1)
        if "policy" in stmt.split():
            raise NftSyntaxError(f"unrecognised policy statement: {stmt!r}")
    raise NftSyntaxError("no policy statement in chain")


def dport_accepts(stmts: List[str]) -> Set[Tuple[str, str]]:
    """Return ``{(proto, port)}`` for every dport accept in a chain.

    Recognises both ``tcp dport 22 ... accept`` and the combined form
    ``tcp dport 22, ssh2 accept``. Service names (e.g. ``dport ssh``)
    are not resolvable offline, so they are reported verbatim and simply
    will not match the numeric allow set.

    Raises :class:`NftSyntaxError` on a dport accept without a value or
    with a bad port value.
    """
    found = set()
    for stmt in stmts:
        tokens = stmt.split()
        if "accept" not in tokens or "dport" not in tokens:
            continue
        idx = tokens.index("dport")
        if idx >= len(tokens) - 1:
            raise NftSyntaxError(f"dport without value in accept: {stmt!r}")
        if idx == 0 or tokens[idx - 1] not in ("tcp", "udp"):
            raise NftSyntaxError(f"unrecognised dport accept: {stmt!r}")
        proto = tokens[idx - 1]
        # 'dport 22, 8080' tokenises as ['22,', '8080']; re-join the
        # comma-continuation into one value before splitting.
        value = tokens[idx + 1]
        # 'tcp dport accept' — the verdict keyword standing in for the
        # port value means the value is missing, not a service name.
        if value in ("accept", "drop", "reject"):
            raise NftSyntaxError(f"dport without value in accept: {stmt!r}")
        if value.endswith(",") and idx + 2 < len(tokens):
            value = value + tokens[idx + 2]
        for port in value.split(","):
            if not (port.isdigit() or port.isalpha()):
                raise NftSyntaxError(f"bad port in accept: {stmt!r}")
            found.add((proto, port))
    return found


def find_rate_limit_drop(stmts: List[str]) -> Optional[str]:
    """Return the sshguard/fail2ban-style rate-limit/ban statement, if any.

    The SSH brute-force mitigation is a statement that combines a
    ``limit rate`` with a ``drop`` verdict — the automatic "ban" of
    repeat offenders. Returns the first such statement, or ``None`` when
    the chain carries no rate-limit drop.
    """
    for stmt in stmts:
        tokens = stmt.split()
        if "drop" in tokens and "limit" in tokens and "rate" in tokens:
            return stmt
    return None


# --------------------------------------------------------- policy layer


class Port:
    """A single ``(protocol, port)`` inbound allow entry."""

    __slots__ = ("proto", "port")

    def __init__(self, proto: str, port: str):
        self.proto = proto
        self.port = port

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Port):
            return NotImplemented
        return self.proto == other.proto and self.port == other.port

    def __hash__(self) -> int:
        return hash((self.proto, self.port))

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return f"Port({self.proto!r}, {self.port!r})"

    def __str__(self) -> str:
        return f"{self.proto}/{self.port}"


class ValidationResult:
    """Outcome of validating one ruleset: ``errors`` + parsed ``root``."""

    def __init__(self, root: Optional[dict], errors: List[str]):
        self.root = root
        self.errors = list(errors)

    @property
    def ok(self) -> bool:
        return not self.errors

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:  # pragma: no cover - repr only
        state = "OK" if self.ok else f"FAIL ({len(self.errors)} errors)"
        return f"ValidationResult({state})"


def _declared_ports(ports: PortsConfig) -> Set[Port]:
    """The inbound allow set implied by a :class:`PortsConfig`."""
    allow = ports.allow_list()  # {"tcp": [...], "udp": [...]}
    ports_out: Set[Port] = set()
    for proto in ("tcp", "udp"):
        for p in allow[proto]:
            ports_out.add(Port(proto, str(p)))
    return ports_out


def _validate_policy(root: dict, ports: PortsConfig, errors: List[str]) -> None:
    """Append policy violations for a declared-ports ruleset (mutates)."""
    # 1. Must flush the ruleset up front so nothing pre-existing leaks in.
    if "flush ruleset" not in root["stmts"]:
        errors.append("top-level 'flush ruleset' required")

    # 2. Default-deny inbound: there must be an inet table holding both an
    #    input and a forward chain, and both must be policy drop.
    inet_tables = [
        name for name, node in root["blocks"].items()
        if node["header"].startswith("table inet ")
    ]
    if not inet_tables:
        errors.append("no 'table inet <name>' block found")
        return

    table_name = inet_tables[0]
    table = root["blocks"][table_name]
    chains = table["blocks"]
    for chain in ("input", "forward"):
        if chain not in chains:
            errors.append(f"chain {chain!r} missing from {table_name!r}")
            continue
        _, stmts = chain_statements(root, table_name, chain)
        try:
            policy = chain_policy(stmts)
        except NftSyntaxError as exc:
            errors.append(f"chain {chain!r}: {exc}")
            continue
        if policy != "drop":
            errors.append(
                f"chain {chain!r} must be default-deny, got policy {policy!r}"
            )

    # 3. The inbound allow list must be exactly the declared ports.
    input_chain = chains.get("input")
    if input_chain is not None:
        _, in_stmts = chain_statements(root, table_name, "input")
        try:
            found = dport_accepts(in_stmts)
        except NftSyntaxError as exc:
            errors.append(f"input chain: {exc}")
        else:
            found_ports = {Port(proto, port) for proto, port in found}
            declared = _declared_ports(ports)
            if found_ports != declared:
                missing = sorted(str(p) for p in declared - found_ports)
                extra = sorted(str(p) for p in found_ports - declared)
                detail = ", ".join(
                    part for part in (
                        f"missing {missing}" if missing else None,
                        f"extra {extra}" if extra else None,
                    ) if part
                )
                errors.append(
                    f"inbound allow list must be exactly {sorted(str(p) for p in declared)}, "
                    f"got {sorted(str(p) for p in found_ports)} ({detail})"
                )

    # 4. SSH brute-force mitigation: an sshguard/fail2ban-style rate-limit /
    #    ban must be present — a `limit rate` combined with a `drop` verdict
    #    in the input chain. Without it, a flood of new SSH connections is
    #    simply accepted by the allow list.
    if input_chain is not None:
        _, in_stmts = chain_statements(root, table_name, "input")
        if find_rate_limit_drop(in_stmts) is None:
            errors.append(
                "input chain must carry an sshguard/fail2ban-style "
                "rate-limit/ban (a `limit rate` ... `drop` statement)"
            )


def validate_ports_ruleset_text(text: str, ports: PortsConfig) -> ValidationResult:
    """Validate ``text`` against ``ports`` (declared-ports policy).

    Returns a :class:`ValidationResult`; never raises for expected
    failures (syntax errors land in ``errors``; ``root`` is ``None`` in
    that case).
    """
    try:
        root = parse_nft_ruleset(text)
    except NftSyntaxError as exc:
        return ValidationResult(None, [f"syntax: {exc}"])
    errors: List[str] = []
    _validate_policy(root, ports, errors)
    return ValidationResult(root, errors)


def validate_ports_ruleset(path: str, ports: PortsConfig) -> ValidationResult:
    """Read ``path`` and validate it against ``ports`` (see the text form)."""
    with open(path, encoding="utf-8") as fh:
        return validate_ports_ruleset_text(fh.read(), ports)
