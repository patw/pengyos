"""uv on the host, and Python builds that need a compiler (2026-11-05).

Pengy's Server field review (R4) asked for uv on the host: the one-venv-per-app
recipe (USE-CASES US-SRV-10) needs it, and it came only from the dev guest's
installer in ~/.local/bin. Testing the recipe on behothinkpad (26.11.1) found the
second half: the host Python's sysconfig named the *build host's* cross compiler
(/data/build/.../x86_64-buildroot-linux-gnu-gcc), so a pip/uv install that had to
compile failed with "No such file or directory" for a path on another machine
instead of reaching the no-toolchain stub and its dev-guest recipe.

1. uv is a from-source cargo package, in Server and Desktop, not Core;
2. external.mk strips the cross prefix from sysconfig, and Server's post-build
   refuses an image that still has it (without tripping on FREEZE_MODULE or the
   stdlib's own -L paths, which keep the build host's dir);
3. the gcc stub prints the wheel recipe for the host's Python, other stubs do not;
4. pengyos.txt has the recipe exactly when the image has uv, dx and a Python.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "buildroot-external"
sys.path.insert(0, str(Path(__file__).parent))
import test_pengyos_txt as T  # noqa: E402  (its fake-tree helpers)


def test_uv_package_from_source():
    mk = (EXT / "package/uv/uv.mk").read_text()
    assert re.search(r"^UV_VERSION = \d+\.\d+\.\d+$", mk, re.M)
    assert "UV_SITE = $(call github,astral-sh,uv,$(UV_VERSION))" in mk
    assert "UV_SUBDIR = crates/uv" in mk
    assert "$(eval $(cargo-package))" in mk
    assert "self-update" not in re.sub(r"#.*", "", mk)  # never enabled
    assert (EXT / "package/uv/uv.hash").is_file()
    assert 'package/uv/Config.in"' in (EXT / "Config.in").read_text()


def test_uv_in_server_and_desktop_not_core():
    cfg = lambda n: (EXT / f"configs/pengyos_{n}_defconfig").read_text()
    for n in ("server", "server_nvidia", "desktop", "desktop_nvidia"):
        assert "BR2_PACKAGE_UV=y" in cfg(n), n
    assert "BR2_PACKAGE_UV=y" not in cfg("core")
    req = (EXT / "board/server/features-required.txt").read_text()
    assert re.search(r"^exec\s+usr/bin/uv\s", req, re.M)
    assert re.search(r"^exec\s+usr/bin/uvx\s", req, re.M)


SYSCONFIG = """build_time_vars = {'AR': '/B/host/bin/x86_64-buildroot-linux-gnu-gcc-ar',
'BLDSHARED': '/B/host/bin/x86_64-buildroot-linux-gnu-gcc -shared',
'CC': '/B/host/bin/x86_64-buildroot-linux-gnu-gcc',
'CXX': '/B/host/bin/x86_64-buildroot-linux-gnu-g++',
'FREEZE_MODULE': '/B/host/bin/python3 ./Programs/_freeze_module.py',
'LDCXXSHARED': '/B/host/bin/x86_64-buildroot-linux-gnu-g++ -shared',
'MODULE_ZLIB_LDFLAGS': '-L/B/host/bin/../x86_64-buildroot-linux-gnu/sysroot/usr/lib -lz',
'INSTALL': '/usr/bin/install -c'}
"""


def _hook_sed() -> str:
    mk = (EXT / "external.mk").read_text()
    m = re.search(r"define PYTHON3_TARGET_TOOLCHAIN_NAMES\n\tsed -i '([^']+)'", mk)
    assert m and "PYTHON3_POST_INSTALL_TARGET_HOOKS += PYTHON3_TARGET_TOOLCHAIN_NAMES" in mk
    return m.group(1).replace("$(HOST_DIR)", "/B/host").replace("$(GNU_TARGET_NAME)", "x86_64-buildroot-linux-gnu")


def _guard(tmp: Path, text: str) -> subprocess.CompletedProcess:
    """Server post-build's sysconfig block, run alone on a fake tree."""
    body = (EXT / "board/server/post-build.sh").read_text()
    m = re.search(r"\n(for f in \"\$\{TARGET_DIR\}\"/usr/lib/python3\*/_sysconfigdata_.*?\ndone)\n", body, re.S)
    assert m, "the post-build guard"
    t = tmp / "target"
    (t / "usr/lib/python3.14").mkdir(parents=True)
    (t / "usr/lib/python3.14/_sysconfigdata__linux_x86_64-linux-gnu.py").write_text(text)
    return subprocess.run(["bash", "-c", m.group(1)], capture_output=True, text=True,
                          env={**os.environ, "TARGET_DIR": str(t), "HOST_DIR": "/B/host"})


def test_sysconfig_hook_and_guard(tmp_path):
    fixed = subprocess.run(["sed", _hook_sed()], input=SYSCONFIG, capture_output=True,
                           text=True, check=True).stdout
    vars_ = eval(fixed.split("=", 1)[1])  # noqa: S307 - our own literal
    assert vars_["CC"] == "gcc" and vars_["CXX"] == "g++" and vars_["AR"] == "gcc-ar"
    assert vars_["BLDSHARED"] == "gcc -shared" and vars_["LDCXXSHARED"] == "g++ -shared"
    assert vars_["FREEZE_MODULE"].startswith("/B/host/bin/python3")  # untouched
    assert vars_["INSTALL"] == "/usr/bin/install -c"
    assert _guard(tmp_path / "bad", SYSCONFIG).returncode == 1
    assert _guard(tmp_path / "good", fixed).returncode == 0


def test_guard_runs_before_pengyos_txt():
    body = (EXT / "board/server/post-build.sh").read_text()
    assert body.index("_sysconfigdata_") < body.index("pengyos-txt.py") < body.index("check-needed-libs.py")


def _stub(tmp: Path, name: str, with_uv: bool) -> str:
    bin_ = tmp / "bin"
    bin_.mkdir(parents=True)
    for tool in ["pengyos-guest"] + (["uv"] if with_uv else []):
        (bin_ / tool).write_text("#!/bin/sh\n")
        (bin_ / tool).chmod(0o755)
    (bin_ / "python3").symlink_to(sys.executable)
    (bin_ / name).symlink_to(EXT / "board/core/tool-stubs/pengyos-no-toolchain")
    r = subprocess.run([str(bin_ / name)], capture_output=True, text=True,
                       env={"PATH": f"{bin_}:/usr/bin:/bin"})
    assert r.returncode == 127 and r.stdout == ""
    assert r.stderr.rstrip().endswith("How this system works: /usr/share/pengyos.txt")
    return r.stderr


def test_gcc_stub_prints_the_wheel_recipe(tmp_path):
    ver = "%d.%d" % sys.version_info[:2]
    err = _stub(tmp_path, "gcc", with_uv=True)
    assert f"dx uvx --python {ver} pip wheel --no-deps -w ~/wheels PKG==VERSION" in err
    assert "uv pip install --find-links ~/wheels PKG" in err
    assert err.count("How this system works") == 1
    assert "pip wheel" not in _stub(tmp_path / "make", "make", with_uv=True)
    assert "pip wheel" not in _stub(tmp_path / "nouv", "gcc", with_uv=False)


def _server(tmp: Path, uv: bool, python: bool) -> str:
    tools = T.SERVER_TOOLS + (["uv"] if uv else [])
    extra = ["usr/lib/python3.14/os.pyc"] if python else []
    return T.run(T.tree(tmp, "Server", "server", tools, stubs=["cc", "gcc"], extra=extra))


def test_pengyos_txt_recipe_follows_the_tree(tmp_path):
    txt = _server(tmp_path / "a", uv=True, python=True)
    assert "dx uvx --python 3.14 pip wheel --no-deps -w ~/wheels PKG==VERSION" in txt
    assert "dx uv build --wheel --python 3.14 --out-dir ~/wheels" in txt
    assert "uv pip install --find-links ~/wheels PKG" in txt
    assert all(len(l) <= 80 for l in txt.splitlines())
    assert "pip wheel" not in _server(tmp_path / "b", uv=False, python=True)
    assert "pip wheel" not in _server(tmp_path / "c", uv=True, python=False)
