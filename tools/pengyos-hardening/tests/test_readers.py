"""Tests for the file readers (:mod:`pengyos_hardening.check.readers`).

The sshd_config reader is the first reader: it parses ``sshd_config`` into an
effective key/value view (last-wins) and is the backbone of the ``key_value``
matcher. The sysctl.d and fstab readers back the ``sysctl_value`` and
``mount_option`` matchers.
"""

from __future__ import annotations

from pengyos_hardening.check.readers import (
    parse_fstab,
    parse_nftables,
    parse_sshd_config,
    parse_sysctl_d,
    parse_sysctl_file,
    parse_defconfig,
)


# --- parse_sshd_config: last-wins effective key/value ---------------------

def test_parse_empty_config():
    config = parse_sshd_config("")
    assert config.values == {}
    assert config.raw == []


def test_parse_basic_keys():
    text = "PermitRootLogin no\nPasswordAuthentication no\n"
    config = parse_sshd_config(text)
    assert config.get("PermitRootLogin") == "no"
    assert config.get("PasswordAuthentication") == "no"


def test_parse_ignores_comments_and_blanks():
    text = (
        "# a comment\n"
        "   # indented comment\n"
        "\n"
        "PermitRootLogin no\n"
    )
    config = parse_sshd_config(text)
    assert set(config.values) == {"PermitRootLogin"}
    assert config.get("PermitRootLogin") == "no"


def test_parse_last_wins():
    text = "PermitRootLogin yes\nPermitRootLogin no\nPermitRootLogin yes\n"
    config = parse_sshd_config(text)
    # The last write wins, exactly as sshd resolves duplicate directives.
    assert config.get("PermitRootLogin") == "yes"
    assert config.line_for("PermitRootLogin") == 3


def test_parse_value_is_rest_of_line_stripped():
    text = "LogLevel  VERBOSE   \n"
    config = parse_sshd_config(text)
    assert config.get("LogLevel") == "VERBOSE"


def test_parse_case_insensitive_key_canonicalises():
    text = "permitrootlogin no\nPERMITROOTLOGIN yes\n"
    config = parse_sshd_config(text)
    # Both spellings resolve to the canonical key; last wins.
    assert config.get("PermitRootLogin") == "yes"


def test_parse_line_numbers_are_1_based_and_accurate():
    text = (
        "# header\n"       # 1
        "PermitRootLogin no"  # 2 (no trailing newline)
    )
    config = parse_sshd_config(text)
    assert config.line_for("PermitRootLogin") == 2


def test_parse_unknown_key_kept_canonicalised():
    config = parse_sshd_config("FooBar baz\n")
    # Unknown keys are canonicalised (title-case) and retained; they simply
    # never match a baseline rule, but are preserved for fidelity.
    assert config.get("Foobar") == "baz"


# --- parse_sshd_config against the committed hardened fixture --------------

def test_parse_hardened_sshd_config():
    from conftest import HARDENED_TREE

    text = (
        HARDENED_TREE
        / "board/pengyos-server/rootfs-overlay/etc/ssh/sshd_config"
    ).read_text(encoding="utf-8")
    config = parse_sshd_config(text)
    assert config.get("PermitRootLogin") == "no"
    assert config.get("PasswordAuthentication") == "no"
    assert config.get("PubkeyAuthentication") == "yes"
    assert config.get("PermitEmptyPasswords") == "no"


# --- parse_sysctl_file ------------------------------------------------------

def test_parse_sysctl_file_empty():
    assert parse_sysctl_file("") == {}


def test_parse_sysctl_file_ignores_comments_and_blanks():
    text = (
        "# a comment\n"
        "   # indented comment\n"
        "\n"
        "kernel.kptr_restrict = 2\n"
    )
    parsed = parse_sysctl_file(text)
    assert set(parsed) == {"kernel.kptr_restrict"}
    assert parsed["kernel.kptr_restrict"] == "2"


def test_parse_sysctl_file_value_is_rest_of_line_stripped():
    parsed = parse_sysctl_file("kernel.dmesg_restrict   =   1  \n")
    assert parsed["kernel.dmesg_restrict"] == "1"


def test_parse_sysctl_file_line_without_equals_ignored():
    parsed = parse_sysctl_file("kernel.kptr_restrict\nkernel.dmesg_restrict = 1\n")
    assert set(parsed) == {"kernel.dmesg_restrict"}


def test_parse_sysctl_file_last_wins_within_file():
    parsed = parse_sysctl_file(
        "kernel.kptr_restrict = 1\nkernel.kptr_restrict = 2\n"
    )
    assert parsed["kernel.kptr_restrict"] == "2"


# --- parse_sysctl_d (merge across files) -----------------------------------

def test_parse_sysctl_d_empty_list():
    values = parse_sysctl_d([])
    assert values.values == {}
    assert values.sources == {}
    assert values.get("kernel.kptr_restrict") is None


def test_parse_sysctl_d_single_file():
    text = "kernel.kptr_restrict = 2\nkernel.dmesg_restrict = 1\n"
    values = parse_sysctl_d([text])
    assert values.get("kernel.kptr_restrict") == "2"
    # The source is the (synthetic) file path + the 1-based line number.
    assert values.source("kernel.kptr_restrict") == ("sysctl.d/1", 1)
    assert values.source("kernel.dmesg_restrict") == ("sysctl.d/1", 2)


def test_parse_sysctl_d_last_wins_across_files():
    """Later files override earlier ones for the same key (sysctl.d semantics)."""
    first = "kernel.kptr_restrict = 1\nfs.protected_hardlinks = 1\n"
    second = "kernel.kptr_restrict = 2\n"
    values = parse_sysctl_d([first, second])
    # The effective value is the last file's write...
    assert values.get("kernel.kptr_restrict") == "2"
    # ...and its source points at that file (sysctl.d/2), not the first.
    assert values.source("kernel.kptr_restrict") == ("sysctl.d/2", 1)
    # A key only in the first file keeps the first file as its source.
    assert values.source("fs.protected_hardlinks") == ("sysctl.d/1", 2)


# --- parse_fstab ------------------------------------------------------------

def test_parse_fstab_empty():
    fstab = parse_fstab("")
    assert fstab.entries == []
    assert fstab.get("/") is None


def test_parse_fstab_ignores_comments_and_blanks():
    text = (
        "# /etc/fstab\n"
        "\n"
        "/dev/sda1  /          ext4  defaults  0  1\n"
    )
    fstab = parse_fstab(text)
    assert len(fstab.entries) == 1


def test_parse_fstab_columns_and_line_number():
    text = (
        "# header\n"
        "/dev/mapper/root  /          ext4  defaults,nodev,nosuid  0  1\n"
    )
    fstab = parse_fstab(text)
    assert len(fstab.entries) == 1
    entry = fstab.entries[0]
    assert entry.source == "/dev/mapper/root"
    assert entry.mountpoint == "/"
    assert entry.fstype == "ext4"
    assert entry.options == "defaults,nodev,nosuid"
    assert entry.dump == "0"
    assert entry.passno == "1"
    assert entry.line == 2  # 1-based, comment is line 1


def test_parse_fstab_short_lines_ignored():
    text = "/dev/sda1  /  ext4\n"  # only 3 columns
    assert parse_fstab(text).entries == []


def test_parse_fstab_get_by_mountpoint():
    text = (
        "/dev/sda1  /          ext4  defaults  0  1\n"
        "/dev/sda2  /var       ext4  defaults  0  2\n"
    )
    fstab = parse_fstab(text)
    assert fstab.get("/").mountpoint == "/"
    assert fstab.get("/var").mountpoint == "/var"
    assert fstab.get("/boot") is None


def test_fstab_entry_option_set_splits_on_comma():
    text = "/dev/sda1  /          ext4  defaults,nodev,nosuid  0  1\n"
    entry = parse_fstab(text).entries[0]
    assert entry.option_set() == {"defaults", "nodev", "nosuid"}


# --- parse_nftables ---------------------------------------------------------

def test_parse_nftables_empty():
    ruleset = parse_nftables("")
    assert ruleset.chains == {}
    assert ruleset.get("input") is None


def test_parse_nftables_ignores_comments_and_blanks():
    text = (
        "# a comment\n"
        "\n"
        "table inet filter {\n"
        "    chain input {\n"
        "        type filter hook input priority 0; policy deny;\n"
        "    }\n"
        "}\n"
    )
    ruleset = parse_nftables(text)
    assert set(ruleset.chains) == {"input"}


def test_parse_nftables_reads_chain_policy_sharing_hook_line():
    """The policy keyword trails the hook declaration on the same line."""
    text = (
        "table inet filter {\n"
        "    chain input {\n"
        "        type filter hook input priority 0; policy deny;\n"
        "    }\n"
        "}\n"
    )
    ruleset = parse_nftables(text)
    chain = ruleset.get("input")
    assert chain is not None
    assert chain.name == "input"
    assert chain.policy == "deny"  # returned as written (canonicalised by matcher)
    assert chain.line == 2  # 1-based, the `chain input {` line


def test_parse_nftables_policy_alone():
    text = (
        "table inet filter {\n"
        "    chain input {\n"
        "        policy drop;\n"
        "    }\n"
        "}\n"
    )
    chain = parse_nftables(text).get("input")
    assert chain.policy == "drop"


def test_parse_nftables_chain_without_policy_is_none():
    text = (
        "table inet filter {\n"
        "    chain input {\n"
        "        iif \"lo\" accept\n"
        "    }\n"
        "}\n"
    )
    chain = parse_nftables(text).get("input")
    assert chain is not None
    assert chain.policy is None


def test_parse_nftables_multiple_chains():
    text = (
        "table inet filter {\n"
        "    chain input {\n"
        "        type filter hook input priority 0; policy deny;\n"
        "    }\n"
        "    chain output {\n"
        "        type filter hook output priority 0; policy accept;\n"
        "    }\n"
        "}\n"
    )
    ruleset = parse_nftables(text)
    assert {c.policy for c in ruleset.chains.values()} == {"deny", "accept"}


def test_parse_nftables_line_numbers_are_1_based():
    text = (
        "# header\n"        # 1
        "table inet filter {\n"  # 2
        "    chain input {\n"    # 3
        "    }\n"                # 4
        "}\n"                    # 5
    )
    chain = parse_nftables(text).get("input")
    assert chain.line == 3


def test_parse_nftables_last_chain_wins_on_duplicate_name():
    text = (
        "table t {\n"
        "    chain input {\n"
        "        policy drop;\n"
        "    }\n"
        "    chain input {\n"
        "        policy accept;\n"
        "    }\n"
        "}\n"
    )
    chain = parse_nftables(text).get("input")
    assert chain.policy == "accept"  # the last block with that name wins


def test_parse_nftables_hardened_fixture():
    from conftest import HARDENED_TREE

    text = (
        HARDENED_TREE
        / "board/pengyos-server/rootfs-overlay/etc/nftables.conf"
    ).read_text(encoding="utf-8")
    ruleset = parse_nftables(text)
    chain = ruleset.get("input")
    assert chain is not None
    assert chain.policy == "deny"



# --- parse_defconfig --------------------------------------------------------

# The Buildroot defconfig quirk: a *set* symbol is ``BR2_FOO=y`` (no
# ``CONFIG_`` prefix); a *disabled* symbol is ``# CONFIG_BR2_FOO is not set``
# (a comment WITH the ``CONFIG_`` prefix). A faithful parser records only the
# set lines. The tests below read the committed hardened defconfig so the
# expected line numbers are real, not hand-counted.

from conftest import HARDENED_TREE


def _server_defconfig():
    return (HARDENED_TREE / "configs/pengyos_server_defconfig").read_text(
        encoding="utf-8"
    )


def test_parse_defconfig_empty():
    parsed = parse_defconfig("")
    assert parsed.symbols == {}
    assert parsed.is_set("BR2_PACKAGE_OPENSSH_SERVER") is False
    assert parsed.line("BR2_PACKAGE_OPENSSH_SERVER") is None


def test_parse_defconfig_records_only_set_lines():
    parsed = parse_defconfig(_server_defconfig())
    # The five real set lines (arch symbol is lowercase; the string value
    # BR2_ARCH="x86_64" is not a set symbol).
    assert parsed.is_set("BR2_x86_64")
    assert parsed.is_set("BR2_INIT_SYSTEMD")
    assert parsed.is_set("BR2_ROOTFS_SUDO_SERVICE")
    assert parsed.is_set("BR2_PACKAGE_OPENSSH_SERVER")
    assert parsed.is_set("BR2_PACKAGE_PENGYOS_HARDENING")
    # BR2_ARCH is bound to a string, not "=y" — not a set symbol.
    assert not parsed.is_set("BR2_ARCH")
    # The disabled symbols (comments) are NOT recorded as set.
    assert not parsed.is_set("BR2_PACKAGE_TELNET")
    assert not parsed.is_set("BR2_PACKAGE_VALGRIND")
    assert not parsed.is_set("BR2_PACKAGE_NETCAT")
    assert not parsed.is_set("BR2_TARGET_ROOTFS_TAR")


def test_parse_defconfig_disabled_comment_is_not_a_set_symbol():
    """The whole point: ``# CONFIG_BR2_FOO is not set`` must not count as set."""
    text = "# CONFIG_BR2_PACKAGE_TELNET is not set\n"
    parsed = parse_defconfig(text)
    assert parsed.symbols == {}
    assert parsed.is_set("BR2_PACKAGE_TELNET") is False


def test_parse_defconfig_set_line_line_numbers_are_1_based():
    parsed = parse_defconfig(_server_defconfig())
    # BR2_PACKAGE_OPENSSH_SERVER is the 6th line of the hardened fixture.
    assert parsed.line("BR2_PACKAGE_OPENSSH_SERVER") == 6
    # BR2_PACKAGE_PENGYOS_HARDENING is the last set line (line 12).
    assert parsed.line("BR2_PACKAGE_PENGYOS_HARDENING") == 12


def test_parse_defconfig_ignores_non_y_values():
    """Only ``BR2_FOO=y`` is a set line; a value bound to something else is
    not a boolean set symbol (e.g. ``BR2_FOO="str"`` or ``BR2_FOO=(choice)``)."""
    text = (
        'BR2_STRING_FOO="hello"\n'
        "BR2_X86_64=y\n"
        "BR2_CHOICE=(y)\n"
    )
    parsed = parse_defconfig(text)
    assert parsed.is_set("BR2_X86_64")
    assert not parsed.is_set("BR2_STRING_FOO")
    assert not parsed.is_set("BR2_CHOICE")


def test_parse_defconfig_last_write_wins():
    text = "BR2_FOO=y\nBR2_FOO=y\n"
    parsed = parse_defconfig(text)
    assert parsed.line("BR2_FOO") == 2
