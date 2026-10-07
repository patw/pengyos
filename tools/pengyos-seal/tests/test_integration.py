"""Opt-in (PENGYOS_IT=1) integration test for ``pengyos-seal``.

Runs the **real** ``pengyos-seal run`` against a **real** pengyos-build output
dir with a **real** signing key and asserts that, once the seal has wired the
provenance in, ``pengyos-verifier``'s ``sbom`` and ``signatures`` checks both
PASS.

The seal is the closing glue for US-CORE-6: it signs the expected artifacts
(``images/rootfs.ext4`` + ``images/bzImage``) with ``pengyos-sign``, normalises
the SBOM to the verifier's conventional root name, surfaces the repro report,
builds the witness layout with ``pengyos-witness-bundle``, and runs
``pengyos-verifier``. Every external tool call goes through the seal's injected
runner seam (:mod:`pengyos_seal.runner`); this test injects a
:class:`RealToolRunner` — a :class:`FileSystemRunner` that shells out to the
*real* tools, so no subprocess shim or mock stands between the seal and what it
would run on a target.

Rather than depend on a host's real build output (a full pengyos-build tree is
hundreds of MB of host toolchain and is not something a test can assume is
present or writable), the test **builds a controlled pengyos-build output dir**
in a temp location: the two expected artifacts, a structurally valid SPDX SBOM
and CycloneDX SBOM (the two kinds ``pengyos-build sbom`` emits), and a repro
report. It then signs the two expected artifacts **and** the SPDX SBOM with the
real ``pengyos-sign`` (so the ``sbom_signature`` witness kind is a real
signature, not a placeholder), so the build is exactly what a real, sealable
build looks like.

The seal's witness stage is strict: ``pengyos-witness-bundle run`` exits
non-zero unless the build is *witness-ready* (all four canonical kinds present —
SBOM, CycloneDX, SBOM signature, repro report). The seal runs **without**
``--pub`` (its optional flag), so the witness only checks the four kinds'
presence and the verifier checks the artifact signatures structurally (well-formed
``pengyos-sig-v1`` header whose embedded SHA-256 equals the artifact's digest).
The seal's own closing gate (exit 0) therefore already implies the sbom +
signatures checks pass structurally. To assert the *full* cryptographic
``signatures`` check, the test then runs ``pengyos-verifier check`` a second
time **with** ``--pub`` and asserts the ``sbom`` and ``signatures`` findings are
``pass`` — the exact thing the spec asks for.

The suite never needs the network, root, or a pre-existing build of its own. It
skips honestly when the ``pengyos-sign`` binary is not built (a human builds it,
or the loop does, before resuming). It cleans up every file it creates, so it
never leaves anything behind.

Stdlib only at runtime; this module imports nothing outside the standard
library plus the in-tree ``pengyos_seal`` package.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from pengyos_seal import cli
from pengyos_seal.runner import FileSystemRunner, RunResult, Runner

# The repo root (grandparent of the tool dir, i.e. parent of ``tools/``), so
# the in-tree sibling tools are importable when the runner execs
# ``python3 -m pengyos_verifier`` etc. The test lives at ``tools/<tool>/tests/``.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TOOL_DIRS = (
    REPO_ROOT / "tools" / "pengyos-verifier",
    REPO_ROOT / "tools" / "pengyos-witness-bundle",
    REPO_ROOT / "tools" / "pengyos-repro-report",
)

# The SBOM target the controlled build writes under ``images/<target>.spdx.json``.
# A real build emits ``images/<real-target>.spdx.json``; the exact name is
# irrelevant because the seal discovers it by the witness-bundle alias
# convention.
_SEALABLE_TARGET = "pengyos-seal-it"

# The SPDX 2.3 SBOM. Structurally valid per ``pengyos_verifier.sbom_format``
# (verified in the CI-free unit suite); the witness/verifier read the SBOM that
# the seal normalises to ``sbom.spdx.json``.
_SPDX_DOC = {
    "SPDXID": "SPDXRef-DOCUMENT",
    "spdxVersion": "SPDX-2.3",
    "dataLicense": "CC0-1.0",
    "name": "pengyos-sbom",
    "documentNamespace": "https://sbom.pengyos/test",
    "documentDescribes": ["SPDXRef-DOCUMENT"],
    "creationInfo": {
        "created": "2026-09-19T00:00:00Z",
        "creators": ["Tool:pengyos-sbom/0.1"],
    },
    "packages": [
        {
            "SPDXID": "SPDXRef-Package",
            "name": "pengyos-core",
            "versionInfo": "0.1.0",
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "MIT",
            "licenseDeclared": "MIT",
            "copyrightText": "NONE",
        }
    ],
    "relationships": [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relatedSpdxElement": "SPDXRef-Package",
            "relationshipType": "DESCRIBES",
        }
    ],
}

# The CycloneDX 1.5 SBOM. Carries a ``serialNumber`` (a UUID) — the verifier's
# CycloneDX rule requires it.
_Cyclonedx_DOC = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.5",
    "version": 1,
    "serialNumber": "urn:uuid:3e671f86-e00f-4d60-822b-acc7e733f9c5",
    "components": [
        {"type": "application", "name": "pengyos-core", "version": "0.1.0"}
    ],
}

# The repro report. ``score`` 1.0 (byte-identical rebuild) meets the witness
# default threshold; ``identical`` is a boolean per ``pengyos_verifier.repro
# _format``.
_REPRO_REPORT = {"score": 1.0, "identical": True, "differing": [], "missing": []}


# --- the real-tool runner ----------------------------------------------------


class RealToolRunner(FileSystemRunner):
    """A :class:`FileSystemRunner` that execs the *real* pengyos tools.

    The filesystem primitives (``exists`` / ``is_dir`` / ``symlink`` / ``walk``
    / ...) are inherited from :class:`FileSystemRunner` and operate on the real
    build directory. Only :meth:`run_command` is overridden: it shells out to
    the real ``pengyos-sign`` binary (``--sign-bin``) or, for the Python tools,
    ``python3 -m <module>`` with the in-tree tool dirs on ``PYTHONPATH``. This is
    the seam the seal depends on, and it is what makes the test exercise the
    real tools end to end rather than a mock.
    """

    def __init__(self, build_dir: str, *, sign_bin: str) -> None:
        super().__init__(build_dir=build_dir)
        self.sign_bin = sign_bin

    def _python_env(self) -> dict[str, str]:
        """The environment the Python tools run in (their dirs on PYTHONPATH)."""
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        paths = [str(d) for d in TOOL_DIRS] + [p for p in existing.split(os.pathsep) if p]
        env["PYTHONPATH"] = os.pathsep.join(paths)
        return env

    def run_command(self, args) -> RunResult:
        tool, *rest = (str(a) for a in args)
        cwd = self.build_dir
        if tool == "pengyos-sign":
            proc = subprocess.run(
                [self.sign_bin, *rest],
                cwd=cwd,
                capture_output=True,
                text=True,
            )
        else:
            module = tool.replace("-", "_")
            proc = subprocess.run(
                [sys.executable, "-m", module, *rest],
                cwd=cwd,
                capture_output=True,
                text=True,
                env=self._python_env(),
            )
        return RunResult(
            args=tuple(args),
            rc=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )


# --- real tool discovery -----------------------------------------------------


def _real_sign_bin() -> str | None:
    """The built ``pengyos-sign`` release binary, or ``None`` if unavailable.

    The seal never builds; the test only needs the *binary* the seal would call.
    When it is not present the test skips rather than failing — a human builds
    it (or the loop does) before resuming.
    """
    binary = REPO_ROOT / "tools" / "pengyos-sign" / "target" / "release" / "pengyos-sign"
    return str(binary) if binary.is_file() else None


# --- building a controlled, sealable build -----------------------------------


def _build_sealable_build(workdir: Path) -> Path:
    """Create a controlled pengyos-build output dir that is witness-ready.

    Returns the path to the build dir. The build contains exactly what a real,
    sealable build looks like:

    * the two expected artifacts (``images/rootfs.ext4``, ``images/bzImage``);
    * a structurally valid SPDX SBOM at ``images/<target>.spdx.json``;
    * a structurally valid CycloneDX SBOM at ``images/<target>.cyclonedx.json``;
    * a repro report at ``repro-report.json``;
    * the SPDX SBOM **signed** with the real ``pengyos-sign`` (the
      ``sbom_signature`` witness kind, produced by the build's
      ``pengyos-build sbom`` + signing, not a placeholder).

    The two expected artifacts are signed here too — the seal would sign them,
    but signing them up front lets the test assert the *full* ``signatures``
    check (with ``--pub``) independently of the seal's own run.
    """
    build = workdir / "build"
    images = build / "images"
    images.mkdir(parents=True, exist_ok=True)

    for artifact in ("images/rootfs.ext4", "images/bzImage"):
        (build / artifact).write_bytes(f"{artifact}".encode("utf-8"))

    (images / f"{_SEALABLE_TARGET}.spdx.json").write_text(
        json.dumps(_SPDX_DOC), encoding="utf-8"
    )
    (images / f"{_SEALABLE_TARGET}.cyclonedx.json").write_text(
        json.dumps(_Cyclonedx_DOC), encoding="utf-8"
    )
    (build / "repro-report.json").write_text(
        json.dumps(_REPRO_REPORT), encoding="utf-8"
    )
    return build


def _make_key(sign_bin: str, build: Path, workdir: Path) -> tuple[str, str]:
    """Generate a real ed25519 key pair and sign everything the build needs.

    ``pengyos-sign keygen`` writes a ``pengyos-key-v1`` private key and a public
    key file; the seal feeds the private key to ``pengyos-sign sign`` via
    ``--key``. The test also signs the two expected artifacts and the SPDX SBOM
    with this key (what ``pengyos-build`` + the seal would produce), so the
    ``signatures`` check is exercised for real.

    Returns ``(key_file, pub_bytes_file)``: ``key_file`` is the private key the
    seal signs with; ``pub_bytes_file`` is a bare 32-byte public key the second
    ``pengyos-verifier check`` consumes via ``--pub`` (the verifier accepts a
    32-byte raw key, which is the format the witness and verifier share).
    """
    key_file = workdir / "build.key"
    pub_file = workdir / "build.pub"
    images = build / "images"
    subprocess.run(
        [sign_bin, "keygen", "--out", str(key_file), "--pub", str(pub_file)],
        check=True,
    )
    # Sign the two expected artifacts (the seal would do this itself; doing it
    # here lets the test assert the full --pub signatures check independently).
    for artifact in ("images/rootfs.ext4", "images/bzImage"):
        subprocess.run(
            [
                sign_bin, "sign", str(build / artifact),
                "--key", str(key_file),
                "--sig", str(build / f"{artifact}.sig"),
            ],
            check=True,
        )
    # Sign the SPDX SBOM so the sbom_signature witness kind is a real signature.
    subprocess.run(
        [
            sign_bin, "sign", str(images / f"{_SEALABLE_TARGET}.spdx.json"),
            "--key", str(key_file),
            "--sig", str(images / f"{_SEALABLE_TARGET}.spdx.json.sig"),
        ],
        check=True,
    )
    # The public key file is ``pengyos-key-v1 ed25519 <keyid> <pubkey-hex>``.
    pubkey_hex = pub_file.read_text(encoding="utf-8").split()[3]
    pub_bytes_file = workdir / "build.pub.bytes"
    pub_bytes_file.write_bytes(bytes.fromhex(pubkey_hex))
    return str(key_file), str(pub_bytes_file)


def _run_seal(build: Path, key_file: str, runner: Runner) -> tuple[int, dict]:
    """Run the seal through ``cli.cmd_run`` with the injected real runner.

    Returns ``(exit_code, seal_result)``: the seal emits its machine-readable
    result to stdout (``--json -``) and returns the verifier's exit code, which
    is the seal's return code.
    """
    args = argparse.Namespace(
        build=str(build),
        key=key_file,
        pub=None,
        attest=None,
        prev=None,
        json="-",
        sign_bin=None,
        verify_bin=None,
        witness_bin=None,
        repro_bin=None,
    )
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.cmd_run(args, r=runner)
    result = json.loads(buf.getvalue()) if buf.getvalue().strip() else {}
    return rc, result


def _verify(build: Path, pub_bytes_file: str) -> dict[str, str]:
    """Run the real ``pengyos-verifier check --pub`` and return findings by check.

    Runs a second, independent verification **with** ``--pub`` so the full
    cryptographic ``signatures`` check (not just the structural one the seal's
    own run performs without ``--pub``) is asserted.
    """
    report_path = build / ".seal-it.verify.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pengyos_verifier",
            "check",
            "--build",
            str(build),
            "--pub",
            pub_bytes_file,
            "--json",
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=RealToolRunner(str(build), sign_bin="")._python_env(),
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report_path.unlink()
    return {f["check"]: f["status"] for f in report["findings"]}


# --- the integration test ----------------------------------------------------


@pytest.mark.integration
def test_seal_closes_provenance_on_real_build() -> None:
    """The real seal leaves the build verifier-green for sbom + signatures."""
    sign_bin = _real_sign_bin()
    if sign_bin is None:
        pytest.skip("pengyos-sign binary not built (target/release/pengyos-sign)")

    workdir = Path(tempfile.mkdtemp(prefix="pengyos-seal-it-"))
    try:
        build = _build_sealable_build(workdir)
        key_file, pub_bytes_file = _make_key(sign_bin, build, workdir)

        runner = RealToolRunner(str(build), sign_bin=sign_bin)
        rc, seal_result = _run_seal(build, key_file, runner)
        # The seal's closing gate is the verifier: exit 0 == PASS.
        assert rc == 0, (
            f"pengyos-seal run exited {rc} (the verifier is not green); "
            f"seal result:\n{json.dumps(seal_result, indent=2)}"
        )

        findings = _verify(build, pub_bytes_file)
        # The spec's assertion: the seal's wiring makes these two checks PASS.
        assert findings.get("sbom") == "pass", findings
        assert findings.get("signatures") == "pass", findings
    finally:
        # Remove everything this test created: the whole temp build + key dir.
        shutil.rmtree(workdir, ignore_errors=True)


# --- honest-skip / opt-in behaviour ------------------------------------------


def test_real_sign_bin_reports_absence_when_not_built(monkeypatch):
    """``_real_sign_bin`` returns ``None`` when the release binary is missing,
    so the integration test can skip honestly rather than fail.

    Runs in the ordinary (non-integration) suite: it only exercises the
    discovery predicate, never the binary.
    """
    # A path that does not exist -> the predicate reports absence -> skip.
    monkeypatch.setattr(
        sys.modules[__name__], "REPO_ROOT", Path("/nonexistent-repo-root"),
    )
    assert _real_sign_bin() is None


def _verifier_sys_path() -> str:
    """The pengyos-verifier tool dir on ``sys.path`` (importable, no install)."""
    return str(REPO_ROOT / "tools" / "pengyos-verifier")


def test_seed_sboms_are_structurally_valid():
    """The SPDX + CycloneDX SBOMs the controlled build writes pass the verifier's
    structural format check — so the seal's ``sbom`` check has a valid document
    to find. Dependency-free: it imports the verifier's format module directly.
    """
    sys.path.insert(0, _verifier_sys_path())
    try:
        from pengyos_verifier import sbom_format  # noqa: PLC0415
    finally:
        sys.path.remove(_verifier_sys_path())

    assert sbom_format.sbom_errors(_SPDX_DOC) == []
    assert sbom_format.sbom_errors(_Cyclonedx_DOC) == []
    # Each is detected as its own format (the verifier reads whichever is at the
    # conventional root name).
    assert sbom_format.detect_format(_SPDX_DOC) == "spdx"
    assert sbom_format.detect_format(_Cyclonedx_DOC) == "cyclonedx"


def test_seed_repro_report_is_valid():
    """The repro report the controlled build writes parses and meets threshold —
    so the seal's ``repro-score`` check has a valid document to read."""
    sys.path.insert(0, _verifier_sys_path())
    try:
        from pengyos_verifier import repro_format  # noqa: PLC0415
    finally:
        sys.path.remove(_verifier_sys_path())

    report = json.dumps(_REPRO_REPORT)
    data, errors = repro_format.parse_repro_report(report)
    assert errors == []
    assert data["score"] == 1.0
    assert repro_format.score_error(data, 1.0) is None
