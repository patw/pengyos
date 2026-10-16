#!/usr/bin/env python3
"""pengyos-jev — talk to a local System One (Jev-class) decision service.

Stdlib only. The service is llama-server's `/v1/systemone` endpoint
(TypeSafe-compatible): a state in, typed decisions out, one forward pass,
no text generation. This CLI builds the request, parses the answer, and
never talks to anything but the loopback address unless you point it
somewhere else on purpose.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = os.environ.get("PENGYOS_JEV_URL", "http://127.0.0.1:8199")
DEFAULT_TIMEOUT = 60.0


class JevError(Exception):
    """The decision service could not be reached, or answered badly."""


def build_payload(state: str, questions: dict) -> dict:
    """Assemble the /v1/systemone request body.

    questions maps an id to {"type": choice|score|noul, ...}:
      choice -> "criteria": {option: description|null}
      score  -> "criteria": [level descriptions, lowest first]
      noul   -> "criteria" optional {true: ..., false: ...}
    """
    if not isinstance(state, str) or not state.strip():
        raise JevError("state must be a non-empty string")
    if not questions:
        raise JevError("at least one question is required")
    out = {}
    for qid, spec in questions.items():
        if not isinstance(spec, dict) or "type" not in spec:
            raise JevError(f"question {qid!r} needs a type")
        qtype = spec["type"]
        if qtype not in ("choice", "score", "noul"):
            raise JevError(f"question {qid!r} has unknown type {qtype!r}")
        if not spec.get("instructions"):
            raise JevError(f"question {qid!r} needs instructions")
        entry = {"type": qtype, "instructions": spec["instructions"]}
        criteria = spec.get("criteria")
        if qtype == "choice":
            if not isinstance(criteria, dict) or not criteria:
                raise JevError(f"choice question {qid!r} needs a criteria object")
            entry["criteria"] = criteria
        elif qtype == "score":
            if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
                raise JevError(f"score question {qid!r} needs 2..10 level descriptions")
            entry["criteria"] = criteria
        elif criteria is not None:
            if not isinstance(criteria, dict) or not {"true", "false"} <= set(criteria):
                raise JevError(f"noul question {qid!r} criteria needs true and false")
            entry["criteria"] = criteria
        out[qid] = entry
    return {"state": state, "questions": out}


def post_decide(url: str, payload: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/systemone",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise JevError(f"service returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise JevError(f"decision service not reachable at {url}: {exc}") from exc
    if "answers" not in body:
        raise JevError(f"unexpected response (no answers): {str(body)[:200]}")
    return body


def parse_questions(pairs: list[str]) -> dict:
    """Turn --question flags into the questions dict.

    Format: ID:TYPE:INSTRUCTIONS[:criteria,criteria,...]
    """
    out: dict = {}
    for i, raw in enumerate(pairs):
        parts = raw.split(":", 2)
        if len(parts) < 3:
            raise JevError(f"--question {raw!r} must be id:type:instructions[:criteria]")
        qid, qtype, rest = parts[0], parts[1], parts[2]
        instructions, _, crit = rest.partition("::")
        spec: dict = {"type": qtype, "instructions": instructions}
        if crit:
            levels = [c for c in crit.split(",") if c]
            if qtype == "score":
                spec["criteria"] = levels
            elif qtype == "choice":
                spec["criteria"] = {lvl: None for lvl in levels}
            elif qtype == "noul" and len(levels) == 2:
                spec["criteria"] = {"true": levels[0], "false": levels[1]}
        out[qid] = spec
    if not out:
        raise JevError("no --question given")
    return out


def summarize(answers: dict) -> str:
    lines = []
    for qid, ans in answers.items():
        kind = ans.get("type")
        if kind == "choice":
            lines.append(f"{qid}: {ans.get('choice')} (confidence {ans.get('confidence', 0):.3f})")
        elif kind == "noul":
            lines.append(f"{qid}: noul={ans.get('noul', 0):.3f}")
        elif kind == "score":
            lines.append(f"{qid}: score={ans.get('score', 0):.2f}")
        else:
            lines.append(f"{qid}: {json.dumps(ans)}")
    return "\n".join(lines)


def cmd_ask(args: argparse.Namespace) -> int:
    if args.questions_file:
        with open(args.questions_file) as fh:
            questions = json.load(fh)
    else:
        questions = parse_questions(args.question)
    state = sys.stdin.read() if args.state == "-" else args.state
    body = post_decide(args.url, build_payload(state, questions), args.timeout)
    if args.json:
        print(json.dumps(body, indent=2))
    else:
        print(summarize(body["answers"]))
        usage = body.get("usage", {})
        print(f"# input_tokens={usage.get('input_tokens')} output_tokens={usage.get('output_tokens')}")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    try:
        with urllib.request.urlopen(args.url.rstrip("/") + "/v1/models", timeout=args.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise JevError(f"decision service not reachable at {args.url}: {exc}") from exc
    models = data.get("data", [])
    if args.json:
        print(json.dumps(models, indent=2))
        return 0
    for m in models:
        arch = m.get("architecture") or {}
        mods = arch.get("output_modalities", [])
        print(f"{m.get('id')}: decision={'decisions' in mods} type={arch.get('decision_type', '-')}")
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    """One-shot convenience: noul / score / choice from the command line."""
    kind = args.kind
    spec: dict = {"type": kind, "instructions": args.instructions}
    if kind == "choice":
        spec["criteria"] = {o: None for o in args.criteria}
    elif kind == "score":
        spec["criteria"] = args.criteria
    elif args.criteria:
        spec["criteria"] = {"true": args.criteria[0], "false": args.criteria[1]}
    body = post_decide(args.url, build_payload(args.state, {"q": spec}), args.timeout)
    ans = body["answers"]["q"]
    if args.json:
        print(json.dumps(ans, indent=2))
    elif kind == "noul":
        print(f"{ans['noul']:.4f}")
    elif kind == "score":
        print(f"{ans['score']:.3f}")
    else:
        print(ans["choice"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pengyos-jev", description=__doc__.splitlines()[0])
    p.add_argument("--url", default=DEFAULT_URL, help="decision service base URL")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ask", help="ask several questions about one state")
    a.add_argument("--state", required=True, help="text to evaluate, or - for stdin")
    a.add_argument("--question", action="append", default=[],
                   help="id:type:instructions[:criteria] (repeatable)")
    a.add_argument("--questions-file", help="JSON file of questions")
    a.set_defaults(func=cmd_ask)

    d = sub.add_parser("decide", help="one question: noul | score | choice")
    d.add_argument("kind", choices=["noul", "score", "choice"])
    d.add_argument("--state", required=True)
    d.add_argument("--instructions", required=True)
    d.add_argument("--criteria", nargs="*", default=[],
                   help="score: levels low->high; choice: options; noul: true,false labels")
    d.set_defaults(func=cmd_decide)

    h = sub.add_parser("health", help="is the service up, and is it a decision model?")
    h.set_defaults(func=cmd_health)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except JevError as exc:
        print(f"pengyos-jev: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"pengyos-jev: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
