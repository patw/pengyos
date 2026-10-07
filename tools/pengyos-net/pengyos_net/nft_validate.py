"""Offline validator for the baseline nftables ruleset.

Validates ``nftables/pengyos.nft`` for **syntax** (a structural parse of
the nft grammar: comments/strings, ``;``- or newline-terminated
statements, balanced braces, block headers) and **security policy**
(default-deny inbound, no ``accept`` before the allow table, an explicit
minimal inbound allow list — exactly SSH, tcp/22).

Everything runs offline with the standard library only: the ``nft``
binary is not required (``nft -c`` needs privileges anyway), and real
application of the ruleset is a non-goal (see spec.md).

nft grammar notes the parser relies on:

* a statement ends at ``;`` **or a newline** (real-world ``.nft`` files
  and the man-page examples both omit the ``;`` before a newline);
* blocks are ``{ header { … } }`` keyed by the LAST word of the header
  (``table inet pengyos`` → ``pengyos``, ``chain input`` → ``input``);
* comments are ``#``, ``//``, and ``/* … */`` (double-quoted strings are
  preserved verbatim so a ``#`` inside a value cannot hide a statement).
"""
import re

__all__ = [
    "ALLOW_TABLE",
    "EXPECTED_NEW_ACCEPTS",
    "NftSyntaxError",
    "PINNED_INPUT_STATEMENTS",
    "ValidationResult",
    "chain_policy",
    "chain_statements",
    "dport_accepts",
    "parse_nft_ruleset",
    "strip_comments",
    "validate_ruleset",
    "validate_ruleset_text",
]

# The allow table: the one table that may carry inbound accepts.
ALLOW_TABLE = "pengyos"

# The baseline allow list for inbound NEW connections: exactly SSH.
EXPECTED_NEW_ACCEPTS = {("tcp", "22")}

# Every statement in the input chain must be one of these (as a token
# set). This is what "explicit allow list" means: no room for stray
# accepts.
PINNED_INPUT_STATEMENTS = frozenset(
    {
        frozenset(("type", "filter", "hook", "input", "priority", "0")),
        frozenset(("policy", "drop")),
        frozenset(("iif", '"lo"', "accept")),
        frozenset(("ct", "state", "established,related", "accept")),
        frozenset(("ct", "state", "invalid", "drop")),
        frozenset(("tcp", "dport", "22", "ct", "state", "new", "accept")),
    }
)


class NftSyntaxError(ValueError):
    """The ruleset text is not structurally valid nft syntax."""


# ------------------------------------------------------------- nft parsing


def strip_comments(text):
    """Remove ``#``/``//`` line comments and ``/* … */`` block comments.

    Double-quoted strings are preserved verbatim so a ``#`` inside a
    quoted value cannot hide a statement. Newlines outside strings are
    converted to ``"; "`` up-front because nft treats a newline as a
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


def tokenize(text):
    r"""Tokenise nft text.

    Words are ``[^\s{};]+`` — NOT ``\\S+``: ``\\S+`` would swallow a
    glued ``;``/``{``/``}`` (e.g. ``0;`` → one token) and silently
    corrupt statements. Double-quoted strings are single tokens.
    """
    return [m.group(0) for m in _TOKEN_RE.finditer(text)]


def parse_nft_ruleset(text):
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


def chain_statements(root, table, chain):
    """Return ``(header, [stmt, ...])`` for the named table/chain blocks.

    Raises ``KeyError`` if the table or chain is missing.
    """
    tables = root["blocks"]
    if table not in tables:
        raise KeyError(f"table {table!r} not found")
    chains = tables[table]["blocks"]
    if chain not in chains:
        raise KeyError(f"chain {chain!r} not found in table {table!r}")
    node = chains[chain]
    return node["header"], node["stmts"]


def chain_policy(stmts):
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


def dport_accepts(stmts):
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


# ---------------------------------------------------------- policy checks


def _accepts_before_allow_table(root):
    """Return the top-level accept statements preceding the allow table.

    An ``accept`` anywhere in the ruleset BEFORE the allow table (the
    ``table inet <ALLOW_TABLE>`` block) is a policy violation: it would
    open traffic on top of any pre-existing ruleset state (the baseline
    relies on ``flush ruleset`` + drop policies, and nothing may accept
    before the allow list is in place). Top-level statements and blocks
    are walked in source order (nested blocks recursively); the first
    occurrence of the allow table terminates the scan.
    """
    found = []
    for kind, value in root["items"]:
        if kind == "block" and value == ALLOW_TABLE:
            break
        if kind == "stmt":
            if "accept" in value.split():
                found.append(value)
        else:
            # A block preceding the allow table: accepts in its own
            # statements or nested blocks count as pre-table too.
            block = root["blocks"][value]
            for s in block["stmts"]:
                if "accept" in s.split():
                    found.append(s)
            found.extend(_accepts_before_allow_table(block))
    return found


def _check_policy(root, errors):
    """Append policy violations to ``errors`` (mutates, returns nothing)."""
    # Start from a clean slate so nothing pre-existing leaks in.
    if "flush ruleset" not in root["stmts"]:
        errors.append("top-level 'flush ruleset' required")

    # Ordering: no accept may appear before the allow table. Independent
    # of the table-shape checks below, so it runs even when they fail.
    for stmt in _accepts_before_allow_table(root):
        errors.append(f"accept before allow table: {stmt!r}")

    # The baseline ships exactly one table: inet/pengyos.
    tables = set(root["blocks"])
    if tables != {ALLOW_TABLE}:
        errors.append(
            f"expected exactly table inet {ALLOW_TABLE}, got: {sorted(tables)}"
        )
        return  # the checks below assume the allow table exists

    table = root["blocks"][ALLOW_TABLE]
    if table["header"] != f"table inet {ALLOW_TABLE}":
        errors.append(
            f"table header must be 'table inet {ALLOW_TABLE}', "
            f"got: {table['header']!r}"
        )
    if set(table["blocks"]) != {"input", "forward", "output"}:
        errors.append(
            "table must define exactly input/forward/output chains, "
            f"got: {sorted(table['blocks'])}"
        )
        return

    # Default-deny inbound: input AND forward drop by policy.
    for chain in ("input", "forward"):
        _, stmts = chain_statements(root, ALLOW_TABLE, chain)
        try:
            policy = chain_policy(stmts)
        except NftSyntaxError as exc:
            errors.append(f"chain {chain!r}: {exc}")
            continue
        if policy != "drop":
            errors.append(
                f"chain {chain!r} must be default-deny, got policy {policy!r}"
            )

    # Baseline convenience: outbound is open (recorded in spec.md).
    _, out_stmts = chain_statements(root, ALLOW_TABLE, "output")
    try:
        if chain_policy(out_stmts) != "accept":
            errors.append("output chain policy must be accept (baseline)")
    except NftSyntaxError as exc:
        errors.append(f"chain 'output': {exc}")

    # The input chain may contain ONLY the pinned statements.
    _, in_stmts = chain_statements(root, ALLOW_TABLE, "input")
    for stmt in in_stmts:
        tokens = frozenset(stmt.split())
        if tokens not in PINNED_INPUT_STATEMENTS:
            errors.append(f"unrecognised statement in input chain: {stmt!r}")

    # The new-connection allow list is exactly SSH.
    try:
        opens = dport_accepts(in_stmts)
    except NftSyntaxError as exc:
        errors.append(f"input chain: {exc}")
    else:
        if opens != EXPECTED_NEW_ACCEPTS:
            errors.append(
                "inbound allow list must be exactly "
                f"{sorted(EXPECTED_NEW_ACCEPTS)}, got: {sorted(opens)}"
            )


class ValidationResult:
    """Outcome of validating one ruleset: ``errors`` + parsed ``root``."""

    def __init__(self, root, errors):
        self.root = root
        self.errors = list(errors)

    @property
    def ok(self):
        return not self.errors

    def __bool__(self):
        return self.ok

    def __repr__(self):
        state = "OK" if self.ok else f"FAIL ({len(self.errors)} errors)"
        return f"ValidationResult({state})"


def validate_ruleset_text(text):
    """Validate ruleset text: syntax parse, then policy checks.

    Returns a :class:`ValidationResult`; never raises for expected
    failures (syntax errors land in ``errors``; ``root`` is ``None`` in
    that case).
    """
    try:
        root = parse_nft_ruleset(text)
    except NftSyntaxError as exc:
        return ValidationResult(None, [f"syntax: {exc}"])
    errors = []
    _check_policy(root, errors)
    return ValidationResult(root, errors)


def validate_ruleset(path):
    """Read ``path`` and validate it (see :func:`validate_ruleset_text`)."""
    with open(path, encoding="utf-8") as fh:
        return validate_ruleset_text(fh.read())
