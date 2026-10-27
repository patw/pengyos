# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- `laya-model` could not build: a bare `.gguf` needs `EXTRACT_CMDS`, and its
  `LICENSE_FILES` named a file the upstream repository does not have. The
  model card (`README.md`, `license: apache-2.0`) is now a pinned extra
  download and the license file. Built in a throwaway Buildroot 2026.08 tree.
- `pengyos-jev.service` allowed `DeviceAllow=/dev/dri`, a directory, which
  systemd ignores — the service would have had no GPU. It is now
  `DeviceAllow=char-drm rw`. The `Documentation=` line is a valid URL.
- `llama-cpp-vk` has a `.hash` (the b11318 tag archive and its LICENSE), so
  `pengyos-recipe-lint` passes on the skeleton.

### Added
- **Prototype evaluation of a Jev-class (System One) decision service for
  PengyOS.** Measured 2026-10-15 with a static, Vulkan-only `llama-server`
  built from source: Laya-Q8_0 (421 M) at 8.4 ms/decision on an RTX 3070,
  304 ms/decision on a PengyOS Desktop Intel N100 using its integrated GPU as
  a Vulkan device, 405 ms/decision CPU-only on the same N100; peak RSS
  146 MB (Vulkan) / 96 MB (CPU). d1-3B-Q4_K_M: 16 ms/decision, 2.08 GB peak.
- **`pengyos-jev`** — stdlib client for the `/v1/systemone` endpoint:
  `ask` (many questions, one state, one pass), `decide` (one noul/score/
  choice), `health`. `--json` everywhere; exit 1 with one line on stderr when
  the service is down.
- **`pengyos-jev.service`** unit skeleton — loopback-only, own user, hardened.
- **Recipe skeleton** (`recipe/`): pinned `llama-cpp-vk` source recipe +
  `laya-model` weights recipe, with the two Buildroot gaps documented.
- **Offline test suite** (14 tests): payload validation, all three answer
  types, `--json`, unreachable-service path; responses are real captures.
