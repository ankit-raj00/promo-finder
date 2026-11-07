"""
Independent critic: scores a clip from its script text alone, with no show context and
without seeing the picking model's own ratings. The picker rates its own picks too
generously to rank them; this is the separate, cold-viewer opinion.

  python critic.py <promos_dir> <out_dir>
Set PROMO_FINDER_CRITIC_MODEL to choose the model.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

import llm_client
from transcript import indexed_elements, label

MODEL = os.environ.get("PROMO_FINDER_CRITIC_MODEL") or os.environ.get("PROMO_FINDER_MODEL", "claude-opus-5-5")
Score = Literal[1, 2, 3, 4, 5]


class CriticScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hook: Score
    cliffhanger: Score
    standalone: Score
    viewer_question: str


RULES = """You are scrolling a social feed and have never seen this show. Below is the script of a short clip,
with times counted from the clip's start. Score it honestly from 1 to 5. Most clips deserve 2 or 3; a 5 is rare.

hook: would you stop scrolling because of the first 3 seconds alone?
  1 = nothing happening or a filler line. 3 = mildly intriguing. 5 = a shocking line, reveal or act you can't scroll past.
cliffhanger: does the end leave you needing the next moment?
  1 = it resolves or just stops. 3 = some curiosity. 5 = you would click right now to find out.
standalone: with no other context, do you understand who wants what and why it matters?
  1 = confusing names and stakes. 3 = the gist is clear. 5 = fully clear.
viewer_question: in one short sentence, the question you're left with, or "none".

"""


def clip_text(parts: list) -> str:
    """parts: [(parsed episode, segments)], in clip order. Times run from the clip's start."""
    lines, offset = [], 0.0
    for episode, segments in parts:
        els = indexed_elements(episode)
        for s in segments:
            for e in els:
                if s["start_s"] - 0.01 <= e["start_s"] < s["end_s"]:
                    lines.append(f"{offset + e['start_s'] - s['start_s']:5.1f}s {label(e)}: {e['text']}")
            offset += s["end_s"] - s["start_s"]
    return "\n".join(lines)


def score_clip(parts: list, model: str | None = None) -> dict:
    model = model or MODEL
    start, tokens = time.time(), 0
    client = llm_client.get_client()
    system = [{"type": "text", "text": "Answer in the requested structure."}]
    messages = [{"role": "user", "content": RULES + clip_text(parts)}]
    for attempt in (1, 2):
        r = llm_client.create(client, model, system, messages, CriticScore, max_tokens=8000)
        tokens += r.usage.output_tokens or 0
        if r.stop_reason == "refusal":
            return {"refused": True, "attempts": attempt, "seconds": round(time.time() - start, 2), "output_tokens": tokens}
        text = next(b.text for b in r.content if b.type == "text")
        try:
            c = CriticScore.model_validate_json(llm_client.extract_json(text))
            return {**c.model_dump(), "refused": False, "attempts": attempt,
                    "seconds": round(time.time() - start, 2), "output_tokens": tokens}
        except ValidationError as e:
            if attempt == 2:
                raise RuntimeError(f"schema mismatch: {str(e)[:200]}") from e
            messages = messages + [{"role": "assistant", "content": r.content},
                                   {"role": "user", "content": "That didn't match the schema. Return only the corrected JSON."}]


def main():
    promos_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    parsed_dir = Path("data/parsed")
    out_dir.mkdir(parents=True, exist_ok=True)

    failures = []

    def run(f: Path):
        out = out_dir / f.name
        if out.exists():
            return
        d = json.loads(f.read_text(encoding="utf-8"))
        episode = json.loads((parsed_dir / f.name).read_text(encoding="utf-8"))
        try:
            for p in d.get("promos", []):
                p["critic"] = score_clip([(episode, p["segments"])])
        except Exception as e:  # noqa: BLE001 - one episode must not lose the rest; a re-run retries it
            failures.append(f.name)
            print(f"  FAILED {f.name}: {type(e).__name__}: {str(e)[:150]}", flush=True)
            return
        out.write_text(json.dumps(d, indent=2), encoding="utf-8")

    with ThreadPoolExecutor(max_workers=int(os.environ.get("PROMO_FINDER_CONCURRENCY", "4"))) as pool:
        list(pool.map(run, sorted(promos_dir.glob("*.json"))))
    if failures:
        sys.exit(f"{len(failures)} episodes not scored. Re-run the same command to retry them.")
    print(f"Scored -> {out_dir}")


if __name__ == "__main__":
    main()
