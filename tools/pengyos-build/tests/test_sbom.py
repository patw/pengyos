"""Tests for the SBOM step (pengyos_build.sbom)."""

import subprocess
from pathlib import Path

from pengyos_build.acquire import source_dir
from pengyos_build.configure import output_dir
from pengyos_build.sbom import SbomResult, _sbom_tool, generate_sbom
from pengyos_build.targets import Target


def _target():
    return Target(
        name="pengyos-core",
        br_version="2026.08",
        external_tree=Path("buildroot-external"),
        defconfig="pengyos_core_defconfig",
        board="",
    )


def _layout(tmp_path):
    root = tmp_path / "build"
    t = _target()
    source_dir(root, t.br_version).mkdir(parents=True)
    out = output_dir(root, t)
    out.mkdir(parents=True)
    (out / ".config").write_text("BR2_x86_64=y\n")
    return root, t, out


def _fake_make(out: Path, calls: dict):
    def fake_make(argv, env=None, stdout=None, stderr=None, text=None):
        goal = argv[-1]
        calls[goal] = calls.get(goal, 0) + 1
        if goal == "legal-info":
            (out / "legal-info").mkdir(exist_ok=True)
            (out / "legal-info" / "manifest.csv").write_text("name,version\nbusybox,1.38\n")
        if goal == "show-info":
            stdout.write('{"packages": []}\n')
        return subprocess.CompletedProcess(argv, 0)

    return fake_make


def _fake_tool(calls: dict):
    def fake_tool(argv, env=None, text=None, capture_output=None):
        sub = "generate" if "generate" in argv else "sign"
        calls[sub] = calls.get(sub, 0) + 1
        args = list(argv)

        def val(flag):
            return Path(args[args.index(flag) + 1])

        if sub == "generate":
            val("--out").write_text('{"spdxVersion": "SPDX-2.3"}')
            val("--cdx").write_text('{"bomFormat": "CycloneDX"}')
        else:  # sign
            Path(argv[argv.index("sign") + 1] + ".sig").write_text("sig")
        return subprocess.CompletedProcess(argv, 0)

    return fake_tool


def test_generate_sbom_emits_spdx_and_cdx(tmp_path):
    root, t, out = _layout(tmp_path)
    mcalls, tcalls = {}, {}

    res = generate_sbom(
        t, root,
        runner=_fake_make(out, mcalls),
        tool_runner=_fake_tool(tcalls),
        repo_root=None,
    )

    assert isinstance(res, SbomResult)
    assert res.spdx.is_file() and res.cdx.is_file()
    assert res.show_info.is_file()
    assert mcalls == {"legal-info": 1, "show-info": 1}
    assert tcalls == {"generate": 1}
    assert res.signature is None
    assert not res.embedded


def test_generate_sbom_signs_when_key_given(tmp_path):
    root, t, out = _layout(tmp_path)
    tcalls = {}
    key = tmp_path / "seed.key"
    key.write_bytes(b"\x00" * 32)

    res = generate_sbom(
        t, root,
        sign_key=key,
        runner=_fake_make(out, {}),
        tool_runner=_fake_tool(tcalls),
    )

    assert tcalls.get("sign") == 1
    assert res.signature is not None
    assert res.signature.name == "pengyos-core.spdx.json.sig"
    assert res.signature.is_file()


def test_generate_sbom_errors_when_not_configured(tmp_path):
    root = tmp_path / "build"
    t = _target()
    source_dir(root, t.br_version).mkdir(parents=True)  # tree present, no output
    import pytest

    with pytest.raises(Exception):
        generate_sbom(t, root, runner=_fake_make(tmp_path, {}), tool_runner=_fake_tool({}))


def test_sbom_tool_prefers_repo_root(tmp_path):
    repo = tmp_path / "repo"
    tool = repo / "tools" / "pengyos-sbom"
    (tool / "pengyos_sbom").mkdir(parents=True)

    prefix, tool_dir = _sbom_tool(repo)
    assert prefix[1:] == ["-m", "pengyos_sbom"]
    assert tool_dir == tool

    prefix2, tool_dir2 = _sbom_tool(None)
    assert prefix2 == ["pengyos-sbom"]
    assert tool_dir2 is None


def test_generate_sbom_removes_sboms_left_under_another_name(tmp_path):
    """post-build embeds every SBOM file in images/, so a renamed target must
    not keep shipping the old-named one next to the new."""
    root, t, out = _layout(tmp_path)
    images = out / "images"
    images.mkdir()
    stale = ["pengyos-core-m2.spdx.json", "pengyos-core-m2.spdx.json.sig",
             "pengyos-core-m2.cdx.json", "pengyos-core-m2.show-info.json"]
    for name in stale:
        (images / name).write_text("old")
    (images / "bzImage").write_text("kernel")

    res = generate_sbom(t, root, runner=_fake_make(out, {}),
                        tool_runner=_fake_tool({}), repo_root=None)

    assert res.spdx.is_file()
    for name in stale:
        assert not (images / name).exists(), name
    assert (images / "bzImage").is_file()
