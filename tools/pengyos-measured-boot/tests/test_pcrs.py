"""Tests for PCR value parsing (``pengyos_measured_boot.pcrs``).

No real ``swtpm`` is invoked: parsing operates on strings, so these tests need
no root, hardware, or network.
"""

from __future__ import annotations

import json

import pytest

from pengyos_measured_boot.errors import PengyOSMeasuredBootError
from pengyos_measured_boot.pcrs import Pcrs, parse_pcrs, parse_pcrs_file
from pengyos_measured_boot.swtpm import SwtpmBootConfig, SwtpmRunner


def _sha256_hex(n: int) -> str:
    return format(n, "064x")


# --- parse_pcrs: tpm2-tools JSON format -----------------------------------


def test_parse_tpm2_tools_json() -> None:
    raw = json.dumps(
        {
            "pcr-0": {"sha256": _sha256_hex(0)},
            "pcr-7": {"sha256": _sha256_hex(0xDEADBEEF)},
        }
    )
    pcrs = parse_pcrs(raw)
    assert pcrs == Pcrs({0: 0, 7: 0xDEADBEEF})
    assert pcrs[7] == 0xDEADBEEF


def test_parse_tpm2_tools_json_prefers_requested_algo() -> None:
    # Two algorithms present; the requested one (sha256) is used.
    raw = json.dumps(
        {
            "pcr-0": {"sha1": "00", "sha256": _sha256_hex(42)},
        }
    )
    assert parse_pcrs(raw, algo="sha256").values == {0: 42}
    # A different requested algo selects that digest instead.
    assert parse_pcrs(raw, algo="sha1").values == {0: 0}


def test_parse_tpm2_tools_json_single_algo_implicit() -> None:
    # Only one algorithm present -> it is used without needing the algo hint.
    raw = json.dumps({"pcr-3": {"sha256": _sha256_hex(7)}})
    assert parse_pcrs(raw).values == {3: 7}


# --- parse_pcrs: committed fixture format ----------------------------------


def test_parse_fixture_flat_map_pcr_dash_n() -> None:
    raw = json.dumps({"pcr-0": _sha256_hex(1), "pcr-1": _sha256_hex(2)})
    assert parse_pcrs(raw).values == {0: 1, 1: 2}


def test_parse_fixture_flat_map_pcr_n() -> None:
    # The "pcrN" spelling (no dash) is accepted too.
    raw = json.dumps({"pcr0": _sha256_hex(5), "pcr7": _sha256_hex(6)})
    assert parse_pcrs(raw).values == {0: 5, 7: 6}


def test_parse_fixture_short_hex() -> None:
    # Short hex digests (not zero-padded) still parse.
    assert parse_pcrs('{"pcr-0": "dead"}').values == {0: 0xDEAD}


# --- Pcrs container behaviour ----------------------------------------------


def test_pcrs_get_and_contains() -> None:
    pcrs = parse_pcrs('{"pcr-7": "1"}')
    assert 7 in pcrs
    assert 8 not in pcrs
    assert pcrs.get(7) == 1
    assert pcrs.get(8) is None
    assert pcrs.get(8, 99) == 99


def test_pcrs_indices_len_iter() -> None:
    pcrs = parse_pcrs('{"pcr-0": "0", "pcr-1": "1", "pcr-7": "7"}')
    assert pcrs.indices() == frozenset({0, 1, 7})
    assert len(pcrs) == 3
    assert sorted(pcrs) == [0, 1, 7]


def test_pcrs_equality_with_mapping() -> None:
    pcrs = parse_pcrs('{"pcr-0": "0"}')
    assert pcrs == {0: 0}
    assert pcrs != {0: 1}
    assert pcrs != "not a mapping"


# --- to_json round-trip -----------------------------------------------------


def test_to_json_round_trips() -> None:
    original = parse_pcrs('{"pcr-0": "0", "pcr-7": "deadbeef"}')
    round_tripped = parse_pcrs(original.to_json())
    assert round_tripped == original


def test_to_json_uses_64_char_sha256_hex() -> None:
    text = parse_pcrs('{"pcr-0": "0"}').to_json()
    payload = json.loads(text)
    # Committed fixture uses "pcrN" keys; sha256 digests serialise to 64 hex chars.
    assert len(payload["pcr0"]) == 64


# --- error handling ---------------------------------------------------------


def test_parse_invalid_json_raises() -> None:
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        parse_pcrs("{not json")
    assert "not valid JSON" in str(exc.value)


def test_parse_non_object_raises() -> None:
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        parse_pcrs("[1, 2, 3]")
    assert "must be a JSON object" in str(exc.value)


def test_parse_key_without_index_raises() -> None:
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        parse_pcrs('{"foo": "0"}')
    assert "must start with 'pcr'" in str(exc.value)


def test_parse_key_with_non_integer_index_raises() -> None:
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        parse_pcrs('{"pcr-x": "0"}')
    assert "no integer index" in str(exc.value)


def test_parse_invalid_hex_raises() -> None:
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        parse_pcrs('{"pcr-0": "nothex"}')
    assert "not a hex string" in str(exc.value)


def test_parse_multiple_algos_without_request_raises() -> None:
    raw = json.dumps({"pcr-0": {"sha1": "00", "sha384": "11"}})
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        parse_pcrs(raw)  # default algo sha256 is absent
    assert "multiple algorithms" in str(exc.value)


def test_parse_empty_output_raises() -> None:
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        parse_pcrs("{}")
    assert "no PCRs" in str(exc.value)


# --- end-to-end read step -> parse boundary --------------------------------


def _recording_runner(returncode: int, stdout: str = "", stderr: str = "") -> tuple:
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        from pengyos_measured_boot.swtpm import ProcResult

        return ProcResult(returncode, stdout, stderr)

    return runner, calls


def test_read_step_stdout_parses() -> None:
    # The raw stdout of the read step feeds straight into parse_pcrs.
    raw = json.dumps({"pcr-7": {"sha256": _sha256_hex(0xBEEF)}})
    fake, _ = _recording_runner(0, stdout=raw)
    runner = SwtpmRunner(SwtpmBootConfig(image="img.raw"), proc_runner=fake)

    pcrs = parse_pcrs(runner.read_pcrs().stdout)
    assert pcrs[7] == 0xBEEF


def test_parse_pcrs_file(tmp_path) -> None:
    p = tmp_path / "pcrs.json"
    p.write_text(json.dumps({"pcr-0": _sha256_hex(123)}), encoding="utf-8")
    assert parse_pcrs_file(p).values == {0: 123}
