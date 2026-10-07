"""Tests for the canonical ``repro-report.json`` emission (spec.md).

These pin the exact schema the external witness reads:

* a golden test that asserts the *whole* report dict against a fixed reference
  (every key, every per-artifact field, the summary counts), so an accidental
  schema change fails loudly; and
* a parse-back round-trip that writes the report to disk, loads it again, and
  asserts it is deep-equal to the original — the witness only ever sees the
  on-disk JSON, so that JSON must faithfully reproduce the in-memory report.

No network, root, or external tools — only tmp files and the stdlib.
"""

from __future__ import annotations

import copy
import hashlib
import json

from pengyos_repro_report import core


def _sha(data: bytes) -> str:
    """SHA-256 hex of ``data`` (a realistic build-manifest digest)."""
    return hashlib.sha256(data).hexdigest()


# ---- a controlled Comparison covering every status ------------------------
#
# Built by hand so the golden reference is deterministic; the end-to-end test
# below re-derives the same statuses through ``compare_dirs`` + the
# classification table.

def _golden_comparison():
    # Statuses are the *post-classification* values: ``classify_comparison``
    # transitions a ``STATUS_DIFFERS`` artifact onto ``STATUS_NORMALIZED`` /
    # ``STATUS_REAL``. ``build_report`` reports ``artifact.status`` verbatim, so
    # the golden comparison must carry the classified statuses directly.
    artifacts = [
        core.Artifact("images/a.bin", core.STATUS_IDENTICAL, _sha(b"alpha"), _sha(b"alpha")),
        core.Artifact("images/b.bin", core.STATUS_IDENTICAL, _sha(b"beta"), _sha(b"beta")),
        # Differ only in a recorded build timestamp -> normalized.
        core.Artifact("images/c.bin", core.STATUS_NORMALIZED, _sha(b"c-2026"), _sha(b"c-2021"), normalized=True),
        # A genuine body change -> real.
        core.Artifact("images/d.bin", core.STATUS_REAL, _sha(b"d-a"), _sha(b"d-b"), normalized=False),
        # Present on only one side.
        core.Artifact("images/e.bin", core.STATUS_MISSING_A, _sha(b"e"), None),
        core.Artifact("images/f.bin", core.STATUS_MISSING_B, None, _sha(b"f")),
    ]
    return core.Comparison(artifacts=artifacts)


def _golden_report():
    """The exact expected report dict for :func:`_golden_comparison`."""
    return {
        "schema": core.SCHEMA_VERSION,
        "score": 0.5,  # matching (2 identical + 1 normalized) / 6 total
        "artifacts": [
            {
                "name": "images/a.bin",
                "status": core.STATUS_IDENTICAL,
                "hash_a": _sha(b"alpha"),
                "hash_b": _sha(b"alpha"),
                # ``normalized`` is None for a byte-identical artifact (no
                # difference to explain); it is a bool only for a differing one.
                "normalized": None,
            },
            {
                "name": "images/b.bin",
                "status": core.STATUS_IDENTICAL,
                "hash_a": _sha(b"beta"),
                "hash_b": _sha(b"beta"),
                "normalized": None,
            },
            {
                "name": "images/c.bin",
                "status": core.STATUS_NORMALIZED,
                "hash_a": _sha(b"c-2026"),
                "hash_b": _sha(b"c-2021"),
                "normalized": True,
            },
            {
                "name": "images/d.bin",
                "status": core.STATUS_REAL,
                "hash_a": _sha(b"d-a"),
                "hash_b": _sha(b"d-b"),
                "normalized": False,
            },
            {
                "name": "images/e.bin",
                "status": core.STATUS_MISSING_A,
                "hash_a": _sha(b"e"),
                "hash_b": None,
                "normalized": None,
            },
            {
                "name": "images/f.bin",
                "status": core.STATUS_MISSING_B,
                "hash_a": None,
                "hash_b": _sha(b"f"),
                "normalized": None,
            },
        ],
        "summary": {
            "total": 6,
            "matching": 3,
            # Canonical counts bucket the *final* statuses; ``differs`` never
            # survives classification, so it is absent and ``real`` is fixed.
            "counts": {
                core.STATUS_IDENTICAL: 2,
                core.STATUS_NORMALIZED: 1,
                core.STATUS_REAL: 1,
                core.STATUS_MISSING_A: 1,
                core.STATUS_MISSING_B: 1,
            },
        },
    }


# ---- golden schema --------------------------------------------------------


def test_build_report_matches_golden_schema():
    """The report equals the pinned golden dict exactly (keys, order, values)."""
    report = core.build_report(_golden_comparison())
    assert report == _golden_report()

    # The schema string is pinned; a bump is an intentional incompatible change.
    assert report["schema"] == core.SCHEMA_VERSION
    assert report["schema"] == "pengyos-repro-report/1"


def test_report_top_level_keys_are_exact():
    """The top-level report has exactly the pinned keys, in order."""
    report = core.build_report(_golden_comparison())
    assert list(report.keys()) == ["schema", "score", "artifacts", "summary"]


def test_report_artifact_fields_are_exact():
    """Each artifact entry has exactly the pinned fields, in order."""
    report = core.build_report(_golden_comparison())
    for entry in report["artifacts"]:
        assert list(entry.keys()) == ["name", "status", "hash_a", "hash_b", "normalized"]


def test_report_summary_counts_cover_all_statuses():
    """The summary counts bucket every final status, even when the count is zero.

    Uses an all-identical comparison so the ``normalized``/``real``/``missing``
    buckets are genuinely zero — a golden dict that omits a zero bucket would
    fail this.
    """
    identical = core.Comparison(
        artifacts=[
            core.Artifact("images/a.bin", core.STATUS_IDENTICAL, "h", "h"),
            core.Artifact("images/b.bin", core.STATUS_IDENTICAL, "h", "h"),
        ]
    )
    report = core.build_report(identical)
    counts = report["summary"]["counts"]
    assert set(counts.keys()) == {
        core.STATUS_IDENTICAL,
        core.STATUS_NORMALIZED,
        core.STATUS_REAL,
        core.STATUS_MISSING_A,
        core.STATUS_MISSING_B,
    }
    assert counts[core.STATUS_IDENTICAL] == 2
    # Every non-matching bucket is present and zero (not omitted).
    assert counts[core.STATUS_NORMALIZED] == 0
    assert counts[core.STATUS_REAL] == 0
    assert counts[core.STATUS_MISSING_A] == 0
    assert counts[core.STATUS_MISSING_B] == 0


def test_score_is_matching_over_total():
    """score == matching / total, matching = identical + normalized."""
    comparison = _golden_comparison()
    assert comparison.score() == 3 / 6
    report = core.build_report(comparison)
    assert report["score"] == report["summary"]["matching"] / report["summary"]["total"]


def test_artifacts_are_deterministically_ordered():
    """Artifacts are sorted by name so the JSON is stable across runs."""
    import random

    names = ["images/z.bin", "images/a.bin", "images/m.bin", "images/p.bin"]
    artifacts = [core.Artifact(n, core.STATUS_IDENTICAL, "h", "h") for n in names]
    random.shuffle(artifacts)
    report = core.build_report(core.Comparison(artifacts=artifacts))
    assert [a["name"] for a in report["artifacts"]] == sorted(names)


# ---- parse-back round-trip ------------------------------------------------


def test_write_load_roundtrip_deep_equal(tmp_path):
    """A report survives a write->load cycle unchanged (the witness reads the file)."""
    original = core.build_report(_golden_comparison())
    path = tmp_path / "nested" / "images" / core.REPORT_FILENAME

    written = core.write_report(original, str(path))
    assert written == str(path)
    assert path.exists()

    loaded = core.load_report(str(path))
    # Deep-equal: the on-disk JSON faithfully reproduces the in-memory report.
    assert loaded == original


def test_roundtrip_json_is_canonical_pretty_text(tmp_path):
    """The file is valid, indent-2 JSON ending in a newline."""
    original = core.build_report(_golden_comparison())
    path = tmp_path / core.REPORT_FILENAME
    core.write_report(original, str(path))

    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    # Round-trips through the JSON parser.
    parsed = json.loads(text)
    assert parsed == original
    # Pretty (indent-2): the second line is indented.
    assert "\n  " in text


def test_roundtrip_preserves_schema_version(tmp_path):
    """The pinned schema survives the round-trip."""
    original = core.build_report(_golden_comparison())
    path = tmp_path / core.REPORT_FILENAME
    core.write_report(original, str(path))
    loaded = core.load_report(str(path))
    assert loaded["schema"] == core.SCHEMA_VERSION


def test_compute_score_from_loaded_report(tmp_path):
    """`compute_score` reads the score back from a loaded report dict."""
    original = core.build_report(_golden_comparison())
    path = tmp_path / core.REPORT_FILENAME
    core.write_report(original, str(path))
    loaded = core.load_report(str(path))
    assert core.compute_score(loaded) == 0.5


def test_valid_artifacts_accepts_and_rejects(tmp_path):
    """`valid_artifacts` accepts a well-formed report and rejects a malformed one."""
    original = core.build_report(_golden_comparison())
    assert core.valid_artifacts(original["artifacts"]) is True

    bad = [{"name": "x"}]  # missing "status"
    assert core.valid_artifacts(bad) is False
    bad_non_dict = ["not-a-dict"]
    assert core.valid_artifacts(bad_non_dict) is False


def test_build_report_does_not_mutate_input(tmp_path):
    """Rebuilding a report twice from the same Comparison is idempotent."""
    comparison = _golden_comparison()
    first = core.build_report(comparison)
    second = core.build_report(comparison)
    assert first == second == _golden_report()


def test_golden_reference_matches_manual_copy():
    """The golden reference is not accidentally aliasing the built report."""
    reference = _golden_report()
    built = core.build_report(_golden_comparison())
    # A defensive copy that would fail if the test author aliased the dicts.
    assert copy.deepcopy(reference) == built


# ---- end-to-end: CLI writes the witness report, then we parse it back ------


def _make_build(tmp_path, name, files):
    build = tmp_path / name
    for rel, data in files.items():
        target = build / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return build


def test_cli_writes_witness_path_and_roundtrips(tmp_path, capsys):
    """``diff --out <build>/images/repro-report.json`` writes a parseable file.

    The witness only ever reads the on-disk JSON, so this ties the CLI end to
    end: build the in-memory report, run the CLI to write it, parse the file
    back, and assert the file faithfully reproduces the in-memory report.
    """
    from pengyos_repro_report import cli

    a = _make_build(tmp_path, "a", {"images/f.bin": b"same", "images/g.bin": b"same"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"same", "images/g.bin": b"same"})
    out = a / "images" / core.REPORT_FILENAME

    expected = core.build_report(core.compare_sources(str(a), str(b)))

    rc = cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out)])
    assert rc == 0

    # Parse the on-disk witness report back and confirm it deep-equals.
    with open(out, encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert loaded == expected
    assert loaded["schema"] == core.SCHEMA_VERSION
    assert loaded["score"] == 1.0
    assert [art["name"] for art in loaded["artifacts"]] == ["images/f.bin", "images/g.bin"]
    capsys.readouterr()


def test_cli_json_flag_is_parseable_and_roundtrips(tmp_path, capsys):
    """``--json`` prints the exact report; parsing it reproduces the in-memory one."""
    from pengyos_repro_report import cli

    a = _make_build(tmp_path, "a", {"images/f.bin": b"same"})
    b = _make_build(tmp_path, "b", {"images/f.bin": b"same"})
    out = a / "images" / core.REPORT_FILENAME

    expected = core.build_report(core.compare_sources(str(a), str(b)))
    rc = cli.main(["diff", "--a", str(a), "--b", str(b), "--out", str(out), "--json"])
    assert rc == 0

    printed = capsys.readouterr().out
    parsed = json.loads(printed)
    assert parsed == expected
    assert core.valid_artifacts(parsed["artifacts"])
