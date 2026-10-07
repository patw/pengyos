"""Tests for the key-set definition and file-permission policy.

These pin down the contract ``spec.md`` "Security posture" promises: the finite
PK/KEK/db key set and the ``0600`` private-key / ``0644`` certificate policy.
"""

import stat

from pengyos_sbkeys import keys
from pengyos_sbkeys.keys import (
    KEY_FILES,
    KEY_ROLES,
    KeyFile,
    KeyKind,
    KeyPerms,
    is_private,
    perms_for,
    role_of,
)

# The exact set of files ``init`` must materialize.
EXPECTED_NAMES = {"PK.key", "PK.cert", "KEK.key", "KEK.cert", "DB.key", "DB.cert"}


def test_key_set_contains_pk_kek_db():
    names = {key.name for key in KEY_FILES}
    assert names == EXPECTED_NAMES


def test_each_role_has_private_and_public():
    for role in ("PK", "KEK", "DB"):
        priv = [k for k in KEY_FILES if k.role == role and is_private(k)]
        pub = [k for k in KEY_FILES if k.role == role and not is_private(k)]
        assert len(priv) == 1, f"{role} must have exactly one private key"
        assert len(pub) == 1, f"{role} must have exactly one certificate"


def test_private_keys_are_0600():
    for key_file in KEY_FILES:
        if is_private(key_file):
            assert perms_for(key_file) == KeyPerms.PRIVATE == 0o600
            assert key_file.kind is KeyKind.PRIVATE


def test_certificates_are_0644():
    for key_file in KEY_FILES:
        if not is_private(key_file):
            assert perms_for(key_file) == KeyPerms.PUBLIC == 0o644
            assert key_file.kind is KeyKind.PUBLIC


def test_private_perms_have_no_group_or_world_bits():
    # 0600 => no group, no world bits at all.
    assert (KeyPerms.PRIVATE & 0o077) == 0


def test_enrollment_order_is_pk_then_kek_then_db():
    assert KEY_ROLES == ("PK", "KEK", "DB")


def test_role_of_returns_role():
    pk = next(k for k in KEY_FILES if k.name == "PK.key")
    assert role_of(pk) == "PK"


def test_permissions_are_valid_octal_modes():
    for mode in (KeyPerms.PRIVATE, KeyPerms.PUBLIC, KeyPerms.DIR):
        assert 0 <= mode <= 0o777
        # The mode must be expressible as a stat permission mask.
        assert stat.S_IMODE(mode) == mode


def test_directory_is_owner_locked():
    assert KeyPerms.DIR == 0o700
