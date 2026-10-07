"""Tests for the render module: golden unit output + JSON."""

from __future__ import annotations

from pengyos_podman.render import (
    render_json,
    render_unit,
    unit_filename,
)


EXPECTED_UNIT = """\
[Unit]
Description=PengyOS Container Service: nginx
After=network.target

[Container]
Image=docker.io/library/nginx:1.27
PodmanArgs=--rm --name nginx --user 1000:1000 --publish 8080:80 --volume $HOME/.pengyos/nginx/html:/usr/share/nginx/html:Z --env NGINX_HOST=localhost --env NGINX_PORT=80

[Service]
Type=simple
User=1000:1000
ExecStartPre=-podman rm -f nginx
ExecStart=podman run
ExecStop=podman stop -t 10 nginx
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def test_render_unit_golden(sample_service) -> None:
    assert render_unit(sample_service) == EXPECTED_UNIT


def test_render_unit_ends_with_newline(minimal_service) -> None:
    assert render_unit(minimal_service).endswith("\n")


def test_render_unit_deterministic(sample_service) -> None:
    assert render_unit(sample_service) == render_unit(sample_service)


def test_render_unit_excludes_user_when_unset(minimal_service) -> None:
    body = render_unit(minimal_service)
    assert "User=" not in body


def test_render_unit_lists_every_port(minimal_service) -> None:
    from pengyos_podman.model import Service

    svc = Service(name="x", image="img", ports=["8080:80", "443:443"])
    body = render_unit(svc)
    assert "--publish 8080:80" in body
    assert "--publish 443:443" in body


def test_render_env_sorted_alphabetically() -> None:
    from pengyos_podman.model import Service

    svc = Service(name="x", image="img", env={"ZEBRA": "1", "ALPHA": "2"})
    body = render_unit(svc)
    assert body.index("ALPHA") < body.index("ZEBRA")


def test_render_json_roundtrips(sample_service) -> None:
    import json

    data = json.loads(render_json(sample_service))
    assert data["name"] == "nginx"
    assert data["image"] == "docker.io/library/nginx:1.27"
    assert data["ports"] == ["8080:80"]
    assert data["env"]["NGINX_HOST"] == "localhost"


def test_render_json_defaults_reachable_true(minimal_service) -> None:
    # The default is reachable=True (US-SRV-2 services are reachable on the LAN).
    import json

    data = json.loads(render_json(minimal_service))
    assert data["reachable"] is True


def test_render_json_roundtrips_reachable_false() -> None:
    # reachable=False roundtrips through JSON so the escape hatch survives
    # a gen -> render -> load cycle.
    import json

    from pengyos_podman.model import Service

    svc = Service(name="worker", image="img", reachable=False)
    data = json.loads(render_json(svc))
    assert data["reachable"] is False


def test_unit_filename(sample_service) -> None:
    # Quadlet source -> a .container unit.
    assert unit_filename(sample_service) == "nginx.quadlet"


def test_unit_has_all_required_components(sample_service) -> None:
    """The item's template must expose every listed component."""

    body = render_unit(sample_service)
    for token in (
        "[Unit]",
        "[Container]",
        "[Service]",
        "[Install]",
        "PodmanArgs=",
        "Image=",
        "--publish",
        "--volume",
        "Restart=",
        "WantedBy=default.target",
    ):
        assert token in body, f"missing required component {token!r}"


def test_image_emitted_in_container_section(sample_service) -> None:
    body = render_unit(sample_service)
    assert "Image=docker.io/library/nginx:1.27" in body


def test_podmanargs_contains_rm_and_name(sample_service) -> None:
    body = render_unit(sample_service)
    podman_args = [
        line[len("PodmanArgs="):] for line in body.splitlines() if line.startswith("PodmanArgs=")
    ]
    assert len(podman_args) == 1
    args = podman_args[0].split()
    assert "--rm" in args
    assert "--name" in args and "nginx" in args


def test_no_inline_podman_run_image(minimal_service) -> None:
    """The old inline ``ExecStart=podman run ... <image>`` form is gone; the
    image now lives on ``Image=`` and args on ``PodmanArgs=``."""

    body = render_unit(minimal_service)
    assert "ExecStart=podman run --rm" not in body
    assert "ExecStart=podman run" in body
