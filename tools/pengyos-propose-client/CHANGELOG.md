# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-09-18

### Added
- Initial scaffold of the PengyOS propose-client.
- `pengyos-propose` CLI with `submit` and `render` subcommands.
- Proposal data model and JSON loading (`pengyos_propose.proposal`).
- Markdown renderer for human review (`pengyos_propose.render`).
- Mockable HTTP submit client for the propose/approve gate (`pengyos_propose.gate`).
- Tier model and human-approval predicate (`pengyos_propose.gating`).
- **Tier-gating enforcement:** a Tier 2 (human-signed) proposal is refused with
  `GateError` before any request is sent unless `--human-approved` is given; the
  CLI surfaces this as exit 1 with a clear message. Cannot bypass the human gate.
- **Required-fields-at-load enforcement:** a proposal missing every human-usable
  field (`title`, `component` or `tier`) is rejected by the loader with a clear
  error before `render`/`submit` do anything; the CLI maps it to exit 1.
- GitHub-ready scaffolding: README, LICENSE (MIT), CHANGELOG, `.gitignore`.
- Test suite with a `test_github_ready` contract check.

[0.1.0]: https://github.com/patw/pengyos/tree/main/tools/pengyos-propose-client
