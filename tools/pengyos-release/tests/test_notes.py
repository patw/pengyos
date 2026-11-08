"""Release notes are short plain text; older Markdown notes still render cleanly."""

from pathlib import Path

import pytest

from pengyos_release.cli import main
from pengyos_release.notes import MAX_CHARS, plain, problems, to_html

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = (
    "SSH key setup works again while password logins are on.\n"
    "\n"
    "- ssh-copy-id no longer gets refused halfway through\n"
    "- if you turned passwords on in 26.11.6, run: sudo pengyos-ssh-password on\n"
)
OLD = (FIXTURES / "notes-markdown-26.11.6.txt").read_text()


def test_good_notes_pass_and_come_back_unchanged():
    assert problems(GOOD) == []
    assert plain(GOOD) == GOOD.strip()


def test_the_26_11_6_notes_would_have_been_refused():
    bad = "\n".join(problems(OLD))
    assert "characters; keep it under" in bad
    assert "backticks" in bad and "** or __" in bad and "indented" in bad


@pytest.mark.parametrize("text, why", [
    ("", "empty"),
    ("x" * (MAX_CHARS + 1), "characters"),
    ("Summary\nsecond line glued on", "one-line summary"),
    ("- starts with a bullet", "one-line summary"),
    ("Summary\n\n* star bullet", "'- '"),
    ("Summary\n\n## Heading", "heading"),
    ("Summary\n\nsee [the page](https://x)", "link"),
    ("Summary\n\nrun `sudo x`", "backticks"),
])
def test_problems(text, why):
    assert any(why in p for p in problems(text)), problems(text)


def test_plain_strips_markdown_and_joins_wrapped_items():
    out = plain(OLD)
    assert "**" not in out and "`" not in out
    assert "- **SSH password logins, opt-in.**" not in out
    assert "- SSH password logins, opt-in. PengyOS's SSH accepts keys only, and that stays the default." in out
    assert "- the console installer has an SSH login page: Keys only (recommended) or Keys or password;" in out
    assert all(not line.startswith(" ") for line in out.splitlines())


def test_html_makes_dash_lines_a_list():
    h = to_html(GOOD + "\nA closing <remark> & more.")
    assert h.startswith("<p>SSH key setup works again while password logins are on.</p><ul><li>")
    assert h.count("<li>") == 2 and "</ul><p>A closing &lt;remark&gt; &amp; more.</p>" in h


def test_notes_check_cli(tmp_path, capsys):
    good, bad = tmp_path / "good.txt", tmp_path / "bad.txt"
    good.write_text(GOOD)
    bad.write_text(OLD)
    assert main(["notes-check", str(good)]) == 0
    assert main(["notes-check", str(bad)]) != 0
    assert "plain text, no Markdown" in capsys.readouterr().err
