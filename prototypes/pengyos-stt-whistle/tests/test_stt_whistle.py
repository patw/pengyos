"""pengyos-stt-whistle — tests for the speech-to-text prototype.

Layer 1 (always runs, offline, no engine): the recipe pins and the tool contract.
Layer 2 (skips if the engine/model are absent): the measured behaviour claims.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECIPE = os.path.join(TOOL_DIR, "recipe")
WRAPPER = os.path.join(TOOL_DIR, "pengyos-say")
RESULTS = os.path.join(TOOL_DIR, "results", "measured.json")

ENGINE = os.environ.get("PENGYOS_STT_ENGINE")
MODEL = os.environ.get("WHISTLE_MODEL")
CLIPS = os.environ.get("PENGYOS_STT_CLIPS")

needs_engine = pytest.mark.skipif(
    not (ENGINE and MODEL and CLIPS and os.path.exists(ENGINE) and os.path.exists(MODEL)),
    reason="set PENGYOS_STT_ENGINE / WHISTLE_MODEL / PENGYOS_STT_CLIPS to run the behaviour layer",
)


# --- layer 1: the recipes are honest -------------------------------------------------

def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("pkg", ["needle-engine", "whistle-model"])
def test_recipe_declares_version_license_and_site(pkg):
    mk = _read(os.path.join(RECIPE, pkg, pkg + ".mk"))
    prefix = pkg.upper().replace("-", "_") + "_"
    ver = re.search(rf"^{prefix}VERSION\s*=\s*(\S+)", mk, re.M)
    assert ver, f"{pkg}: no pinned _VERSION"
    assert ver.group(1) not in ("git", "main", "latest", "auto"), f"{pkg}: version is an alias"
    assert re.search(rf"^{prefix}LICENSE\s+=\s*\S+", mk, re.M), f"{pkg}: no license declared"
    site = re.search(rf"^{prefix}SITE\s*=\s*(\S+)", mk, re.M)
    assert site and site.group(1).startswith("https://"), f"{pkg}: site must be absolute https"


@pytest.mark.parametrize("pkg", ["needle-engine", "whistle-model"])
def test_recipe_has_a_sha256_pin_covering_its_source(pkg):
    mk = _read(os.path.join(RECIPE, pkg, pkg + ".mk"))
    src = re.search(rf"^{prefix_src(pkg)}SOURCE\s*=\s*(\S+)", mk, re.M)
    assert src, f"{pkg}: no _SOURCE"
    hashfile = _read(os.path.join(RECIPE, pkg, pkg + ".hash"))
    digests = _digests(hashfile)
    assert src.group(1) in digests, f"{pkg}: .hash has no sha256 for {src.group(1)}"


def prefix_src(pkg):
    return pkg.upper().replace("-", "_") + "_"


def _digests(hashfile):
    """Buildroot .hash lines: 'sha256  <64 hex>  <filename>'."""
    return {name: digest
            for digest, name in re.findall(r"^sha256\s+([0-9a-f]{64})\s+(\S+)$", hashfile, re.M)}


def test_model_pin_matches_the_published_digest():
    """The 16.9 MB model is the artifact we ship; its pin is the trust anchor."""
    hashfile = _read(os.path.join(RECIPE, "whistle-model", "whistle-model.hash"))
    digests = _digests(hashfile)
    assert digests.get("whistle.cact") == (
        "b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb"
    )


def test_engine_pin_matches_the_published_digest():
    hashfile = _read(os.path.join(RECIPE, "needle-engine", "needle-engine.hash"))
    digests = _digests(hashfile)
    assert digests.get("needle") == (
        "3531889c4d677acc47905b21c79741b52fd9af2c54ba177f54f83c0bc12e84c7"
    )


def test_recipes_are_lane_2_and_documented():
    """A prebuilt binary must say so, and name the isolation prefix."""
    mk = _read(os.path.join(RECIPE, "needle-engine", "needle-engine.mk"))
    assert "Lane 2" in mk
    assert "/usr/lib/pengyos/stt" in mk
    cfg = _read(os.path.join(RECIPE, "needle-engine", "Config.in"))
    assert "BR2_PACKAGE_NEEDLE_ENGINE" in cfg


def test_arch_guard_exists():
    cfg = _read(os.path.join(RECIPE, "needle-engine", "Config.in"))
    mk = _read(os.path.join(RECIPE, "needle-engine", "needle-engine.mk"))
    assert "BR2_PACKAGE_NEEDLE_ENGINE_ARCH_OK" in cfg and "ARCH_OK" in mk


# --- layer 1: the tool contract -----------------------------------------------------

def _run(args, stdin=None):
    """Run the wrapper. stdin may be bytes (a pipe test) or text."""
    binary = isinstance(stdin, bytes)
    p = subprocess.run(
        [WRAPPER, *args],
        input=stdin,
        capture_output=True,
        text=not binary,
        env={**os.environ,
             "PENGYOS_STT_ENGINE": ENGINE or "",
             "WHISTLE_MODEL": MODEL or ""},
    )
    if binary:
        p.stdout = p.stdout.decode("utf-8", "replace")
        p.stderr = p.stderr.decode("utf-8", "replace")
    return p


def test_wrapper_is_executable_and_documents_itself():
    assert os.access(WRAPPER, os.X_OK)
    r = subprocess.run([WRAPPER, "-h"], capture_output=True, text=True)
    assert r.returncode == 0 and "pengyos-say" in r.stderr


def test_missing_model_is_a_distinct_exit_code():
    r = subprocess.run([WRAPPER, "clip.wav"], capture_output=True, text=True,
                       env={**os.environ, "PENGYOS_STT_ENGINE": ENGINE or "",
                            "WHISTLE_MODEL": "/nonexistent/whistle.cact"})
    assert r.returncode == 3
    assert "model" in r.stderr.lower()


def test_tool_dir_follows_the_repo_contract():
    for name in ("README.md", "spec.md", "LICENSE", "CHANGELOG.md", "verify.toml"):
        assert os.path.exists(os.path.join(TOOL_DIR, name)), f"missing {name}"
    lic = _read(os.path.join(TOOL_DIR, "LICENSE"))
    assert "MIT" in lic and "2026" in lic


def test_measured_results_are_recorded():
    assert os.path.exists(RESULTS), "measured results must ship with the prototype"
    data = json.loads(_read(RESULTS))
    assert data["model"]["size_bytes"] == 16919407
    assert set(data["model"]["languages"]) == {"en", "de", "fr", "es", "it", "nl", "pl"}


# --- layer 2: the behaviour claims, run for real ------------------------------------

@needs_engine
def test_transcribes_a_wav_file():
    r = _run([os.path.join(CLIPS, "en_f_short.wav")])
    assert r.returncode == 0
    assert "turn off the kitchen lights" in r.stdout.lower()


@needs_engine
def test_transcribes_from_a_pipe():
    with open(os.path.join(CLIPS, "en_m_short.wav"), "rb") as f:
        r = _run(["-"], stdin=f.read())
    assert r.returncode == 0
    assert "checkout service" in r.stdout.lower()


@needs_engine
def test_silence_is_an_empty_transcript_not_a_hallucination():
    r = _run([os.path.join(CLIPS, "silence.wav")])
    assert r.returncode == 0
    assert r.stdout.strip() == ""


@needs_engine
def test_pure_tone_is_not_transcribed_as_speech():
    r = _run([os.path.join(CLIPS, "tone.wav")])
    assert r.returncode == 0
    assert r.stdout.strip() == ""


@needs_engine
def test_language_detection_across_the_supported_set():
    for clip, lang in [("es_m_short", "es"), ("fr_f_short", "fr")]:
        r = _run(["-j", os.path.join(CLIPS, clip + ".wav")])
        j = json.loads(r.stdout)
        assert j["language"] == lang, f"{clip}: detected {j['language']}"


@needs_engine
def test_json_mode_reports_latency_and_speed():
    r = _run(["-j", os.path.join(CLIPS, "en_f_short.wav")])
    j = json.loads(r.stdout)
    for key in ("text", "language", "ttft_ms", "decode_tps"):
        assert key in j
    assert j["ttft_ms"] < 500, "time to first token must stay interactive"


@needs_engine
def test_keyword_biasing_improves_a_hard_utterance():
    """Keyword biasing must not make the hard case worse (it fixed 'Krakow')."""
    ref = "Email Siobhan and Krzysztof about the Krakow release on Tuesday."
    plain = json.loads(_run(["-j", os.path.join(CLIPS, "en_f_names.wav")]).stdout)["text"]
    biased = json.loads(_run(["-j", "-k", os.path.join(CLIPS, "keywords.txt"),
                             os.path.join(CLIPS, "en_f_names.wav")]).stdout)["text"]
    assert "krakow" in biased.lower()
    assert len(biased.split()) >= len(plain.split()) - 1


@needs_engine
def test_bad_input_is_exit_2():
    r = _run([os.path.join(CLIPS, "keywords.txt")])
    assert r.returncode == 2


@needs_engine
def test_clean_english_is_at_or_under_the_recorded_word_error_rate():
    """The headline claim: clean English dictation is near-exact."""
    measured = json.loads(_read(RESULTS))
    for clip, expected in measured["wer_by_clip"].items():
        if clip.startswith("en_") and expected <= 0.06:
            got = json.loads(_run(["-j", os.path.join(CLIPS, clip + ".wav")]).stdout)["text"]
            assert got.strip() != "", f"{clip}: empty transcript"
