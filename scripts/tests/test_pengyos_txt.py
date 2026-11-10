"""Tests for board/common/pengyos-txt.py: /usr/share/pengyos.txt and the motd nudge.

Why these checks exist. pengyos.txt is what a person or an agent reads first on a
PengyOS machine (Pengy's 2026-11-03 Server review: an agent concluded the box
could not build anything before it found the dev guest). Its value is that it is
true for the edition it is on, so the page is assembled from what the built tree
holds. These tests build small fake trees for Core-, Server- and Desktop-shaped
images and check that each claim follows the tree, not the edition name:

1. no claim about a tool the tree lacks (dev guest, dx, podman, flatpak, updates);
2. a stub counts as absent, a real file as present, in the absent list;
3. every pengy*/pengyos-* command the repository installs has a TOOLS line;
4. the motd gets exactly one nudge line, with edition and version, on every pass;
5. the package list takes each package's own version (util-linux, not
   util-linux-libs) and leaves out Buildroot's skeleton pseudo-packages;
6. the pointer reaches `ssh host cmd` users: every stub message and os-release.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "buildroot-external"
SCRIPT = EXT / "board/common/pengyos-txt.py"
STUBS = EXT / "board/core/tool-stubs"

spec = importlib.util.spec_from_file_location("pengyos_txt", SCRIPT)
ptxt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ptxt)


def tree(tmp: Path, variant: str, vid: str, tools: list[str], stubs=(), extra=()) -> Path:
    t = tmp / vid
    (t / "usr/lib").mkdir(parents=True)
    (t / "usr/bin").mkdir(parents=True)
    (t / "etc").mkdir()
    (t / "usr/lib/os-release").write_text(
        f'NAME="PengyOS"\nVARIANT="{variant}"\nVARIANT_ID={vid}\nIMAGE_ID=pengyos-{vid}\n'
        f'IMAGE_VERSION="26.11.0"\nBUILD_ID="abc1234567"\n')
    for n in tools:
        (t / "usr/bin" / n).write_text("#!/bin/sh\n")
    for n in stubs:
        (t / "usr/bin" / n).write_text("#!/bin/sh\n# PengyOS stub: test\n")
    for rel in extra:
        p = t / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n")
    (t / "etc/motd").write_text(f"\nPengyOS {variant} -- https://pengyos.catbee.ca\n  pengy-cli  ask Pengy\n")
    return t


def run(t: Path, build: Path | None = None) -> str:
    args = [sys.executable, str(SCRIPT), str(t)] + ([str(build)] if build else [])
    subprocess.run(args, check=True, capture_output=True)
    return (t / "usr/share/pengyos.txt").read_text()


CORE_TOOLS = ["pengy-cli", "pengyos-firewall", "pengyos-services"]
SERVER_TOOLS = CORE_TOOLS + ["sudo", "pengyos-update", "pengyos-guest", "dx", "podman",
                             "pengyos-vm", "pengyos-shares", "wpa_supplicant"]
DESKTOP_TOOLS = [t for t in SERVER_TOOLS if t != "wpa_supplicant"] + [
    "flatpak", "NetworkManager", "pengyos-appimage", "pengyos-autologin"]


def test_core_claims_only_what_core_has(tmp_path):
    t = tree(tmp_path, "Core", "core", CORE_TOOLS, stubs=["cc", "crontab"])
    txt = run(t)
    assert "PengyOS Core 26.11.0 -- how this system works" in txt
    for absent in ("pengyos-guest", "dx ", "flatpak", "podman", "sudo pengyos", "pengyos-update apply",
                   "CONTAINERS AND SERVICES"):
        assert absent not in txt, absent
    assert "does not update in place" in txt
    assert "no compilers: build on another machine" in txt
    assert "no dev guest" in txt


def test_server_shape(tmp_path):
    t = tree(tmp_path, "Server", "server", SERVER_TOOLS, stubs=["cc", "make"],
             extra=["etc/containers/systemd/README"])
    txt = run(t)
    assert "sudo pengyos-update apply" in txt
    assert "pengyos-guest apply dev" in txt and "dx make" in txt
    assert "/etc/containers/systemd/README has the details" in txt
    assert "no nmcli" in txt and "NetworkManager's" not in txt
    assert "every port above 1024" not in txt
    assert "Flatpak" not in txt
    assert "in the dev guest or a container" in txt


def test_desktop_shape(tmp_path):
    t = tree(tmp_path, "Desktop NVIDIA", "desktop-nvidia", DESKTOP_TOOLS,
             extra=["usr/lib/pengyos/nftables.d/input-desktop.nft"])
    txt = run(t)
    assert "PengyOS Desktop NVIDIA 26.11.0" in txt
    assert "every port above 1024" in txt
    assert "Flatpak from Flathub" in txt
    assert "NetworkManager's" in txt and "wpa_cli" not in txt


def test_stub_is_absent_real_is_present(tmp_path):
    t = tree(tmp_path, "Server", "server", SERVER_TOOLS + ["make"], stubs=["cc", "gcc"])
    txt = run(t)
    absent = txt.split("DELIBERATELY ABSENT", 1)[1].split("WHAT IS INSTALLED", 1)[0]
    line = next(l for l in absent.splitlines() if l.strip().startswith("cc"))
    assert line.split(", ")[:2] == ["  cc", "gcc"] and "make" not in line.replace(",", " ").split()
    assert re.search(r"^\s+cc gcc\s*$", absent, re.M)  # the stub list
    cmds = txt.split("COMMANDS ON THIS MACHINE", 1)[1].split("DELIBERATELY ABSENT", 1)[0]
    assert "pengyos-guest" in cmds and "cc" not in cmds.split()


def test_lines_fit_a_console(tmp_path):
    t = tree(tmp_path, "Desktop NVIDIA", "desktop-nvidia", DESKTOP_TOOLS + list(ptxt.TOOLS),
             stubs=[p.name for p in STUBS.iterdir() if not p.name.startswith("pengyos-no-")],
             extra=["usr/lib/pengyos/nftables.d/input-desktop.nft"])
    for line in run(t).splitlines():
        assert len(line) <= 80, line


def test_motd_nudge_once(tmp_path):
    t = tree(tmp_path, "Server", "server", SERVER_TOOLS)
    run(t)
    run(t)  # release.sh's SBOM pass runs post-build again
    lines = (t / "etc/motd").read_text().splitlines()
    nudges = [l for l in lines if "how this system works" in l]
    assert nudges == ["PengyOS Server 26.11.0 -- how this system works: /usr/share/pengyos.txt"]
    assert not any("https://pengyos.catbee.ca" in l for l in lines)
    assert lines[0] == "" and "pengy-cli" in lines[2]


def test_writes_by_rename(tmp_path):
    t = tree(tmp_path, "Server", "server", SERVER_TOOLS)
    other = tmp_path / "linked-motd"
    other.hardlink_to(t / "etc/motd")
    before = other.read_text()
    run(t)
    assert other.read_text() == before  # a hard-linked copy of the tree is untouched


def test_packages_versions(tmp_path):
    t = tree(tmp_path, "Core", "core", CORE_TOOLS)
    b = tmp_path / "build"
    b.mkdir()
    for d in ("util-linux-2.41.5", "util-linux-libs-2.41.5", "bash-5.2.37", "skeleton-init-systemd",
              "ca-certificates-20260816+nmu1", "host-python3-3.14.8", "linux-firmware-20260810"):
        (b / d).mkdir()
    (b / "packages-file-list.txt").write_text(
        "util-linux,./usr/bin/lsblk\nutil-linux-libs,./usr/lib/libblkid.so.1\nbash,./usr/bin/bash\n"
        "skeleton-init-systemd,./etc/fstab\nca-certificates,./etc/ssl/x\nlinux-firmware,./lib/firmware/a\n")
    txt = run(t, b)
    pk = dict(l.split() for l in (t / "usr/share/pengyos/packages.txt").read_text().splitlines())
    assert pk == {"bash": "5.2.37", "ca-certificates": "20260816+nmu1", "linux-firmware": "20260810",
                  "util-linux": "2.41.5", "util-linux-libs": "2.41.5"}
    assert "5 packages, one per line" in txt


def _installed_names() -> set[str]:
    names = set()
    for p in EXT.glob("board/*/rootfs-overlay/usr/bin/*"):
        names.add(p.name)
    for f in list(EXT.glob("board/*/post-build.sh")) + list(EXT.glob("package/*/*.mk")):
        names |= set(re.findall(r"usr/s?bin/((?:pengy|pengyos)-[a-z0-9-]+|pengy|dx)\b", f.read_text()))
    return {n for n in names if n == "pengy" or n == "dx" or n.startswith(("pengy-", "pengyos-"))}


def test_descriptions_fit_one_column():
    w = max(len(n) for n in ptxt.TOOLS) + 2
    for n, d in ptxt.TOOLS.items():
        assert 2 + w + len(d) <= 80, n


def test_every_shipped_command_has_a_description():
    missing = sorted(_installed_names() - set(ptxt.TOOLS))
    assert not missing, f"add these to TOOLS in board/common/pengyos-txt.py: {missing}"


def test_every_post_build_runs_it_before_the_lib_check():
    for board in ("core", "server", "desktop"):
        s = (EXT / f"board/{board}/post-build.sh").read_text()
        assert "common/pengyos-txt.py" in s, board
        assert s.index("common/pengyos-txt.py") < s.rindex("check-needed-libs.py"), board
        assert s.index("common/pengyos-txt.py") > s.index("os-release.sh"), board


def test_ssh_cmd_users_get_the_pointer():
    for stub in STUBS.iterdir():
        if stub.is_symlink():
            continue
        body = stub.read_text()
        # Every way out of a stub prints the pointer: one per `exit 127`, in a
        # heredoc or (no-toolchain, after its optional Python hint) an echo.
        exits = body.count("exit 127")
        assert body.count("/usr/share/pengyos.txt") >= exits > 0, stub.name
    osr = (EXT / "board/common/os-release.sh").read_text()
    assert 'PENGYOS_README="/usr/share/pengyos.txt"' in osr
    for skill in ("skill_index.md", "pengyos/pengyos_skill.md"):
        assert "/usr/share/pengyos.txt" in (EXT / "board/core/rootfs-overlay/etc/skel/skills" / skill).read_text()



CUDA_TOML = '[guests.cuda]\nimage = "nvidia/cuda"\ndescription = "CUDA box"\nnvidia = true\n'
DEV_TOML = '[guests.dev]\nimage = "ubuntu"\ndescription = "build box"\n'


def guest_tree(tmp_path, variant, vid, profiles):
    t = tree(tmp_path, variant, vid, DESKTOP_TOOLS + ["nvidia-ctk"])
    d = t / "usr/share/pengyos/guests.d"
    d.mkdir(parents=True)
    for name, body in profiles.items():
        (d / f"{name}.toml").write_text(body)
    return t


def test_nvidia_names_the_gpu_guest(tmp_path):
    # The dev guest has no GPU; an NVIDIA image must route GPU work to cuda.
    txt = run(guest_tree(tmp_path, "Desktop NVIDIA", "desktop-nvidia",
                         {"dev": DEV_TOML, "cuda": CUDA_TOML}))
    assert "    dev      build box\n    cuda     CUDA box [GPU]" in txt
    assert "dx -g NAME CMD" in txt and "goes in a [GPU] guest" in txt
    assert "/usr/lib (libcuda.so.1)" in txt


def test_a_new_guest_profile_is_listed_without_code(tmp_path):
    txt = run(guest_tree(tmp_path, "Desktop", "desktop",
                         {"dev": DEV_TOML, "rocm": '[guests.rocm]\nimage = "r"\ndescription = "ROCm box"\n'}))
    assert "    rocm     ROCm box" in txt
    assert "[GPU]" not in txt


def test_dev_only_lists_nothing_extra(tmp_path):
    txt = run(guest_tree(tmp_path, "Desktop", "desktop", {"dev": DEV_TOML}))
    assert "guests this image declares" not in txt and "[GPU]" not in txt
