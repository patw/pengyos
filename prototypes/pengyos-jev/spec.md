# spec — pengyos-jev

The contract for the Jev-class decision-service client. Acceptance criteria are
testable and offline; the behaviour layer activates only when a service is
reachable.

## Scope

*in*: build a valid `/v1/systemone` request for a state and 1..N typed
questions; send it to a loopback service; render the answers; report failures
as clean exits.
*out*: running the model, converting weights, serving policy, model choice.
That is the service's business (see `docs/jev-model-service-viability.md`).

## Behaviour

### CLI

```
pengyos-jev [--url URL] [--timeout S] [--json] <command>

ask     --state TEXT|- (--question id:type:instructions[:criteria] | --questions-file F)
decide  noul|score|choice --state TEXT --instructions TEXT [--criteria ...]
health
```

1. `--url` defaults to `http://127.0.0.1:8199`, overridable by
   `PENGYOS_JEV_URL`. The client never dials anything by itself beyond that URL.
2. `ask` accepts several questions over one state; the service answers them in
   one pass and `usage.output_tokens` is 0.
3. Validation happens **before** the request:
   - `choice` needs a criteria object (options).
   - `score` needs 2..10 level descriptions, lowest first.
   - `noul` criteria, when given, needs `true` and `false`.
   - `type` must be one of the three; `instructions` must be non-empty;
     `state` must be non-empty.
4. Output: human form is one line per question (`choice` + confidence,
   `noul=` probability, `score=` expected level). `--json` prints the service's
   JSON verbatim. Machine output is always valid JSON.
5. Failure: an unreachable service, a non-2xx, or a response without `answers`
   is exit 1 with one line on stderr. No traceback.
6. Stdlib only; Python ≥ 3.10; no network at import time.

## Acceptance criteria

| # | Given | When | Then |
|---|---|---|---|
| A1 | a state and a `choice` question with options | `ask` | request carries `criteria` as an object |
| A2 | a `score` question with 1 level | `ask` | exit 1, criteria-size message, no request sent |
| A3 | a `score` question with 4 levels | `ask` | answer renders `score=` expected level |
| A4 | a `noul` question | `decide` | stdout is a probability in 0..1 |
| A5 | any command with `--json` | run | stdout parses as JSON |
| A6 | service down | any command | exit 1, one stderr line, no traceback |
| A7 | a captured response fixture | `summarize` | all three types render |
| A8 | `health` against a decision model | run | reports `decision=True` and the model's decision type |

## Non-goals

- No model download, conversion, quantization, or serving in this tool.
- No Python SDK dependency (`pip install laya` and friends stay out; the
  service is the interface).
- No remote endpoints by default — a decision service is a local service.
