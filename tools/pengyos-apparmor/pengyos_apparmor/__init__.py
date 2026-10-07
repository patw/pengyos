"""PengyOS apparmor — minimal profiles + profile validator.

Confines PengyOS core components (SPEC §1) with small, strict-by-default
AppArmor profiles, and ships a validator that checks profile grammar
basics: header, balanced braces, and well-formed capability/path rules.
The port-pin transition for the model-server grant is carried by
``pengyos_apparmor.portdiscovery`` (upstream port re-check),
``pengyos_apparmor.portpin`` (plan/render/apply),
``pengyos_apparmor.portstatus`` (transition-state probe), and
``pengyos_apparmor.portdocsync`` (the post-apply prose-docs sync),
and ``pengyos_apparmor.layoutdiscovery`` (the upstream re-check for the
agent's working layout, for the still-blocked workspace file-write
surface item).

The runtime is stdlib-only; validation never loads AppArmor or needs root.
Real kernel integration is opt-in via ``PENGYOS_IT=1``.
"""

__version__ = "0.1.0"
