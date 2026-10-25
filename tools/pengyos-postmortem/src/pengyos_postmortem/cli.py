"""`pengyos-postmortem` — what ended that session?

Read-only. Answers one question from the journal: when a desktop "crashed back
to a login screen" or a pile of processes vanished at once, was it a crash, a
logout, an out-of-memory kill, or one signal sent to many processes?
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys

from . import analyse, journal

__version__ = "0.1.0"

EXIT_OK = 0
EXIT_ERROR = 2


def _fmt(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pengyos-postmortem",
        description="Read the journal and say what ended a session or a burst of processes.",
    )
    p.add_argument("--boot", default="0",
                   help="journalctl boot index: 0 = this boot, -1 = previous, -2 … (default 0)")
    p.add_argument("--since", help="journalctl time window start (default: the whole boot)")
    p.add_argument("--until", help="journalctl time window end")
    p.add_argument("--window", type=float, default=2.0,
                   help="seconds within which deaths are one burst (default 2.0)")
    p.add_argument("--min-victims", type=int, default=3, dest="min_victims",
                   help="distinct processes in a burst before it counts as a broadcast "
                        "(default 3)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _render(rep: analyse.Report, entries: list[journal.Entry], boot: str) -> str:
    out: list[str] = []
    boots = rep.session.boots
    span = (f"{_fmt(entries[0].ts)} → {_fmt(entries[-1].ts)}" if entries
            else "no rule-relevant entries in this window")
    out.append(f"pengyos-postmortem {__version__} — boot {boot}, {span}")
    out.append(f"{len(entries)} rule-relevant entries (the stream is filtered as it "
               f"is read), {len(boots)} boot id(s)")
    out.append("")
    out.append(f"VERDICT: {rep.headline}")
    for line in rep.detail:
        out.append(f"  {line}")
    out.append("")

    multi = [b for b in rep.bursts if len(b.processes) > 1]
    if multi:
        hidden = len(rep.bursts) - len(multi)
        out.append(f"BURSTS ({len(multi)} with more than one process"
                   + (f", {hidden} single-process not shown)" if hidden else ")"))
        for b in multi:
            names = ", ".join(f"{c}({p})" if p is not None else c for p, c in b.processes)
            out.append(f"  {_fmt(b.start)}  {len(b.processes)} process(es), "
                       f"{b.end - b.start:.2f}s: {names}")
        out.append("")

    out.append("RULED OUT / FOUND")
    for name, found in rep.exclusions.items():
        if found:
            out.append(f"  {name:<18} FOUND — {found[0].message[:100]}")
        else:
            out.append(f"  {name:<18} no evidence")
    out.append("")

    if len(boots) > 1:
        out.append("NOTE: this window spans more than one boot — a reboot is part of the story.")
    else:
        out.append("NOTE: one boot id — the session ended without a reboot. That is why it "
                   "looked like a crash from the outside.")
    out.append("")

    out.append("WHAT THIS DOES NOT PROVE")
    for line in analyse.LIMITS:
        out.append(f"  {line}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        entries = journal.read(args.boot, args.since, args.until, keep=analyse.interesting)
    except journal.JournalError as exc:
        print(f"pengyos-postmortem: {exc}", file=sys.stderr)
        return EXIT_ERROR

    report = analyse.analyse(entries, window=args.window, min_victims=args.min_victims)
    if args.json:
        payload = {
            "tool": "pengyos-postmortem",
            "version": __version__,
            "boot": {
                "index": args.boot,
                "first": entries[0].ts,
                "last": entries[-1].ts,
                "entries": len(entries),
                "ids": report.session.boots,
                "since": args.since,
                "until": args.until,
            },
            **report.as_dict(),
        }
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print(_render(report, entries, args.boot))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
