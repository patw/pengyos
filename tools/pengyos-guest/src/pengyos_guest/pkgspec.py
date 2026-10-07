"""Package-manager resolution and provisioning command construction.

``apply`` provisions the ``packages`` declared for each guest *inside* the guest
by running a package-manager install command through
``distrobox enter <name> -- <install>``. Different distros ship different
package managers with different flag syntax, so this module resolves the
package manager for a guest's image and builds the exact install argv.

The package manager is derived from the image name (a distrobox guest is
identified by its image). This keeps the ``guests.toml`` schema at the five
documented fields; an explicit override is a documented follow-up, not part of
this iteration.
"""

from __future__ import annotations

import shlex

import enum
from typing import Sequence


class PackageManager(enum.Enum):
    """The package managers this tool knows how to provision."""

    APK = "apk"          # Alpine / wolfi
    DNF = "dnf"          # Fedora / RHEL-family
    APT = "apt"          # Debian / Ubuntu
    PACMAN = "pacman"    # Arch
    NIX_ENV = "nix-env"  # NixOS
    UNKNOWN = "unknown"


# Substrings (matched case-insensitively, anywhere in the image name) that map
# to a package manager. Order matters: the first match wins.
_IMAGE_HINTS: list[tuple[str, PackageManager]] = [
    ("nixos", PackageManager.NIX_ENV),
    ("wolfi", PackageManager.APK),
    ("alpine", PackageManager.APK),
    ("fedora", PackageManager.DNF),
    ("centos", PackageManager.DNF),
    ("rhel", PackageManager.DNF),
    ("rocky", PackageManager.DNF),
    ("almalinux", PackageManager.DNF),
    ("ubuntu", PackageManager.APT),
    ("debian", PackageManager.APT),
    ("archlinux", PackageManager.PACMAN),
    ("arch", PackageManager.PACMAN),
]

# Default when the image name does not match any known hint. Alpine/wolfi's
# ``apk`` is the most common distrobox base image, so it is the safe default.
_DEFAULT = PackageManager.APK


def resolve(image: str) -> PackageManager:
    """Resolve the :class:`PackageManager` for an image name.

    The image name is matched case-insensitively against a list of distro
    hints; the first match wins. Falls back to :data:`_DEFAULT` when nothing
    matches.
    """
    needle = image.lower()
    for hint, manager in _IMAGE_HINTS:
        if hint in needle:
            return manager
    return _DEFAULT


def install_argv(image: str, packages: Sequence[str]) -> list[str]:
    """Build the package-manager install argv for *packages*.

    The argv is the fragment that runs *inside* the guest (after
    ``distrobox enter <name> --``); it does not include the ``distrobox`` prefix
    or the ``<name>``.
    """
    pkgs = list(packages)
    manager = resolve(image)
    if manager is PackageManager.APK:
        # distrobox enters as the user; guests get passwordless sudo.
        return ["sudo", "apk", "add", "--no-cache", *pkgs]
    if manager is PackageManager.DNF:
        return ["sudo", "dnf", "install", "-y", *pkgs]
    if manager is PackageManager.APT:
        # A fresh image can ship without package lists: update first.
        return ["sh", "-c",
                "sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive "
                "apt-get install -y " + shlex.join(pkgs)]
    if manager is PackageManager.PACMAN:
        return ["sudo", "pacman", "-Syu", "--noconfirm", "--needed", *pkgs]
    if manager is PackageManager.NIX_ENV:
        # nix-env -iA takes Nixpkgs attribute paths (e.g. nixpkgs.ripgrep).
        return ["nix-env", "-iA", *[f"nixpkgs.{p}" for p in pkgs]]
    # Unknown: best-effort guess of a Debian-style front-end.
    return ["sudo", "apt-get", "install", "-y", *pkgs]


def provision_argv(
    name: str,
    packages: Sequence[str],
    image: str,
) -> list[str]:
    """Build the full ``distrobox enter <name> -- <install>`` argv.

    This is exactly what :class:`~pengyos_guest.distrobox.Distrobox.enter` is
    handed (the ``command`` argument), so tests can assert on the precise argv
    that would be executed. When *packages* is empty the install fragment is
    omitted and the command is a bare ``distrobox enter <name>`` (an
    interactive shell), which is the no-op provisioning case.
    """
    argv: list[str] = ["enter", name]
    if not packages:
        return argv

    argv += ["--", *install_argv(image, packages)]
    return argv


__all__ = ["PackageManager", "resolve", "install_argv", "provision_argv"]
