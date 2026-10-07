"""The wizard state machine — pure, offline-testable, no I/O.

This module is the *only* place the installer's decisions live. It models the
guided Core/Server installer (spec.md) as a small, ordered state machine with
six steps:

    target -> edition -> layout -> user -> network -> confirm

Every step validates its input (an invalid answer is rejected with a message,
never a crash), the wizard supports **back-navigation** (return to the prior
step), and later steps **derive** their values from earlier answers (the plan).

The whole module is pure: it opens no files, touches no device, reads no
environment, and prints nothing. All external state (block-device facts, the
set of disks to offer) is *injected* through optional constructor arguments, so
the entire wizard is golden-testable without a terminal, a disk, or root.

Public surface
--------------
* :class:`StepKey` — the six canonical step identifiers.
* :data:`WIZARD_ORDER` — the ordered tuple of every step.
* :class:`Edition`, :class:`Layout`, :class:`NetworkMode` — the closed value
  sets each step accepts.
* :func:`validate_*` — the per-step pure validators (answer -> ``(value, None)``
  or ``(None, message)``).
* :class:`ValidationError` — raised when a raw answer fails validation.
* :class:`Wizard` — the state machine (current step, answers, back-nav, plan).
* :class:`Plan` — the derived answer set the interactive path produces (and
  :mod:`pengyos_install_tui.auto` must reproduce).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple

__all__ = [
    "StepKey",
    "WIZARD_ORDER",
    "Edition",
    "Layout",
    "NetworkMode",
    "ValidationError",
    "Plan",
    "Wizard",
    "validate_target",
    "validate_edition",
    "validate_layout",
    "validate_user",
    "validate_network",
]

# ---------------------------------------------------------------------------
# Canonical step identifiers
# ---------------------------------------------------------------------------


class StepKey:
    """The six canonical wizard steps, in order.

    These strings are the stable key both the interactive path and ``--auto``
    use for an answer, so the two front-ends (this Python wizard and the Rust
    GUI, per the architecture PIN) must agree on them exactly.
    """

    TARGET = "target"
    EDITION = "edition"
    LAYOUT = "layout"
    USER = "user"
    NETWORK = "network"
    CONFIRM = "confirm"


#: The ordered tuple of every step. The wizard walks this list front to back;
#: :attr:`StepKey.CONFIRM` is always last. This is the single source of truth
#: for ordering, back-navigation, and "is the wizard complete?".
WIZARD_ORDER: Tuple[str, ...] = (
    StepKey.TARGET,
    StepKey.EDITION,
    StepKey.LAYOUT,
    StepKey.USER,
    StepKey.NETWORK,
    StepKey.CONFIRM,
)

# ---------------------------------------------------------------------------
# Closed value sets
# ---------------------------------------------------------------------------


class Edition:
    """The two installable editions (spec.md: "Core or Server")."""

    CORE = "core"
    SERVER = "server"
    #: All valid edition values, in display order.
    ALL: Tuple[str, ...] = (CORE, SERVER)


class Layout:
    """The disk layouts the wizard offers.

    A layout is a *named, documented* partition scheme. The default is the
    spec's "GPT + ESP + single ext4 root"; the A/B split (btrfs root + data,
    systemd-sysupdate friendly — the operator-pinned architecture) is the other
    documented option. New layouts are added here only; nothing else hard-codes
    the set.
    """

    #: GPT: a small EFI System Partition + a single ext4 root partition.
    SINGLE_ROOT = "single-root"
    #: GPT: ESP + btrfs root-A + btrfs root-B (A/B, sysupdate) + data.
    AB_SPLIT = "ab-split"
    #: All valid layout values, in display order. The first is the default.
    ALL: Tuple[str, ...] = (SINGLE_ROOT, AB_SPLIT)

    #: The layout chosen when none is given (spec.md: "the default").
    DEFAULT: str = SINGLE_ROOT


class NetworkMode:
    """The network configuration modes the wizard accepts."""

    DHCP = "dhcp"
    STATIC = "static"
    #: All valid network-mode values.
    ALL: Tuple[str, ...] = (DHCP, STATIC)


# ---------------------------------------------------------------------------
# Documented constants
# ---------------------------------------------------------------------------

#: Minimum acceptable block-device path length, documented so the validator's
#: threshold is a named constant rather than a magic number.
MIN_PATH_LEN = 5

#: The UID the initial admin user is assigned (spec.md: "the initial admin
#: user"; 1000 is the conventional first non-root UID).
DEFAULT_UID = 1000


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class ValidationError(Exception):
    """Raised when a raw answer fails a step's validator.

    Carries the offending step, the human-readable message, and the raw value
    that was rejected, so a caller can render a clean error rather than a
    traceback (spec.md: "an invalid answer is rejected with a message, not a
    crash").
    """

    def __init__(self, step: str, message: str, raw: object) -> None:
        super().__init__(message)
        self.step = step
        self.message = message
        self.raw = raw


# ---------------------------------------------------------------------------
# Per-step validators
# ---------------------------------------------------------------------------

#: A validator takes a raw answer and returns ``(value, None)`` on success or
#: ``(None, message)`` on failure. ``value`` is the *normalised* answer the
#: wizard stores; it may differ from the raw input (e.g. trimmed/upper-cased).
Validator = Callable[[object], Tuple[object, Optional[str]]]

# A block device path: ``/dev/`` + a node name. Node names are the common
#: block-device spellings (sd*, vd*, nvme*n*, md*, dm-*). This is a structural
#: check only — the model is pure and never stats the device. The block-device
#: *existence* and *size* checks (spec.md: "validated as a block device at a
#: documented minimum size") are injected via :class:`Wizard`'s ``probe``.
_BLOCK_DEVICE_RE = re.compile(r"^/dev/(sd[a-z]+|vd[a-z]+|nvme\d+n\d+|md\d+|dm-\d+)$")


def validate_target(raw: object) -> Tuple[object, Optional[str]]:
    """Validate a target-device answer.

    The raw answer must be a non-empty string that looks like a block-device
    path (``/dev/sdb``, ``/dev/nvme0n1``, ...). On success the stored value is
    the trimmed string; on failure the message explains why.
    """
    if not isinstance(raw, str):
        return None, "target must be a string device path, e.g. /dev/sdb"
    path = raw.strip()
    if not path:
        return None, "target device path must not be empty"
    if not _BLOCK_DEVICE_RE.match(path):
        return None, (
            f"target '{raw}' is not a block-device path (expected /dev/sdX, "
            "/dev/nvmeNnM, /dev/vdX, ...)"
        )
    if len(path) < MIN_PATH_LEN:
        return None, f"target '{raw}' is too short to be a device path"
    return path, None


def validate_edition(raw: object) -> Tuple[object, Optional[str]]:
    """Validate an edition answer (``core`` or ``server``, case-insensitive)."""
    if not isinstance(raw, str):
        return None, "edition must be 'core' or 'server'"
    value = raw.strip().lower()
    if value not in Edition.ALL:
        return None, (
            f"edition '{raw}' is invalid; choose one of: {', '.join(Edition.ALL)}"
        )
    return value, None


def validate_layout(raw: object) -> Tuple[object, Optional[str]]:
    """Validate a layout answer (a named layout from :class:`Layout`)."""
    if not isinstance(raw, str):
        return None, "layout must be one of: " + ", ".join(Layout.ALL)
    value = raw.strip().lower()
    if value not in Layout.ALL:
        return None, (
            f"layout '{raw}' is invalid; choose one of: {', '.join(Layout.ALL)}"
        )
    return value, None


# A username: letters, digits, '_' or '-'. Documented so the validator's
# threshold is a named constant rather than a magic number.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_USERNAME_LEN = 32


def validate_user(raw: object) -> Tuple[object, Optional[str]]:
    """Validate an initial-admin-user answer.

    Accepts either a bare username string or a mapping with a ``username``
    key. The stored value is a :class:`UserAnswer` with a normalised username
    and the default UID (a real UID may be injected later; the model assigns
    the conventional first non-root UID).
    """
    username: Optional[str] = None
    if isinstance(raw, str):
        username = raw.strip()
    elif isinstance(raw, dict):
        candidate = raw.get("username")
        if isinstance(candidate, str):
            username = candidate.strip()
    if not username:
        return None, "user requires a non-empty username"
    if not _USERNAME_RE.match(username):
        return None, (
            f"username '{username}' is invalid; use letters, digits, '_' or '-'"
        )
    if len(username) > MAX_USERNAME_LEN:
        return None, f"username '{username}' is too long (max {MAX_USERNAME_LEN})"
    return UserAnswer(username=username), None


def validate_network(raw: object) -> Tuple[object, Optional[str]]:
    """Validate a network-config answer.

    Accepts either a bare network-mode string (``dhcp``/``static``) or a
    mapping. With ``static`` the mapping must carry an ``address`` (and, when
    present, a ``gateway`` and ``nameservers``). The stored value is a
    :class:`NetworkAnswer`.
    """
    mode: Optional[str] = None
    extra: dict = {}
    if isinstance(raw, str):
        mode = raw.strip().lower()
    elif isinstance(raw, dict):
        candidate = raw.get("mode")
        if isinstance(candidate, str):
            mode = candidate.strip().lower()
        extra = {
            k: v
            for k, v in raw.items()
            if k not in ("mode",) and v is not None
        }
    if mode is None:
        return None, "network requires a mode: 'dhcp' or 'static'"
    if mode not in NetworkMode.ALL:
        return None, (
            f"network mode '{raw}' is invalid; choose one of: "
            f"{', '.join(NetworkMode.ALL)}"
        )
    if mode == NetworkMode.STATIC:
        address = extra.get("address")
        if not isinstance(address, str) or not address.strip():
            return None, "static network requires a non-empty 'address'"
    # Normalise nameservers to a tuple of strings.
    nameservers = extra.get("nameservers", [])
    if isinstance(nameservers, str):
        nameservers = (nameservers,)
    elif isinstance(nameservers, (list, tuple)):
        nameservers = tuple(str(n) for n in nameservers)
    else:
        return None, "nameservers must be a string or a list of strings"
    return (
        NetworkAnswer(
            mode=mode,
            address=extra.get("address") or None,
            gateway=extra.get("gateway") or None,
            nameservers=nameservers,
        ),
        None,
    )


# ---------------------------------------------------------------------------
# Normalised answer values
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserAnswer:
    """The normalised initial-admin-user answer."""

    username: str
    uid: int = DEFAULT_UID

    def to_dict(self) -> dict:
        return {"username": self.username, "uid": self.uid}


@dataclass(frozen=True)
class NetworkAnswer:
    """The normalised network-config answer."""

    mode: str
    address: Optional[str] = None
    gateway: Optional[str] = None
    nameservers: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        data: dict = {"mode": self.mode}
        if self.address:
            data["address"] = self.address
        if self.gateway:
            data["gateway"] = self.gateway
        if self.nameservers:
            data["nameservers"] = list(self.nameservers)
        return data


# ---------------------------------------------------------------------------
# The derived plan (the answer set the interactive path produces)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """The derived answer set: everything the wizard decided, in order.

    This is the canonical output of the interactive path and the exact shape
    :mod:`pengyos_install_tui.auto` must reproduce from ``install.toml`` (spec.md:
    "auto(config) equals the interactive answer set for the same inputs").
    """

    target: str
    edition: str
    layout: str
    user: UserAnswer
    network: NetworkAnswer

    def to_dict(self) -> dict:
        """The plain-dict form (what ``run --json`` / ``auto --json`` emit)."""
        return {
            "target": {"path": self.target},
            "edition": self.edition,
            "layout": self.layout,
            "user": self.user.to_dict(),
            "network": self.network.to_dict(),
        }

    def as_lines(self) -> Sequence[str]:
        """A stable, human-readable rendering of the plan (for ``run``)."""
        return (
            f"target:   {self.target}",
            f"edition:  {self.edition}",
            f"layout:   {self.layout}",
            f"user:     {self.user.username} (uid {self.user.uid})",
            f"network:  {self.network.mode}"
            + (f" ({self.network.address})" if self.network.address else ""),
        )


# ---------------------------------------------------------------------------
# The wizard state machine
# ---------------------------------------------------------------------------


# A probe answers "is this target device acceptable?" for the block-device and
# size checks the pure validator cannot do. ``None`` means "no extra check".
Probe = Callable[[str], Optional[str]]


class Wizard:
    """The guided Core/Server installer as a pure state machine.

    Construct with no arguments for the default wizard, or inject a ``probe``
    to supply block-device/size facts (tests pass a fixture; production could
    pass a real collector). The wizard never performs I/O itself.
    """

    def __init__(self, probe: Optional[Probe] = None) -> None:
        self._probe = probe
        self._answers: dict[str, object] = {}
        self._current_index: int = 0

    # -- position ----------------------------------------------------------

    @property
    def current_step(self) -> str:
        """The step the wizard is currently prompting for."""
        return WIZARD_ORDER[self._current_index]

    @property
    def step_index(self) -> int:
        """Zero-based index of the current step (0 = target, 5 = confirm)."""
        return self._current_index

    @property
    def steps(self) -> Tuple[str, ...]:
        """All steps, in order (a convenience for rendering the progress)."""
        return WIZARD_ORDER

    @property
    def current(self) -> "StepState":
        """A :class:`StepState` snapshot of the current step (for rendering)."""
        return StepState(
            key=self.current_step,
            index=self._current_index,
            total=len(WIZARD_ORDER),
            answer=self._answers.get(self.current_step),
            done=self.current_step in self._answers,
        )

    @property
    def done(self) -> bool:
        """True once the wizard has emitted a plan (confirm succeeded)."""
        return hasattr(self, "_plan")

    # -- advancing ---------------------------------------------------------

    def answer(self, step: str, raw: object) -> None:
        """Validate and store an answer for ``step``, then advance.

        * ``step`` must be a real step (never :attr:`StepKey.CONFIRM` — use
          :meth:`confirm`).
        * The raw answer is validated by the step's validator; a failure raises
          :class:`ValidationError` (a message, never a crash).
        * On success the wizard moves one step forward (or stays on ``confirm``).

        Re-answering a step replaces its stored value. Back-navigation is
        :meth:`back`.
        """
        if step == StepKey.CONFIRM:
            raise ValidationError(
                StepKey.CONFIRM, "confirm is not an answerable step; call confirm()", raw
            )
        if step not in WIZARD_ORDER:
            raise ValidationError(
                step, f"unknown step '{step}'", raw
            )
        validator = _VALIDATORS[step]
        value, message = validator(raw)
        if message is not None:
            raise ValidationError(step, message, raw)

        # The block-device / size check (spec.md) is injected: a probe that
        # returns a message fails the answer; a probe that returns None passes.
        if self._probe is not None and step == StepKey.TARGET:
            probe_msg = self._probe(value)
            if probe_msg is not None:
                raise ValidationError(
                    StepKey.TARGET, f"target '{value}': {probe_msg}", raw
                )

        self._answers[step] = value
        ahead = WIZARD_ORDER.index(step) > self._current_index
        if ahead:
            # Answering a step ahead of the cursor (e.g. an auto-seek) lands on
            # it; otherwise answering the current step advances one forward.
            self._current_index = WIZARD_ORDER.index(step)
        elif self._current_index < len(WIZARD_ORDER) - 1:
            self._current_index += 1

    def back(self) -> str:
        """Return to the prior step.

        Returns the key of the step the wizard is now on. No-ops at the very
        first step. Prior answers are retained (back-navigation is for review
        and re-editing, not for discarding decisions).
        """
        if self._current_index > 0:
            self._current_index -= 1
        return self.current_step

    def confirm(self) -> Plan:
        """Confirm the derived plan and return it (the terminal action).

        Requires every step before :attr:`StepKey.CONFIRM` to have an answer;
        otherwise raises :class:`ValidationError` naming the first missing step.
        The cursor position is irrelevant — the run layer navigates to the
        confirm step before calling this, but ``--answers`` replay (which applies
        answers in order) can confirm directly. On success the wizard is marked
        done and the :class:`Plan` is returned.
        """
        missing = [s for s in WIZARD_ORDER[:-1] if s not in self._answers]
        if missing:
            raise ValidationError(
                StepKey.CONFIRM, f"incomplete: '{missing[0]}' not answered yet", None
            )
        plan = Plan(
            target=str(self._answers[StepKey.TARGET]),
            edition=str(self._answers[StepKey.EDITION]),
            layout=str(self._answers[StepKey.LAYOUT]),
            user=self._answers[StepKey.USER],
            network=self._answers[StepKey.NETWORK],
        )
        self._plan = plan  # type: ignore[attr-defined]
        return plan

    # -- inspection --------------------------------------------------------

    def is_answered(self, step: str) -> bool:
        """True if ``step`` has a stored, validated answer."""
        return step in self._answers

    def get_answer(self, step: str) -> object:
        """Return the stored answer for ``step`` (raises if none)."""
        if step not in self._answers:
            raise KeyError(step)
        return self._answers[step]

    @property
    def plan(self) -> Plan:
        """The confirmed plan (raises :class:`AttributeError` if not confirmed)."""
        return getattr(self, "_plan")  # type: ignore[attr-defined]

    def progress(self) -> int:
        """How many of the six steps have been answered (0-5)."""
        return sum(1 for s in WIZARD_ORDER[:-1] if s in self._answers)


@dataclass(frozen=True)
class StepState:
    """A render-ready snapshot of one step (used by :mod:`pengyos_install_tui.run`)."""

    key: str
    index: int
    total: int
    answer: object
    done: bool


# The per-step validators, keyed by step. Kept as a module constant so the
# wizard and tests share one mapping.
_VALIDATORS: dict[str, Validator] = {
    StepKey.TARGET: validate_target,
    StepKey.EDITION: validate_edition,
    StepKey.LAYOUT: validate_layout,
    StepKey.USER: validate_user,
    StepKey.NETWORK: validate_network,
}
