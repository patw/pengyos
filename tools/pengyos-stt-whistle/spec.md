# pengyos-stt-whistle — contract for the primary speech input

## Summary
Give a PengyOS machine an on-device, offline, sub-second speech-to-text command
with a stable contract, so dictation and voice-driven tools have one thing to
call.

## Purpose
PengyOS has no speech input today. The agent (`pengy-cli`), the Desktop session
and any voice front end all need a transcript. The bar for the OS is: runs on
CPU, no network, small enough to ship in an image, permissively licensed,
pinned by hash, and predictable enough that a caller can rely on its exit codes.

## Deliverable
`pengyos-say`: a command that reads a RIFF WAV from a file or from stdin and
writes the transcript to stdout, backed by the Cactus Whistle model on the
Cactus `needle` engine.

## Interface

```
pengyos-say [FILE | -] [-l LANG] [-k KEYWORDS_FILE] [-t] [-j]
```

| Input | Behaviour |
|---|---|
| `FILE` | a RIFF WAV, 8/16/24/32-bit PCM or 32-bit float, mono or stereo (averaged) |
| `-` | the same format read from stdin, so `pw-record … - \| pengyos-say -` works |
| `-l LANG` | force one of `en de fr es it nl pl`; default is model detection |
| `-k FILE` | keyword biasing, one phrase per line |
| `-t` | include per-word start/end/probability in the JSON |
| `-j` | print the engine JSON: `text`, `language`, `ttft_ms`, `decode_tps` |

Default output is the bare transcript, one line, newline terminated. An empty
transcript is a **success** (exit 0), not an error: silence, steady noise and
non-speech are expected inputs and must not invent a sentence.

| Exit | Meaning |
|---|---|
| 0 | transcript written (including the empty one) |
| 2 | input is not a readable RIFF WAV |
| 3 | engine or model file missing |
| 4 | engine reported a failure |

Paths are overridable by environment (`PENGYOS_STT_ENGINE`, `WHISTLE_MODEL`) so
the prototype runs before the image installs them to
`/usr/lib/pengyos/stt/needle` and `/usr/share/pengyos/stt/whistle.cact`.

## Behaviour contract

- **Offline.** No network at load time or transcribe time. Verified by `strace`:
  zero `socket`/`connect` calls. The engine binary links no curl and carries no
  telemetry endpoint. The Python package around the engine is **not** shipped:
  it sends anonymous usage counts by default and re-fetches a config file from
  Hugging Face on every load.
- **Deterministic for a given clip.** Same clip, same flags, same transcript.
- **Language detection is reported, not assumed.** `-j` exposes it; a caller that
  knows the language passes `-l`.
- **Keyword biasing is additive.** It may raise the probability of the listed
  phrases; it must not make a hard utterance worse.
- **Latency stays interactive.** Time to first token under 500 ms for a clip
  under 10 s on a 2020-era x86-64 CPU.
- **Memory stays small.** Peak RSS under 128 MB for a 30 s clip.

## Supply posture (the part that decides whether it ships)

The engine has **no published source**. The weights do (including the 220 MB
safetensors checkpoint the `.cact` is quantised from). So:

- `needle-engine` is a **Lane 2** component (SPEC §2.5): vendor prebuilt binary,
  pinned by hash in `.hash`, installed under the isolated prefix
  `/usr/lib/pengyos/stt`. It is not a Lane 1 recipe and it is not reproducible
  from source.
- `whistle-model` is pinned data, installed to `/usr/share/pengyos/stt`.
- Both are Apache-2.0, which the license-audit policy allows in every tier.
- Shipping it is a **Tier-2 act** (SPEC §2.5 trust rule): a human signs a binary
  that runs with system access.
- It requires a `divergence.toml` entry whose `expiry` is upstream publishing
  the engine source. An entry with no expiry condition is not an approved
  divergence.
- Edition placement: **Desktop** (dictation, voice tools) and optionally
  **Server** (Lane 2 is allowed there). **Not Core** — Core is Lane 1 only, and
  18 MB of model plus a prebuilt binary does not belong in the minimal base.

## Acceptance criteria

- `pengyos-say clip.wav` returns the transcript for a clean 16 kHz English clip.
- `pw-record … - | pengyos-say -` returns the transcript of live capture.
- Silence and a pure tone return an empty transcript with exit 0.
- Non-WAV input exits 2; a missing model exits 3.
- `-j` reports language, time to first token and decode speed.
- Keyword biasing fixes a proper noun that was misheard without it.
- Both recipes declare a pinned version, a licence, an `https` site, and a
  `.hash` sha256 covering the source they download.
- `pytest -q` green in this directory (the behaviour layer skips cleanly when
  the engine is not provided).

## Verification

`tests/test_stt_whistle.py`, two layers:

1. **Recipe and contract tests** — always run, offline, no engine needed: pins
   match the published digests, licences declared, Lane 2 documented, arch guard
   present, tool directory follows the repo contract, measured results ship with
   the prototype.
2. **Behaviour tests** — run against a real engine when `PENGYOS_STT_ENGINE`,
   `WHISTLE_MODEL` and `PENGYOS_STT_CLIPS` are set; skip otherwise. These
   re-run the claims in the README: file and pipe transcription, silence and
   tone returning empty, language detection, latency bound, keyword biasing,
   exit codes.

## Non-goals

- Streaming input, speaker diarisation, and text-to-speech (Kokoro covers that).
- A COSMIC voice UI. This is the command and the recipes; the UI is a separate
  decision.
- Shipping the Python `cactus-needle` package, the training/export toolchain, or
  the Needle tool-calling model.
- Fine-tuning the model. Whistle ships no training path; keyword biasing is the
  only customisation.

## Constraints

- Runs as a non-root user; no network at test time.
- No root, no reboots, no signing. Release acceptance stays a human act.
