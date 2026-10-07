"""Shared pytest fixtures for the pengyos-podman test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_podman.model import Service

# Repo root = parent of this tests/ directory.
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sample_service() -> Service:
    """A representative, valid service used across the suite."""

    return Service(
        name="nginx",
        image="docker.io/library/nginx:1.27",
        user="1000:1000",
        restart="on-failure",
        ports=["8080:80"],
        volumes=["$HOME/.pengyos/nginx/html:/usr/share/nginx/html:Z"],
        env={"NGINX_HOST": "localhost", "NGINX_PORT": "80"},
    )


@pytest.fixture
def minimal_service() -> Service:
    """The smallest valid service (name + image only)."""

    return Service(name="busybox", image="docker.io/library/busybox:latest")


@pytest.fixture
def sample_yaml_path(tmp_path: Path) -> Path:
    """Write the sample service to disk and return its path."""

    path = tmp_path / "nginx.yaml"
    path.write_text(
        "name: nginx\n"
        "image: docker.io/library/nginx:1.27\n"
        "user: '1000:1000'\n"
        "restart: on-failure\n"
        "ports:\n  - '8080:80'\n"
        "volumes:\n  - '$HOME/.pengyos/nginx/html:/usr/share/nginx/html:Z'\n"
        "env:\n  NGINX_HOST: localhost\n  NGINX_PORT: '80'\n",
        encoding="utf-8",
    )
    return path
