"""Core's kernel fragment keeps the memory-safety hardening on, in every edition.

The nightly reports kernel CVEs but never patches the kernel (pengyos-nightly's
guards.DENIED), so the kernel config is the only lever on how exploitable one is.
The 2026-10-28 audit found every one of these off in all five editions. The core
post-build (check-kernel-fragments.py --built) fails a build where a fragment line
does not survive Kconfig; this test fails the gate when a line is deleted or
flipped, which that check cannot see.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BOARD = REPO / "buildroot-external" / "board"

HARDENING = (
    "CONFIG_SLAB_FREELIST_HARDENED=y",
    "CONFIG_SLAB_FREELIST_RANDOM=y",
    "CONFIG_LIST_HARDENED=y",
    "CONFIG_FORTIFY_SOURCE=y",
    "CONFIG_HARDENED_USERCOPY=y",
    "CONFIG_INIT_ON_ALLOC_DEFAULT_ON=y",
    "CONFIG_ZERO_CALL_USED_REGS=y",
)


def _lines(path: Path) -> list[str]:
    return [l.strip() for l in path.read_text().splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


def test_core_fragment_turns_the_hardening_on():
    core = _lines(BOARD / "core" / "linux.fragment")
    missing = [h for h in HARDENING if h not in core]
    assert not missing, f"board/core/linux.fragment lost {missing}"


def test_no_later_layer_turns_it_back_off():
    """Fragments merge in layer order, so a Server or Desktop line would win."""
    for frag in sorted(BOARD.glob("*/linux.fragment")):
        if frag.parent.name == "core":
            continue
        text = frag.read_text()
        for h in HARDENING:
            sym = h.split("=")[0]
            assert f"# {sym} is not set" not in text and f"{sym}=n" not in text, \
                f"{frag.relative_to(REPO)} turns {sym} off"
