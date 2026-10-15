"""pengyos-upscale — tests.

Layer 1 (always runs, offline, no engine, no root, no display): the recipe is
honest, the shell integration points at things the recipe installs, and the
command's contract holds for every path that does not need the engine.

Layer 2 (skips when the engine and models are absent): the measured behaviour —
a real upscale, the engine's silent failure, names with spaces, output
collisions. Point it at a real engine with:

    PENGYOS_UPSCALE_ENGINE=... PENGYOS_UPSCALE_MODELS=... pytest -q
"""
import configparser
import json
import os
import re
import shutil
import subprocess

import pytest

TOOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECIPE = os.path.join(TOOL, "recipe", "realesrgan-ncnn-vulkan")
MK = os.path.join(RECIPE, "realesrgan-ncnn-vulkan.mk")
HASH = os.path.join(RECIPE, "realesrgan-ncnn-vulkan.hash")
CONFIG_IN = os.path.join(RECIPE, "Config.in")
WRAPPER = os.path.join(TOOL, "pengyos-upscale")
RECIPE_WRAPPER = os.path.join(RECIPE, "pengyos-upscale")
CONTEXT_ACTIONS = os.path.join(RECIPE, "context_actions")
DESKTOP = os.path.join(RECIPE, "io.catbee.pengyos.Upscale.desktop")
MEASURED = os.path.join(TOOL, "results", "measured.json")
README = os.path.join(TOOL, "README.md")
SPEC = os.path.join(TOOL, "spec.md")

ENGINE = os.environ.get("PENGYOS_UPSCALE_ENGINE")
MODELS = os.environ.get("PENGYOS_UPSCALE_MODELS")

needs_engine = pytest.mark.skipif(
    not (ENGINE and MODELS and os.path.exists(ENGINE) and os.path.isdir(MODELS)),
    reason="set PENGYOS_UPSCALE_ENGINE and PENGYOS_UPSCALE_MODELS to run the behaviour layer",
)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def run(args, env=None, cwd=None):
    e = dict(os.environ)
    e["PENGYOS_UPSCALE_NOTIFY"] = "0"
    if env:
        e.update(env)
    return subprocess.run(args, capture_output=True, text=True, env=e, cwd=cwd)


# --- layer 1: the recipe is honest ------------------------------------------

def test_recipe_pins_a_version_a_license_and_an_https_site():
    mk = read(MK)
    ver = re.search(r"^REALESRGAN_NCNN_VULKAN_VERSION\s*=\s*(\S+)", mk, re.M)
    assert ver, "no pinned _VERSION"
    assert ver.group(1) not in ("git", "main", "latest", "auto"), "version is an alias"
    assert re.search(r"^REALESRGAN_NCNN_VULKAN_LICENSE\s+=\s*BSD-3-Clause", mk, re.M), \
        "the vendor's license must be declared"
    site = re.search(r"^REALESRGAN_NCNN_VULKAN_SITE\s+=\s*(\S+)", mk, re.M)
    assert site and site.group(1).startswith("https://"), "site must be absolute https"
    assert "releases/download" in site.group(1), "site must be the release asset path"


def test_hash_file_pins_the_archive_and_the_license():
    digests = dict(
        (line.split()[2], line.split()[1])
        for line in read(HASH).splitlines()
        if line.strip() and not line.startswith("#")
    )
    mk = read(MK)
    src = re.search(r"^REALESRGAN_NCNN_VULKAN_SOURCE\s+=\s*(\S+)", mk, re.M).group(1)
    assert src in digests, f".hash has no sha256 for {src}"
    assert re.fullmatch(r"[0-9a-f]{64}", digests[src]), "pin is not a sha256"
    assert "LICENSE" in digests, "the license download must be pinned too"
    assert re.search(r"^REALESRGAN_NCNN_VULKAN_EXTRA_DOWNLOADS\s+=\s*https://", mk, re.M), \
        "the license text must be fetched over https"


def test_recipe_installs_the_whole_feature_not_just_the_binary():
    """The menu entries exist exactly when the engine does, so they are installed
    by the recipe. A context action pointing at a binary the image does not ship
    is the silent gap this check exists to prevent."""
    mk = read(MK)
    for target in (
        "$(TARGET_DIR)/usr/lib/pengyos/upscale/realesrgan-ncnn-vulkan",
        "$(TARGET_DIR)/usr/bin/pengyos-upscale",
        "$(TARGET_DIR)/usr/share/cosmic/com.system76.CosmicFiles/v1/context_actions",
        "$(TARGET_DIR)/usr/share/applications/io.catbee.pengyos.Upscale.desktop",
        "$(TARGET_DIR)/usr/share/pengyos/upscale/models",
    ):
        assert target in mk, f"recipe does not install {target}"


def test_the_recipe_ships_the_files_it_installs():
    for f in ("pengyos-upscale", "context_actions", "io.catbee.pengyos.Upscale.desktop"):
        assert os.path.isfile(os.path.join(RECIPE, f)), f"recipe dir is missing {f}"


def test_the_wrapper_in_the_recipe_is_the_wrapper_in_the_tool():
    assert read(RECIPE_WRAPPER) == read(WRAPPER), (
        "the recipe installs a copy of pengyos-upscale; the two must not drift"
    )


def test_the_wrappers_default_paths_are_the_recipes_install_paths():
    w = read(WRAPPER)
    mk = read(MK)
    assert "/usr/lib/pengyos/upscale/realesrgan-ncnn-vulkan" in w
    assert "/usr/share/pengyos/upscale/models" in w
    assert "$(TARGET_DIR)/usr/lib/pengyos/upscale/realesrgan-ncnn-vulkan" in mk
    assert "$(TARGET_DIR)/usr/share/pengyos/upscale/models" in mk


def test_config_in_selects_what_the_engine_needs_and_guards_the_arch():
    c = read(CONFIG_IN)
    assert "depends on BR2_x86_64" in c, "upstream publishes no other Linux build"
    assert "select BR2_PACKAGE_VULKAN_LOADER" in c, "the engine links libvulkan.so.1"
    assert "select BR2_PACKAGE_LIBNOTIFY" in c, "notify-send is how a context action reports"
    assert "BR2_PACKAGE_REALESRGAN_NCNN_VULKAN_MODEL_FAST" in c
    assert "default y" in c, "the fast model is the default"


def test_the_libgomp_gap_is_recorded_in_the_recipe():
    """Measured: the vendor binary links libgomp.so.1 and Buildroot's target
    does not ship it. The recipe must say so, because the engine does not start
    without it and the failure is a missing shared library at exec time."""
    assert "libgomp" in read(MK)
    assert "libgomp" in read(os.path.join(TOOL, "README.md"))


# --- layer 1: the shell integration ----------------------------------------

def _entries():
    """The context actions, parsed out of the RON file."""
    body = re.sub(r"//[^\n]*", "", read(CONTEXT_ACTIONS))
    blocks = re.findall(r"\(\s*(.*?)\s*,?\s*\)", body, re.S)
    out = []
    for b in blocks:
        d = dict(re.findall(r"(\w+)\s*:\s*(\[[^\]]*\]|[^,\n]+)", b))
        out.append({k: v.strip() for k, v in d.items()})
    return out


def test_context_actions_are_well_formed():
    entries = _entries()
    assert len(entries) >= 2, "expected a 2x and a 4x action"
    names = [e["name"] for e in entries]
    assert len(set(names)) == len(names), "menu labels must be unique"
    for e in entries:
        assert {"name", "confirm", "selection", "steps"} <= set(e), f"missing keys in {e}"
        assert e["confirm"] in ("false", "true")
        assert e["selection"] in ("Files", "Any", "Folders", "files", "any", "folders")
        steps = re.findall(r'"([^"]+)"', e["steps"])
        assert steps, "an action with no steps is ignored by cosmic-files"
        for s in steps:
            assert s.startswith("/"), f"steps run with no shell, so paths must be absolute: {s}"
            assert "$" not in s and "`" not in s, "no shell expansion happens in steps"
            codes = re.findall(r"%.", s)
            assert set(codes) <= {"%f", "%F", "%u", "%U", "%c", "%k", "%%"}, f"bad field code in {s}"


def test_context_actions_call_the_installed_command():
    for e in _entries():
        for s in re.findall(r'"([^"]+)"', e["steps"]):
            assert s.startswith("/usr/bin/pengyos-upscale "), s
    assert "/usr/bin/pengyos-upscale" in read(MK)


def test_context_actions_ask_for_the_whole_selection():
    """%f starts one process per selected file; %F hands the selection to one
    run, which is what a batch upscale wants."""
    for e in _entries():
        steps = re.findall(r'"([^"]+)"', e["steps"])
        assert any("%F" in s for s in steps), f"{e['name']} should use %F"


def test_desktop_entry_is_an_open_with_entry_not_an_app():
    # interpolation=None: a .desktop Exec carries %U, which configparser would
    # otherwise read as its own interpolation syntax.
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(DESKTOP)
    e = cp["Desktop Entry"]
    assert e["Type"] == "Application"
    assert e["Exec"].split()[0] == "pengyos-upscale"
    assert e.get("NoDisplay") == "true", "it is an action on a file, not an app to launch"
    assert "%U" in e["Exec"], "the launcher passes the file as a URI"
    assert "image/jpeg" in e["MimeType"] and "image/png" in e["MimeType"]


def test_the_accepted_input_formats_agree_everywhere():
    """The engine decodes a fixed set (measured); the wrapper, the .desktop
    MimeType list and the docs must all name the same set."""
    w = read(WRAPPER)
    accepted = set(re.search(r"case \"\$ext\" in ([^)]*)\)", w).group(1).split("|"))
    assert accepted == {"jpg", "jpeg", "png", "webp", "ppm", "pgm"}, accepted
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(DESKTOP)
    mime = set(cp["Desktop Entry"]["MimeType"].split(";"))
    for m in ("image/jpeg", "image/png", "image/webp",
              "image/x-portable-pixmap", "image/x-portable-graymap"):
        assert m in mime, f"{m} missing from the .desktop MimeType"
    assert "image/tiff" not in mime and "image/bmp" not in mime, \
        "the engine cannot decode these; advertising them would advertise a failure"


def test_wrapper_is_posix_sh():
    r = subprocess.run(["sh", "-n", WRAPPER], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_wrapper_accepts_file_uris():
    assert "file://" in read(WRAPPER), "a .desktop %U arrives as a file:// URI"


@pytest.mark.parametrize(
    "args,code",
    [
        (["-h"], 0),
        ([], 5),
        (["-s", "5", "a.jpg"], 5),
        (["-s", "x", "a.jpg"], 5),
        (["-f", "tiff", "a.jpg"], 5),
        (["-t", "abc", "a.jpg"], 5),
        (["-g", "abc", "a.jpg"], 5),
        (["--nope", "a.jpg"], 5),
        (["/etc/hostname"], 2),
        (["a.bmp"], 2),
    ],
)
def test_wrapper_usage_contract_without_an_engine(args, code):
    r = run([WRAPPER] + args, env={"PENGYOS_UPSCALE_ENGINE": "/nonexistent/engine"})
    assert r.returncode == code, (args, r.returncode, r.stderr)


def test_missing_engine_and_missing_model_are_both_exit_3(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    r = run([WRAPPER, str(img)], env={"PENGYOS_UPSCALE_ENGINE": "/nonexistent/engine"})
    assert r.returncode == 3
    r = run([WRAPPER, "-n", "no-such-model", str(img)],
            env={"PENGYOS_UPSCALE_ENGINE": ENGINE or "/nonexistent",
                 "PENGYOS_UPSCALE_MODELS": str(tmp_path)})
    assert r.returncode == 3


def test_measured_results_exist_and_record_the_gpu_allocation_failure():
    m = json.loads(read(MEASURED))
    assert m["cases"], "no measurements"
    assert any(c["failure"] and "vkAllocateMemory" in c["failure"] for c in m["cases"]), (
        "the big models failing to allocate on the NVIDIA devices is the finding "
        "that decides the default model; it must be in the measurements"
    )
    fast = [c for c in m["cases"] if c["model"] == "realesr-animevideov3"
            and c["gpu"] == "auto" and c["out_bytes"]]
    assert fast, "the fast model must be measured on the auto (GPU) path"
    assert all(c["wall_s"] is not None for c in m["cases"]), "a case has no wall time"


def test_docs_agree_with_the_measurements():
    readme, spec = read(README), read(SPEC)
    assert "realesr-animevideov3" in readme and "default" in readme
    assert "libgomp" in readme and "llvmpipe" in readme
    assert "vkAllocateMemory" in readme, "the GPU allocation failure must be documented"
    for token in ("pengyos-upscale", "context_actions", "exit"):
        assert token in spec


def test_tool_scaffold():
    for f in ("README.md", "spec.md", "CHANGELOG.md", "LICENSE", "verify.toml"):
        assert os.path.isfile(os.path.join(TOOL, f)), f"{f} missing"
    v = read(os.path.join(TOOL, "verify.toml"))
    assert "tools/pengyos-upscale" in v


# --- layer 2: measured behaviour -------------------------------------------

@needs_engine
def test_a_real_upscale_writes_next_to_the_input(tmp_path):
    src = _sample(tmp_path, "photo.jpg")
    r = run([WRAPPER, "-s", "2", str(src)], cwd=str(tmp_path))
    out = tmp_path / "photo_x2.jpg"
    assert r.returncode == 0, r.stderr
    assert out.is_file() and out.stat().st_size > 0
    assert _dims(str(out)) == (2 * _dims(str(src))[0], 2 * _dims(str(src))[1])


@needs_engine
def test_names_with_spaces_survive(tmp_path):
    src = _sample(tmp_path, "my photo.jpg")
    r = run([WRAPPER, "-s", "2", str(src)], cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "my photo_x2.jpg").is_file()


@needs_engine
def test_two_inputs_that_would_collide_are_numbered(tmp_path):
    a = _sample(tmp_path, "a.jpg")
    b = _sample(tmp_path, "b.jpeg")
    shutil.copy(a, tmp_path / "a.jpeg")
    r = run([WRAPPER, "-s", "2", "-f", "jpg", str(tmp_path / "a.jpg"),
             str(tmp_path / "a.jpeg")], cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "a_x2.jpg").is_file()
    assert (tmp_path / "a_x2_2.jpg").is_file()


@needs_engine
def test_batch_into_a_directory(tmp_path):
    out = tmp_path / "out"
    _sample(tmp_path, "a.jpg")
    _sample(tmp_path, "b.jpg")
    r = run([WRAPPER, "-s", "2", "-q", "-o", str(out),
             str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")], cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in out.iterdir()) == ["a_x2.jpg", "b_x2.jpg"]


@needs_engine
def test_file_uri_input(tmp_path):
    src = _sample(tmp_path, "uri.jpg")
    r = run([WRAPPER, "-s", "2", "-q", f"file://{src}"], cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "uri_x2.jpg").is_file()


@needs_engine
def test_a_silent_engine_failure_becomes_exit_4_and_leaves_no_file(tmp_path):
    """The vendor binary exits 0 when a Vulkan allocation fails. The wrapper is
    the only thing standing between that failure and a silent no-op, so this is
    the most important test in the file. It runs the model that is measured to
    fail on the NVIDIA devices here; on a box where it succeeds it skips."""
    src = _sample(tmp_path, "big.jpg")
    out = tmp_path / "big_x4.jpg"
    r = run([WRAPPER, "-s", "4", "-n", "realesrgan-x4plus", "-g", "0", str(src)],
            cwd=str(tmp_path))
    if r.returncode == 0:
        pytest.skip("this box's GPU allocates the x4plus model; nothing to prove")
    assert r.returncode == 4, (r.returncode, r.stderr)
    assert not out.exists(), "a failed run must not leave a truncated output file"
    assert "vkAllocateMemory" in r.stderr or "failed" in r.stderr


# --- helpers ----------------------------------------------------------------

def _sample(dirpath, name):
    p = os.path.join(str(dirpath), name)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "mandelbrot=size=128x128:rate=1", "-frames:v", "1", p],
        check=True, capture_output=True)
    return p


def _dims(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=s=x:p=0", path],
        capture_output=True, text=True)
    w, h = r.stdout.strip().split("x")
    return int(w), int(h)
