"""USER_GROUPS and the image's owner-groups list must name the same groups.

The installer puts the user it creates in USER_GROUPS; merge-accounts (run by
preinit on every boot) adds existing owners, once, to any group in
/usr/lib/pengyos/owner-groups they are missing. A group added to only one of the
two either never reaches new installs or never reaches existing ones.
"""

from pathlib import Path

from pengyos_install.image import USER_GROUPS

OWNER_GROUPS = (
    Path(__file__).resolve().parents[3]
    / "buildroot-external/board/server/rootfs-overlay/usr/lib/pengyos/owner-groups"
)


def test_owner_groups_match_user_groups():
    listed = [
        ln.strip()
        for ln in OWNER_GROUPS.read_text().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert listed == list(USER_GROUPS)
