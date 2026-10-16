# pengyos-stt-whistle — speech to text for PengyOS (prototype)

Status: **prototype, not shipped.** This directory holds a measured evaluation of
[Cactus Whistle](https://cactuscompute.com/whistle) as PengyOS's primary speech
input, a working `pengyos-say` command, a Buildroot recipe pair that installs it,
and the numbers behind the recommendation.

## What Whistle is

A 16.9 MB speech-to-text model from Cactus Compute, Apache-2.0, that runs on the
CPU through a small self-contained C++ engine (`needle`). One 30-second pass,
windowed beyond that; English, German, French, Spanish, Italian, Dutch and Polish;
word timestamps; keyword biasing; live streaming; speech embeddings. The same
engine also runs Needle (their tool-calling model), so one binary can take a clip
in and produce tool calls out.

It is **not** a Whisper derivative. It shares no code with openai-whisper; the
comparison in their benchmarks is against Whisper base as a baseline.

## Measured on this box (Ryzen 9 5900XT, 16 cores, AVX2, no AVX-512)

Audio was generated with Kokoro-82M at 16 kHz mono — synthetic voices, so treat
accuracy as a smoke test and the timings as real. Full data:
[`results/measured.json`](results/measured.json).

| Clip | Audio | Wall time | Time to first token | Peak RSS | WER |
|---|---|---|---|---|---|
| short command, female voice | 1.9 s | 0.06 s | 12 ms | 40 MB | **0.000** |
| dictation sentence, male voice | 4.0 s | 0.10 s | 23 ms | 40 MB | **0.000** |
| numbers and service names | 5.5 s | 0.32 s | 248 ms | 40 MB | 0.333 |
| proper nouns (Siobhan, Krzysztof, Krakow) | 5.0 s | 0.11 s | 34 ms | 40 MB | 0.300 |
| 5-sentence paragraph | 35.9 s | 2.22 s | 1324 ms | 65 MB | 0.053 |
| Spanish, French | 2.1 / 3.5 s | 0.07 / 0.11 s | 13 / 23 ms | 40 MB | 0.333 / 0.125 |
| 2 s of silence | 2.0 s | 0.03 s | — | 39 MB | empty transcript |

Cold start (load the model, transcribe, exit): **0.06–0.20 s**, peak RSS
**40 MB**. Decode runs 440–560 tokens/s at full depth; the decoder ladder
(`--audio-depth`) trades depth for speed — 869 t/s at 2 layers, 376 t/s at 8.

Weaknesses, measured: proper nouns, numbers spelled as digits, and non-English
accents. Keyword biasing is the fix for the first two and it works — the proper
nouns clip went 0.300 → 0.200 WER and `Krakow` came back correctly once it was in
the keyword file. Ship a keyword list per app, not a global one.

## Why it fits PengyOS

- **Size.** 16.9 MB of model plus a 1.5 MB binary. Whisper base is 145 MB.
- **Dependencies.** The published binary needs only libc, libm, pthread and dl.
  No Python, no torch, no ONNX runtime, no GPU.
- **Offline.** `strace` on a full load-and-transcribe: **zero** network syscalls.
  The binary links no curl and carries no telemetry endpoint. (The *Python*
  package around it does send anonymous usage counts by default, and re-fetches a
  config file from Hugging Face on every load. Ship the binary, not the package.)
- **Licence.** Apache-2.0 on both the weights and the engine — passes the
  license-audit policy (permissive family) in every tier.
- **Audio stack already exists.** The Desktop layer ships PipeWire, WirePlumber,
  ALSA UCM profiles and `pw-record`; the kernel fragment builds `snd-usb-audio`
  and SOF. Dictation needs no new audio plumbing.
- **musl.** Upstream publishes a musl build that needs only `libc.so` — the
  Buildroot-musl toolchain path works.

## The one real cost

**There is no source for the engine.** The weights are published (including the
220 MB safetensors checkpoint), the engine is not. So this is a **Lane 2**
component under SPEC §2.5: a vendor prebuilt binary, pinned by hash, installed
under an isolated prefix — not a Lane 1 recipe, and it does not build from
source. That needs a `divergence.toml` entry with a real expiry (upstream
publishing the engine source), and it is a Tier-2 decision: a human signs a
binary that runs with system access.

## Use it now

```sh
export PENGYOS_STT_ENGINE=/path/to/needle WHISTLE_MODEL=/path/to/whistle.cact

./pengyos-say clip.wav                                  # bare transcript
pw-record --target 16000 --rate 16000 --channels 1 - | ./pengyos-say -
./pengyos-say -j clip.wav                               # text, language, ttft, tps
./pengyos-say -l en -k keywords.txt clip.wav            # force language, bias names
```

Exit codes: 0 transcript written (an empty transcript is a valid 0), 2 bad input,
3 model/engine missing, 4 engine failure.

## Layout

```
pengyos-stt-whistle/
├── pengyos-say            # the command (POSIX shell over the pinned engine)
├── pengyos-say.c          # the C path: needle_transcribe() via libneedle.a
├── recipe/needle-engine/  # Lane 2 recipe for the prebuilt engine
├── recipe/whistle-model/  # pinned model weights -> /usr/share/pengyos/stt
├── results/measured.json  # every number quoted here
├── spec.md                # the contract
└── tests/                 # recipe honesty + the behaviour claims, run for real
```

## Known limits of this prototype

- No human speech in the test set — synthetic voices only. Real accents, room
  noise and overlapping speech are unmeasured.
- No mic on this host (headless, PipeWire dummy sink), so live capture is
  untested end to end; the pipe path is tested.
- `libneedle.a` links only against a modern clang/libc++ (≥ 19). libstdc++ and
  libc++ 14 both fail. The prebuilt binary carries its own C++ runtime, so the
  shipped path avoids the issue; embedding the library needs libc++ on the
  build host.
- The engine binary contains AVX-512 kernels. It ran correctly on a CPU with no
  AVX-512, so dispatch is guarded, but that is one data point on one CPU.
- No `pengyos-desktop` image has been built with these recipes; the recipe pair
  is written to the repo's conventions and linted by hand, not resolved against
  a Buildroot configure run.
