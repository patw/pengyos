"""Tests for the host list loader (``pengyos_fleet.hosts``).

These exercise the ``hosts.toml`` schema: required fields, optional
``fixtures`` path resolution, and the typed error paths. No network, no root,
no real hardware — just TOML files on disk.
"""

from __future__ import annotations

import pytest

from pengyos_fleet.hosts import Host, HostsError, load_hosts


def _write(tmp_path, text):
    path = tmp_path / "hosts.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_reads_all_entries(tmp_path):
    path = _write(
        tmp_path,
        """
[[hosts]]
name = "n1"
address = "n1.home.lab"

[[hosts]]
name = "n2"
address = "n2.home.lab"
""",
    )
    hosts = load_hosts(path)
    assert [h.name for h in hosts] == ["n1", "n2"]
    assert hosts[0].address == "n1.home.lab"
    assert hosts[1].address == "n2.home.lab"
    # No fixtures declared -> None.
    assert hosts[0].fixtures is None


def test_host_is_frozen_dataclass(tmp_path):
    path = _write(
        tmp_path,
        """
[[hosts]]
name = "n1"
address = "n1.home.lab"
""",
    )
    (host,) = load_hosts(path)
    assert isinstance(host, Host)
    with pytest.raises(Exception):
        host.name = "x"  # type: ignore[misc]


def test_fixtures_relative_to_host_file(tmp_path):
    # A nested hosts file so we can prove relative `fixtures` resolve against
    # the file's own directory, not the cwd.
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "hosts.toml").write_text(
        """
[[hosts]]
name = "n1"
address = "n1.home.lab"
fixtures = "hosts/n1"
""",
        encoding="utf-8",
    )
    (tmp_path / "sub" / "hosts" / "n1").mkdir(parents=True)
    hosts = load_hosts(tmp_path / "sub" / "hosts.toml")
    (host,) = hosts
    assert host.fixtures == str(tmp_path / "sub" / "hosts" / "n1")


def test_absolute_fixtures_kept(tmp_path):
    abs_dir = tmp_path / "abs_fixtures"
    abs_dir.mkdir()
    path = _write(
        tmp_path,
        f"""
[[hosts]]
name = "n1"
address = "n1.home.lab"
fixtures = "{abs_dir}"
""",
    )
    (host,) = load_hosts(path)
    assert host.fixtures == str(abs_dir)


def test_missing_file_raises(tmp_path):
    with pytest.raises(HostsError, match="not found"):
        load_hosts(tmp_path / "nope.toml")


def test_invalid_toml_raises(tmp_path):
    path = _write(tmp_path, "this is = = not valid toml [[[\n")
    with pytest.raises(HostsError, match="invalid TOML"):
        load_hosts(path)


def test_missing_name_raises(tmp_path):
    path = _write(
        tmp_path,
        """
[[hosts]]
address = "n1.home.lab"
""",
    )
    with pytest.raises(HostsError, match="name"):
        load_hosts(path)


def test_missing_address_raises(tmp_path):
    path = _write(
        tmp_path,
        """
[[hosts]]
name = "n1"
""",
    )
    with pytest.raises(HostsError, match="address"):
        load_hosts(path)


def test_empty_hosts_array_raises(tmp_path):
    path = _write(
        tmp_path,
        """
# no hosts
""",
    )
    with pytest.raises(HostsError, match="no '\\[\\[hosts\\]\\]'"):
        load_hosts(path)


def test_hosts_not_a_list_raises(tmp_path):
    path = _write(
        tmp_path,
        """
hosts = { name = "n1" }
""",
    )
    with pytest.raises(HostsError, match="array of tables"):
        load_hosts(path)


def test_missing_fixtures_type_raises(tmp_path):
    path = _write(
        tmp_path,
        """
[[hosts]]
name = "n1"
address = "n1.home.lab"
fixtures = 42
""",
    )
    with pytest.raises(HostsError, match="fixtures"):
        load_hosts(path)


def test_sample_fixture_list_loads():
    # The committed test host list resolves its relative fixtures paths.
    here = __import__("pathlib").Path(__file__).parent
    hosts = load_hosts(here / "fixtures" / "hosts.toml")
    assert [h.name for h in hosts] == ["n1", "n2"]
    assert hosts[0].fixtures is not None
    assert hosts[0].address == "n1.home.lab"
