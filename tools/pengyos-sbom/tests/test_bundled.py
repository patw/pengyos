"""Bundled crates and Go modules (pengyos_sbom.bundled) in both documents."""

import json
from pathlib import Path

from pengyos_sbom import bundled
from pengyos_sbom.cyclonedx import build_cyclonedx_document
from pengyos_sbom.model import Bundled, Component, IngestResult
from pengyos_sbom.spdx import build_spdx_document
from pengyos_sbom.validate import validate_document

LOCK = """version = 4

[[package]]
name = "cosmic-files"
version = "1.0.0"

[[package]]
name = "zip"
version = "8.6.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "00"

[[package]]
name = "libcosmic"
version = "0.1.0"
source = "git+https://github.com/pop-os/libcosmic?rev=abc#abc"
"""

OLD_LOCK = LOCK.replace("8.6.0", "0.5.0")


def _tree(tmp_path: Path) -> Path:
    build = tmp_path / "build"
    pkg = build / "cosmic-ee5f"
    (pkg / "cosmic-files").mkdir(parents=True)
    (pkg / "cosmic-files" / "Cargo.lock").write_text(LOCK)
    (pkg / "target").mkdir()  # cargo ran (one shared target dir, as COSMIC's)
    (pkg / "target" / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")
    # A C package's optional Rust part that Buildroot never builds: no target/.
    (build / "qemu-11.0.0" / "rust").mkdir(parents=True)
    (build / "qemu-11.0.0" / "rust" / "Cargo.lock").write_text(LOCK)
    (build / "qemu-11.0.0" / "target" / "arm").mkdir(parents=True)  # source, not cargo
    # The compiler's libgo sources.
    (build / "gcc-final-15.3.0" / "libgo" / "go" / "vendor").mkdir(parents=True)
    (build / "gcc-final-15.3.0" / "libgo" / "go" / "vendor" / "modules.txt").write_text(
        "# golang.org/x/net v0.0.1\n")
    # A vendored crate's own lockfile: what its authors built, not what we did.
    (pkg / "cosmic-files" / "vendor" / "zip-8.6.0").mkdir(parents=True)
    (pkg / "cosmic-files" / "vendor" / "zip-8.6.0" / "Cargo.lock").write_text(OLD_LOCK)
    go = build / "podman-v5.7.0" / "vendor"
    go.mkdir(parents=True)
    (go / "modules.txt").write_text(
        "# golang.org/x/crypto v0.43.0\n## explicit; go 1.24\ngolang.org/x/crypto/ssh\n"
        "# github.com/docker/docker v28.5.1+incompatible\n")
    return build


def _components():
    return (Component(name="cosmic", scope="target", version="ee5f"),
            Component(name="podman", scope="target", version="v5.7.0"),
            Component(name="zlib", scope="target", version="1.3.1"),
            Component(name="qemu", scope="target", version="11.0.0"),
            Component(name="gcc-final", scope="target", version="15.3.0"),
            Component(name="cosmic", scope="host", version="ee5f"))


def test_scan_reads_own_lockfiles_only(tmp_path):
    found = bundled.scan_build_dir(_tree(tmp_path), _components())
    assert found["cosmic"] == (Bundled("cargo", "zip", "8.6.0"),), \
        "crates.io only, and not the vendored crate's own Cargo.lock"
    assert found["podman"] == (Bundled("golang", "github.com/docker/docker", "v28.5.1+incompatible"),
                               Bundled("golang", "golang.org/x/crypto", "v0.43.0"))
    assert "zlib" not in found
    assert "qemu" not in found, "a Cargo.lock cargo never built from"
    assert "gcc-final" not in found


def test_documents_carry_bundled_deps_and_stay_valid(tmp_path):
    found = bundled.scan_build_dir(_tree(tmp_path), _components())
    comps = tuple(Component(name=c.name, scope=c.scope, version=c.version,
                            bundled=found.get(c.name, ()) if c.scope == "target" else ())
                  for c in _components())
    result = IngestResult(components=comps)
    cdx = build_cyclonedx_document(result, created="2026-11-12T00:00:00+00:00")
    cos = next(c for c in cdx["components"] if c["bom-ref"] == "pkg-target-cosmic")
    assert cos["components"][0]["purl"] == "pkg:cargo/zip@8.6.0"
    pod = next(c for c in cdx["components"] if c["bom-ref"] == "pkg-target-podman")
    assert "pkg:golang/golang.org/x/crypto@v0.43.0" in [d["purl"] for d in pod["components"]]
    assert validate_document(json.loads(json.dumps(cdx)))[1] == []

    spdx = build_spdx_document(result, created="2026-11-12T00:00:00+00:00")
    zipp = next(p for p in spdx["packages"] if p["name"] == "zip")
    assert zipp["externalRefs"][0]["referenceLocator"] == "pkg:cargo/zip@8.6.0"
    assert {"spdxElementId": "SPDXRef-Package-target-cosmic", "relationshipType": "CONTAINS",
            "relatedSpdxElement": zipp["SPDXID"]} in spdx["relationships"]
    assert validate_document(spdx)[1] == []
    # A different dependency set is a different document.
    bare = build_cyclonedx_document(IngestResult(components=_components()),
                                    created="2026-11-12T00:00:00+00:00")
    assert bare["serialNumber"] != cdx["serialNumber"]
