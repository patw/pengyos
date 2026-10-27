# pengyos-jev — a System One (Jev-class) decision service for PengyOS (prototype)

Status: **prototype, not shipped.** This directory holds the measured evaluation
of running a Jev-class decision model as a PengyOS service, a working
`pengyos-jev` client, the Buildroot recipe skeleton that would install it, and
the numbers behind the recommendation. Nothing here is enabled in an image.
The full research record is [`docs/jev-model-service-viability.md`](../../docs/jev-model-service-viability.md).

## What a System One model is

[Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) is TypeSafe
AI's "System One" model: state in, **typed probabilistic decisions** out, one
forward pass, no text generated. Three primitives:

| Primitive | Question shape | Answer |
|---|---|---|
| `noul` | yes/no | P(true), 0..1 |
| `choice` | pick one of 2..N options | distribution + top option + confidence |
| `score` | rate on a 2..10-level scale | expected level + distribution + confidence |

**TypeSafe's own Jev is closed-weight** — API only, no published checkpoint.
What *is* open are the Jev-class reproductions, and llama.cpp grew native
support for them: a `/v1/systemone` endpoint (TypeSafe-compatible) added
2026-10-02, serving `laya`, `openjev`, `clef`, `nimble`, `lev`, `kev`,
`pplx-decider`, `vllm-decision2`, `vllm-d3`, `lfm2-d1`, `lfm2-d1-omni`.

## What we measured (2026-10-15, real runs, not vendor numbers)

One binary — a **static, Vulkan-only `llama-server`** built from source —
serving the model as a loopback HTTP service:

| Machine | Device | Model | Per decision | Peak RSS | VRAM |
|---|---|---|---|---|---|
| behotv (RTX 3070) | Vulkan0 (GPU) | Laya-Q8_0 (421 M) | **8.4 ms** | 632 MB | ~850 MiB |
| behotv (RTX 3070) | Vulkan0 (GPU) | d1-3B-Q4_K_M | **16 ms** | 736 MB (2.08 GB peak) | ~2.2 GiB |
| behoweather (Intel N100, **PengyOS Desktop 26.10**) | Vulkan0 = Intel UHD | Laya-Q8_0 | **304 ms** | **146 MB** | shared |
| behoweather (Intel N100, PengyOS) | CPU only, 4 threads | Laya-Q8_0 | 405 ms | **96 MB** | — |
| behoserver (RTX 5060 Ti) | CPU only, 8 threads | Laya-Q8_0 | 210 ms | 639 MB | — |

The 16 GB-laptop answer is the behoweather row: a 421 M model, 146 MB resident,
a third of a second per decision **on integrated graphics**, under 0.5 s with no
GPU at all. Cold start (process up to serving) is 1.2–3.4 s.

## Why it fits PengyOS

- **One binary, no Python, no torch.** `llama-server` links only `libvulkan`,
  OpenSSL, libstdc++/libgcc/libc, zlib/zstd. Built `-DBUILD_SHARED_LIBS=OFF`
  there is no `libllama.so`/`libggml.so` to ship.
- **Vulkan is already in the Desktop layer** (`vulkan-loader`, Mesa ANV/RADV,
  `llvmpipe`; NVIDIA editions add NVIDIA's ICD). COSMIC already needs Vulkan.
- **Licences.** Laya is Apache-2.0 (weights and GGUF, commercial-use tag).
  d1-3B is the LFM Open License (Apache-2.0 plus a $10 M revenue threshold).
  OpenJev is CC-BY-NC-4.0 — **not shippable**.
- **It composes.** A decision is a JSON call with a calibrated probability
  attached: routing, triage, moderation, urgency scoring, confidence-gated
  escalation to the chat model. No parsing, nothing to hallucinate.

## Using the client

```sh
# one question
python3 pengyos_jev/pengyos_jev.py decide noul --state "The build server is offline and 3 deploys are queued." \
    --instructions "Is the build server unavailable?"
# 0.9918

# several questions, one state, one pass
python3 pengyos_jev/pengyos_jev.py ask --state "$(cat ticket.txt)" \
    --question "route:choice:Which team should handle this?::billing,shipping,technical" \
    --question "angry:noul:Is the customer angry?" \
    --question "urgency:score:How urgent is this?::can wait,this week,today,right now"

# is it up, and is it a decision model?
python3 pengyos_jev/pengyos_jev.py health
```

`--url` (default `http://127.0.0.1:8199`, env `PENGYOS_JEV_URL`), `--json` on
every command, exit 1 with a one-line message on stderr when the service is
down. Stdlib only.

## Starting the service

```sh
llama-server -m /usr/share/pengyos/jev/Laya-Q8_0.gguf --device Vulkan0 \
             -c 4096 -ngl 99 --host 127.0.0.1 --port 8199 --api-key <key>
```

`pengyos-jev.service` (system) is the unit skeleton: loopback-only, its own
user, hardening flags on, model weights read-only.

## Recipe skeleton

[`recipe/`](recipe/) holds the Buildroot skeleton: a pinned `llama-cpp-vk`
build recipe (Lane 1, source) and a `laya-model` weights recipe (pinned data).
**Not installed** — see [`recipe/README.md`](recipe/README.md) for the two
Buildroot gaps that must be closed first (no `host-shaderc` for the Vulkan
shaders; the 2026.08 `llama-cpp` pin predates `/v1/systemone`).

## Tests

```sh
cd prototypes/pengyos-jev && python3 -m pytest -q
```

Offline: the HTTP transport is mocked and the responses are real captures in
`tests/fixtures/`. The suite checks payload validation, all three answer
types, `--json`, and that an unreachable service is a clean exit 1.
