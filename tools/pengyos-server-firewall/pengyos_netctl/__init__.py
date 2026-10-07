"""pengyos_netctl — PengyOS server-firewall CLI (US-SRV-5).

A default-deny nftables ruleset generator + a WireGuard config generator,
with an offline validator. See spec.md and the README.
"""

__version__ = "0.1.0"

from .config import (
    PeerSpec,
    PortsConfig,
    ServerSpec,
    WgSpec,
    load_config,
    load_ports_config,
    load_wg_spec,
)
from .errors import (
    KeyAlreadyExistsError,
    NetctlError,
    NftValidationError,
    OutputError,
    SpecError,
)
from .nft import (
    NftSyntaxError,
    Port,
    ValidationResult,
    chain_policy,
    chain_statements,
    dport_accepts,
    find_rate_limit_drop,
    parse_nft_ruleset,
    strip_comments,
    tokenize,
    validate_ports_ruleset,
    validate_ports_ruleset_text,
)
from .render import (
    render_allow_list,
    render_ports,
    render_ports_ruleset,
    mitigation_statements,
    write_ruleset,
)
from .wireguard import (
    WgOutput,
    decode_private_key,
    generate,
    generate_private_key,
    private_to_public,
    render_peer_config,
    render_server_config,
    x25519,
)

__all__ = [
    "__version__",
    "PeerSpec",
    "PortsConfig",
    "ServerSpec",
    "WgSpec",
    "KeyAlreadyExistsError",
    "NetctlError",
    "NftValidationError",
    "OutputError",
    "SpecError",
    "NftSyntaxError",
    "Port",
    "ValidationResult",
    "chain_policy",
    "chain_statements",
    "dport_accepts",
    "find_rate_limit_drop",
    "parse_nft_ruleset",
    "strip_comments",
    "tokenize",
    "validate_ports_ruleset",
    "validate_ports_ruleset_text",
    "render_ports_ruleset",
    "render_allow_list",
    "render_ports",
    "mitigation_statements",
    "write_ruleset",
    "load_config",
    "load_ports_config",
    "load_wg_spec",
    "WgOutput",
    "decode_private_key",
    "generate",
    "generate_private_key",
    "private_to_public",
    "render_peer_config",
    "render_server_config",
    "x25519",
]
