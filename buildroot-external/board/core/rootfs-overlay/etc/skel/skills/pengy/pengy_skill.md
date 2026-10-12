# Pengy Skill

**What Pengy is, and how it works.** Pengy is the local-first AI agent that
ships in this OS. This skill is the map of it: where its config lives, what it
can do, and how to drive it from a shell.

## The shape of it

One agent core, three front ends, one config. Pengy talks to **any
OpenAI-compatible endpoint** — Ollama, llama.cpp, vLLM, LM Studio, OpenRouter,
Groq, OpenAI — and does all of its work on your own machine.

| Front end | Command | What it is |
|---|---|---|
| CLI | `pengy-cli` | Terminal REPL with slash commands, plus a single-shot mode for scripts and cron |
| Web | `pengy-web` | Browser UI on `http://127.0.0.1:5000` (single-user, personal use) |
| Desktop | `pengy` | Qt6 window with markdown rendering, chat sidebar, file attachments (Desktop edition) |

All three read the same config and the same chat history, so a chat started in
the GUI continues in the CLI.

## Where everything lives

| Path | What |
|---|---|
| `~/.config/pengy/settings.json` | base URL, API key, model, system message, tool confirmation, timeouts, theme |
| `~/.config/pengy/chats.json` | every chat session, shared by all three front ends |
| `~/.config/pengy/tasks.json` | reusable prompt templates with `%placeholder%` tokens |
| `~/skills/` | the skills, listed in `~/skills/skill_index.md` |

`settings.json` is plain JSON and safe to edit by hand; every field has a
default, so a missing key is not an error. The system message is a template —
`{date}`, `{username}`, `{hostname}` and `{osinfo}` are filled in on every
request, and it is sent fresh each turn rather than stored in the history.

## Driving it from a shell

```sh
pengy-cli "summarize the last 50 lines of /var/log/messages"   # single-shot
pengy-cli --model qwen3:8b "..."                               # pin the model
pengy-cli                                                      # interactive REPL
```

In the REPL: `/model <name>` switches model, `/models` lists what the endpoint
offers, `/baseurl <url>` and `/apikey <key>` set the endpoint, `/config` shows
the whole config. `@path` in a message attaches a file.

Single-shot mode makes a throw-away chat and exits, which is what makes it
usable from cron and scripts. **Pin `--model` in any automated call** — a
machine with no model configured answers "pick a model" instead of doing the
work, and a cron job has no way to answer.

## Tools

Pengy's built-in tools are what make it an agent and not a chat window: read
files and images, write and edit files (transactionally, with a dry-run diff),
run bash and Python (locally, or over ssh on another host), search the web,
fetch and download URLs, walk a directory tree, grep a codebase, glob files,
keep a to-do list for long jobs, and ask a clarifying question when a request is
genuinely ambiguous.

**Tool confirmation** gates what runs without asking: `all` (confirm every
call), `safe` (read-only tools run, writes and commands ask), `none` (run
freely). It is a setting in `settings.json` (`tool_confirmation`), not a
property of the model — set it to match how much you trust the endpoint you
pointed at.

## Skills — how Pengy grows

Tools are fixed; skills are not. A skill is a markdown file an agent reads
before acting, plus an optional script that does the deterministic part. That
is the whole mechanism: no protocol, no server, no registry — a file in
`~/skills/` and a row in `~/skills/skill_index.md`.

This image ships two skills (`pengyos`, `pengy`) and an index that tells Pengy
to read it before starting. Ask Pengy to write a skill for something you do
often and it writes the markdown, the script, and the index row in one sitting.

## On PengyOS specifically

Pengy is a userland program like any other: it lives in the read-only system
image, its config lives in your home on the `data` partition, and an OS update
replaces the binary without touching your chats. `pengy-web` is not started for
you — run it, or make your own systemd user unit for it. See the `pengyos`
skill for what the OS will and will not let you change.
