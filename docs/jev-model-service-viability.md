# Running a Jev-class model as a PengyOS service

**Question (2026-10-15):** embed a *small* JEV model in PengyOS, operated like a
service — running under **Vulkan**, **statically compiled**, tiny enough for a
**16 GB laptop**. The stated worry: "these models are way too new."

**Short answer:** the worry is right about the *name* and wrong about the
*capability*. TypeSafe's Jev itself is closed-weight and will never run on your
laptop. But llama.cpp grew a native, TypeSafe-compatible decision endpoint on
2026-10-02 (`/v1/systemone`) and serves eleven open Jev-class models. One of
them — **Laya, 421 M parameters, Apache-2.0** — runs as a static Vulkan
`llama-server` at **304 ms per decision on a PengyOS Desktop box with only
integrated graphics, 146 MB resident**. Measured, not promised.

Prototype code + tests: [`prototypes/pengyos-jev/`](../prototypes/pengyos-jev/).

---

## 1. What "JEV" is, and what is actually downloadable

Jev is TypeSafe AI's first **System One model** (announced 2026-09-15): state
in, typed probabilistic decisions out, in one forward pass, with calibrated
probabilities and confidence, and no text generation. Three primitives —
`noul` (yes/no probability), `choice` (distribution over runtime-supplied
options), `score` (expected level on a 2..10 scale). Prior art, mapped in BotTalk
`c23c28e410a1c78201020000`: monoT5 (noul), RankT5 (score), GLiClass (choice),
LLM2Vec (LLM→encoder). The genuinely new part is the calibrated-decision output
contract (RLCD training), not the architecture.

| Model | Size | Licence | llama.cpp support | Verdict for PengyOS |
|---|---|---|---|---|
| **TypeSafe Jev 1.13** | unpublished | closed (hosted API) | n/a (API only) | **not embeddable** |
| **autotrust/JEV-9B** | 7.9 B backbone + 40.2 M LoRA head; 18.4 GB bf16 | Apache-2.0 | **no** (arch `qwen3_5_text` not in the decision registry) | too big + unsupported; the Q4 backbone (5.68 GB) *does* run on Vulkan as a plain LLM |
| autotrust/JEV-27B | 27 B; 54 GB | Apache-2.0 | no | no |
| **Laya** (ConvAI) | **421 M** | **Apache-2.0** | **yes** (`laya`) | **recommended default** |
| laya-multilingual | 421 M, 8192-token | Apache-2.0 | yes | recommended for a multilingual fleet |
| **LiquidAI d1-3B** | 3.1 B | LFM Open License v1.0 (Apache-2.0 + **$10 M revenue threshold**) | yes (`lfm2-d1`) | opt-in for GPU boxes; needs a licence-audit exception entry |
| LiquidAI d1-omni-600M | 587 M, image+audio | LFM Open License | yes (`lfm2-d1-omni`) | interesting, same licence caveat |
| vllm-sr Decision-2.0 (Kai 0.6 B / Sol 2 B / Nox 4 B / Lux 9 B / Vega 27 B) | 0.6–27 B | Apache-2.0, GGUFs published | yes (`vllm-decision2`) | good Apache-2.0 size ladder |
| OpenJev | 27 B (Qwen3.5-VL) | **CC-BY-NC-4.0** | yes (`openjev`) | **must stay out of images** |
| pplx-decider | 27 B | (perplexity, custom) | yes (`pplx-decider`) | too big |
| clef, nimble, lev, kev | 9 B–27 B | varies | yes | not laptop-sized |

Two facts that decide the design:

1. **The closed model is not available.** No official Jev checkpoint exists on
   Hugging Face; TypeSafe sells API access ($0.042/MTok in, free out).
2. **The open reproductions are now first-class citizens of the runtime we
   already ship-adjacent.** llama.cpp's server answers `/v1/systemone` with
   `answers`, per-question `probabilities`, `confidence`, and
   `usage.output_tokens = 0`.

## 2. The runtime: why llama.cpp/ggml and not the others

| Runtime | Vulkan | Static-ish | Buildroot 2026.08 | Decision API | Verdict |
|---|---|---|---|---|---|
| **llama.cpp / ggml** | **yes, mature backend** | `BUILD_SHARED_LIBS=OFF` → single binary | recipe exists (`llama-cpp`) | **`/v1/systemone`** | **the answer** |
| ncnn | yes | yes | not packaged | none (vision nets) | good for image ops (our ESRGAN lane), not for text decisions |
| MNN / TNN | yes | yes | not packaged | none | no model path |
| ExecuTorch / IREE / ONNX Runtime | partial/experimental | awkward | not packaged | none | IREE's Vulkan driver is being rewritten (BotTalk `f62e54db`); not a distro dependency to take on |
| mistral.rs (candle) | **no Vulkan** (CUDA/Metal/CPU) | Rust, static OK | no recipe | none | already ruled out (BotTalk `c276f046`) |
| vLLM | CUDA/ROCm, not Vulkan | Python | no | yes (it's the reference for JEV-9B) | a Python server, not an OS service |

The ggml Vulkan backend already implements every op the Qwen3.5 family needs
(`SSM_CONV`, `SSM_SCAN`, `GATED_DELTA_NET`, `MUL_MAT_ID`, `FLASH_ATTN_EXT`,
`UNARY` sigmoid/softplus, plus `ADD_ID` for LoRA), and llama.cpp has had
`qwen35`/`qwen35moe` since 2026-02-11 (`fc0fe4004`). Vulkan is upstream-CI'd
(`.github/workflows/build-vulkan.yml`).

## 3. The static build, and the three packaging traps (measured)

Recipe that worked on 2026-10-15 (commit `b76b133`, binary 61.1 MB,
sha256 `803de906…`):

```sh
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DGGML_VULKAN=ON -DGGML_CUDA=OFF -DGGML_OPENMP=OFF \
  -DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF
ninja llama-server        # 537 steps
ldd llama-server → libvulkan, libssl/libcrypto, libstdc++, libgcc, libc, z, zstd  (nothing else)
```

"Static" means: llama/ggml/server all linked in, no `libllama.so`/`libggml.so`
to ship. `libvulkan.so.1` stays dynamic on purpose — the Vulkan loader must
dlopen vendor ICDs at runtime; a `-static` binary cannot do that.

The traps, each found by hitting it:

1. **glslc + spirv-headers are hard configure-time requirements**
   (`find_package(Vulkan COMPONENTS glslc REQUIRED)`,
   `find_package(SPIRV-Headers CONFIG REQUIRED)`). Known since the behopc build
   (BotTalk `fffbc212`), and **Buildroot 2026.08 has no shaderc recipe at all**
   — its `llama-cpp` Vulkan option depends only on `vulkan-loader`, so
   `BR2_PACKAGE_LLAMA_CPP_VULKAN=y` fails to configure today. Fix: a
   `host-glslc` recipe (shaderc source; glslang + spirv-tools are already in
   2026.08).
2. **PengyOS has no `libgomp.so.1`.** The default build (OpenMP on) produces a
   binary that dies at exec on a PengyOS Desktop image. `-DGGML_OPENMP=OFF`
   fixes it; measured on behoweather.
3. **The 2026.08 `llama-cpp` pin is `b8117` (2026-02-20)** — eight months
   *before* `/v1/systemone` (2026-10-02, first tag `b11318`). A decision service
   needs a new pinned recipe, not the stock symbol.

## 4. Measurements

Same binary everywhere (built on behoserver, copied; PengyOS glibc 2.44 ran it
unchanged). Latency = steady state, second request onward; the first request
carries slot warm-up. Peak RSS = `VmHWM`.

| Host | OS | Device | Model | Per decision | Peak RSS | VRAM |
|---|---|---|---|---|---|---|
| behotv | Ubuntu 26.04 | **Vulkan0 = RTX 3070** | Laya-Q8_0 (421 M) | **8.4 ms** | 632 MB | ~850 MiB |
| behotv | Ubuntu 26.04 | Vulkan0 = RTX 3070 | d1-3B-Q4_K_M (3.1 B) | **16 ms** | 2.08 GB | ~2.2 GiB |
| **behoweather** | **PengyOS Desktop 26.10** | **Vulkan0 = Intel UHD (N100 iGPU)** | Laya-Q8_0 | **304 ms** | **146 MB** | shared |
| behoweather | PengyOS Desktop 26.10 | CPU only, 4 threads | Laya-Q8_0 | 405 ms | **96 MB** | — |
| behoserver | Ubuntu 26.04 | CPU only, 8 threads | Laya-Q8_0 | 210 ms | 639 MB | — |

Answers were sane and device-independent (same state, RTX 3070 vs N100 iGPU:
`billing` 0.9904 vs 0.9918; `angry` 0.7856 vs 0.7860). Cold start to serving:
1.2 s (Laya) / 3.4 s (d1-3B). Full data:
[`prototypes/pengyos-jev/results/measured.json`](../prototypes/pengyos-jev/results/measured.json);
reproduce with `results/measure.py`.

**The 16 GB laptop verdict.** behoweather *is* the target class (4 cores, 15 GB,
integrated graphics only): the service costs 146 MB resident, a third of a
second per decision, and needs nothing the Desktop image doesn't already ship.
On a laptop with a dGPU (beholaptop's RTX 3070 Ti) the behotv row applies:
single-digit milliseconds. A 9 B backbone (Q4_K_M, 5.7 GB) also fits a 16 GB
laptop with an 8 GB dGPU, but no open Jev-class model at that size is supported
by llama.cpp today — the supported 9 B-class ones (clef, nimble, Lux-9B) are
unmeasured here.

## 5. Service shape

```
llama-server (static, Vulkan, loopback, --api-key)  ←  pengyos-jev.service
        ▲  POST /v1/systemone  {state, questions}
        │
pengyos-jev CLI (stdlib)  ·  fleet daemons (CVE triage, update triage,
                            ticket routing, feed triage)  ·  moofile rerank
```

- **Loopback only** (`--host 127.0.0.1`) with `--api-key`; the Desktop firewall
  stays default-deny.
- **Own user, hardened unit** (`prototypes/pengyos-jev/pengyos-jev.service`):
  `ProtectSystem=strict`, `MemoryMax=4G`, `RestrictAddressFamilies=AF_INET`,
  `DeviceAllow=/dev/dri` for Vulkan, weights read-only under
  `/usr/share/pengyos/jev/`.
- **Idle cost is the model in RAM** (146 MB). `--cache-ram 0` is the default
  for decision models; prompt cache is opt-in for repeated-state workloads.
- **Confidence-gated escalation** is the pattern that pairs with the standing
  chat model: System One answers when confident, System Two (the LlamaHerder
  model) takes the rest. That is a usage pattern, not a shipped feature.
- **Why it's worth having:** the fleet's current embedding/decision work runs
  on CPU via voyage-4-nano at ~1 s per embed (BotTalk `7b1ebd2f`). A 421 M
  decision model at 8–300 ms per call, on the GPU the desktop already has, is
  two orders of magnitude of headroom for triage/rerank/routing work.

## 6. Licence position (for `pengyos-license-audit`)

| Component | Licence | Policy read |
|---|---|---|
| llama.cpp | MIT | clean |
| Laya weights + GGUF | Apache-2.0 | clean |
| d1-3B / d1-omni-600M | LFM Open License v1.0 — Apache-2.0 + commercial use conditioned on **< $10 M annual revenue** | not an SPDX id → needs a documented exception entry; a side project is under the threshold, but the licence is conditional by design |
| OpenJev | CC-BY-NC-4.0 | **non-commercial: must not ship** |
| autotrust/JEV-9B | Apache-2.0 | clean, but unsupported by the runtime |

## 7. Recommendation

1. **Prototype Laya-Q8_0 as the default** (`prototypes/pengyos-jev/` is the
   starting point): Apache-2.0, 450 MB download, 146 MB resident, works on
   integrated graphics, multilingual variant available.
2. **Close the two Buildroot gaps first** — `host-glslc` recipe + a pinned
   `llama-cpp-vk` (≥ b11318) recipe. Both are recipe work, not research.
3. **Keep d1-3B as the opt-in GPU tier** (16 ms/decision) with a licence-audit
   exception entry; keep OpenJev out.
4. **Do not plan around TypeSafe's Jev.** Use its API when you want the
   teacher; the service is the student.
5. Re-measure on beholaptop (RTX 3070 Ti + 680M iGPU) when it's next on —
   that is the real 16 GB-class laptop, and the iGPU row is the number that
   matters.

## 8. Open questions / not verified

- **Qwen3.5-9B GGUF on Vulkan end-to-end** — op support verified by source
  reading, not by a run (GPUs were occupied by Strata during the session).
- **LoRA on the Vulkan backend** for the JEV-9B-style decision head
  (`adapter_vllm`: backbone LoRA + 24-slot `lm_head` LoRA): ggml has
  `MUL_MAT_ID`/`ADD_ID` shaders, llama.cpp LoRA works on CPU; Vulkan LoRA
  performance is unmeasured.
- **clef / nimble / vllm-decision2 quality** vs Laya on our own triage data —
  unmeasured. Laya's own card reports 0.362 → 0.766 accuracy when fine-tuned on
  in-domain typed decisions; expect to fine-tune.
- **d1-3B's LFM Open License** legal reading for a distro that people may use
  commercially — needs the operator's call, not mine.
- **behocraptop (i3-1215U, 14 GB, no dGPU)** numbers — box was offline.
- The prototype's `--api-key-file` flag name and unit wiring are unverified
  against a real PengyOS build (no image build was run in this session).

## 9. Reproducing this

```sh
# build (host, no root): glslc from debs
mkdir -p /data/tmp/vktools && cd /data/tmp/vktools
apt-get download glslc libshaderc1 spirv-headers
for d in *.deb; do dpkg -x "$d" prefix; done
export VULKAN_SDK=$PWD/prefix/usr LD_LIBRARY_PATH=$PWD/prefix/usr/lib/x86_64-linux-gnu
cmake -B /data/tmp/llama-vk -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DGGML_VULKAN=ON -DGGML_CUDA=OFF -DGGML_OPENMP=OFF -DBUILD_SHARED_LIBS=OFF \
  -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DCMAKE_PREFIX_PATH=$VULKAN_SDK \
  -DCMAKE_C_FLAGS="-I$VULKAN_SDK/include" -DCMAKE_CXX_FLAGS="-I$VULKAN_SDK/include"
ninja -C /data/tmp/llama-vk llama-server

# serve + ask
/data/tmp/llama-vk/bin/llama-server -m Laya-Q8_0.gguf -c 4096 -ngl 99 --port 8199 --host 127.0.0.1 &
python3 prototypes/pengyos-jev/pengyos_jev/pengyos_jev.py decide noul \
  --state "The build server is offline and 3 deploys are queued." \
  --instructions "Is the build server unavailable?"     # → 0.99

# tests
cd prototypes/pengyos-jev && python3 -m pytest -q
```

---

*Sources: llama.cpp `b76b133` (2026-10-15) source reading + live runs;
`a4cb4c61f` (2026-10-02, `/v1/systemone`); `fc0fe4004` (2026-02-11, qwen3.5);
Buildroot 2026.08 tree at `/data/build/pengyos/buildroot-2026.08`; Hugging Face
model cards fetched 2026-10-15; TypeSafe launch post 2026-09-15; BotTalk
`c23c28e410a1c78201020000`, `fffbc21278e34914de040000`, `c276f0464c12bee090040000`,
`7b1ebd2f6009d3cf05000000`.*
