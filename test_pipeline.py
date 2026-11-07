"""
Offline tests: no API key needed. Run: python test_pipeline.py  (or pytest)
"""
import csv
import io
import json
import os
import types
from pathlib import Path

import llm_client
import llm_scorer as ls
from editor_sheet import to_csv
from heuristics import generate_windows
from parser import parse_episode
from transcript import indexed_elements
from validate import check_promo, elements

SAMPLES = Path(__file__).parent / "samples" / "demo_show"
llm_client.BASE_URL = ""  # the fake client below has the Anthropic shape, whatever this shell has set

HOOK, CLIFF = "Put it down.", "Then you were the last person in this room."


def ep(n):
    return parse_episode(next(SAMPLES.glob(f"episode-{n:03d}-*.txt")))


def at(e, text):
    return next(x for x in indexed_elements(e) if x["text"] == text)


def test_parser_handles_uppercase_scene_labels():
    # Episode 2 writes "SCENE START:" instead of "Scene start:", as one of the real scripts did
    e = ep(2)
    assert len(e["scenes"]) == 1 and e["duration_s"] == 62.0 and e["scenes"][0]["heading"]


def test_parser_all_samples_have_timed_scenes():
    for f in sorted(SAMPLES.glob("*.txt")):
        e = parse_episode(f)
        assert e["scenes"] and all(s["end_s"] is not None for s in e["scenes"]), f.name


def test_heuristic_always_returns_a_candidate():
    for f in sorted(SAMPLES.glob("*.txt")):
        assert generate_windows(parse_episode(f))


def _promo(start, end, hook_ts, hook_q, cliff_ts, cliff_q):
    return {"segments": [{"start": "", "end": "", "start_s": start, "end_s": end}],
            "hook": {"quote": hook_q, "timestamp": hook_ts}, "cliffhanger": {"quote": cliff_q, "timestamp": cliff_ts},
            "total_length_s": end - start}


def test_validator_accepts_good_and_rejects_bad():
    e = ep(1)
    els = elements(e)
    h, c = at(e, HOOK), at(e, CLIFF)
    good = _promo(h["start_s"], c["end_s"], h["start"], HOOK, c["start"], CLIFF)
    assert check_promo(good, els, e["duration_s"])[0] == []
    late_hook = _promo(h["start_s"] - 10, c["end_s"], h["start"], HOOK, c["start"], CLIFF)
    assert any("within 3s" in m for m in check_promo(late_hook, els, e["duration_s"])[0])
    invented = _promo(h["start_s"], c["end_s"], h["start"], "I never loved you anyway.", c["start"], CLIFF)
    assert any("not found" in m for m in check_promo(invented, els, e["duration_s"])[0])
    too_long = _promo(0.0, 68.0, "00:05.00", "Hello? Housekeeping.", c["start"], CLIFF)
    assert any("outside 20-60s" in m for m in check_promo(too_long, els, e["duration_s"])[0])


def _fake_client(replies, stop_reasons=None):
    stop_reasons = list(stop_reasons or ["end_turn"] * len(replies))
    calls = []

    class Msgs:
        def create(self, **kw):
            calls.append(kw)
            reason = stop_reasons.pop(0)
            body = json.dumps(replies.pop(0)) if reason == "end_turn" else ""
            return types.SimpleNamespace(
                stop_reason=reason, stop_details=types.SimpleNamespace(category="test") if reason == "refusal" else None,
                content=[types.SimpleNamespace(type="text", text=body)],
                usage=types.SimpleNamespace(input_tokens=1000, output_tokens=500, cache_read_input_tokens=0, cache_creation_input_tokens=0))
    return types.SimpleNamespace(messages=Msgs()), calls


def _pick(start_id, end_id, hook_id, cliff_id):
    return {"segments": [{"start_element": start_id, "end_element": end_id}], "hook_element": hook_id,
            "hook_why": "", "cliffhanger_element": cliff_id, "cliffhanger_why": "", "hook_strength": 5,
            "cliffhanger_strength": 4, "standalone_score": 5, "standalone_reasoning": "", "spoiler_risk": "none",
            "platform_risk": "none", "risk_note": "", "why_it_works": "", "confidence": "high"}


def test_scorer_resolves_ids_to_verbatim_text_and_retries_on_bad_pick():
    e = ep(1)
    h, c = at(e, HOOK), at(e, CLIFF)
    far = at(e, "Your father's funeral was yesterday.")
    bad = _pick(h["id"], c["id"], far["id"], c["id"])  # hook far outside the first 3 seconds
    good = _pick(h["id"], c["id"], h["id"], c["id"])
    client, calls = _fake_client([{"promos": [bad], "no_good_promo_reason": None},
                                  {"promos": [good], "no_good_promo_reason": None}])
    ls._client = lambda: client
    out = ls.score_episode(e, {"candidates": []}, "")
    assert out["_meta"]["validation_retry"] and len(calls) == 2
    p = out["promos"][0]
    assert p["hook"]["quote"] == HOOK and p["hook"]["timestamp"] == h["start"]
    assert calls[1]["messages"][-1]["content"].startswith("These picks failed automatic validation")


def test_scorer_falls_back_on_refusal():
    e = ep(1)
    h, c = at(e, HOOK), at(e, CLIFF)
    client, calls = _fake_client([{"promos": [_pick(h["id"], c["id"], h["id"], c["id"])], "no_good_promo_reason": None}],
                                 stop_reasons=["refusal", "end_turn"])
    ls._client = lambda: client
    out = ls.score_episode(e, {"candidates": []}, "")
    assert out["_meta"]["refused"] and calls[1]["model"] == ls.FALLBACK_MODEL and out["promos"]


def test_model_layer_waits_out_a_rate_limit():
    class Limited(Exception):
        status_code = 429

    calls, waits = [], []

    def fake_create(**kw):
        calls.append(kw)
        if len(calls) == 1:
            raise Limited("{'reset_seconds': 7}")
        return types.SimpleNamespace(
            usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            choices=[types.SimpleNamespace(finish_reason="stop", message=types.SimpleNamespace(content="{}"))])

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=fake_create)))
    real_sleep = llm_client.time.sleep
    llm_client.time.sleep = waits.append
    try:
        r = llm_client._openai_compatible(client, "m", [{"type": "text", "text": "s"}],
                                          [{"role": "user", "content": "u"}], ls.EpisodeJudgment, 100)
    finally:
        llm_client.time.sleep = real_sleep
    assert waits == [12] and len(calls) == 2 and r.stop_reason == "end_turn"


def test_editor_sheet_has_every_required_field_and_blocks_formulas():
    row = {"key": "E1#0", "rank": 1, "episode": "Episode 1", "total_length_s": 30.0, "rank_score": 4.5,
           "segments": [{"start": "00:01.00", "end": "00:31.00"}], "hook_quote": "=Put it down.",
           "hook_timestamp": "00:01.00", "cliffhanger_quote": "Who?", "cliffhanger_timestamp": "00:30.00",
           "why_it_works": "Clear stakes."}
    got = next(csv.DictReader(io.StringIO(to_csv([row], {"E1#0": 2}).decode("utf-8-sig"))))
    assert got["post_first_slot"] == "2" and got["segments"] == "00:01.00-00:31.00"
    assert got["hook"] == "'=Put it down." and got["cliffhanger"] == "Who?" and got["why_it_works"] == "Clear stakes."
    assert got["editor_decision"] == ""


def test_key_typed_in_the_app_reaches_anthropic_when_base_url_is_empty():
    token = llm_client._session.set({"base_url": "", "api_key": "typed-key"})
    try:
        assert llm_client.get_client().api_key == "typed-key"
    finally:
        llm_client._session.reset(token)


def _app(password=""):
    from streamlit.testing.v1 import AppTest

    os.environ["PROMO_FINDER_VIEW_PASSWORD"] = password
    try:
        return AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=60).run()
    finally:
        del os.environ["PROMO_FINDER_VIEW_PASSWORD"]


def test_app_opens_with_no_key():
    assert not _app().exception


def test_password_keeps_real_results_hidden():
    # A hosted copy must open on the sample show until the password is entered.
    demo = json.loads((Path(__file__).parent / "demo/outputs/ranked_promos.json").read_text(encoding="utf-8"))
    app = _app("a-password")
    assert not app.exception
    assert any(s.value.startswith(f"{demo['total_promos']} validated promos") for s in app.subheader)


if __name__ == "__main__":
    tests =[v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed")
