"""Unit tests for the declarative smoke matrix (``pengyos_qemu_verify.smoke``).

The matrix is data plus a runner with an injectable ``boot_fn``, so almost
everything here runs with no QEMU, no root, no network and no hardware. The
fake boot still evaluates *real* assertions over *real* log text, so the whole
matrix wiring is exercised apart from the QEMU launch itself.

The last section is the important one: it proves the shipped matrix is not
decorative. Every check is evaluated against genuine Core serial captures
committed as fixtures — UEFI/GRUB2 boots with and without a TPM (all checks
must pass) and a bare ``-kernel`` boot that cannot find its root (exactly the
facts that did not happen must fail). If someone "fixes" a check by weakening
its pattern to something that can never fail, that asymmetry breaks and this
suite fails.
"""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from pengyos_qemu_verify import argv as argv_mod
from pengyos_qemu_verify import cli, smoke
from pengyos_qemu_verify.assertions import (
    KIND_ABSENT,
    KIND_PRESENT,
    run_assertions,
)
from pengyos_qemu_verify.boot import BootResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CORE_UEFI_LOG = FIXTURES / "serial_login_core_uefi.log"
CORE_TPM_LOG = FIXTURES / "serial_login_core_uefi_tpm.log"
# A real failed boot: the Core kernel booted bare (-kernel, no firmware) with
# root=/dev/vda, which is the partition table, not the root -> panic.
CORE_NO_ROOT_LOG = FIXTURES / "serial_core_kernel_boot_no_root.log"
PANIC_LOG = FIXTURES / "serial_kernel_panic_sample.log"


# --- helpers ----------------------------------------------------------------


def _boot_from(
    log_path,
    *,
    ready=True,
    timed_out=False,
    error=None,
    drop_checks=False,
    raises=None,
    duration=0.5,
):
    """A ``boot_fn`` that runs the real assertion runner over a real log.

    Injected wherever the runner would call :func:`boot.boot_image`, so the
    tests exercise the matrix's check wiring without launching anything.
    """
    log_text = Path(log_path).read_text(encoding="utf-8", errors="replace")

    def _boot(image, *, assertions=(), **kwargs):
        if raises is not None:
            raise raises
        checks = [] if drop_checks else run_assertions(log_text, tuple(assertions))
        return BootResult(
            image=image,
            ready=ready,
            timed_out=timed_out,
            duration=duration,
            serial_log=f"/tmp/fake-{Path(log_path).name}",
            checks=checks,
            error=error,
        )

    return _boot


def _matrix(**overrides):
    """A tiny valid two-variant matrix, with overrides for validation tests."""
    base = {
        "version": smoke.MATRIX_VERSION,
        "name": "t",
        "image": "dist/img",
        "variant": [{"id": "a", "uefi": True}, {"id": "b", "tpm": True}],
        "check": [{"id": "c", "kind": KIND_PRESENT, "pattern": "login:"}],
    }
    base.update(overrides)
    return smoke.matrix_from_dict(base, source="<test>")


# --- the shipped matrix -----------------------------------------------------


def test_shipped_matrix_loads_and_is_well_formed():
    matrix = smoke.load_matrix()
    assert matrix.name == "core"
    assert matrix.version == smoke.MATRIX_VERSION
    assert matrix.image.endswith("pengyos-core-x86_64.img")
    assert [v.id for v in matrix.variants] == ["uefi", "uefi-tpm"]
    assert matrix.checks, "the shipped matrix must declare checks"


def test_shipped_matrix_image_resolves_into_the_build_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PENGYOS_BUILD_ROOT", str(tmp_path))
    matrix = smoke.load_matrix()
    image = Path(smoke.resolve_image(matrix))
    # It must point at pengyos-build's output, not somewhere relative to the
    # caller's working directory.
    assert image == tmp_path / "output" / "pengyos-core" / "images" / "pengyos-core-x86_64.img"


def test_build_root_defaults_to_home_build_pengyos(monkeypatch):
    monkeypatch.delenv("PENGYOS_BUILD_ROOT", raising=False)
    assert smoke.build_root() == Path.home() / "build" / "pengyos"


def test_shipped_matrix_variants_are_both_uefi_boots():
    matrix = smoke.load_matrix()
    assert matrix.variant("uefi").uefi is True
    assert matrix.variant("uefi").tpm is False
    assert matrix.variant("uefi-tpm").uefi is True
    assert matrix.variant("uefi-tpm").tpm is True


def test_shipped_matrix_tpm_variant_asserts_the_same_facts():
    """Core has no TPM consumer, so the TPM variant's value is *wiring*: it
    must reach the same login prompt, i.e. run the same checks."""
    matrix = smoke.load_matrix()
    assert matrix.checks_for("uefi") == matrix.checks_for("uefi-tpm")


def test_shipped_matrix_check_ids_are_unique_and_patterns_compile():
    matrix = smoke.load_matrix()
    ids = [c.id for c in matrix.checks]
    assert len(set(ids)) == len(ids)
    for check in matrix.checks:
        re.compile(check.pattern)  # raises re.error if malformed
        assert check.description, f"{check.id} must explain why it matters"


@pytest.mark.parametrize("log_path", [CORE_UEFI_LOG, CORE_TPM_LOG, CORE_NO_ROOT_LOG, PANIC_LOG])
def test_shipped_matrix_checks_are_grounded_in_real_captures(log_path):
    """Every shipped check is decided by a real capture — nothing is vacuous.

    A check that can never change its verdict would pass against *all* of
    these logs; the good/failed-boot asymmetry below proves these do not.
    """
    matrix = smoke.load_matrix()
    log_text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    checks = matrix.checks_for("uefi")
    results = run_assertions(log_text, tuple(c.to_assertion() for c in checks))
    assert len(results) == len(checks)


@pytest.mark.parametrize("log_path", [CORE_UEFI_LOG, CORE_TPM_LOG])
def test_shipped_matrix_passes_a_real_core_boot(log_path):
    matrix = smoke.load_matrix()
    outcome = smoke.run_matrix(
        matrix, "img", boot_fn=_boot_from(log_path)
    )
    assert outcome.passed is True
    assert outcome.result == "pass"
    assert outcome.checks_failed == 0
    assert outcome.checks_run == len(matrix.checks) * len(matrix.variants)


def test_shipped_matrix_rejects_a_real_failed_boot_for_the_right_reasons():
    """A kernel that boots without firmware or GRUB and cannot find its root
    must fail on exactly the facts that did not happen — and every
    "absent" check that has nothing to do with it must still pass."""
    matrix = smoke.load_matrix()
    outcome = smoke.run_matrix(matrix, "img", boot_fn=_boot_from(CORE_NO_ROOT_LOG))
    assert outcome.passed is False
    expected = {
        "firmware-handoff", "grub-cmdline", "rootfs-mounted", "systemd-pid1",
        "distro-welcome", "rootfs-remounted-rw", "nic-link-up", "networkd-started",
        "resolved-started", "network-target", "chrony-started", "sshd-started",
        "multi-user", "reached-login", "autologin-shell", "no-kernel-panic",
        "no-oops",
    }
    for variant in outcome.variants:
        assert set(variant.failed_checks) == expected, variant.failed_checks


def test_shipped_matrix_fails_a_panicking_boot():
    matrix = smoke.load_matrix()
    outcome = smoke.run_matrix(matrix, "img", boot_fn=_boot_from(PANIC_LOG))
    assert outcome.passed is False
    failed = outcome.variants[0].failed_checks
    assert "no-kernel-panic" in failed
    assert "reached-login" in failed



# --- the Server matrix ------------------------------------------------------

SERVER_MATRIX = Path(smoke.default_matrix_path()).with_name("server.toml")
SERVER_UEFI_LOG = FIXTURES / "serial_login_server_uefi.log"
SERVER_TPM_LOG = FIXTURES / "serial_login_server_uefi_tpm.log"


def test_server_matrix_loads_and_targets_the_server_image():
    matrix = smoke.load_matrix(str(SERVER_MATRIX))
    assert matrix.name == "server"
    assert matrix.image.endswith("pengyos-server-x86_64.img")
    assert [v.id for v in matrix.variants] == ["uefi", "uefi-tpm"]
    for check in matrix.checks:
        assert check.description, f"{check.id} must explain why it matters"


@pytest.mark.parametrize("log_path", [SERVER_UEFI_LOG, SERVER_TPM_LOG])
def test_server_matrix_passes_a_real_server_boot(log_path):
    matrix = smoke.load_matrix(str(SERVER_MATRIX))
    outcome = smoke.run_matrix(matrix, "img", boot_fn=_boot_from(log_path))
    assert outcome.passed is True, [v.failed_checks for v in outcome.variants]


def test_server_matrix_rejects_a_core_boot_on_exactly_the_server_facts():
    """Core is Server minus the Server layer, so a real Core boot must fail
    the Server-only checks and nothing else."""
    matrix = smoke.load_matrix(str(SERVER_MATRIX))
    outcome = smoke.run_matrix(matrix, "img", boot_fn=_boot_from(CORE_UEFI_LOG))
    expected = {
        "distro-welcome", "network-online", "nftables-loaded", "sshd-started",
        "podman-socket", "podman-service", "metrics-timer",
        "update-timer", "server-target", "reached-login", "autologin-shell",
    }
    for variant in outcome.variants:
        assert set(variant.failed_checks) == expected, variant.failed_checks

# --- validation -------------------------------------------------------------


def test_matrix_requires_a_name():
    with pytest.raises(smoke.MatrixError, match="missing 'name'"):
        smoke.matrix_from_dict({"variant": [], "check": []})


def test_matrix_rejects_unknown_version():
    with pytest.raises(smoke.MatrixError, match="unsupported version"):
        _matrix(version=99)


def test_matrix_rejects_no_variants():
    with pytest.raises(smoke.MatrixError, match="no \\[\\[variant\\]\\] declared"):
        _matrix(variant=[])


def test_matrix_rejects_no_checks():
    with pytest.raises(smoke.MatrixError, match="no \\[\\[check\\]\\] declared"):
        _matrix(check=[])


def test_matrix_rejects_a_variant_that_no_check_covers():
    """A variant nothing asserts could never fail — reject it outright."""
    with pytest.raises(smoke.MatrixError, match="has no checks"):
        smoke.matrix_from_dict(
            {
                "name": "t",
                "variant": [{"id": "a"}, {"id": "b"}],
                "check": [
                    {"id": "c", "kind": KIND_PRESENT, "pattern": "x", "variants": ["a"]}
                ],
            }
        )


def test_matrix_rejects_a_dangling_variant_reference():
    with pytest.raises(smoke.MatrixError, match="unknown variant"):
        smoke.matrix_from_dict(
            {
                "name": "t",
                "variant": [{"id": "a"}],
                "check": [
                    {"id": "c", "kind": KIND_PRESENT, "pattern": "x", "variants": ["nope"]}
                ],
            }
        )


def test_matrix_rejects_duplicate_variant_ids():
    with pytest.raises(smoke.MatrixError, match="duplicate variant id"):
        _matrix(variant=[{"id": "a"}, {"id": "a"}])


def test_matrix_rejects_duplicate_check_ids():
    with pytest.raises(smoke.MatrixError, match="duplicate check id"):
        _matrix(
            check=[
                {"id": "c", "kind": KIND_PRESENT, "pattern": "x"},
                {"id": "c", "kind": KIND_PRESENT, "pattern": "y"},
            ]
        )


def test_check_rejects_unknown_kind():
    with pytest.raises(smoke.MatrixError, match="kind must be"):
        smoke.SmokeCheck(id="c", kind="maybe", pattern="x")


def test_check_rejects_empty_pattern():
    with pytest.raises(smoke.MatrixError, match="pattern must be non-empty"):
        smoke.SmokeCheck(id="c", kind=KIND_PRESENT, pattern="")


def test_check_rejects_empty_id():
    with pytest.raises(smoke.MatrixError, match="id must be a non-empty string"):
        smoke.SmokeCheck(id="  ", kind=KIND_ABSENT, pattern="x")


def test_variant_rejects_uefi_and_kernel_together():
    with pytest.raises(smoke.MatrixError, match="mutually exclusive"):
        smoke.SmokeVariant(id="v", uefi=True, kernel="/boot/bzImage")


def test_variant_rejects_non_positive_timeout():
    with pytest.raises(smoke.MatrixError, match="timeout must be positive"):
        smoke.SmokeVariant(id="v", timeout=0)


def test_unknown_variant_lookup_raises():
    with pytest.raises(smoke.MatrixError, match="unknown variant"):
        _matrix().variant("nope")


def test_kind_defaults_for_variants_table():
    """A [[variant]] with no flags is a SeaBIOS disk boot (valid, if unused)."""
    variant = smoke.SmokeVariant(id="v")
    assert variant.uefi is False and variant.tpm is False and variant.kernel is None


# --- loading ----------------------------------------------------------------


def test_load_matrix_missing_file(tmp_path):
    with pytest.raises(smoke.MatrixError, match="not found"):
        smoke.load_matrix(str(tmp_path / "nope.toml"))


def test_load_matrix_directory(tmp_path):
    with pytest.raises(smoke.MatrixError, match="is a directory"):
        smoke.load_matrix(str(tmp_path))


def test_load_matrix_invalid_toml(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("name = [unterminated\n", encoding="utf-8")
    with pytest.raises(smoke.MatrixError, match="not valid TOML"):
        smoke.load_matrix(str(path))


def test_load_matrix_reports_source_path(tmp_path):
    """A malformed entry must name the file it came from, so the error is
    actionable when several matrices exist."""
    path = tmp_path / "m.toml"
    path.write_text(
        'version = 1\nname = "x"\n\n[[variant]]\nuefi = true\n\n'
        '[[check]]\nid = "c"\nkind = "present"\npattern = "x"\n',
        encoding="utf-8",
    )
    with pytest.raises(smoke.MatrixError, match=re.escape(str(path))):
        smoke.load_matrix(str(path))


def test_default_matrix_path_points_at_the_shipped_file():
    assert Path(smoke.default_matrix_path()).is_file()
    assert Path(smoke.default_matrix_path()).name == smoke.DEFAULT_MATRIX_FILE


def test_matrix_from_dict_rejects_non_mapping():
    with pytest.raises(smoke.MatrixError, match="must be a TOML table"):
        smoke.matrix_from_dict(["not", "a", "table"])


def test_variant_entry_must_be_a_table():
    with pytest.raises(smoke.MatrixError, match="each \\[\\[variant\\]\\] must be a table"):
        smoke.matrix_from_dict(
            {"name": "t", "variant": ["nope"], "check": [{"id": "c", "kind": KIND_PRESENT, "pattern": "x"}]}
        )


def test_variant_entry_needs_an_id():
    with pytest.raises(smoke.MatrixError, match="missing its 'id'"):
        smoke.matrix_from_dict(
            {"name": "t", "variant": [{"uefi": True}], "check": [{"id": "c", "kind": KIND_PRESENT, "pattern": "x"}]}
        )


def test_check_entry_needs_its_keys():
    with pytest.raises(smoke.MatrixError, match="missing 'kind'"):
        smoke.matrix_from_dict({"name": "t", "variant": [{"id": "a"}], "check": [{"id": "c", "pattern": "x"}]})


# --- resolve_image ----------------------------------------------------------


def test_resolve_image_relative_matrix_path_is_build_rooted(tmp_path):
    matrix = _matrix(image="output/pengyos-core/images/pengyos-core-x86_64.img")
    resolved = smoke.resolve_image(matrix, base=str(tmp_path))
    assert resolved == str(tmp_path / "output" / "pengyos-core" / "images" / "pengyos-core-x86_64.img")


def test_resolve_image_falls_back_to_cwd_when_build_root_path_is_absent(tmp_path, monkeypatch):
    """Running from the image's own directory must still find it."""
    (tmp_path / "img.bin").write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    matrix = _matrix(image="img.bin")
    assert smoke.resolve_image(matrix, base=str(tmp_path / "elsewhere")) == "img.bin"


def test_resolve_image_override_is_verbatim():
    matrix = _matrix(image="dist/ignored.img")
    assert smoke.resolve_image(matrix, "some/other.img") == "some/other.img"


def test_resolve_image_absolute_matrix_path_is_kept():
    matrix = _matrix(image="/abs/img.bin")
    assert smoke.resolve_image(matrix) == "/abs/img.bin"


def test_resolve_image_requires_one():
    with pytest.raises(smoke.MatrixError, match="declares no image"):
        smoke.resolve_image(_matrix(image=""))


# --- running ----------------------------------------------------------------


def test_run_matrix_all_pass():
    matrix = _matrix()
    outcome = smoke.run_matrix(matrix, "img", boot_fn=_boot_from(CORE_UEFI_LOG))
    assert outcome.passed
    assert len(outcome.variants) == 2
    assert all(v.passed for v in outcome.variants)
    assert outcome.checks_run == 2
    assert outcome.checks_failed == 0


def test_run_matrix_selects_one_variant():
    outcome = smoke.run_matrix(
        _matrix(), "img", boot_fn=_boot_from(CORE_UEFI_LOG), variants=["b"]
    )
    assert [v.variant.id for v in outcome.variants] == ["b"]


def test_run_matrix_unknown_variant_selection_raises():
    with pytest.raises(smoke.MatrixError, match="unknown variant"):
        smoke.run_matrix(_matrix(), "img", boot_fn=_boot_from(CORE_UEFI_LOG), variants=["zz"])


def test_run_matrix_empty_selection_raises():
    with pytest.raises(smoke.MatrixError, match="no variants selected"):
        smoke.run_matrix(_matrix(), "img", boot_fn=_boot_from(CORE_UEFI_LOG), variants=[])


def test_run_matrix_fails_when_boot_is_not_ready():
    outcome = smoke.run_matrix(
        _matrix(), "img", boot_fn=_boot_from(CORE_UEFI_LOG, ready=False)
    )
    assert outcome.passed is False
    assert outcome.variants[0].to_dict()["ready"] is False


def test_run_matrix_fails_when_boot_timed_out():
    outcome = smoke.run_matrix(
        _matrix(), "img", boot_fn=_boot_from(CORE_UEFI_LOG, ready=False, timed_out=True)
    )
    assert outcome.passed is False


def test_run_matrix_fails_on_boot_error():
    outcome = smoke.run_matrix(
        _matrix(), "img", boot_fn=_boot_from(CORE_UEFI_LOG, ready=False, error="qemu exploded")
    )
    assert outcome.passed is False
    assert outcome.variants[0].boot.error == "qemu exploded"
    assert outcome.variants[0].to_dict()["result"] == "fail"


def test_run_matrix_fails_when_the_checks_did_not_run():
    """A boot that silently dropped the checks must fail, never pass vacuously."""
    outcome = smoke.run_matrix(
        _matrix(), "img", boot_fn=_boot_from(CORE_UEFI_LOG, drop_checks=True)
    )
    assert outcome.passed is False
    error = outcome.variants[0].error
    assert error and "did not run" in error


def test_run_matrix_captures_a_raising_boot_fn():
    outcome = smoke.run_matrix(
        _matrix(), "img", boot_fn=_boot_from(CORE_UEFI_LOG, raises=RuntimeError("boom"))
    )
    assert outcome.passed is False
    assert outcome.variants[0].error == "RuntimeError: boom"
    assert outcome.variants[0].boot is None


def test_run_matrix_passes_extra_boot_kwargs():
    calls = []

    def _boot(image, *, assertions=(), **kwargs):
        calls.append(kwargs)
        return BootResult(image=image, ready=True, checks=run_assertions("login: ", tuple(assertions)))

    smoke.run_matrix(
        _matrix(), "img", boot_fn=_boot, boot_kwargs={"kvm": False}, variants=["a"]
    )
    assert calls[0]["kvm"] is False
    assert calls[0]["uefi"] is True  # the variant's own kwargs still apply
    assert calls[0]["tpm"] is False


def test_run_matrix_assertions_reach_the_boot_fn():
    seen = {}

    def _boot(image, *, assertions=(), **kwargs):
        seen["assertions"] = list(assertions)
        return BootResult(image=image, ready=True, checks=run_assertions("login: ", tuple(assertions)))

    smoke.run_matrix(_matrix(), "img", boot_fn=_boot, variants=["a"])
    assert [a.name for a in seen["assertions"]] == ["c"]


# --- reports ----------------------------------------------------------------


def test_matrix_to_dict_shape():
    outcome = smoke.run_matrix(_matrix(), "img", boot_fn=_boot_from(CORE_UEFI_LOG))
    data = outcome.to_dict()
    assert data["matrix"] == "t"
    assert data["result"] == "pass"
    assert data["variants_total"] == 2
    assert data["checks_total"] == 2
    assert data["checks_failed"] == 0
    assert [v["id"] for v in data["variants"]] == ["a", "b"]
    assert data["variants"][0]["checks"][0]["name"] == "c"


def test_matrix_to_json_round_trips():
    outcome = smoke.run_matrix(_matrix(), "img", boot_fn=_boot_from(CORE_UEFI_LOG))
    assert json.loads(outcome.to_json())["result"] == "pass"


def test_matrix_junit_has_one_suite_per_variant():
    outcome = smoke.run_matrix(_matrix(), "img", boot_fn=_boot_from(CORE_UEFI_LOG))
    root = ET.fromstring(outcome.to_junit())
    suites = root.findall("testsuite")
    assert [s.get("variant") for s in suites] == ["a", "b"]
    assert all(s.get("name").startswith("smoke:t:") for s in suites)
    assert int(root.get("failures")) == 0
    # one synthetic boot testcase + one per check, per variant
    assert int(root.get("tests")) == 2 * 2


def test_matrix_junit_records_failures_and_evidence():
    outcome = smoke.run_matrix(_matrix(), "img", boot_fn=_boot_from(PANIC_LOG))
    root = ET.fromstring(outcome.to_junit())
    assert int(root.get("failures")) >= 1
    failures = [tc for tc in root.iter("testcase") if tc.find("failure") is not None]
    assert failures, "a failing run must emit <failure> elements"


def test_matrix_junit_reports_a_boot_failure():
    outcome = smoke.run_matrix(_matrix(), "img", boot_fn=_boot_from(CORE_UEFI_LOG, ready=False))
    root = ET.fromstring(outcome.to_junit())
    boot_case = root.find("testsuite").find("testcase")
    assert boot_case.get("name") == "boot-reached-readiness"
    assert boot_case.find("failure") is not None


def test_matrix_junit_reports_a_variant_error():
    outcome = smoke.run_matrix(
        _matrix(), "img", boot_fn=_boot_from(CORE_UEFI_LOG, raises=RuntimeError("boom"))
    )
    root = ET.fromstring(outcome.to_junit())
    failure = root.find("testsuite").find("testcase").find("failure")
    assert failure is not None
    assert failure.get("type") == "MatrixError"
    assert "boom" in (failure.text or "")


def test_write_json_and_junit(tmp_path):
    outcome = smoke.run_matrix(_matrix(), "img", boot_fn=_boot_from(CORE_UEFI_LOG))
    json_path = smoke.write_json(str(tmp_path / "nested" / "out.json"), outcome)
    junit_path = smoke.write_junit(str(tmp_path / "nested" / "out.xml"), outcome)
    assert json.loads(Path(json_path).read_text())["result"] == "pass"
    assert ET.fromstring(Path(junit_path).read_text()) is not None


# --- plan_argv --------------------------------------------------------------


def test_plan_argv_renders_the_variant_and_snapshots_the_disk():
    matrix = _matrix()
    argv = smoke.plan_argv(
        matrix.variant("a"), "img", ovmf=argv_mod.OvmfFirmware("/usr/share/OVMF/OVMF_CODE.fd")
    )
    assert "-bios" in argv
    assert "file=img,if=virtio,format=raw,snapshot=on" in argv


def test_plan_argv_tpm_variant_renders_the_placeholder_socket():
    argv = smoke.plan_argv(_matrix().variant("b"), "img")
    assert any(smoke.TPM_SOCKET_PLACEHOLDER in a for a in argv)
    assert "-tpmdev" in argv


def test_plan_argv_kernel_variant_uses_append():
    variant = smoke.SmokeVariant(id="k", kernel="/boot/bzImage")
    argv = smoke.plan_argv(variant, "img")
    assert "-kernel" in argv and "-append" in argv


# --- CLI --------------------------------------------------------------------


def test_cli_dry_run_prints_the_plan_and_boots_nothing(capsys):
    rc = cli.main(["smoke", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert "core" in out
    assert "check reached-login" in out
    assert "qemu-system-x86_64" in out
    assert "snapshot=on" in out
    assert out.count("variant uefi") == 2  # uefi + uefi-tpm


def test_cli_dry_run_honours_image_override(capsys):
    rc = cli.main(["smoke", "--dry-run", "--image", "/tmp/other.img"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert "file=/tmp/other.img,if=virtio,format=raw,snapshot=on" in out


def test_cli_dry_run_single_variant(capsys):
    rc = cli.main(["smoke", "--dry-run", "--variant", "uefi-tpm"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert out.count("variant uefi") == 1
    assert "-tpmdev" in out


def test_cli_missing_matrix_is_a_usage_error(tmp_path, capsys):
    rc = cli.main(["smoke", "--matrix", str(tmp_path / "nope.toml")])
    err = capsys.readouterr().err
    assert rc == cli.EXIT_USAGE
    assert "not found" in err


def test_cli_invalid_matrix_is_a_usage_error(tmp_path, capsys):
    path = tmp_path / "m.toml"
    path.write_text('version = 1\nname = "x"\n', encoding="utf-8")
    rc = cli.main(["smoke", "--matrix", str(path)])
    err = capsys.readouterr().err
    assert rc == cli.EXIT_USAGE
    assert "no [[variant]] declared" in err


def test_cli_unknown_variant_is_a_usage_error(capsys):
    rc = cli.main(["smoke", "--variant", "nope"])
    assert rc == cli.EXIT_USAGE
    assert "unknown variant" in capsys.readouterr().err


def test_cli_dry_run_missing_matrix_is_a_usage_error(tmp_path, capsys):
    rc = cli.main(["smoke", "--dry-run", "--matrix", str(tmp_path / "nope.toml")])
    assert rc == cli.EXIT_USAGE


def test_smoke_subcommand_is_documented_in_help(capsys):
    rc = cli.main([])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert "smoke" in out


def test_smoke_parser_registers_its_flags():
    args = cli.build_parser().parse_args(
        ["smoke", "--matrix", "m.toml", "--image", "i.img", "--variant", "a", "--variant", "b", "--json", "o.json", "--junit", "o.xml"]
    )
    assert args.command == "smoke"
    assert args.matrix == "m.toml"
    assert args.image == "i.img"
    assert args.variant == ["a", "b"]
    assert args.json_out == "o.json" and args.junit_out == "o.xml"
    assert args.dry_run is False


def test_smoke_parser_defaults():
    args = cli.build_parser().parse_args(["smoke"])
    assert args.matrix is None
    assert args.image is None
    assert args.variant is None
    assert args.dry_run is False
    # The matrix's autologin check needs the prompt that follows the login line,
    # so the settle window is on by default and --settle can retune it.
    assert args.settle == cli.DEFAULT_SETTLE
    args = cli.build_parser().parse_args(["smoke", "--settle", "0"])
    assert args.settle == 0.0


def test_boot_parser_settle_default_and_override():
    args = cli.build_parser().parse_args(["boot", "i.img"])
    assert args.settle == cli.DEFAULT_SETTLE
    args = cli.build_parser().parse_args(["boot", "i.img", "--settle", "1.5"])
    assert args.settle == 1.5

# --- the 26.10.3 findings: a split console line, and a login that was not ----
# Both fixtures are derived from the real Core capture, not typed out: the
# interleaved one has a real kernel line injected at the write boundary before
# the unit name, the autologin one is cut at the login line with the
# 'Permission denied' loop appended.

CORE_INTERLEAVED_LOG = FIXTURES / "serial_core_interleaved_network.log"
CORE_AUTOLOGIN_DENIED_LOG = FIXTURES / "serial_core_autologin_denied.log"


def _check(matrix, check_id):
    return next(c for c in matrix.checks if c.id == check_id)


def test_network_target_survives_a_kernel_message_splitting_its_line():
    """systemd writes a status line in pieces and a kernel message can land
    between them; that is what made network-target flake once on 26.10.3
    (Core, uefi-tpm)."""
    check = _check(smoke.load_matrix(), "network-target")
    log_text = CORE_INTERLEAVED_LOG.read_text(encoding="utf-8", errors="replace")

    # The single-line pattern this check used to carry misses the split log;
    # the shipped one must not.
    single_line = r"Reached target (?:\u001b\[[0-9;]*m)?Network(?:\u001b\[[0-9;]*m)?\."
    assert re.search(single_line, log_text) is None
    result = run_assertions(log_text, (check.to_assertion(),))[0]
    assert result.passed is True
    # The evidence shows the injected line, so a reader can see what was joined.
    assert "NIC Link is Up" in result.evidence


def test_network_target_refuses_the_look_alike_targets():
    """Bounded gaps must not turn the check into 'the word Network appears
    somewhere near a target line'. These three all appear in a real Core boot."""
    check = _check(smoke.load_matrix(), "network-target")
    look_alikes = {
        "Preparation": (
            "Reached target \x1b[0;1;39mPreparation for Network\x1b[0m.\n\n"
            "         Starting \x1b[0;1;39mRemount Root and Kernel File Systems\x1b[0m...\n\n"
            "         Starting \x1b[0;1;39mNetwork Name Resolution\x1b[0m...\n\n"
        ),
        "Host and Network Name Lookups": (
            "Reached target \x1b[0;1;39mHost and Network Name Lookups\x1b[0m.\n\n"
            "Reached target \x1b[0;1;39mSwaps\x1b[0m.\n"
        ),
        "Network is Online": (
            "Reached target \x1b[0;1;39mNetwork is Online\x1b[0m.\n\n"
            "         Starting \x1b[0;1;39mSomething\x1b[0m...\n"
        ),
    }
    for label, log_text in look_alikes.items():
        assert run_assertions(log_text, (check.to_assertion(),))[0].passed is False, label


def test_core_matrix_still_passes_a_boot_with_a_split_console_line():
    """The split must cost the one check that was flaking, nothing else."""
    matrix = smoke.load_matrix()
    outcome = smoke.run_matrix(matrix, "img", boot_fn=_boot_from(CORE_INTERLEAVED_LOG))
    assert outcome.passed is True, [v.failed_checks for v in outcome.variants]


def test_core_matrix_rejects_a_login_that_never_produced_a_shell():
    """26.10.3: the automatic-login line printed, then 'Permission denied' on a
    loop, and every other check passed. reached-login is not the fact that can
    catch this; autologin-shell and no-login-denied are."""
    matrix = smoke.load_matrix()
    outcome = smoke.run_matrix(
        matrix, "img", boot_fn=_boot_from(CORE_AUTOLOGIN_DENIED_LOG)
    )
    assert outcome.passed is False
    for variant in outcome.variants:
        assert set(variant.failed_checks) == {
            "autologin-shell", "no-login-denied"
        }, variant.failed_checks
        assert "reached-login" not in variant.failed_checks


def test_server_matrix_requires_the_servers_own_autologin_shell():
    """The autologin fact is anchored on the edition's own login line, so a Core
    boot cannot satisfy the Server matrix's copy of it (and a Server boot can)."""
    matrix = smoke.load_matrix(str(SERVER_MATRIX))
    check = _check(matrix, "autologin-shell")
    core_log = CORE_UEFI_LOG.read_text(encoding="utf-8", errors="replace")
    assert run_assertions(core_log, (check.to_assertion(),))[0].passed is False
    server_log = SERVER_UEFI_LOG.read_text(encoding="utf-8", errors="replace")
    assert run_assertions(server_log, (check.to_assertion(),))[0].passed is True


SERVER_STARSHIP_LOG = Path(__file__).parent / "fixtures" / "serial_login_server_starship.log"


def test_server_autologin_shell_accepts_the_starship_prompt():
    """26.10.27's release run: a healthy Server image whose root console shows
    the comfort shell's starship prompt (`root ~ ❯`, after ~500 bytes of systemd
    OSC 3008 context sequences) failed autologin-shell, which only knew `# `.
    The real serial log is the fixture; the old plain-`# ` boot still passes and
    a login that never produced a shell still fails (test above)."""
    matrix = smoke.load_matrix(str(SERVER_MATRIX))
    check = _check(matrix, "autologin-shell")
    log = SERVER_STARSHIP_LOG.read_text(encoding="utf-8", errors="replace")
    assert "\u276f" in log and "# " not in log.split("(automatic login)")[-1]
    assert run_assertions(log, (check.to_assertion(),))[0].passed is True
    old = SERVER_UEFI_LOG.read_text(encoding="utf-8", errors="replace")
    assert run_assertions(old, (check.to_assertion(),))[0].passed is True
    outcome = smoke.run_matrix(matrix, "img", boot_fn=_boot_from(SERVER_STARSHIP_LOG))
    assert outcome.passed is True, [v.failed_checks for v in outcome.variants]
