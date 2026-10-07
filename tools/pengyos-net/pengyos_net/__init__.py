"""PengyOS net — baseline network config + validator.

Validates the committed ``networkd/`` (systemd-networkd/resolved) and
``nftables/`` (pengyos.nft) configs for syntax and security policy
(default-deny inbound, no accept before the allow table, explicit allow
list of exactly SSH tcp/22).
"""
from .nft_validate import (
    ALLOW_TABLE,
    EXPECTED_NEW_ACCEPTS,
    NftSyntaxError,
    PINNED_INPUT_STATEMENTS,
    ValidationResult,
    chain_policy,
    chain_statements,
    dport_accepts,
    parse_nft_ruleset,
    strip_comments,
    validate_ruleset,
    validate_ruleset_text,
)

__version__ = "0.1.0"

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
