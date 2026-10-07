# pengyos-systemd-units

Core [systemd](https://www.freedesktop.org/software/systemd/) unit set for
[PengyOS](../../README.md), plus a small, dependency-free Python validator that
checks unit syntax and section structure.

## What's in here

- `units/` — the Core systemd unit set (`*.service`, `*.target`, `*.socket`).
- `pengyos_systemd_units/` — the validator package (standard library only).
- `tests/` — the pytest suite. The suite is the objective function: it must stay
  green.

## Validator

The validator lives in `pengyos_systemd_units.validator` (standard library only)
and parses INI-style unit files (the same format systemd uses). It enforces a
data-driven rule table:

- a `[Unit]` section is required on every unit,
- `[Section]` names are spelled correctly (no typos),
- `.service` units need `ExecStart`; `.target` units must not have one,
- `.socket` units need `ListenStream` and `Service` in `[Socket]`,
- any `[Install]` section declares `WantedBy` (`WantedBy` sanity).

Unknown section names are reported as errors. The rule table (`RULES`) is the
single source of truth, and every rule has a failing fixture in the test suite.

```python
from pengyos_systemd_units import validate_unit

for finding in validate_unit("units/sshd.service").errors:
    print(finding.rule, finding.message)
```

`validate_unit` returns a `Validation` (`.ok`, `.errors`, `.warnings`);
`validate_dir` scans a directory; and `summary` / `report` / `to_json` render
the results. See `pengyos_systemd_units/validator.py` for the full API.

## Running the tests

```sh
python3 -m pytest -q
```

Integration tests are opt-in and require a real PengyOS image; gate them with
`PENGYOS_IT=1`:

```sh
PENGYOS_IT=1 python3 -m pytest -q
```

## GitHub-ready contract

`tests/test_github_ready.py` asserts the repository ships with:

- `README.md`,
- `LICENSE` (MIT, "Copyright (c) 2026 Pat Wendorf"),
- `CHANGELOG.md`,
- `.gitignore`,
- and a green test suite.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
