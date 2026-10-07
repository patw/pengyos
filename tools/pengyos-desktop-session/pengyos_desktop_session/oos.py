"""The explicitly-out-of-scope facts.

The VM-testable smoke matrix (``SESSION_FACTS`` in
:mod:`pengyos_desktop_session.check`) only covers what QEMU+OVMF can actually
observe. Four classes of fact are **explicitly out of scope** — they are
*real-hardware* facts that cannot be exercised or verified in the VM:

======================  ==========================  ==========================
Category                What it covers              Why it is out of scope
======================  ==========================  ==========================
GPU                     GPU acceleration            no real GPU in QEMU+OVMF
audio                   audio devices (playback)    no real audio hardware
Bluetooth               Bluetooth devices/pairing   no Bluetooth radio
suspend/resume          suspend / resume            power-mgmt real-hardware path
======================  ==========================  ==========================

:func:`render_out_of_scope` turns :data:`OUT_OF_SCOPE` into the human-readable
"real-hardware, not verified here" report section that the check engine appends
to every verdict, so these facts are **reported, never silently omitted**.

Nothing here does I/O; it only produces strings and data.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "OutOfScopeFact",
    "OUT_OF_SCOPE",
    "OUT_OF_SCOPE_LABEL",
    "render_out_of_scope",
]

# The label printed for the out-of-scope section. Kept as a module constant so
# tests can assert against it verbatim.
OUT_OF_SCOPE_LABEL = "Out of scope — real-hardware, not verified here:"


@dataclass(frozen=True)
class OutOfScopeFact:
    """A single fact explicitly out of scope for the VM-testable smoke matrix.

    These are *real-hardware* facts that cannot be verified in QEMU+OVMF. They
    are reported (never dropped) so a caller always sees the full surface of
    what a passing check did **not** verify.
    """

    #: The fact's broad category — ``"GPU"``, ``"audio"``, ``"Bluetooth"``,
    #: ``"suspend/resume"``.
    category: str
    #: What the fact would cover on real hardware (e.g. "GPU acceleration").
    what: str
    #: Why it cannot be verified in the VM — a short real-hardware note.
    why: str


# The out-of-scope facts. This is the single documented list the check engine
# reports as "real-hardware, not verified here".
OUT_OF_SCOPE: tuple[OutOfScopeFact, ...] = (
    OutOfScopeFact(
        "GPU",
        "GPU acceleration (3D rendering / compositing performance)",
        "QEMU+OVMF has no real GPU, so 3D acceleration cannot be exercised or verified in the VM.",
    ),
    OutOfScopeFact(
        "audio",
        "audio devices (playback / capture)",
        "There is no real audio hardware in the VM, so audio device presence and I/O cannot be verified.",
    ),
    OutOfScopeFact(
        "Bluetooth",
        "Bluetooth devices and pairing",
        "The VM has no Bluetooth radio, so BT device presence and pairing are real-hardware only.",
    ),
    OutOfScopeFact(
        "suspend/resume",
        "suspend / resume",
        "Suspend/resume is a power-management real-hardware path that cannot be exercised in QEMU+OVMF.",
    ),
)


def render_out_of_scope(
    facts: "tuple[OutOfScopeFact, ...] | None" = None,
) -> str:
    """Render the out-of-scope facts as a human-readable report section.

    Each fact is one bullet of the form::

      • <category>: <what> — <why>

    The section header names the four categories (so a reader sees the full
    surface at a glance) and states that they are real-hardware facts that are
    not verified here. The facts are listed in canonical
    (:data:`OUT_OF_SCOPE`) order.
    """
    # ``is not None`` (not ``or``): an explicit empty tuple is a valid,
    # intentional override — only a missing arg (None) falls back to the
    # canonical default. Matches ``check_boot_log``.
    facts = OUT_OF_SCOPE if facts is None else tuple(facts)
    categories = " / ".join(f.category for f in facts)
    lines = [
        OUT_OF_SCOPE_LABEL,
        f"  ({categories} — real-hardware facts, not verified in QEMU+OVMF)",
    ]
    for fact in facts:
        lines.append(f"  • {fact.category}: {fact.what} — {fact.why}")
    return "\n".join(lines)
