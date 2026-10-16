# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Prototype evaluation of Cactus Whistle as PengyOS's primary speech input.**
  Measured on a Ryzen 9 5900XT: 0.06–0.10 s wall time for a 2–4 s clip, 9–34 ms
  to the first token, 40 MB peak RSS, 0.000 word error rate on clean English
  dictation, zero network syscalls at load and transcribe.
- **`pengyos-say`** — WAV from a file or a pipe in, transcript out. Exit codes
  0/2/3/4; an empty transcript is a success.
- **A Buildroot recipe pair** (`recipe/needle-engine`, `recipe/whistle-model`):
  Lane 2 prebuilt engine pinned by hash under `/usr/lib/pengyos/stt`, model
  weights pinned under `/usr/share/pengyos/stt`, both Apache-2.0, with an
  architecture guard for the five arches upstream publishes.
- **`pengyos-say.c`** — the library path: `needle_transcribe()` against
  `libneedle.a`. Needs clang/libc++ ≥ 19; documented, not shipped.
- **Two test layers**: recipe honesty (offline, always runs) and behaviour
  claims (run against a real engine, skip cleanly without one).
- **`results/measured.json`** — every number quoted in the README, per clip.

### Known limits
- The test audio is Kokoro-82M TTS, not human speech. Real accents, room noise
  and crosstalk are unmeasured.
- No microphone on the prototype host, so live capture is untested end to end.
- The engine has no published source, so this is a Lane 2 divergence and needs a
  `divergence.toml` entry with an expiry before it can ship.
