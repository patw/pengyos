"""End-to-end tests for the CLI: gen / validate / render / errors.

podman/systemd are never executed — only parsing, validation and text emission.
"""

from __future__ import annotations

import json

import pytest

from pengyos_podman.cli import main


def test_gen_writes_quadlet_file(sample_yaml_path, tmp_path) -> None:
    out = tmp_path / "units"
    rc = main(["gen", "--service", str(sample_yaml_path), "--out", str(out)])
    assert rc == 0
    written = out / "nginx.quadlet"
    assert written.is_file()
    body = written.read_text(encoding="utf-8")
    assert "[Unit]" in body
    # Quadlet form: image on Image=, podman options on PodmanArgs=.
    assert "PodmanArgs=" in body
    assert "Image=docker.io/library/nginx:1.27" in body
    assert "ExecStart=podman run" in body
    assert "WantedBy=default.target" in body


def test_gen_creates_out_dir(sample_yaml_path, tmp_path) -> None:
    out = tmp_path / "nested" / "units"
    rc = main(["gen", "--service", str(sample_yaml_path), "--out", str(out)])
    assert rc == 0
    assert (out / "nginx.quadlet").is_file()


def test_gen_fails_cleanly_on_invalid_schema(tmp_path, capsys) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("image: img\n", encoding="utf-8")  # no name
    out = tmp_path / "units"
    rc = main(["gen", "--service", str(bad), "--out", str(out)])
    # Validation error (distinct from a load error, which is exit code 2).
    assert rc == 1
    assert "validation error" in capsys.readouterr().err
    # Nothing was written (validation happens before any file is emitted).
    assert not out.exists()


def test_validate_ok(sample_yaml_path) -> None:
    assert main(["validate", "--service", str(sample_yaml_path)]) == 0


def test_validate_invalid_returns_1(tmp_path, capsys) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("image: img\n", encoding="utf-8")  # missing name
    rc = main(["validate", "--service", str(bad)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "validation error" in err
    assert "name" in err


def test_validate_missing_file_returns_2(tmp_path, capsys) -> None:
    rc = main(["validate", "--service", str(tmp_path / "nope.yaml")])
    assert rc == 2
    assert "error" in capsys.readouterr().err


def test_render_json(sample_yaml_path, capsys) -> None:
    rc = main(["render", "--service", str(sample_yaml_path), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["name"] == "nginx"


def test_render_requires_json_flag(sample_yaml_path, capsys) -> None:
    rc = main(["render", "--service", str(sample_yaml_path)])
    assert rc == 2


def test_gen_reports_bad_volume_structure_cleanly(tmp_path, capsys) -> None:
    # A volume with no container side is a structural schema error: exit 1 with a
    # clean message (no traceback), and nothing is written.
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: x\n"
        "image: img\n"
        "ports:\n  - '8080:80'\n"
        "volumes:\n  - '/data'\n",  # no container side
        encoding="utf-8",
    )
    out = tmp_path / "units"
    rc = main(["gen", "--service", str(bad), "--out", str(out)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "volume" in err
    assert "must be a" in err
    assert not out.exists()


def test_validate_reports_bad_port_structure(tmp_path, capsys) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: x\n"
        "image: img\n"
        "ports:\n  - '8080:'\n",  # trailing colon -> empty container side
        encoding="utf-8",
    )
    rc = main(["validate", "--service", str(bad)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "port" in err
    assert "must be a" in err


def test_version(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_gen_ok_on_valid_with_volumes(sample_yaml_path, tmp_path) -> None:
    # The sample nginx.yaml has ports AND real-host persistent volumes: gen succeeds.
    out = tmp_path / "units"
    rc = main(["gen", "--service", str(sample_yaml_path), "--out", str(out)])
    assert rc == 0
    assert (out / "nginx.quadlet").is_file()


def test_gen_fails_cleanly_on_policy_error(tmp_path, capsys) -> None:
    # A service with no published ports fails the reachability policy: exit 1 with
    # a clean "policy error" message (no traceback), and nothing is written.
    bad = tmp_path / "nop.yaml"
    bad.write_text("name: x\nimage: img\n", encoding="utf-8")
    out = tmp_path / "units"
    rc = main(["gen", "--service", str(bad), "--out", str(out)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "policy error" in err
    assert "no ports" in err
    assert not out.exists()


def test_validate_volume_policy_advisory_by_default(tmp_path, capsys) -> None:
    # By default volume-persistence is advisory: exit 0 with a clean
    # "policy warning" message (not a fatal "policy error").
    bad = tmp_path / "cv.yaml"
    bad.write_text(
        "name: x\n"
        "image: img\n"
        "ports:\n  - '8080:80'\n"
        "volumes:\n  - '/usr/share/x:/data'\n",  # image/system path source, not persistent
        encoding="utf-8",
    )
    rc = main(["validate", "--service", str(bad)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "policy warning" in err
    assert "survives updates" in err
    assert "policy error" not in err


def test_validate_volume_policy_fatal_under_strict(tmp_path, capsys) -> None:
    # --strict makes volume-persistence fatal: exit 1 with a clean
    # "policy error" message.
    bad = tmp_path / "cv.yaml"
    bad.write_text(
        "name: x\n"
        "image: img\n"
        "ports:\n  - '8080:80'\n"
        "volumes:\n  - '/usr/share/x:/data'\n",  # image/system path source, not persistent
        encoding="utf-8",
    )
    rc = main(["validate", "--service", str(bad), "--strict"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "policy error" in err
    assert "survives updates" in err


def test_no_subcommand_errors() -> None:
    with pytest.raises(SystemExit):
        main([])


# --- check (report-only host prerequisite probe) ------------------------------


def test_check_reports_prerequisites(capsys) -> None:
    # ``check`` prints a report covering subuid/subgid/storage.conf and exits 0.
    rc = main(["check"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "subuid" in out
    assert "subgid" in out
    assert "storage.conf" in out


def test_check_reports_subuid_status(capsys) -> None:
    rc = main(["check"])
    assert rc == 0
    assert capsys.readouterr().out.count("subuid") >= 1


def test_check_is_report_only(capsys) -> None:
    # A missing prerequisite is reported, not an error: exit stays 0.
    rc = main(["check"])
    assert rc == 0


def test_gen_ok_on_reachable_false_no_port_service(tmp_path, capsys) -> None:
    # The escape hatch: gen succeeds for a reachable: false, no-port service
    # (a worker/cron) that would otherwise fail the reachability policy.
    svc = tmp_path / "worker.yaml"
    svc.write_text("name: worker\nimage: img\nreachable: false\n", encoding="utf-8")
    out = tmp_path / "units"
    rc = main(["gen", "--service", str(svc), "--out", str(out)])
    assert rc == 0
    assert (out / "worker.quadlet").is_file()


def test_validate_ok_on_reachable_false_no_port_service(tmp_path) -> None:
    svc = tmp_path / "worker.yaml"
    svc.write_text("name: worker\nimage: img\nreachable: false\n", encoding="utf-8")
    assert main(["validate", "--service", str(svc)]) == 0


def test_gen_fails_on_reachable_true_no_port_service(tmp_path, capsys) -> None:
    # Without the escape hatch (reachable defaults to True), a no-port service
    # still fails the reachability policy.
    svc = tmp_path / "svc.yaml"
    svc.write_text("name: svc\nimage: img\n", encoding="utf-8")
    out = tmp_path / "units"
    rc = main(["gen", "--service", str(svc), "--out", str(out)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "policy error" in err
    assert "no ports" in err
    assert not out.exists()


# --- Strict mode (item 6): volume-persistence advisory by default -----------


def test_gen_volume_policy_advisory_by_default(tmp_path, capsys) -> None:
    # By default a container-source volume is advisory: gen still writes the
    # unit (exit 0) and prints a "policy warning" (not a "policy error").
    bad = tmp_path / "cv.yaml"
    bad.write_text(
        "name: x\n"
        "image: img\n"
        "ports:\n  - '8080:80'\n"
        "volumes:\n  - '/usr/share/x:/data'\n",
        encoding="utf-8",
    )
    out = tmp_path / "units"
    rc = main(["gen", "--service", str(bad), "--out", str(out)])
    assert rc == 0
    assert (out / "x.quadlet").is_file()  # unit still emitted
    err = capsys.readouterr().err
    assert "policy warning" in err
    assert "survives updates" in err
    assert "policy error" not in err


def test_gen_volume_policy_fatal_under_strict(tmp_path, capsys) -> None:
    # --strict makes a container-source volume fatal: exit 1, nothing written.
    bad = tmp_path / "cv.yaml"
    bad.write_text(
        "name: x\n"
        "image: img\n"
        "ports:\n  - '8080:80'\n"
        "volumes:\n  - '/usr/share/x:/data'\n",
        encoding="utf-8",
    )
    out = tmp_path / "units"
    rc = main(["gen", "--service", str(bad), "--out", str(out), "--strict"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "policy error" in err
    assert "survives updates" in err
    assert not out.exists()


def test_gen_reachability_fatal_under_strict(tmp_path, capsys) -> None:
    # Reachability stays fatal under --strict (it always was).
    svc = tmp_path / "svc.yaml"
    svc.write_text("name: svc\nimage: img\n", encoding="utf-8")
    out = tmp_path / "units"
    rc = main(["gen", "--service", str(svc), "--out", str(out), "--strict"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "policy error" in err
    assert "no ports" in err
    assert not out.exists()
