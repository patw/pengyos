"""pengyos-qemu-verify — headless QEMU/KVM boot-test harness for PengyOS.

Boots a PengyOS disk image under QEMU (q35), waits for readiness markers on the
serial console, runs a set of assertions, and emits JUnit XML + a JSON result so
that "it boots" becomes an automated, machine-checkable fact.

See ``spec.md`` and ``README.md`` for the full interface and behaviour contract.
"""

__version__ = "0.5.0"

__all__ = ["__version__"]
