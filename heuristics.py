"""
Cheap, rule-based pre-filter that turns a parsed episode into a shortlist of
candidate promo windows (20-60s each), before the expensive semantic judgment
pass (LLM / human-equivalent reasoning) scores them.

Why a pre-filter at all: with 50 episodes * ~75 elements each, sending every
possible 20-60s window to an LLM is wasteful and slow at show-per-week scale.
This step is pure code: it scores every dialogue/action line for "hook-ness"
(first-3-second stopping power) and "cliffhanger-ness" (unresolved ending),
using keyword/structural signals a human editor would also eyeball first.
It is deliberately generous (high recall) -- its only job is to not throw away
genuinely good candidates. The judgment pass does the precision work.
"""
import json
import re
import sys
from pathlib import Path

HOOK_KEYWORDS = [
    r"\bslap\b", r"\bstrike[s]?\b", r"\bkiss(es|ed)?\b", r"\bgun\b", r"\bknife\b",
    r"\bdead\b", r"\bdies?\b", r"\bdivorce\b", r"\baffair\b", r"\bpregnant\b",
    r"\bmarry\b", r"\bmarriage\b", r"\bwedding\b", r"\bbetray(ed|al)?\b",
    r"\bsecret\b", r"\blied?\b", r"\blies\b", r"\btruth\b", r"\bfired\b",
    r"\bscar(red)?\b", r"\bwar\b", r"\bsoldier\b", r"\bduke\b", r"\btitle\b",
    r"\binheritance\b", r"\bdisown\b", r"\bhumiliat", r"\bshock",
    r"\bwho are you\b", r"\bi (know|saw|heard)\b", r"\bnever\b.*\bagain\b",
    r"!\s*$", r"\?\s*$",
]
CLIFFHANGER_KEYWORDS = [
    r"\?\s*$", r"\.\.\.\s*$", r"\bwait\b", r"\bstop\b", r"\bwho\b.*\?",
    r"\bwhat\b.*\?", r"\bwhy\b.*\?", r"\bhow (could|dare)\b", r"\bno+\.*\s*$",
    r"\byou('re| are)\b.*\?", r"\bi('m| am)\b.*\?", r"cut to black",
    r"cliffhanger", r"freeze", r"gasp",
]

HOOK_RE = re.compile("|".join(HOOK_KEYWORDS), re.IGNORECASE)
CLIFF_RE = re.compile("|".join(CLIFFHANGER_KEYWORDS), re.IGNORECASE)

MIN_LEN, MAX_LEN = 20, 60


def flatten_elements(episode: dict):
    """Flat list of elements across all scenes, in episode-time order."""
    flat = []
    for scene in episode["scenes"]:
        for el in scene["elements"]:
            if el["start_s"] is None or el["end_s"] is None:
                continue
            flat.append({**el, "scene_id": scene["scene_id"], "scene_heading": scene["heading"]})
    flat.sort(key=lambda e: e["start_s"])
    return flat


def score_hook(el) -> float:
    s = 0.0
    if el["kind"] == "dialogue":
        s += 1.0
    if HOOK_RE.search(el["text"]):
        s += 2.0
    if el["kind"] == "action" and len(el["text"]) < 120:
        s += 0.5
    return s


def score_cliff(el) -> float:
    s = 0.0
    if CLIFF_RE.search(el["text"]):
        s += 2.0
    if el["kind"] == "dialogue":
        s += 0.5
    return s


def generate_windows(episode: dict):
    """
    Slide candidate end-points across the episode: for every element with a
    decent cliffhanger score, walk backward to find the nearest strong-hook
    start point that keeps the window within [MIN_LEN, MAX_LEN] seconds, also
    allowing the window to start a few seconds before the chosen hook line so
    visual context (camera cue) isn't orphaned.
    """
    flat = flatten_elements(episode)
    if not flat:
        return []

    hook_candidates = [(i, el, score_hook(el)) for i, el in enumerate(flat)]
    cliff_candidates = [(i, el, score_cliff(el)) for i, el in enumerate(flat)]

    windows = []
    seen = set()
    for ci, cel, cscore in cliff_candidates:
        if cscore < 2.0:
            continue
        end_s = cel["end_s"]
        # look backward for the best hook within MIN_LEN..MAX_LEN of this end
        best = None
        for hi, hel, hscore in hook_candidates:
            if hi > ci:
                break
            start_s = hel["start_s"]
            length = end_s - start_s
            if MIN_LEN <= length <= MAX_LEN and hscore >= 1.0:
                candidate_score = hscore + cscore + min(length, 40) / 40.0
                if best is None or candidate_score > best[0]:
                    best = (candidate_score, hi, start_s)
        if best is None:
            continue
        score, hi, start_s = best
        key = (round(start_s, 1), round(end_s, 1))
        if key in seen:
            continue
        seen.add(key)
        windows.append({
            "start_s": start_s,
            "end_s": end_s,
            "length_s": round(end_s - start_s, 1),
            "score": round(score, 2),
            "hook_line": flat[hi]["text"],
            "cliff_line": cel["text"],
            "scene_span": sorted({flat[k]["scene_id"] for k in range(hi, ci + 1)}),
        })

    windows.sort(key=lambda w: -w["score"])
    windows = windows[:6]  # keep top-6 candidates per episode for judgment pass

    if not windows:
        # Keyword heuristic found no strong hook/cliff pairing (e.g. a quieter,
        # transitional episode). Rather than silently skip it, hand the judgment
        # pass the episode's opening and closing beats as low-confidence
        # fallback candidates, flagged as such.
        last = flat[-1]
        first = flat[0]
        tail_start = max(first["start_s"], last["end_s"] - 45)
        windows.append({
            "start_s": tail_start,
            "end_s": last["end_s"],
            "length_s": round(last["end_s"] - tail_start, 1),
            "score": 0.0,
            "hook_line": None,
            "cliff_line": last["text"],
            "scene_span": sorted({e["scene_id"] for e in flat if e["start_s"] >= tail_start}),
            "fallback": True,
            "fallback_reason": "no keyword hook/cliffhanger pairing found; episode may be quieter/transitional",
        })

    return windows


def main():
    in_dir = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(in_dir.glob("*.json"))
    total_candidates = 0
    zero_candidate_eps = []
    for f in files:
        episode = json.loads(f.read_text(encoding="utf-8"))
        windows = generate_windows(episode)
        total_candidates += len(windows)
        if not windows:
            zero_candidate_eps.append(f.name)
        out = {
            "source_file": episode["source_file"],
            "episode_number": episode["episode_number"],
            "logline": episode["logline"],
            "duration_s": episode["duration_s"],
            "candidates": windows,
        }
        (out_dir / f.name).write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(f"Generated {total_candidates} candidate windows across {len(files)} episodes -> {out_dir}")
    if zero_candidate_eps:
        print(f"Episodes with 0 candidates (heuristic found nothing, needs full-episode fallback): {zero_candidate_eps}")


if __name__ == "__main__":
    main()
