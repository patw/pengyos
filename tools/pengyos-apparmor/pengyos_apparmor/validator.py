"""Profile validator: grammar basics + over-broad-grant lint.

Validates PengyOS AppArmor profiles without loading AppArmor, needing
root, or touching the network. It checks the authoring contract that
``tests/test_profiles.py`` pins for the committed profiles,
plus the over-broad grant lint (wildcard-tree ``/**`` grants that
carry write/keepcaps/link permission are legal but warned about):

* header — the first non-comment line is ``<name> (alias) {`` and the
  profile name equals the file name (when validating a file),
* braces — exactly one open and one close brace outside comments, and
  the profile ends with the closing brace. The rule body excludes the
  header line and the counted brace lines by line *identity*, not by
  substring: a rule line that itself carries braces (e.g. an
  ``ip={…}`` conditional) is still rule-validated, while the structural
  braces (the header's ``{``, the closing ``}``, and any stray extra
  brace already flagged by the count check) are not rule lines.
* rules — every body line is a comment, blank, or a rule ending in
  exactly one comma (a comma terminates a rule: a double terminator or a
  comma inside a rule token is a grammar error) whose prefix is a valid
  one (bare path rule, ``deny``,
  ``capability``, or ``network``), whose path is absolute, and whose
  permission flags use only valid letters. A path rule is exactly
  ``<path> <flags>`` (one path token and one flag-set token, after an
  optional ``deny``) and a capability rule names exactly one capability
  — extra tokens are grammar errors, not silently dropped (real
  AppArmor rejects such lines). Network rules model the
  apparmor.d(5) grammar ``network [access] [domain] [type|protocol]
  [ip=… port=…] [peer=(…)]``: domains, types/protocols, access
  keywords and ip/port conditionals are grammar-checked (errors), and
  the grant itself — plus any non-loopback ``ip=`` scope — is warned
  about, *unless* the rule is sanctioned: grants the project has
  answered intentionally, one exact rule at a time. Sanctions come from
  two places — the package-level ``SANCTIONED_NETWORK_RULES`` set and
  *profile-local* sanction directives (comment lines of the form
  ``# sanctioned: <rule>``): the profile answers for a specific grant
  in the same file the grant lives in, which is the intended path for
  the pending model-server port pin (``# sanctioned: network inet tcp
  peer=(ip=127.0.0.1 port=N)`` alongside the port-pinned rule makes the
  transition a single-file edit). A local sanction must name an exact
  ``network`` rule this profile actually carries (otherwise the typo
  is an error), applies to allow rules only, and is matched verbatim
  like every other sanction.
* strict posture — no ``complain`` and no ``unconfined``,
* write confinement — write-granting allow rules stay inside the
  profile's declared write prefixes (a few universal plumbing devices
  are exempt).

Two issue severities exist:

* ``error`` — grammar violations and the hard PengyOS invariants
  (whole-filesystem allow grants, writes outside the declared write
  tree, strict-posture drift);
* ``warning`` — posture lints that a legitimate future profile may
  need to answer intentionally: ``network`` rules (the base profiles
  are socket-free except an explicit, documented grant such as the
  pengy-agent's loopback access to the local model server), grants of
  unknown capabilities, and
  over-broad wildcard-tree grants (``/**`` paths carrying any of the
  ``OVER_BROAD_TREE_PERMS`` flags — even inside a declared write
  tree, a single rule like ``/tmp/x/** rwkl,`` is wider than the
  minimum the work needs).

The write allowlist (``WRITE_PREFIXES``) and the exempt plumbing
paths (``WRITE_EXEMPT_PATHS``) live here as the single source of
truth; ``tests/test_profiles.py`` imports them rather than re-declaring
them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

__all__ = [
    "VALID_PERM_FLAGS",
    "WRITE_PREFIXES",
    "WRITE_EXEMPT_PATHS",
    "NON_PROFILE_FILES",
    "KNOWN_CAPABILITIES",
    "OVER_BROAD_TREE_PERMS",
    "NETWORK_ACCESS_KEYWORDS",
    "NETWORK_DOMAINS",
    "NETWORK_TYPE_PROTOCOLS",
    "LOOPBACK_IPS",
    "SANCTIONED_NETWORK_RULES",
    "SANCTION_DIRECTIVE",
    "Issue",
    "ProfileReport",
    "validate_text",
    "validate_profile",
    "discover_profiles",
    "write_allowed",
]

# ---------------------------------------------------------------------------
# Authoring contract constants (single source of truth — tests import these)
# ---------------------------------------------------------------------------

#: Valid AppArmor permission-flag letters used in path/capability rules.
#: A superset is legal in real AppArmor, but PengyOS profiles stay within
#: this set so the well-formed check stays simple.
VALID_PERM_FLAGS = frozenset("rwxcplkmia")

#: Network rule access keywords accepted by the validator. ``r``/``w``/
#: ``rw`` are the classic coarse flags; the socket-operation keywords
#: come from apparmor.d(5) (AppArmor 3.x fine-grained network
#: mediation). A rule with none of these (or a flag set) carries
#: *implied* permissions and is warned about as over-broad.
NETWORK_ACCESS_KEYWORDS = frozenset(
    {
        "create",
        "bind",
        "listen",
        "accept",
        "connect",
        "shutdown",
        "getattr",
        "setattr",
        "getopt",
        "setopt",
        "send",
        "receive",
    }
)

#: Socket families (DOMAIN in apparmor.d(5)) recognized by the validator.
#: An unknown family is an error, not a warning: a typo'd family in a
#: network grant must not pass silently.
NETWORK_DOMAINS = frozenset(
    {
        "unix",
        "inet",
        "inet6",
        "netlink",
        "packet",
        "vsock",
    }
)

#: Socket types / protocols (TYPE | PROTOCOL in apparmor.d(5)).
NETWORK_TYPE_PROTOCOLS = frozenset({"stream", "dgram", "raw", "tcp", "udp"})

#: The address of a loopback interface — the only IP a PengyOS network
#: grant should scope to (PengyOS is single-machine by design).
LOOPBACK_IPS = frozenset({"127.0.0.1", "::1", "none"})

#: Network grants the project has *explicitly* sanctioned, one exact
#: rule at a time — the network analogue of ``WRITE_PREFIXES``. A
#: well-formed rule that appears verbatim in this set (or in a
#: profile's local ``# sanctioned:`` directives) carries no posture
#: warnings: the socket-grant, implied-permissions, and non-loopback-
#: scope lints were answered deliberately for that exact rule. Any
#: deviation — a different peer IP, a port added or removed, a
#: different type or family — is NOT sanctioned and is warned about
#: (grammar mistakes are errors either way). Currently sanctioned: the
#: pengy-agent's loopback TCP grant to the local model server (the port
#: is deliberately unbound until PengyOS defines it).
SANCTIONED_NETWORK_RULES = frozenset(
    {
        "network inet tcp peer=(ip=127.0.0.1)",
    }
)

#: The comment prefix that turns a ``#`` comment line into a
#: *profile-local network sanction*: ``# sanctioned: <rule>`` (trailing
#: whitespace after an empty rule is tolerated by
#: ``_SANCTION_LINE_RE``). The rule
#: must be an exact ``network`` rule string of the same profile (comma
#: optional, matched verbatim like ``SANCTIONED_NETWORK_RULES`` entries,
#: including the space inside ``peer=(…)``), applies to allow rules
#: only, and silences the posture warnings for that one exact rule.
#: This is how a profile answers for a specific grant in its own file —
#: e.g. the pending model-server port pin will ship as the port-pinned
#: rule plus ``# sanctioned: network inet tcp peer=(ip=127.0.0.1
#: port=N)`` in ``profiles/pengy-agent``.
SANCTION_DIRECTIVE = "# sanctioned: "

# A directive line after stripping: '# sanctioned:' + optional spaces,
# then the rule (may be empty, may end in a comma).
_SANCTION_LINE_RE = re.compile(r"^# sanctioned:\s*(.*)$")

#: Files in ``profiles/`` that are documentation, not profiles.
NON_PROFILE_FILES = frozenset({"README.md"})

#: Write-granting paths that are universal plumbing, not data: null-like
#: devices and the TTY. Exempt from the per-profile write allowlist.
WRITE_EXEMPT_PATHS = frozenset(("/dev/null", "/dev/zero", "/dev/full", "/dev/tty"))

#: Per-profile allowlist: a write-granting allow rule must have its path
#: under one of these prefixes (or be in ``WRITE_EXEMPT_PATHS``). A new
#: profile without an entry here fails validation rather than passing
#: silently wide-open.
WRITE_PREFIXES: Dict[str, Tuple[str, ...]] = {
    "pengy-agent": (
        "/opt/pengy/workspaces/pengy-agent",
        "/tmp/pengy-agent",
        "/var/log/pengy",
    ),
    "sshd": (
        "/run/sshd",
        "/var/log",
        "/var/run/utmp",
        "/dev/log",
    ),
}

#: Linux capability names, current through kernel 6.x (cap(7)). Unknown
#: names are warned about, not errored, so a future kernel capability
#: does not hard-break the validator.
KNOWN_CAPABILITIES = frozenset(
    {
        "chown",
        "dac_override",
        "dac_read_search",
        "fowner",
        "fsetid",
        "kill",
        "setgid",
        "setuid",
        "setpcap",
        "linux_immutable",
        "net_bind_service",
        "net_broadcast",
        "net_admin",
        "net_raw",
        "ipc_lock",
        "ipc_owner",
        "sys_module",
        "sys_rawio",
        "sys_chroot",
        "sys_ptrace",
        "sys_pacct",
        "sys_admin",
        "sys_boot",
        "sys_nice",
        "sys_resource",
        "sys_time",
        "sys_tty_config",
        "mknod",
        "lease",
        "audit_write",
        "audit_control",
        "setfcap",
        "mac_override",
        "mac_admin",
        "syslog",
        "wake_alarm",
        "block_suspend",
        "audit_read",
        "perfmon",
        "bpf",
        "checkpoint_restore",
    }
)

#: Permission flags that make an allow grant on a wildcard tree (a path
#: containing ``/**``) over-broad: ``w`` lets one rule write any file
#: under the tree, ``k`` (keepcaps) turns everything under the tree into
#: a capability carrier, and ``l`` lets the process link to any file in
#: the tree. A ``/**`` allow grant using any of these is *legal* (a
#: declared write tree still passes write confinement) but is warned
#: about — it is usually wider than the minimum the work needs.
#: Read/execute/map (``r``, ``x``, ``m``) on a scoped tree stays clean;
#: that is the intended minimal style (e.g. ``/usr/lib/openssh/** r``).
OVER_BROAD_TREE_PERMS = frozenset("wkl")

NAME_RE = r"[A-Za-z0-9._-]+"
HEADER_RE = re.compile(rf"^({NAME_RE}) \(({NAME_RE})\) \{{$")
CAP_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# apparmor.d(5): "The ip conditional accepts both IPv4 and IPv6 using the
# regular representation ... Contiguous leading zeros can be replaced by
# '::' once." Plus the 'none' keyword for unknown/unbound addresses.
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_IPV6_RE = re.compile(r"^(?:[0-9A-Fa-f]{0,4}:){1,7}[0-9A-Fa-f]{0,4}$")


def _valid_ip(value: str) -> bool:
    """True for a well-formed IPv4 literal, an IPv6 literal, or 'none'."""
    return (
        value == "none"
        or (bool(_IPV4_RE.match(value)) and all(int(o) <= 255 for o in value.split(".")))
        or bool(_IPV6_RE.match(value))
    )


# port=NNNN or port=NNNN-MMMM (apparmor.d(5) port ranges)
_PORT_RE = re.compile(r"^\d{1,5}(?:-\d{1,5})?$")


def _valid_port(value: str) -> bool:
    """True for a well-formed TCP port or a well-ordered port range.

    A single port must be in ``1..65535``; a range ``N-M`` must have both
    endpoints in range with ``N <= M``. The shape is checked first (``_PORT_RE``
    rejects non-decimal / non-numeric tokens); this adds the range and
    ordering invariants the shape check alone misses — ``port=99999``,
    ``port=0`` and ``port=2000-1000`` all match ``_PORT_RE`` but are not
    real TCP ports, and the port-pin transition depends on the validator
    enforcing the same ``1..65535`` window that
    :func:`pengyos_apparmor.portpin.normalize_port` enforces.
    """
    if not _PORT_RE.match(value):
        return False
    endpoints = [int(p) for p in value.split("-")]
    if any(n < 1 or n > 65535 for n in endpoints):
        return False
    if len(endpoints) == 2 and endpoints[0] > endpoints[1]:
        return False
    return True


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    """One validator finding.

    ``severity`` is ``"error"`` (grammar violation / hard invariant
    broken) or ``"warning"`` (posture lint a future profile may need to
    answer intentionally).
    """

    severity: str
    lineno: Optional[int]
    message: str

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    def format(self, name: str) -> str:
        loc = f"{name}:{self.lineno}" if self.lineno is not None else name
        return f"{loc}: {self.severity}: {self.message}"


@dataclass
class ProfileReport:
    """Validation outcome for one profile."""

    name: str
    issues: List[Issue] = field(default_factory=list)

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.is_error]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if not i.is_error]

    @property
    def ok(self) -> bool:
        """True when the profile has no errors (warnings are allowed)."""
        return not self.errors


def write_allowed(path: str, prefixes: Sequence[str]) -> bool:
    """True when a write-granting path is universal plumbing or under one
    of the profile's declared write prefixes."""
    if path in WRITE_EXEMPT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in prefixes)


# ---------------------------------------------------------------------------
# Rule parsing
# ---------------------------------------------------------------------------


@dataclass
class ParsedRule:
    kind: str  # "path" | "capability" | "unknown"
    is_allow: bool
    cap: Optional[str] = None  # capability name (kind == "capability")
    path: Optional[str] = None  # rule path (kind == "path")
    perms: Optional[str] = None  # permission-flag token (kind == "path")


def _parse_rule(rule: str) -> ParsedRule:
    """Classify a stripped rule line (trailing comma already removed)."""
    tokens = rule.split()
    if not tokens:
        return ParsedRule(kind="unknown", is_allow=True)
    first = tokens[0]
    if first == "deny":
        is_allow = False
        rest = tokens[1:]
    else:
        is_allow = True
        rest = tokens
    if not rest:
        return ParsedRule(kind="unknown", is_allow=is_allow)
    if rest[0] == "capability":
        cap = None
        if len(rest) > 1:
            cap = rest[1] if len(rest) == 2 else " ".join(rest[1:])
        return ParsedRule(kind="capability", is_allow=is_allow, cap=cap)
    if len(rest) >= 2 and rest[0] == "network":
        # network [type [protocol]] <flags> — the last token is the
        # permission-flag set; the path position is synthetic.
        return ParsedRule(kind="path", is_allow=is_allow, path="network", perms=rest[-1])
    if len(rest) >= 2:
        path = rest[0]
        perms = rest[-1]
        return ParsedRule(kind="path", is_allow=is_allow, path=path, perms=perms)
    if len(rest) == 1:
        # "capability," (all capabilities) or a bare path with no flags.
        if rest[0] == "capability":
            return ParsedRule(kind="capability", is_allow=is_allow, cap=None)
        return ParsedRule(kind="path", is_allow=is_allow, path=rest[0], perms="")
    return ParsedRule(kind="unknown", is_allow=is_allow)


def _validate_network_rule(
    lineno: int,
    rule: str,
    is_allow: bool,
    sanctioned_network_rules: frozenset,
) -> List[Issue]:
    """Validate a ``network`` rule (trailing comma already removed).

    Models apparmor.d(5)::

        network [access] [domain] [type|protocol] [local expr] [peer expr]

    where *access* is a flag set (``rw``) or socket-operation keywords
    (``connect``, ``send``, …), and the local/peer exprs are ``ip=…``,
    ``port=…`` conditionals (peer ones wrapped in ``peer=(…)``).
    Grammar mistakes are errors. Posture warnings (the socket grant
    itself, implied permissions, non-loopback ``ip=`` scope) fire for
    every rule that is not sanctioned — the sanctioned grants (the
    package-level ``SANCTIONED_NETWORK_RULES`` unioned with the
    profile's local ``# sanctioned:`` directives) are the intentional,
    documented exceptions (``LOOPBACK_IPS``).
    """
    issues: List[Issue] = []
    if not is_allow:
        issues.append(
            Issue(
                "warning",
                lineno,
                "deny network rules are a no-op under default-deny; review intent",
            )
        )
    # peer=(…) may contain spaces (apparmor.d(5) shows
    # `peer=(ip=10.139.15.23 port=8081)`), so extract those blobs before
    # tokenizing the rest of the rule.
    raw = rule.rstrip(",").strip()
    peer_blobs = re.findall(r"peer=\([^)]*\)", raw)
    rest = re.sub(r"peer=\([^)]*\)", " ", raw)
    tokens = rest.split()
    # tokens[0] is 'deny' for deny rules, 'network' for allow rules.
    idx = tokens.index("network")
    if len(tokens) <= idx + 1 and not peer_blobs:
        issues.append(Issue("error", lineno, "network rule has no permission flags"))
        return issues

    ip_conditions: List[str] = []
    has_access = False
    # Multiplicity slots (apparmor.d(5)): a network rule carries at most
    # one domain, at most one type/protocol, and at most one peer=(…) —
    # each grammar slot appears once. The per-token validity check below
    # accepts any *valid* domain/type token regardless of position, so a
    # rule like `network inet inet rw` (two families) or `network inet
    # tcp udp rw` (two types) passes the shape check on its own; the
    # counts below turn the duplicate slot into a grammar error.
    domain_tokens: List[str] = []
    type_tokens: List[str] = []

    for blob in peer_blobs:
        body = blob[len("peer="):][1:-1]  # strip 'peer=' and the parentheses
        fields = body.split()
        if not fields:
            issues.append(
                Issue(
                    "error",
                    lineno,
                    f"network peer conditional is empty: {blob!r}",
                )
            )
            continue
        for field in fields:
            if field.startswith("ip="):
                value = field[len("ip="):]
                if "{" in value or "}" in value:
                    issues.append(
                        Issue(
                            "error",
                            lineno,
                            f"network peer conditional has an unsupported "
                            f"address set: {field!r} (the ip= conditional "
                            "takes a single IPv4/IPv6 literal or 'none' — "
                            "use one rule per address; a multi-member set "
                            "also carries a comma, which is a rule "
                            "terminator)",
                        )
                    )
                elif not _valid_ip(value):
                    issues.append(
                        Issue(
                            "error",
                            lineno,
                            f"network peer conditional has a malformed ip: {field!r} "
                            "(expected an IPv4/IPv6 literal or 'none')",
                        )
                    )
                else:
                    ip_conditions.append(value)
            elif field.startswith("port="):
                if "{" in field or "}" in field:
                    issues.append(
                        Issue(
                            "error",
                            lineno,
                            f"network peer conditional has an unsupported "
                            f"port set: {field!r} (the port= conditional "
                            "takes port=N or port=N-M — use one rule per "
                            "port)",
                        )
                    )
                elif not _valid_port(field[len("port="):]):
                    issues.append(
                        Issue(
                            "error",
                            lineno,
                            f"network peer conditional has a malformed port: "
                            f"{field!r} (expected port=N or port=N-M, "
                            "1..65535, N<=M)",
                        )
                    )
            else:
                issues.append(
                    Issue(
                        "error",
                        lineno,
                        f"unknown network peer conditional {field!r} "
                        "(expected ip=… and/or port=…)",
                    )
                )

    for token in tokens[idx + 1:]:
        if token.startswith("peer="):
            # A 'peer=' token that the blob extraction did not consume: it
            # is not wrapped in parentheses.
            issues.append(
                Issue(
                    "error",
                    lineno,
                    f"network peer conditional must be 'peer=(…)': {token!r}",
                )
            )
            continue
        if token.startswith("ip="):
            value = token[len("ip="):]
            if "{" in value or "}" in value:
                issues.append(
                    Issue(
                        "error",
                        lineno,
                        f"network rule has an unsupported address set: "
                        f"{token!r} (the ip= conditional takes a single "
                        "IPv4/IPv6 literal or 'none' — use one rule per "
                        "address; a multi-member set also carries a comma, "
                        "which is a rule terminator)",
                    )
                )
            elif not _valid_ip(value):
                issues.append(
                    Issue(
                        "error",
                        lineno,
                        f"network rule has a malformed ip conditional: {token!r} "
                        "(expected an IPv4/IPv6 literal or 'none')",
                    )
                )
            else:
                ip_conditions.append(value)
            continue
        if token.startswith("port="):
            if "{" in token or "}" in token:
                issues.append(
                    Issue(
                        "error",
                        lineno,
                        f"network rule has an unsupported port set: {token!r} "
                        "(the port= conditional takes port=N or port=N-M — "
                        "use one rule per port)",
                    )
                )
            elif not _valid_port(token[len("port="):]):
                issues.append(
                    Issue(
                        "error",
                        lineno,
                        f"network rule has a malformed port conditional: {token!r} "
                        "(expected port=N or port=N-M, 1..65535, N<=M)",
                    )
                )
            continue
        if token in NETWORK_DOMAINS:
            domain_tokens.append(token)
            continue
        if token in NETWORK_TYPE_PROTOCOLS:
            type_tokens.append(token)
            continue
        if token in NETWORK_ACCESS_KEYWORDS:
            has_access = True
            continue
        if set(token) <= VALID_PERM_FLAGS:
            has_access = True
            continue
        issues.append(
            Issue(
                "error",
                lineno,
                f"network rule has invalid token {token!r} (expected a domain "
                f"{'/'.join(sorted(NETWORK_DOMAINS))}, a type/protocol "
                f"{'/'.join(sorted(NETWORK_TYPE_PROTOCOLS))}, an access keyword "
                f"{'/'.join(sorted(NETWORK_ACCESS_KEYWORDS))}, ip=/port= "
                f"conditionals, peer=(…), or flags from "
                f"{''.join(sorted(VALID_PERM_FLAGS))})",
            )
        )

    # Each grammar slot appears at most once: a second domain, a second
    # type/protocol, or a second peer=(…) makes the rule malformed even
    # though every token on its own is a legal one.
    if len(peer_blobs) > 1:
        issues.append(
            Issue(
                "error",
                lineno,
                f"network rule has more than one peer=(…) conditional "
                f"({len(peer_blobs)} found); the apparmor.d(5) grammar "
                "allows at most one peer per rule",
            )
        )
    if len(domain_tokens) > 1:
        issues.append(
            Issue(
                "error",
                lineno,
                f"network rule has more than one domain "
                f"({' '.join(domain_tokens)!r}); the apparmor.d(5) grammar "
                "allows exactly one domain per rule",
            )
        )
    if len(type_tokens) > 1:
        issues.append(
            Issue(
                "error",
                lineno,
                f"network rule has more than one type/protocol "
                f"({' '.join(type_tokens)!r}); the apparmor.d(5) grammar "
                "allows exactly one type/protocol per rule",
            )
        )

    if not issues:
        if is_allow and raw in sanctioned_network_rules:
            # This exact rule was answered intentionally (see
            # SANCTIONED_NETWORK_RULES and the local # sanctioned:
            # directives): no posture warnings. Any deviation —
            # different IP, port, type — misses the exact match and is
            # warned about below.
            return issues
        if not has_access:
            issues.append(
                Issue(
                    "warning",
                    lineno,
                    "network rule carries implied permissions (no explicit "
                    "access flags or keywords); list the socket operations "
                    "intentionally",
                )
            )
        if any(ip not in LOOPBACK_IPS for ip in ip_conditions):
            issues.append(
                Issue(
                    "warning",
                    lineno,
                    "network grant is not loopback-scoped: PengyOS network "
                    "grants should scope ip= to the loopback interfaces "
                    "only",
                )
            )
        issues.append(
            Issue(
                "warning",
                lineno,
                "network rule: intentional socket grant — the base profiles "
                "are socket-free by default; document the destination and "
                "keep it as narrow as possible",
            )
        )
    return issues


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _code_lines(text: str) -> List[Tuple[int, str, bool]]:
    """(lineno, stripped_line, is_comment) for every line of the profile."""
    return [
        (lineno, line.strip(), line.strip().startswith("#"))
        for lineno, line in enumerate(text.splitlines(), 1)
        if line.strip()
    ]


def _validate_body(
    lines: Sequence[Tuple[int, str, str]],
    prefixes: Sequence[str],
    sanctioned_network_rules: frozenset,
) -> List[Issue]:
    issues: List[Issue] = []
    for lineno, raw, rule in lines:
        if not raw.endswith(","):
            issues.append(
                Issue(
                    "error",
                    lineno,
                    f"rule must end with ',': {raw.rstrip()!r}",
                )
            )
            continue
        # Commas TERMINATE rules: a well-formed rule line ends with exactly
        # one comma, and no comma may appear inside the rule itself. The old
        # check only required the last character to be a comma, so
        # `/etc/passwd, r,` validated clean as the path `/etc/passwd,` with
        # flag `r` — a comma-laden path no profile could have intended (real
        # AppArmor reads the comma as a rule terminator either way), and
        # `network inet tcp rw,,` / `capability setuid,,` (a double
        # terminator) passed as if the second comma were not there.
        # A comma-inside a rule is malformed as a whole, so the per-field
        # checks below are skipped for it (same discipline as the token-
        # shape errors): a rule with a stray comma reports its own error
        # only. Address sets such as `ip={a,b}` are not supported by this
        # validator's model of the ip conditional.
        trailing = len(raw) - len(raw.rstrip(","))
        if trailing > 1:
            issues.append(
                Issue(
                    "error",
                    lineno,
                    f"rule must end with exactly one ',' (found {trailing} "
                    f"trailing commas): {raw!r}",
                )
            )
            continue
        if "," in rule:
            bad = [tok for tok in rule.split() if "," in tok]
            issues.append(
                Issue(
                    "error",
                    lineno,
                    "commas terminate rules and may not appear inside a rule: "
                    f"token(s) {bad!r} contain one (address sets such as "
                    "ip={a,b} are not supported by this validator)",
                )
            )
            continue
        parsed = _parse_rule(rule)
        if parsed.kind == "unknown":
            issues.append(
                Issue("error", lineno, f"invalid rule prefix or malformed rule: {rule!r}")
            )
            continue
        if parsed.kind == "capability":
            if not parsed.is_allow:
                issues.append(
                    Issue(
                        "warning",
                        lineno,
                        "deny rules do not take capability rules (no-op); review intent",
                    )
                )
            # AppArmor capability rules name exactly ONE capability per
            # rule. The rule is tokenized as 'capability <name>', so more
            # than one name token is malformed — e.g. `capability setuid
            # chown,` is not a grant of two capabilities, it is a parse
            # error. (The earlier behaviour joined the extras into one
            # bogus name and only warned 'suspicious name'.)
            cap_tokens = rule.split()
            if cap_tokens[0] == "deny":
                cap_tokens = cap_tokens[1:]
            cap_tokens = cap_tokens[1:]  # drop the 'capability' keyword itself
            if len(cap_tokens) > 1:
                issues.append(
                    Issue(
                        "error",
                        lineno,
                        f"capability rule must name exactly one capability "
                        f"(found {len(cap_tokens)}: {' '.join(cap_tokens)!r}) "
                        f"— one capability per rule: {rule!r}",
                    )
                )
                continue
            if parsed.cap is None:
                issues.append(
                    Issue(
                        "warning",
                        lineno,
                        "bare 'capability' rule grants every capability (over-broad)",
                    )
                )
            elif not CAP_NAME_RE.match(parsed.cap):
                issues.append(
                    Issue(
                        "warning",
                        lineno,
                        f"capability rule has a suspicious name: {parsed.cap!r}",
                    )
                )
            elif parsed.cap not in KNOWN_CAPABILITIES:
                issues.append(
                    Issue(
                        "warning",
                        lineno,
                        f"unknown capability {parsed.cap!r} (not in the known set)",
                    )
                )
            continue
        # kind == "path"
        path = parsed.path
        perms = parsed.perms or ""
        if path == "network":
            issues.extend(
                _validate_network_rule(lineno, rule, parsed.is_allow, sanctioned_network_rules)
            )
            continue
        # A path rule is exactly '<path> <flags>' — one path token and one
        # flag-set token (after an optional 'deny'). More tokens is
        # malformed: the old parse silently took the FIRST path and the
        # LAST flags and dropped the middle (e.g. `/etc/passwd /etc/shadow
        # r,` parsed as `/etc/passwd r,`), which real AppArmor rejects. The
        # rule is malformed as a whole, so the per-field checks below
        # (absolute path, flag letters, write confinement) are skipped —
        # reporting them for a rule whose token count is already wrong
        # would endorse the silent first/last interpretation.
        rule_tokens = rule.split()
        if rule_tokens and rule_tokens[0] == "deny":
            rule_tokens = rule_tokens[1:]
        if len(rule_tokens) > 2:
            issues.append(
                Issue(
                    "error",
                    lineno,
                    f"path rule must be exactly '<path> <flags>' — "
                    f"{len(rule_tokens)} tokens found: {rule!r}",
                )
            )
            continue
        # Per-field grammar checks. Once ANY of them fails the rule is
        # malformed in a way that changes what its tokens mean (a
        # non-absolute path, a missing or invalid flag token), so the
        # semantic checks below (whole-filesystem grant, over-broad lint,
        # write confinement) are skipped — the same "a malformed rule
        # reports its own error only" discipline the token-shape and
        # comma-terminator checks use. A rule with an invalid flag token or
        # a non-absolute path is one AppArmor rejects outright, so
        # reasoning about its write surface (which tree it confines, whether
        # a /** tree is over-broad) would endorse a check on a rule that can
        # never be active. A *well-formed* rule (absolute path, valid flags)
        # keeps every semantic check: the whole-filesystem error and the
        # write-confinement error still fire together for e.g. `/** rw,`,
        # and the over-broad warning still fires alongside a tree write
        # outside the declared trees.
        field_malformed = False
        if not path.startswith("/"):
            if "/" in path:
                issues.append(
                    Issue("error", lineno, f"path must be absolute: {rule!r}")
                )
            else:
                issues.append(
                    Issue(
                        "error",
                        lineno,
                        "invalid rule prefix (expected an absolute path or a "
                        f"known keyword: {rule!r})",
                    )
                )
            field_malformed = True
        if not perms:
            issues.append(Issue("error", lineno, f"rule has no permission flags: {rule!r}"))
            field_malformed = True
        elif not set(perms) <= VALID_PERM_FLAGS:
            issues.append(
                Issue(
                    "error",
                    lineno,
                    f"invalid permission flags {perms!r} (allowed letters: {''.join(sorted(VALID_PERM_FLAGS))})",
                )
            )
            field_malformed = True
        if field_malformed:
            continue
        if parsed.is_allow:
            if path in ("/**", "/*", "/"):
                issues.append(
                    Issue("error", lineno, f"whole-filesystem allow grant: {rule!r}")
                )
            broad = sorted(set(perms) & OVER_BROAD_TREE_PERMS)
            if "/**" in path and broad:
                issues.append(
                    Issue(
                        "warning",
                        lineno,
                        f"over-broad wildcard-tree grant: {path!r} with flags "
                        f"{''.join(broad)!r} ({perms!r}); a single /** rule grants "
                        "this for every file under the tree — narrow the path "
                        "or the flags",
                    )
                )
            if perms and "w" in perms and not write_allowed(path, prefixes):
                issues.append(
                    Issue(
                        "error",
                        lineno,
                        f"write grant outside the declared write prefixes: {rule!r}",
                    )
                )
    return issues


def _parse_local_sanctions(text: str) -> Tuple[List[Tuple[int, str]], List[Issue]]:
    """Extract the profile-local ``# sanctioned:`` directives.

    Returns ``(sanctions, issues)``: each sanction is a
    ``(lineno, rule)`` pair (rule comma-stripped, matched verbatim
    later) and ``issues`` carries the grammar findings for the directive
    lines themselves (empty directives, non-network rules).
    """
    sanctions: List[Tuple[int, str]] = []
    issues: List[Issue] = []
    for lineno, line, is_comment in _code_lines(text):
        if not is_comment:
            continue
        m = _SANCTION_LINE_RE.match(line)
        if m is None:
            continue
        rule = m.group(1).strip().rstrip(",")
        if not rule:
            issues.append(
                Issue(
                    "error",
                    lineno,
                    f"empty local sanction directive: {line!r} must name a network rule",
                )
            )
        else:
            tokens = rule.split()
            head = tokens[0] if tokens else ""
            if not (
                (head == "network" and len(tokens) > 1)
                or (head == "deny" and len(tokens) > 1 and tokens[1] == "network")
            ):
                issues.append(
                    Issue(
                        "error",
                        lineno,
                        f"local sanction must name a network rule: {rule!r} "
                        "(only 'network …' rules can be sanctioned)",
                    )
                )
            else:
                sanctions.append((lineno, rule))
    return sanctions, issues


def validate_text(
    text: str,
    name: str,
    *,
    write_prefixes: Optional[Sequence[str]] = None,
) -> ProfileReport:
    """Validate profile source text.

    ``name`` is the profile name for reporting; it is used to look up
    ``write_prefixes`` in ``WRITE_PREFIXES`` when none are passed. A
    profile with an unknown name and no explicit prefixes gets none, so
    its write grants fail validation — deliberately loud.

    Comment lines of the form ``# sanctioned: <rule>`` are profile-local
    network sanctions: the rule must be an exact ``network`` rule this
    profile carries (a typo is an error), and it silences the posture
    warnings for that one exact allow rule — in addition to the
    package-level ``SANCTIONED_NETWORK_RULES``.
    """
    report = ProfileReport(name=name)
    if write_prefixes is None:
        write_prefixes = WRITE_PREFIXES.get(name, ())

    code = [(ln, line) for ln, line, is_comment in _code_lines(text) if not is_comment]
    if not code:
        report.issues.append(Issue("error", None, "empty profile (no profile header)"))
        return report

    first_lineno, first_line = code[0]
    header_match = HEADER_RE.match(first_line)
    if not header_match:
        report.issues.append(
            Issue("error", first_lineno, f"bad profile header line: {first_line!r} "
                                         "(expected: '<name> (alias) {')")
        )
    elif header_match.group(1) != name:
        report.issues.append(
            Issue(
                "error",
                first_lineno,
                f"profile name {header_match.group(1)!r} does not match {name!r}",
            )
        )

    # Track *every* brace line, not just the first of each: AppArmor
    # profiles have exactly one top-level brace pair (the header's '{' and
    # the profile's final '}'), so a stray extra '{' or '}' anywhere is an
    # unbalanced-braces parse error even when the positional checks below
    # (which look at the first open / first close) are all satisfied.
    open_lines = [ln for ln, line in code if line.endswith("{")]
    close_lines = [ln for ln, line in code if line.endswith("}")]
    open_line = open_lines[0] if open_lines else None
    close_line = close_lines[0] if close_lines else None
    if open_line is None:
        report.issues.append(Issue("error", None, "no open brace: profile body is missing"))
    else:
        if close_line is None:
            report.issues.append(
                Issue("error", open_line, "unbalanced braces: missing closing brace")
            )
        else:
            if open_line != first_lineno:
                report.issues.append(
                    Issue("error", first_lineno, "profile header must open the body with '{'")
                )
            if open_line > close_line:
                report.issues.append(
                    Issue(
                        "error",
                        close_line,
                        "unbalanced braces: closing brace precedes the opening brace",
                    )
                )
            elif code[-1][0] != close_line:
                report.issues.append(
                    Issue(
                        "error",
                        code[-1][0],
                        "profile must end with the closing brace (content found after it)",
                    )
                )
        if len(open_lines) > 1 or len(close_lines) > 1:
            # The count mismatch is reported at the stray brace (the extra
            # open, or the extra close when opens are fine).
            stray = (
                open_lines[-1]
                if len(open_lines) > 1
                else (close_lines[-1] if len(close_lines) > 1 else open_line)
            )
            report.issues.append(
                Issue(
                    "error",
                    stray,
                    "unbalanced braces: a profile has exactly one '{' and one '}' "
                    f"(found {len(open_lines)} open, {len(close_lines)} close)",
                )
            )

    # Strict posture: complain/unconfined anywhere in code lines is drift.
    for lineno, line in code:
        low = line.lower()
        for bad in ("complain", "unconfined"):
            if bad in low:
                report.issues.append(
                    Issue(
                        "error",
                        lineno,
                        f"strict profiles must not use '{bad}'",
                    )
                )

    # The rule body excludes its structural lines BY IDENTITY, not by
    # substring: the profile header line (the first non-comment line) and
    # every line counted as a structural brace (a code line ending in '{'
    # or '}') are not rules. Everything else is rule-validated —
    # including a rule line that carries braces, e.g. an ip={…}
    # conditional. The old filter dropped ANY line containing '{' or '}',
    # so such a rule was silently never rule-checked: a write grant on a
    # brace-bearing path could sit outside the declared write trees and
    # still validate clean. Stray extra braces are structural garbage the
    # count check above already flags, so they are not rule-validated a
    # second time (a broken line reports its own error once).
    structural = {code[0][0]}
    structural.update(open_lines)
    structural.update(close_lines)
    body = [
        (ln, line, line.rstrip(",").strip())
        for ln, line in code
        if ln not in structural
    ]

    # Profile-local network sanctions (# sanctioned: <rule>). Each one
    # must name an exact network rule this profile actually carries —
    # a typo'd sanction is an error rather than a silently-dead line.
    local_sanctions, sanction_issues = _parse_local_sanctions(text)
    report.issues.extend(sanction_issues)
    carried = {
        rule for _ln, _line, rule in body if "network" in rule.split()
    }
    for lineno, rule in local_sanctions:
        if rule not in carried:
            report.issues.append(
                Issue(
                    "error",
                    lineno,
                    f"local sanction matches no network rule in this profile: "
                    f"{rule!r} (sanctions must name an exact rule the profile carries)",
                )
            )

    sanctioned = SANCTIONED_NETWORK_RULES | frozenset(rule for _ln, rule in local_sanctions)
    report.issues.extend(_validate_body(body, write_prefixes, sanctioned))
    return report


def validate_profile(
    path: Union[str, Path],
    *,
    name: Optional[str] = None,
    write_prefixes: Optional[Sequence[str]] = None,
) -> ProfileReport:
    """Validate a profile file.

    ``name`` defaults to the file name (the PengyOS naming convention:
    the profile is named after the confined process).
    """
    path = Path(path)
    profile_name = name if name is not None else path.name
    report = validate_text(
        path.read_text(encoding="utf-8"),
        profile_name,
        write_prefixes=write_prefixes,
    )
    if name is None and profile_name not in WRITE_PREFIXES and write_prefixes is None:
        report.issues.append(
            Issue(
                "warning",
                None,
                f"profile {profile_name!r} has no declared write prefixes; "
                "any write grant fails validation until its write tree is "
                "declared in WRITE_PREFIXES",
            )
        )
    return report


def discover_profiles(profiles_dir: Union[str, Path]) -> List[Path]:
    """Sorted profile files in a directory (``NON_PROFILE_FILES`` excluded)."""
    profiles_dir = Path(profiles_dir)
    return sorted(
        p for p in profiles_dir.iterdir() if p.is_file() and p.name not in NON_PROFILE_FILES
    )
