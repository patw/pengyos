"""Offline tests for pengyos-jev. No network, no model: the transport is mocked
and the response fixture is a real capture from results/measured.json's run."""

import io
import json
import os
import urllib.error
from pathlib import Path

import pytest

from pengyos_jev.pengyos_jev import (
    JevError,
    build_payload,
    cmd_decide,
    cmd_health,
    main,
    parse_questions,
    summarize,
)

FIXTURE = Path(__file__).parent / "fixtures" / "systemone_answer.json"
NOUL = Path(__file__).parent / "fixtures" / "systemone_noul.json"
CHOICE = Path(__file__).parent / "fixtures" / "systemone_choice.json"


def load(path):
    return json.loads(Path(path).read_text())


def load_fixture():
    return json.loads(FIXTURE.read_text())


# ---- payload building -------------------------------------------------------

def test_payload_has_state_and_questions():
    body = build_payload("the build server is offline",
                         {"down": {"type": "noul", "instructions": "Is it down?"}})
    assert body["state"] == "the build server is offline"
    assert body["questions"]["down"]["type"] == "noul"


def test_choice_needs_criteria_object():
    with pytest.raises(JevError):
        build_payload("s", {"q": {"type": "choice", "instructions": "which?"}})


def test_score_needs_two_to_ten_levels():
    with pytest.raises(JevError):
        build_payload("s", {"q": {"type": "score", "instructions": "how bad?", "criteria": ["only"]}})
    ok = build_payload("s", {"q": {"type": "score", "instructions": "how bad?",
                                   "criteria": ["a", "b", "c"]}})
    assert ok["questions"]["q"]["criteria"] == ["a", "b", "c"]


def test_unknown_type_rejected():
    with pytest.raises(JevError):
        build_payload("s", {"q": {"type": "vibe", "instructions": "x"}})


def test_empty_state_rejected():
    with pytest.raises(JevError):
        build_payload("   ", {"q": {"type": "noul", "instructions": "x"}})


def test_noul_criteria_needs_true_and_false():
    with pytest.raises(JevError):
        build_payload("s", {"q": {"type": "noul", "instructions": "x",
                                  "criteria": {"true": "yes"}}})
    build_payload("s", {"q": {"type": "noul", "instructions": "x",
                              "criteria": {"true": "yes", "false": "no"}}})


def test_parse_questions_flag_form():
    q = parse_questions(["route:choice:who handles it?::billing,shipping,technical"])
    assert q["route"]["criteria"] == {"billing": None, "shipping": None, "technical": None}
    s = parse_questions(["sev:score:how bad?::minor,serious,critical"])
    assert s["sev"]["criteria"] == ["minor", "serious", "critical"]


def test_parse_questions_rejects_short_form():
    with pytest.raises(JevError):
        parse_questions(["route:choice"])


# ---- response handling ------------------------------------------------------

def test_summarize_covers_all_three_types():
    lines = summarize(load_fixture()["answers"])
    assert "billing" in lines
    assert "noul=" in lines
    assert "score=" in lines


def test_fixture_is_a_real_decision_shape():
    body = load_fixture()
    ans = body["answers"]["route"]
    assert ans["choice"] in ans["probabilities"]
    assert abs(sum(ans["probabilities"].values()) - 1.0) < 1e-6
    assert body["usage"]["output_tokens"] == 0  # a decision generates no tokens


def test_unreachable_service_is_a_clean_error(monkeypatch, capsys):
    import pengyos_jev.pengyos_jev as mod

    def boom(*a, **k):
        raise urllib.error.URLError("nope")

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    rc = main(["decide", "noul", "--state", "s", "--instructions", "x"])
    assert rc == 1
    assert "not reachable" in capsys.readouterr().err


def test_decide_noul_prints_a_probability(monkeypatch, capsys):
    import pengyos_jev.pengyos_jev as mod

    class Resp:
        def read(self):
            return json.dumps(load(NOUL)).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: Resp())
    rc = main(["decide", "noul", "--state", "s", "--instructions", "x"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert 0.0 <= float(out) <= 1.0


def test_decide_json_output_is_parseable(monkeypatch, capsys):
    import pengyos_jev.pengyos_jev as mod

    class Resp:
        def read(self):
            return json.dumps(load(CHOICE)).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: Resp())
    rc = main(["--json", "decide", "choice", "--state", "s", "--instructions", "x",
               "--criteria", "billing", "shipping"])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["type"] == "choice"


def test_health_reports_decision_modality(monkeypatch, capsys):
    import pengyos_jev.pengyos_jev as mod

    payload = {"data": [{"id": "Laya-Q8_0.gguf",
                         "architecture": {"output_modalities": ["decisions"],
                                          "decision_type": "laya"}}]}

    class Resp:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: Resp())
    rc = main(["health"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "decision=True" in out and "laya" in out
