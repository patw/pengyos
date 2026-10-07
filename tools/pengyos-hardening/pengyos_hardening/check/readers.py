"""File readers: turn a config file on disk into the key/value pairs a matcher
can evaluate.

Each reader is a thin, well-tested parser. They know nothing about the
baseline — they only produce a flat ``{KEY: value}`` mapping so the matcher
layer can decide whether a rule fires. The ``KEY`` casing is normalised to the
canonical form the baseline expects.

Readers are added one domain at a time: the sshd_config
reader (backing ``key_value``) is first, then the sysctl.d / fstab readers
(backing ``sysctl_value`` / ``mount_option``), then the nftables reader
(backing ``nft_policy``) and the systemd-unit reader (backing ``unit_enabled``),
then the Buildroot defconfig reader (backing ``symbol_absent`` /
``symbol_present``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KeyValue:
    """The effective key/value view of a line-oriented config file.

    ``values`` maps a *canonical* key (e.g. ``PermitRootLogin``) to its last
    written value, honouring sshd's last-wins semantics. ``raw`` preserves the
    original ``(key, value, line)`` triples so a matcher can report exact
    evidence.
    """

    values: dict[str, str]
    raw: list[tuple[str, str, int]]

    def get(self, key: str, default: str | None = None) -> str | None:
        """Return the effective value for ``key`` (canonical form) or ``default``."""
        return self.values.get(key, default)

    def line_for(self, key: str) -> int | None:
        """The 1-based line number of the last written ``key`` (canonical)."""
        for k, _v, lineno in reversed(self.raw):
            if k == key:
                return lineno
        return None


def _canonicalise_sshd_key(key: str) -> str:
    """Map an sshd_config key to its canonical CamelCase form.

    sshd matches option names case-insensitively and has a set of known keys in
    canonical CamelCase. We accept the common spellings and normalise so a
    ``permitrootlogin`` or ``PERMITROOTLOGIN`` still resolves. Unknown keys keep
    their original text (canonicalised by capitalising the first letter), which
    is harmless: an unknown key simply never matches a baseline rule.
    """
    known = {
        "permitrootlogin": "PermitRootLogin",
        "passwordauthentication": "PasswordAuthentication",
        "pubkeyauthentication": "PubkeyAuthentication",
        "permitemptypasswords": "PermitEmptyPasswords",
        "x11forwarding": "X11Forwarding",
        "allowtcpforwarding": "AllowTcpForwarding",
        "protocol": "Protocol",
        "challengeresponseauthentication": "ChallengeResponseAuthentication",
        "ignoreuserknownhosts": "IgnoreUserKnownHosts",
        "ignorerhosts": "IgnoreRhosts",
        "loglevel": "LogLevel",
        "maxauthtries": "MaxAuthTries",
        "permittty": "PermitTTY",
    }
    lowered = key.strip().lower()
    return known.get(lowered, key.strip()[0].upper() + key.strip()[1:].lower())


def parse_sshd_config(text: str) -> KeyValue:
    """Parse ``sshd_config`` text into an effective key/value view.

    Semantics (matches OpenSSH):

    * Lines whose first non-space character is ``#`` are comments — ignored.
    * Blank lines are ignored.
    * A line is ``Key Value`` (whitespace separated); the value is the rest of
      the line, stripped.
    * Keys are matched case-insensitively and normalised to canonical form.
    * **Last write wins**: if a key appears more than once, the last value
      wins (this is how sshd resolves duplicate directives).

    Returns a :class:`KeyValue`; an empty config yields an empty mapping.
    """
    values: dict[str, str] = {}
    raw: list[tuple[str, str, int]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # sshd splits on the first run of whitespace: key, then the value.
        parts = stripped.split(None, 1)
        key = parts[0]
        value = parts[1].strip() if len(parts) > 1 else ""
        canonical = _canonicalise_sshd_key(key)
        values[canonical] = value
        raw.append((canonical, value, lineno))
    return KeyValue(values=values, raw=raw)


# --- sysctl.d ---------------------------------------------------------------

@dataclass(frozen=True)
class SysctlValues:
    """The effective key/value view of a set of ``sysctl.d`` files.

    ``values`` maps a sysctl key (e.g. ``kernel.kptr_restrict``) to its
    effective value — the last write wins, both within a file and across files
    processed in lexicographic order (the way the kernel applies
    ``sysctl.d``). ``sources`` records where the effective value came from
    (``(relative_path, line_number)``) so a matcher can pin exact evidence.
    """

    values: dict[str, str]
    sources: dict[str, tuple[str, int]]

    def get(self, key: str, default: str | None = None) -> str | None:
        """Return the effective value for ``key`` (or ``default``)."""
        return self.values.get(key, default)

    def source(self, key: str) -> tuple[str, int] | None:
        """The ``(relative_path, line)`` the effective value for ``key`` came from."""
        return self.sources.get(key)


def parse_sysctl_file(text: str) -> dict[str, str]:
    """Parse a single ``sysctl.d`` file's text into ``{key: value}``.

    Semantics (matches ``sysctl.d``):

    * Lines whose first non-space character is ``#`` are comments — ignored.
    * Blank lines are ignored.
    * A line is ``key = value`` (``=`` separated; the value is the rest,
      stripped). Lines without an ``=`` are ignored.
    * **Last write wins** within the file.

    Returns a plain ``{key: value}`` mapping; an empty file yields ``{}``.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def parse_sysctl_d(files: list[str]) -> SysctlValues:
    """Parse a set of ``sysctl.d`` files (in order) into an effective view.

    ``files`` is the ordered list of file texts to merge — the caller sorts the
    globs, and later files override earlier ones for the same key (last-wins
    across files, exactly as the kernel applies ``sysctl.d``). An empty list
    yields an empty :class:`SysctlValues`.
    """
    values: dict[str, str] = {}
    sources: dict[str, tuple[str, int]] = {}
    for index, text in enumerate(files):
        for key, value in parse_sysctl_file(text).items():
            values[key] = value
            sources[key] = (f"sysctl.d/{index + 1}", _last_written_line(text, key))
    return SysctlValues(values=values, sources=sources)


def _last_written_line(text: str, key: str) -> int:
    """The 1-based line number of the last ``key`` written in ``text``."""
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.split("=", 1)[0].strip() == key:
            return lineno
    return 1


# --- fstab ------------------------------------------------------------------

@dataclass(frozen=True)
class FstabEntry:
    """A single ``/etc/fstab`` mount entry, split into its six columns."""

    source: str
    mountpoint: str
    fstype: str
    options: str
    dump: str
    passno: str
    line: int

    def option_set(self) -> set[str]:
        """The mount options as a set (the ``options`` column split on ``,``)."""
        return {opt.strip() for opt in self.options.split(",") if opt.strip()}


@dataclass(frozen=True)
class Fstab:
    """The parsed ``/etc/fstab``: its entries, indexed by mountpoint."""

    entries: list[FstabEntry]

    def get(self, mountpoint: str) -> FstabEntry | None:
        """The entry mounting ``mountpoint`` (``None`` when it is not present)."""
        for entry in self.entries:
            if entry.mountpoint == mountpoint:
                return entry
        return None


def parse_fstab(text: str) -> Fstab:
    """Parse ``/etc/fstab`` text into :class:`Fstab` entries.

    Semantics:

    * Lines whose first non-space character is ``#`` are comments — ignored.
    * Blank lines are ignored.
    * A line is six whitespace-separated columns:
      ``source mountpoint fstype options dump pass``.
    * Lines that do not have at least six columns are ignored (they are not
      mount entries).
    """
    entries: list[FstabEntry] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) < 6:
            continue
        entries.append(
            FstabEntry(
                source=fields[0],
                mountpoint=fields[1],
                fstype=fields[2],
                options=fields[3],
                dump=fields[4],
                passno=fields[5],
                line=lineno,
            )
        )
    return Fstab(entries=entries)


# --- nftables ---------------------------------------------------------------

@dataclass(frozen=True)
class NftChain:
    """A single nftables ``chain`` block: its name, raw policy, and source line.

    ``policy`` is the chain's default disposition *as written* (``accept``/
    ``drop``/``reject``/the baseline's ``deny`` alias); it is ``None`` when the
    chain block declares no policy (an anonymous chain, or a malformed block).
    ``line`` is the 1-based line the ``chain`` keyword was declared on, so a
    matcher can pin exact evidence. Canonicalisation (``deny`` → nft's ``drop``)
    is the matcher's job, not this reader's — the reader is a faithful parser.
    """

    name: str
    policy: str | None
    line: int


@dataclass(frozen=True)
class Nftables:
    """The parsed nftables ruleset: its chains, indexed by name."""

    chains: dict[str, NftChain]

    def get(self, name: str) -> NftChain | None:
        """The chain named ``name`` (``None`` when it is not declared)."""
        return self.chains.get(name)


def parse_nftables(text: str) -> Nftables:
    """Parse ``nft`` ruleset text into :class:`Nftables` chains.

    Semantics (a pragmatic subset of ``nft`` syntax, enough for the baseline):

    * Lines whose first non-space character is ``#`` are comments — ignored.
    * A ``chain <name> {`` block opens a named chain; its closing ``}`` ends it.
    * The chain's default policy is the ``policy <accept|drop|reject|…>;``
      keyword inside the block (the hardened fixture writes
      ``type filter hook input priority 0; policy deny;`` — the policy keyword
      shares a line with the hook declaration, so it is read from the block
      body, not the ``chain`` line).
    * A chain with no ``policy`` keyword has ``policy = None``.
    * The policy token is returned *as written* (the reader is a faithful
      parser); canonicalisation (``deny`` → nft's ``drop``) is the matcher's
      job.
    * The last chain declared with a given name wins (nft's last-write-wins for
      duplicate chain names within a file).

    Returns a :class:`Nftables`; an empty ruleset yields an empty mapping.
    """
    chains: dict[str, NftChain] = {}
    in_chain = False
    current_name: str | None = None
    current_line: int | None = None
    current_policy: str | None = None

    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if not in_chain:
            # A ``chain <name> {`` line opens a new block.
            if stripped.startswith("chain ") and stripped.endswith("{"):
                in_chain = True
                current_name = stripped[6:].strip().split()[0]
                current_line = lineno
                current_policy = None
            continue

        # Inside a chain block.
        if stripped == "}":
            # Close the block: commit the chain (last write wins on dup names).
            if current_name is not None:
                chains[current_name] = NftChain(
                    name=current_name,
                    policy=current_policy,
                    line=current_line,
                )
            in_chain = False
            current_name = None
            current_line = None
            current_policy = None
            continue

        policy = _nft_chain_policy(stripped)
        if policy is not None:
            current_policy = policy

    return Nftables(chains=chains)


def _nft_chain_policy(stripped: str) -> str | None:
    """Extract a chain ``policy`` value from an nftables statement, if any.

    The policy keyword may sit alone (``policy deny;``) or trail the hook
    declaration on the same line (``type filter hook input priority 0; policy
    deny;``). The token is returned *as written* (case-insensitively lowercased
    for stability) — canonicalisation (``deny`` → nft's ``drop``) is the
    matcher's job, not this reader's. Returns ``None`` when the statement
    declares no policy.
    """
    lowered = stripped.rstrip(";").strip().lower()
    words = lowered.split()
    for index, word in enumerate(words):
        if word == "policy" and index + 1 < len(words):
            return words[index + 1]
    return None


# --- defconfig --------------------------------------------------------------

#: A Buildroot *set* symbol line: ``BR2_FOO=y`` — the ``BR2_`` prefix, no
#: ``CONFIG_`` prefix, value exactly ``y``. Disabled symbols are written
#: ``# CONFIG_BR2_FOO is not set`` (a comment WITH the ``CONFIG_`` prefix) and
#: are deliberately *not* matched here. Symbol names may contain lowercase
#: letters (e.g. the ``BR2_x86_64`` architecture symbol), so the character
#: class allows them.
_SET_SYMBOL_RE = re.compile(r"^BR2_[A-Za-z0-9_]+=y$")


@dataclass(frozen=True)
class Defconfig:
    """The parsed Buildroot defconfig: the symbols set to ``y``, by name.

    ``symbols`` maps a *set* symbol (e.g. ``BR2_PACKAGE_OPENSSH_SERVER``) to the
    1-based line it was set on, so a matcher can pin exact evidence. A disabled
    symbol (``# CONFIG_BR2_FOO is not set``) is *not* recorded — it is a
    comment, not a set line.
    """

    symbols: dict[str, int]

    def is_set(self, symbol: str) -> bool:
        """Whether ``symbol`` is set to ``y`` (a real ``BR2_SYMBOL=y`` line)."""
        return symbol in self.symbols

    def line(self, symbol: str) -> int | None:
        """The 1-based line ``symbol`` was set on (``None`` when not set)."""
        return self.symbols.get(symbol)


def parse_defconfig(text: str) -> Defconfig:
    """Parse a Buildroot defconfig's *set* symbols (``BR2_FOO=y``).

    Semantics (Buildroot defconfig):

    * Lines whose first non-space character is ``#`` are comments — ignored.
      This is the whole ballgame: a disabled symbol is written
      ``# CONFIG_BR2_FOO is not set`` (WITH the ``CONFIG_`` prefix), and a
      faithful parser must not mistake it for a *set* symbol.
    * Blank lines are ignored.
    * A *set* symbol is a line ``BR2_FOO=y`` — the ``BR2_`` prefix, no
      ``CONFIG_`` prefix, value exactly ``y``. Anything else (a value other
      than ``y``, or the disabled ``# CONFIG_… is not set`` comment) is not a
      set symbol.
    * ``symbols`` maps each set symbol to its 1-based source line.

    Returns a :class:`Defconfig`; an empty config yields an empty mapping.
    """
    symbols: dict[str, int] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _SET_SYMBOL_RE.match(stripped):
            symbols[stripped.split("=", 1)[0]] = lineno
    return Defconfig(symbols=symbols)
