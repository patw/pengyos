"""Tests for package-manager resolution and provisioning argv (``pkgspec``).

Exercised entirely in-process — no real distrobox, network, root, or display.
"""

import pytest

from pengyos_guest.pkgspec import (
    PackageManager,
    install_argv,
    provision_argv,
    resolve,
)


@pytest.mark.parametrize(
    "image,expected",
    [
        ("quay.io/aliyunos/wolfi:latest", PackageManager.APK),
        ("alpine:3.19", PackageManager.APK),
        ("ghcr.io/pengyos/nixos:latest", PackageManager.NIX_ENV),
        ("fedora:39", PackageManager.DNF),
        ("centos:9", PackageManager.DNF),
        ("rockylinux:9", PackageManager.DNF),
        ("ubuntu:22.04", PackageManager.APT),
        ("debian:bookworm", PackageManager.APT),
        ("archlinux:latest", PackageManager.PACMAN),
        ("some/random:img", PackageManager.APK),  # unknown -> default (apk)
    ],
)
def test_resolve_from_image(image, expected):
    assert resolve(image) is expected


def test_resolve_is_case_insensitive():
    assert resolve("WOLFI:LATEST") is PackageManager.APK
    assert resolve("GHCR.IO/PENGYOS/NixOS:Latest") is PackageManager.NIX_ENV


def test_install_argv_apk():
    assert install_argv("wolfi:latest", ["git", "curl"]) == [
        "sudo", "apk", "add", "--no-cache", "git", "curl",
    ]


def test_install_argv_dnf():
    assert install_argv("fedora:39", ["git"]) == [
        "sudo", "dnf", "install", "-y", "git",
    ]


def test_install_argv_apt():
    # Package lists are refreshed first: fresh images ship without them.
    assert install_argv("ubuntu:22.04", ["git", "g++"]) == [
        "sh", "-c",
        "sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git g++",
    ]


def test_install_argv_pacman():
    assert install_argv("docker.io/library/archlinux:latest", ["base-devel"]) == [
        "sudo", "pacman", "-Syu", "--noconfirm", "--needed", "base-devel",
    ]


def test_install_argv_nix_env_uses_attribute_paths():
    assert install_argv("nixos:latest", ["ripgrep", "fd"]) == [
        "nix-env", "-iA", "nixpkgs.ripgrep", "nixpkgs.fd",
    ]


def test_install_argv_unknown_defaults_to_apk():
    # An image name with no known distro hint falls back to the default
    # package manager (apk), so install uses apk syntax.
    assert install_argv("random:img", ["git"]) == [
        "sudo", "apk", "add", "--no-cache", "git",
    ]


def test_provision_argv_includes_install_fragment():
    argv = provision_argv("wolfi", ["git", "curl"], "quay.io/aliyunos/wolfi:latest")
    assert argv == [
        "enter", "wolfi", "--", "sudo", "apk", "add", "--no-cache", "git", "curl",
    ]


def test_provision_argv_nixos():
    argv = provision_argv("nixos", ["ripgrep"], "ghcr.io/pengyos/nixos:latest")
    assert argv == [
        "enter", "nixos", "--", "nix-env", "-iA", "nixpkgs.ripgrep",
    ]


def test_provision_argv_without_packages_is_bare_enter():
    # No packages -> provisioning is a no-op shell (no install fragment).
    assert provision_argv("wolfi", [], "wolfi:latest") == ["enter", "wolfi"]
