"""Tests for the loader module: parse service.yaml + clean errors."""

from __future__ import annotations

import pytest

from pengyos_podman.loader import ServiceLoadError, load_service
from pengyos_podman.model import Service


def test_load_valid_service(sample_yaml_path) -> None:
    svc = load_service(str(sample_yaml_path))
    assert isinstance(svc, Service)
    assert svc.name == "nginx"
    assert svc.image == "docker.io/library/nginx:1.27"
    assert svc.user == "1000:1000"
    assert svc.ports == ["8080:80"]
    assert svc.env == {"NGINX_HOST": "localhost", "NGINX_PORT": "80"}


def test_load_defaults(minimal_yaml_path) -> None:
    svc = load_service(str(minimal_yaml_path))
    assert svc.restart == "on-failure"
    assert svc.ports == []
    assert svc.volumes == []
    assert svc.env == {}
    assert svc.user is None


def test_load_missing_file_raises() -> None:
    with pytest.raises(ServiceLoadError):
        load_service("/nonexistent/path/service.yaml")


def test_load_invalid_yaml_raises(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: nginx\n  bad: [unterminated\n", encoding="utf-8")
    with pytest.raises(ServiceLoadError):
        load_service(str(bad))


def test_load_empty_file_raises(tmp_path) -> None:
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ServiceLoadError):
        load_service(str(empty))


def test_load_non_mapping_raises(tmp_path) -> None:
    list_yaml = tmp_path / "list.yaml"
    list_yaml.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ServiceLoadError):
        load_service(str(list_yaml))


def test_load_coerces_unquoted_scalars(tmp_path) -> None:
    """Unquoted YAML numbers (e.g. NGINX_PORT: 80) are stringified, not rejected."""

    path = tmp_path / "nums.yaml"
    path.write_text(
        "name: x\nimage: img\n"
        "env:\n  NGINX_PORT: 80\n  NGINX_HOST: localhost\n"
        "ports:\n  - 8080:80\n",
        encoding="utf-8",
    )
    svc = load_service(str(path))
    assert svc.env == {"NGINX_PORT": "80", "NGINX_HOST": "localhost"}
    assert svc.ports == ["8080:80"]


def test_load_reachable_defaults_true(tmp_path) -> None:
    """A service without ``reachable`` defaults to reachable=True (US-SRV-2)."""

    path = tmp_path / "svc.yaml"
    path.write_text("name: x\nimage: img\n", encoding="utf-8")
    assert load_service(str(path)).reachable is True


def test_load_reachable_false(tmp_path) -> None:
    """``reachable: false`` opts out of the reachability policy (the escape hatch)."""

    path = tmp_path / "svc.yaml"
    path.write_text("name: x\nimage: img\nreachable: false\n", encoding="utf-8")
    assert load_service(str(path)).reachable is False


def test_load_reachable_true_literal(tmp_path) -> None:
    path = tmp_path / "svc.yaml"
    path.write_text("name: x\nimage: img\nreachable: true\n", encoding="utf-8")
    assert load_service(str(path)).reachable is True


def test_load_reachable_quoted_string(tmp_path) -> None:
    """A quoted ``'false'`` is a YAML string; the loader coerces it to bool."""

    path = tmp_path / "svc.yaml"
    path.write_text("name: x\nimage: img\nreachable: 'false'\n", encoding="utf-8")
    assert load_service(str(path)).reachable is False


def test_load_reachable_int_zero_and_one(tmp_path) -> None:
    """``0``/``1`` ints are coerced to False/True (a common YAML spelling)."""

    zero = tmp_path / "zero.yaml"
    zero.write_text("name: x\nimage: img\nreachable: 0\n", encoding="utf-8")
    assert load_service(str(zero)).reachable is False

    one = tmp_path / "one.yaml"
    one.write_text("name: x\nimage: img\nreachable: 1\n", encoding="utf-8")
    assert load_service(str(one)).reachable is True


def test_load_reachable_non_bool_raises(tmp_path) -> None:
    """An ambiguous value (e.g. ``reachable: maybe``) is a clean load error."""

    path = tmp_path / "svc.yaml"
    path.write_text("name: x\nimage: img\nreachable: maybe\n", encoding="utf-8")
    with pytest.raises(ServiceLoadError):
        load_service(str(path))


def test_load_reachable_non_zero_int_raises(tmp_path) -> None:
    """A bare int other than 0/1 (e.g. ``2``) is not a valid boolean."""

    path = tmp_path / "svc.yaml"
    path.write_text("name: x\nimage: img\nreachable: 2\n", encoding="utf-8")
    with pytest.raises(ServiceLoadError):
        load_service(str(path))


@pytest.fixture
def minimal_yaml_path(tmp_path) -> str:
    path = tmp_path / "minimal.yaml"
    path.write_text("name: busybox\nimage: docker.io/library/busybox:latest\n", encoding="utf-8")
    return str(path)
