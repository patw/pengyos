"""Tests for the semantic policy layer (item 4): reachability + volume persistence.

Each policy rule is its own failing fixture so the rule it guards stays
testable in isolation. These are the USE-CASES US-SRV-2 guarantees:

* **Reachability** — a service must publish at least one port (reachable on the
  LAN).
* **Volume persistence** — every volume's *host* source must be a real host
  directory, not a path inside the image/container (data persists across
  updates).
"""

from __future__ import annotations

import pytest

from pengyos_podman.model import Service
from pengyos_podman.validate import (
    PolicyError,
    SchemaError,
    RULE_REACHABILITY,
    RULE_VOLUME_PERSISTENCE,
    policy_warnings,
    validate,
    validate_policy,
    validate_semantic,
)


# --- Rule 1: reachability (at least one published port) ---------------------


def test_reachability_passes_with_a_port() -> None:
    errors = validate_semantic(Service(name="x", image="img", ports=["8080:80"]))
    assert errors == []


def test_reachability_passes_with_bare_container_port() -> None:
    # A bare container port (random host port) still counts as "published".
    errors = validate_semantic(Service(name="x", image="img", ports=["80"]))
    assert errors == []


def test_reachability_fails_without_ports() -> None:
    # The failing fixture: a service with no published ports is not reachable.
    errors = validate_semantic(Service(name="x", image="img"))
    assert len(errors) == 1
    assert "no ports" in errors[0]
    assert "reachable" in errors[0]


def test_reachability_error_message_is_stable() -> None:
    # Golden message so the policy wording stays frozen.
    errors = validate_semantic(Service(name="x", image="img"))
    assert errors == [
        "service publishes no ports; add at least one to be reachable on the "
        "LAN (or set reachable: false for a non-network service)"
    ]


# --- Escape hatch: ``reachable: false`` exempts non-network services --------


def test_escape_hatch_skips_reachability_for_no_port_service() -> None:
    # The core escape-hatch behaviour: a non-network service (worker/cron) with
    # no published ports passes the reachability policy when reachable=False.
    errors = validate_semantic(
        Service(name="worker", image="img", reachable=False)
    )
    assert errors == []


def test_escape_hatch_passes_with_a_port_too() -> None:
    # reachable=False with a port is fine too (nothing contradictory).
    errors = validate_semantic(
        Service(name="x", image="img", ports=["8080:80"], reachable=False)
    )
    assert errors == []


def test_reachability_still_enforced_when_reachable_true() -> None:
    # reachable=True (the default) still requires a port.
    errors = validate_semantic(Service(name="x", image="img", reachable=True))
    assert len(errors) == 1
    assert "no ports" in errors[0]


def test_reachability_defaults_true_even_with_no_ports() -> None:
    # Default reachable is True, so an unset field still enforces reachability.
    errors = validate_semantic(Service(name="x", image="img"))
    assert len(errors) == 1
    assert "no ports" in errors[0]


def test_escape_hatch_does_not_exempt_volume_persistence() -> None:
    # reachable=False exempts ONLY reachability: a container-source volume is
    # still rejected by the volume-persistence rule.
    errors = validate_semantic(
        Service(
            name="worker",
            image="img",
            reachable=False,
            volumes=["/usr/share/x:/data"],
        )
    )
    assert len(errors) == 1
    assert "survives updates" in errors[0]
    assert "/usr/share/x" in errors[0]


def test_escape_hatch_does_not_exempt_bad_volume_with_no_ports() -> None:
    # No ports AND a bad volume, reachable=False -> only the volume is reported.
    errors = validate_semantic(
        Service(name="worker", image="img", reachable=False, volumes=["/etc/x:/data"])
    )
    assert len(errors) == 1
    assert "/etc/x" in errors[0]


def test_validate_policy_passes_for_escape_hatch_service() -> None:
    # validate_policy must NOT raise for a reachable=False, no-port service.
    validate_policy(Service(name="worker", image="img", reachable=False))


# --- Rule 2: volume persistence (host source outside the image) -------------


def test_volume_persistence_passes_for_real_host_dir() -> None:
    errors = validate_semantic(
        Service(
            name="x",
            image="img",
            ports=["8080:80"],
            volumes=["/home/alice/data:/var/lib/app"],
        )
    )
    assert errors == []


def test_volume_persistence_passes_for_tilde_host_dir() -> None:
    # The sample nginx.yaml mounts under $HOME — a real (persistent) host dir.
    errors = validate_semantic(
        Service(
            name="nginx",
            image="img",
            ports=["8080:80"],
            volumes=["$HOME/.pengyos/nginx/html:/usr/share/nginx/html:Z"],
        )
    )
    assert errors == []


def test_volume_persistence_fails_for_container_source() -> None:
    # The failing fixture: a bind mount whose host source is a known image/system
    # path is not persistent (that source is replaced after an image update).
    errors = validate_semantic(
        Service(name="x", image="img", ports=["8080:80"], volumes=["/usr/share/x:/data"])
    )
    assert len(errors) == 1
    assert "survives updates" in errors[0]
    assert "/usr/share/x" in errors[0]


def test_volume_persistence_fails_for_etc_source() -> None:
    errors = validate_semantic(
        Service(name="x", image="img", ports=["8080:80"], volumes=["/etc/foo:/data"])
    )
    assert len(errors) == 1
    assert "/etc/foo" in errors[0]


def test_volume_persistence_reports_every_bad_volume() -> None:
    # Two container-source volumes -> two policy errors, at once.
    errors = validate_semantic(
        Service(
            name="x",
            image="img",
            ports=["8080:80"],
            volumes=["/usr/share/a:/x", "/etc/b:/y"],
        )
    )
    assert len(errors) == 2


def test_volume_persistence_mixed_sources() -> None:
    # A real host dir AND a container-source dir: only the bad one is flagged.
    errors = validate_semantic(
        Service(
            name="x",
            image="img",
            ports=["8080:80"],
            volumes=["/home/alice/data:/x", "/usr/share/b:/y"],
        )
    )
    assert len(errors) == 1
    assert "/usr/share/b" in errors[0]


# --- PolicyError / validate_policy ------------------------------------------


def test_validate_policy_passes_on_valid(sample_service) -> None:
    # Should not raise.
    validate_policy(sample_service)


def test_validate_policy_raises_policy_error() -> None:
    with pytest.raises(PolicyError) as excinfo:
        validate_policy(Service(name="x", image="img"))
    assert excinfo.value.errors
    assert any("no ports" in e for e in excinfo.value.errors)


def test_policy_error_is_runtime_error() -> None:
    # PolicyError and SchemaError are distinct (but both RuntimeError subclasses),
    # so the CLI can tell a policy failure apart from a schema failure.
    assert issubclass(PolicyError, RuntimeError)
    assert issubclass(SchemaError, RuntimeError)
    assert PolicyError is not SchemaError


def test_both_rules_reported_at_once() -> None:
    # No ports AND a container-source volume -> both policies reported at once.
    errors = validate_semantic(
        Service(name="x", image="img", volumes=["/usr/share/x:/data"])
    )
    assert len(errors) == 2
    assert any("no ports" in e for e in errors)
    assert any("survives updates" in e for e in errors)


def test_validate_still_ignores_policy() -> None:
    # The structural validate() must NOT run policy rules (they are separate):
    # a no-ports service is still structurally valid.
    assert validate(Service(name="x", image="img")) == []


# --- helper unit tests ------------------------------------------------------


def test_host_path_extraction() -> None:
    from pengyos_podman.validate import _host_path

    assert _host_path("/a:/b") == "/a"
    assert _host_path("/a:/b:Z") == "/a"
    assert _host_path("$HOME/x:/y") == "$HOME/x"
    assert _host_path("") is None


def test_image_path_detection() -> None:
    # Known image/system dirs are flagged; ordinary host data dirs are not.
    from pengyos_podman.validate import _host_path, _is_image_path

    assert _host_path("/a:/b") == "/a"
    assert _host_path("/a:/b:Z") == "/a"
    assert _host_path("$HOME/x:/y") == "$HOME/x"
    assert _host_path("") is None

    assert _is_image_path("/usr/share")
    assert _is_image_path("/usr/share/x")
    assert _is_image_path("/etc")
    assert _is_image_path("/lib")
    assert not _is_image_path("/home/alice/data")
    assert not _is_image_path("$HOME/.pengyos/nginx/html")
    assert not _is_image_path("relative/path")
    assert not _is_image_path("")


# --- Strict mode (item 6): reachability always fatal, volume advisory -------
#
# By default only reachability is fatal; volume-persistence is advisory (a
# warning). --strict makes volume-persistence fatal too.


def _has(errors: list[str], needle: str) -> bool:
    return any(needle in e for e in errors)


def test_validate_policy_reachability_always_fatal() -> None:
    # A no-port reachable service raises PolicyError even without --strict.
    with pytest.raises(PolicyError) as excinfo:
        validate_policy(Service(name="x", image="img"))
    assert _has(excinfo.value.errors, "no ports")


def test_validate_policy_volume_advisory_by_default() -> None:
    # A container-source volume does NOT raise without --strict (advisory).
    validate_policy(
        Service(name="x", image="img", ports=["8080:80"], volumes=["/usr/share/x:/data"])
    )


def test_validate_policy_volume_fatal_under_strict() -> None:
    # --strict makes a container-source volume raise PolicyError.
    with pytest.raises(PolicyError) as excinfo:
        validate_policy(
            Service(
                name="x",
                image="img",
                ports=["8080:80"],
                volumes=["/usr/share/x:/data"],
            ),
            strict=True,
        )
    assert _has(excinfo.value.errors, "survives updates")


def test_validate_policy_strict_still_fatal_for_reachability() -> None:
    # --strict does not change reachability: still fatal.
    with pytest.raises(PolicyError) as excinfo:
        validate_policy(Service(name="x", image="img"), strict=True)
    assert _has(excinfo.value.errors, "no ports")


def test_validate_policy_strict_passes_on_valid() -> None:
    # --strict on a fully valid service raises nothing.
    validate_policy(
        Service(
            name="nginx",
            image="img",
            ports=["8080:80"],
            volumes=["$HOME/.pengyos/nginx/html:/usr/share/nginx/html:Z"],
        ),
        strict=True,
    )


def test_policy_warnings_returns_volume_violations() -> None:
    # Advisory warnings expose the volume-persistence violations.
    warnings = policy_warnings(
        Service(name="x", image="img", ports=["8080:80"], volumes=["/etc/x:/data"])
    )
    assert len(warnings) == 1
    assert "survives updates" in warnings[0]


def test_policy_warnings_empty_for_valid_service(sample_service) -> None:
    # A valid service (the sample nginx) has no advisory warnings.
    assert policy_warnings(sample_service) == []


def test_policy_warnings_never_include_reachability() -> None:
    # Reachability is fatal, never advisory: a no-port service reports no
    # warnings (its violation is fatal, not advisory).
    assert policy_warnings(Service(name="x", image="img")) == []


def test_policy_warnings_is_pure_regardless_of_strict() -> None:
    # policy_warnings() is a pure view of the service: it returns every
    # volume-persistence violation regardless of strict. The CLI decides whether
    # to surface them as warnings (non-strict) or raise them (strict); the
    # function itself reports all of them.
    warnings = policy_warnings(
        Service(name="x", image="img", ports=["8080:80"], volumes=["/etc/x:/data"])
    )
    assert len(warnings) == 1
    assert "survives updates" in warnings[0]


def test_policy_warnings_multiple_violations() -> None:
    # Two container-source volumes -> two advisory warnings.
    warnings = policy_warnings(
        Service(
            name="x",
            image="img",
            ports=["8080:80"],
            volumes=["/usr/share/a:/x", "/etc/b:/y"],
        )
    )
    assert len(warnings) == 2


def test_rule_constants_are_distinct() -> None:
    # The two rules are distinguishable so the CLI can classify them.
    assert RULE_REACHABILITY != RULE_VOLUME_PERSISTENCE
