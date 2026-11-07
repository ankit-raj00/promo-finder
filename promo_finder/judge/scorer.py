"""
Production judgment step: one Claude call per episode.

Design choices (each fixes a failure seen in the first run):
  - The model answers with element IDs (e12), never timestamps or quotes.
    Code resolves IDs to exact times and verbatim text, so a paraphrased or
    invented quote is impossible by construction.
  - Every resolved promo goes through validate.check_promo. On failure the
    errors are sent back once and the model corrects itself.
  - The judge scores hook strength, cliffhanger strength, spoiler risk and
    platform risk separately, so ranking has real signal to work with.
  - The stable part of the prompt (rules + every episode logline for the
    show) sits in a cached system block, so episodes 2..N reuse it.
  - Refusals are detected and retried once on a fallback model; usage and
    estimated cost are logged per run.

Config (env vars):
  PROMO_FINDER_MODEL           default claude-opus-5-5
  PROMO_FINDER_FALLBACK_MODEL  default claude-sonnet-5-5 (used only on refusal)
  PROMO_FINDER_EFFORT          default high
  PROMO_FINDER_CONCURRENCY     default 4

Usage: python -m promo_finder.judge.scorer <parsed_dir> <candidates_dir> <out_dir>
"""
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from promo_finder.llm import client as llm_client
from promo_finder.ingest.transcript import indexed_elements, render, series_context
from promo_finder.judge.validate import check_promo, elements as validation_elements

MODEL = os.environ.get("PROMO_FINDER_MODEL", "claude-opus-5-5")
FALLBACK_MODEL = os.environ.get("PROMO_FINDER_FALLBACK_MODEL", "claude-sonnet-5-5")
EFFORT = os.environ.get("PROMO_FINDER_EFFORT", "high")
CONCURRENCY = int(os.environ.get("PROMO_FINDER_CONCURRENCY", "4"))
MAX_ATTEMPTS = 3

# $ per million tokens: (input, output, cache_read, cache_write). Estimates for the cost log only.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}

Score = Literal[1, 2, 3, 4, 5]
Risk = Literal["none", "low", "high"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SegmentRef(Strict):
    start_element: str
    end_element: str


class PromoPick(Strict):
    segments: list[SegmentRef]
    hook_element: str
    hook_why: str
    cliffhanger_element: str
    cliffhanger_why: str
    hook_strength: Score
    cliffhanger_strength: Score
    standalone_score: Score
    standalone_reasoning: str
    spoiler_risk: Risk
    platform_risk: Risk
    risk_note: str
    why_it_works: str
    confidence: Literal["high", "medium", "low"]


class EpisodeJudgment(Strict):
    promos: list[PromoPick]
    no_good_promo_reason: str | None


RULES = """You are the editorial judge in a promo-finding pipeline for a short vertical microdrama
(1-2 minute episodes, US audience). Your picks go to a human editor who cuts them for Instagram,
TikTok and YouTube to pull in viewers who have never seen the show.

What a promo is (client definition):
A 20 to 60-second clip taken from the show itself. Usually one continuous stretch, but it can join a
few moments from the same episode. A good promo:
- opens on a hook: something in the first 3 seconds that makes a scrolling viewer stop, like a slap,
  a reveal or a shocking line
- ends on a cliffhanger: it cuts at an unresolved moment, so the viewer needs to know what happens next
- makes sense on its own to someone who has never seen the show

How to answer:
- The episode transcript lists elements as `e12 [start-end] SPEAKER: text`. Refer to elements only by
  their ID. Code turns IDs into timestamps and verbatim quotes, so never write a timestamp or quote.
- A segment runs from start_element's start to end_element's end. Total length across segments must
  be 20-60 seconds. Use at most 3 segments, in story order, all from this episode.
- hook_element must start within 3 seconds of the first segment's start. cliffhanger_element must sit
  at the cut, inside the last segment and ending within a few seconds of its end.
- Pick 1-3 promos, best first, or none. If the episode is quiet or resolves calmly, return no promos
  and say why in no_good_promo_reason. A forced weak pick is worse than none.
- Scores are 1-5. hook_strength: would a stranger stop scrolling in 3 seconds? cliffhanger_strength:
  how badly do they need the next beat? standalone_score: does it land with zero context?
- spoiler_risk: high if the clip gives away a twist the show withholds from new viewers (a death, a
  betrayal reveal, a late-arc identity reveal). The show's premise, as stated in its title and first
  episodes, is not a spoiler.
- platform_risk: high if content (nudity, graphic violence, sexual content) could get the post limited
  on Instagram/TikTok/YouTube. Explain any non-"none" risk in risk_note, else leave it empty.
- The heuristic hints are keyword matches. Treat them as leads, not answers. Be a harsh judge."""


class UsageLog:
    def __init__(self):
        self.lock = threading.Lock()
        self.by_model: dict[str, dict[str, int]] = {}
        self.calls = 0

    def add(self, model: str, usage):
        with self.lock:
            self.calls += 1
            m = self.by_model.setdefault(model, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
            m["input"] += usage.input_tokens or 0
            m["output"] += usage.output_tokens or 0
            m["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
            m["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0

    def summary(self) -> dict:
        total = 0.0
        for model, m in self.by_model.items():
            p = PRICES.get(model)
            if p:
                cost = (m["input"] * p[0] + m["output"] * p[1] + m["cache_read"] * p[2] + m["cache_write"] * p[3]) / 1e6
                m["est_cost_usd"] = round(cost, 4)
                total += cost
        return {"calls": self.calls, "by_model": self.by_model, "est_total_usd": round(total, 4)}


USAGE = UsageLog()


def _client():
    return llm_client.get_client()


def _hints(candidates: dict, els: list[dict]) -> str:
    def nearest_id(t):
        return min(els, key=lambda e: abs(e["start_s"] - t))["id"]
    lines = []
    for c in candidates.get("candidates", []):
        tag = " (fallback: no keyword match)" if c.get("fallback") else ""
        lines.append(f"- {nearest_id(c['start_s'])} -> {nearest_id(c['end_s'])} ({c['length_s']}s){tag}")
    return "\n".join(lines) or "- none"


def resolve(pick: PromoPick, els_by_id: dict) -> dict:
    """Turn ID references into the promo shape used everywhere downstream. Raises KeyError on unknown IDs."""
    if not pick.segments:
        raise KeyError("no segments")
    segs = []
    for s in pick.segments:
        a, b = els_by_id[s.start_element], els_by_id[s.end_element]
        segs.append({"start": a["start"], "end": b["end"], "start_s": a["start_s"], "end_s": b["end_s"]})
    h, c = els_by_id[pick.hook_element], els_by_id[pick.cliffhanger_element]
    return {
        "segments": segs,
        "hook": {"quote": h["text"], "timestamp": h["start"], "element": h["id"], "why": pick.hook_why},
        "cliffhanger": {"quote": c["text"], "timestamp": c["start"], "element": c["id"], "why": pick.cliffhanger_why},
        "standalone": {"score": pick.standalone_score, "reasoning": pick.standalone_reasoning},
        "hook_strength": pick.hook_strength,
        "cliffhanger_strength": pick.cliffhanger_strength,
        "spoiler_risk": pick.spoiler_risk,
        "platform_risk": pick.platform_risk,
        "risk_note": pick.risk_note,
        "why_it_works": pick.why_it_works,
        "total_length_s": round(sum(x["end_s"] - x["start_s"] for x in segs), 2),
        "confidence": pick.confidence,
    }


def _call(client, model: str, system: list, messages: list):
    return llm_client.create(client, model, system, messages, EpisodeJudgment, effort=EFFORT)


def score_episode(episode: dict, candidates: dict, show_context: str, model: str | None = None,
                  fallback_model: str | None = None) -> dict:
    client = _client()
    model = model or MODEL
    fallback = fallback_model or FALLBACK_MODEL
    els = indexed_elements(episode)
    els_by_id = {e["id"]: e for e in els}
    check_els = validation_elements(episode)

    system = [{
        "type": "text",
        "text": f"{RULES}\n\nSHOW CONTEXT (every episode logline, for arc position and spoiler judgment):\n{show_context}",
        "cache_control": {"type": "ephemeral"},
    }]
    messages = [{
        "role": "user",
        "content": f"HEURISTIC HINTS (start -> end):\n{_hints(candidates, els)}\n\nEPISODE TRANSCRIPT:\n{render(episode, els)}",
    }]

    meta = {"model": model, "attempts": 0, "refused": False, "validation_retry": False}
    for attempt in range(MAX_ATTEMPTS):
        meta["attempts"] += 1
        can_retry = attempt < MAX_ATTEMPTS - 1
        response = _call(client, model, system, messages)
        USAGE.add(model, response.usage)

        if response.stop_reason == "refusal" and model != fallback:
            meta["refused"] = True
            category = getattr(response.stop_details, "category", None) if response.stop_details else None
            meta["refusal_category"] = category
            model = fallback
            meta["model"] = model
            continue
        if response.stop_reason == "refusal":
            return {"promos": [], "no_good_promo_reason": "Model declined this episode; needs a human editor.", "_meta": meta}
        if response.stop_reason == "max_tokens":
            raise RuntimeError(f"{episode['episode_number']}: output hit max_tokens")

        text = next(b.text for b in response.content if b.type == "text")
        try:
            judgment = EpisodeJudgment.model_validate_json(llm_client.extract_json(text))
        except ValidationError as e:
            if can_retry:
                meta["validation_retry"] = True
                messages = messages + [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": f"That reply didn't match the required JSON schema:\n{str(e)[:1500]}\nReturn the full corrected JSON."},
                ]
                continue
            raise RuntimeError(f"{episode['episode_number']}: schema mismatch: {e}") from e

        promos, problems = [], []
        for i, pick in enumerate(judgment.promos):
            try:
                promo = resolve(pick, els_by_id)
            except KeyError as e:
                problems.append(f"promo {i}: unknown element id {e}")
                continue
            errors, _, _ = check_promo(promo, check_els, episode["duration_s"])
            if errors:
                problems.append(f"promo {i}: " + "; ".join(errors))
            promos.append(promo)

        if problems and can_retry:
            meta["validation_retry"] = True
            messages = messages + [
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": "These picks failed automatic validation:\n- " + "\n- ".join(problems)
                    + "\nReturn the full corrected judgment. Drop any promo you can't fix."},
            ]
            continue

        valid = [p for p in promos if not check_promo(p, check_els, episode["duration_s"])[0]]
        meta["dropped_after_validation"] = len(promos) - len(valid)
        reason = judgment.no_good_promo_reason
        if judgment.promos and not valid:  # the model did pick clips, so this isn't a real "no promo"
            meta["all_picks_invalid"] = True
            reason = "NEEDS EDITOR: the model's picks failed validation on every attempt."
        return {"promos": valid, "no_good_promo_reason": reason, "_meta": meta}

    return {"promos": [], "no_good_promo_reason": "No valid judgment after retry.", "_meta": meta}


def score_all(parsed_dir: str, candidates_dir: str, out_dir: str, model: str | None = None) -> dict:
    parsed_dir, candidates_dir, out_dir = Path(parsed_dir), Path(candidates_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    context = series_context(parsed_dir)
    files = sorted(candidates_dir.glob("*.json"))

    failures = []

    def run(cand_file: Path):
        out_file = out_dir / cand_file.name
        if out_file.exists():  # finished episodes are skipped, so a re-run resumes
            return
        episode = json.loads((parsed_dir / cand_file.name).read_text(encoding="utf-8"))
        candidates = json.loads(cand_file.read_text(encoding="utf-8"))
        try:
            result = score_episode(episode, candidates, context, model=model)
        except Exception as e:  # noqa: BLE001 - one bad episode must not lose the rest of the run
            failures.append(cand_file.name)
            print(f"  FAILED {episode['episode_number']}: {type(e).__name__}: {e}", flush=True)
            return
        result = {"source_file": cand_file.name, "episode_number": episode["episode_number"], **result}
        out_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"  {episode['episode_number']}: {len(result['promos'])} promos", flush=True)

    if files:
        run(files[0])  # first call alone writes the prompt cache; the rest read it
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        list(pool.map(run, files[1:]))
    if failures:
        print(f"{len(failures)} episodes failed; re-run the same command to retry them: {failures}")

    summary = USAGE.summary()
    Path("outputs").mkdir(exist_ok=True)
    Path("outputs/run_usage.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Done. {summary['calls']} calls, est. ${summary['est_total_usd']}")
    return summary


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    score_all(sys.argv[1], sys.argv[2], sys.argv[3])
