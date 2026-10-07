"""Tests for the data-driven difference classification table.

These cover :mod:`pengyos_repro_report.classify`: the ``Rule`` table,
:func:`classify_payloads` (payload -> normalized/real), and
:func:`classify_comparison` (writes the verdict back onto a core ``Artifact``).
Plus a couple of end-to-end checks through :func:`pengyos_repro_report.core.compare_dirs`.

No network, root, or external tools — only tmp files and the stdlib.
"""

from __future__ import annotations

from pengyos_repro_report import classify, core


# ---- classify_payloads: the verdict table ---------------------------------


def test_identical_payloads_are_normalized():
    # Identical payloads short-circuit: nothing to classify, so they "match".
    assert classify.classify_payloads(b"same", b"same") is True


def test_pure_byte_difference_is_real():
    # A wholesale byte change with no known non-determinism is a real failure.
    assert classify.classify_payloads(b"original", b"changed") is False


def test_iso_build_timestamp_is_normalized():
    payloads = (b"log 2026-09-18T10:00:00", b"log 2021-01-01T00:00:00")
    assert classify.classify_payloads(*payloads) is True


def test_unix_timestamp_is_normalized():
    payloads = (b"v 1700000000", b"v 1600000000")
    assert classify.classify_payloads(*payloads) is True


def test_tar_mtime_field_is_normalized():
    payloads = (b"hdr 00012345 tail", b"hdr 00054321 tail")
    assert classify.classify_payloads(*payloads) is True


def test_build_id_is_normalized():
    payloads = (b"BuildRoot=/a/b/c data", b"BuildRoot=/x/y/z data")
    assert classify.classify_payloads(*payloads) is True


def test_unknown_difference_is_real():
    # Neither payload carries a known field; the difference is real.
    assert classify.classify_payloads(b"aaaa", b"bbbb") is False


def test_known_field_plus_real_body_is_real():
    # A rule blanks the timestamp, but a real body change survives -> real.
    payloads = (b"2026-09-18T10:00:00AAAA", b"2021-01-01T00:00:00BBBB")
    assert classify.classify_payloads(*payloads) is False


def test_two_known_fields_are_normalized():
    # Timestamp blanked first, then build-id: both explained -> normalized.
    payloads = (b"2026-09-18T10:00:00 BuildRoot=/a", b"2021-01-01T00:00:00 BuildRoot=/b")
    assert classify.classify_payloads(*payloads) is True


def test_custom_rules_override_default_table():
    a, b = b"2026-09-18T10:00:00", b"2021-01-01T00:00:00"
    # With the default table the timestamp is normalized...
    assert classify.classify_payloads(a, b) is True
    # ...but an empty rule table leaves it unexplained -> real.
    assert classify.classify_payloads(a, b, rules=()) is False


def test_rule_order_first_match_wins():
    # A contrived rule that blanks *everything* wins before the timestamp rule.
    blank_everything = classify.rule_regex(r".*", name="all", reason="test")
    a, b = b"2026-09-18T10:00:00AAAA", b"2021-01-01T00:00:00BBBB"
    assert classify.classify_payloads(a, b, rules=(blank_everything,)) is True


# ---- Rule table internals --------------------------------------------------


def test_rule_explain_pair_reports_match_flag():
    rule = classify.DEFAULT_RULES[0]  # build-timestamp rule
    matched_a, matched_b, matched = rule.explain_pair(b"2026-09-18T10:00:00", b"2021-01-01T00:00:00")
    assert matched is True
    # The timestamp is blanked in both; the residuals are equal.
    assert matched_a == matched_b


def test_rule_explain_pair_unmatched_when_absent():
    rule = classify.DEFAULT_RULES[0]
    _, _, matched = rule.explain_pair(b"no timestamp here", b"neither here")
    assert matched is False


def test_rule_regex_matches_bytes_pattern():
    rule = classify.rule_regex(rb"\x00{4}", name="nulls", reason="test")
    a, b = b"ab\x00\x00\x00\x00cd", b"ab\x00\x00\x00\x00ef"
    blanked_a, blanked_b, matched = rule.explain_pair(a, b)
    assert matched is True
    # Only the trailing bytes differ now.
    assert blanked_a != blanked_b


def test_rule_regex_requires_presence_in_both():
    # A match on only one side does not explain the difference.
    rule = classify.rule_regex(rb"\d{10}", name="digits", reason="test")
    _, _, matched = rule.explain_pair(b"1234567890 x", b"plain text")
    assert matched is False


def test_default_table_has_expected_rules():
    names = [r.name for r in classify.DEFAULT_RULES]
    assert "build-timestamp" in names
    assert "unix-timestamp" in names
    assert "tar-mtime" in names
    assert "build-id" in names


def test_crc32_rule_is_not_in_default_table():
    # Deliberately excluded: an 8-hex run is an unreliable CRC marker.
    assert all(r.name != "crc32" for r in classify.DEFAULT_RULES)
    # ...but is still available for users who know their format.
    assert classify.rule_crc32().name == "crc32"


# ---- classify_comparison: writes verdicts back onto artifacts --------------


def _diff_artifact(name, status=core.STATUS_DIFFERS):
    return core.Artifact(name=name, status=status, hash_a="a", hash_b="b")


def test_classify_comparison_marks_normalized():
    comparison = core.Comparison(artifacts=[_diff_artifact("img")])
    verdicts = classify.classify_comparison(
        comparison, lambda n: (b"2026-09-18T10:00:00", b"2021-01-01T00:00:00")
    )
    (artifact,) = comparison.artifacts
    assert verdicts == [core.STATUS_NORMALIZED]
    assert artifact.status == core.STATUS_NORMALIZED
    assert artifact.normalized is True


def test_classify_comparison_marks_real():
    comparison = core.Comparison(artifacts=[_diff_artifact("img")])
    verdicts = classify.classify_comparison(
        comparison, lambda n: (b"original", b"changed")
    )
    (artifact,) = comparison.artifacts
    assert verdicts == [core.STATUS_REAL]
    assert artifact.status == core.STATUS_REAL
    assert artifact.normalized is False


def test_classify_comparison_leaves_non_differing_untouched():
    identical = core.Artifact("ok", core.STATUS_IDENTICAL, "h", "h")
    missing = core.Artifact("gone", core.STATUS_MISSING_A, "h", None)
    differing = _diff_artifact("diff")
    comparison = core.Comparison(artifacts=[identical, missing, differing])

    # load_pair is only consulted for the differing artifact.
    verdicts = classify.classify_comparison(
        comparison, lambda n: (b"2026-09-18T10:00:00", b"2021-01-01T00:00:00")
    )

    # Artifact is a frozen dataclass, so classify_comparison replaces the
    # element in the list (via dataclasses.replace); re-fetch rather than
    # trusting the pre-classification local references.
    by_name = {art.name: art for art in comparison.artifacts}
    assert verdicts == [core.STATUS_NORMALIZED]
    assert by_name["ok"].status == core.STATUS_IDENTICAL
    assert by_name["gone"].status == core.STATUS_MISSING_A
    assert by_name["diff"].status == core.STATUS_NORMALIZED


def test_classify_comparison_missing_payload_is_real():
    # A differing artifact whose payload is unavailable on one side is real.
    comparison = core.Comparison(artifacts=[_diff_artifact("img")])
    verdicts = classify.classify_comparison(
        comparison, lambda n: (b"2026-09-18T10:00:00", None)
    )
    (artifact,) = comparison.artifacts
    assert verdicts == [core.STATUS_REAL]
    assert artifact.status == core.STATUS_REAL
    assert artifact.normalized is False


def test_never_loads_returns_none_pair():
    assert classify._never_loads("anything") == (None, None)


# ---- end-to-end through core.compare_dirs ----------------------------------


def test_compare_dirs_timestamp_only_diff_counts_as_matching(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    (a / "images").mkdir(parents=True)
    (b / "images").mkdir(parents=True)
    # Two builds of the same artifact differing only in a recorded timestamp.
    (a / "images" / "kernel.img").write_bytes(b"rootfs 2026-09-18T10:00:00")
    (b / "images" / "kernel.img").write_bytes(b"rootfs 2021-01-01T00:00:00")

    comparison = core.compare_dirs(str(a), str(b))
    (artifact,) = comparison.artifacts
    assert artifact.status == core.STATUS_NORMALIZED
    assert artifact.matching is True
    # A normalized difference still counts toward the reproducibility score.
    assert comparison.score() == 1.0


def test_compare_dirs_real_diff_marks_real_and_scores_below_one(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    (a / "images").mkdir(parents=True)
    (b / "images").mkdir(parents=True)
    (a / "images" / "kernel.img").write_bytes(b"rootfs-a")
    (b / "images" / "kernel.img").write_bytes(b"rootfs-b-changed")

    comparison = core.compare_dirs(str(a), str(b))
    (artifact,) = comparison.artifacts
    assert artifact.status == core.STATUS_REAL
    assert artifact.matching is False
    assert comparison.score() < 1.0


def test_compare_dirs_mixed_diff_scores_between(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    (a / "images").mkdir(parents=True)
    (b / "images").mkdir(parents=True)
    # One artifact normalized (timestamp only); one real (body change).
    (a / "images" / "kernel.img").write_bytes(b"rootfs 2026-09-18T10:00:00")
    (b / "images" / "kernel.img").write_bytes(b"rootfs 2021-01-01T00:00:00")
    (a / "images" / "initrd.img").write_bytes(b"init-a")
    (b / "images" / "initrd.img").write_bytes(b"init-b-changed")

    comparison = core.compare_dirs(str(a), str(b))
    by_name = {art.name: art for art in comparison.artifacts}
    assert by_name["images/kernel.img"].status == core.STATUS_NORMALIZED
    assert by_name["images/initrd.img"].status == core.STATUS_REAL
    # 1 of 2 matches -> score 0.5.
    assert comparison.score() == 0.5
