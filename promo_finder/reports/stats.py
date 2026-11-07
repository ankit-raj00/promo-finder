"""
Computes the numbers the website shows about the 50-episode run, from the result files.
The output holds counts and model names only, with no script text, so it's safe in the public repo.

Run from the project root:  python -m promo_finder.reports.stats    -> demo/input_pack_stats.json
"""
import json
import re
from collections import Counter
from pathlib import Path

from promo_finder.reports.agreement import iou, spans

OUT = Path("outputs")
REF = Path("data/promos_final")  # the earlier run, used for comparison
JUDGE = "agy/gemini-3.8-flash-low"


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def pretty(model: str) -> str:
    """agy/gemini-3.8-flash-low -> Gemini 3.8 Flash Low"""
    words = model.split("/")[-1].replace("claude-sonnet-4-6", "claude-sonnet-4.6").split("-")
    return " ".join(w.capitalize() for w in words)


def model_results() -> dict:
    """The model comparison, from each step's result file. The first row of each file is its top score."""
    step = OUT / "step_eval"
    judge = load(OUT / "model_eval/summary.json")
    blind = load(OUT / "model_eval/blind_result.json")
    critic = load(step / "critic/summary.json")[0]
    spoiler = load(step / "spoiler/summary.json")[0]
    ordering = load(step / "ordering/summary.json")
    montages = load(step / "montages/summary.json")
    lite = next(k for k in blind if "lite" in k)
    return {
        "judge_winner": pretty(JUDGE),
        "judge_models_compared": sum(r["completed_rate"] > 0 for r in judge),
        "judge_winner_quality": blind[JUDGE]["overall"],
        "judge_runner_up": pretty(lite),
        "judge_runner_up_quality": blind[lite]["overall"],
        "critic_winner": pretty(critic["model"]),
        "critic_separation_pct": round(critic["separation"] * 100),
        "spoiler_winner": pretty(spoiler["model"]),
        "spoiler_high_caught": spoiler["high_clips_caught"],
        "spoiler_false_alarms": spoiler["false_high"],
        "order_winner": pretty(ordering[0]["model"]),
        "order_stability": round(ordering[0]["stability"], 2),
        "order_speed_vs_next": round(ordering[1]["avg_seconds"] / ordering[0]["avg_seconds"], 1),
        "montage_winner": pretty(montages[0]["model"]),
        "montage_valid": montages[0]["valid_count"],
        "montage_proposed": montages[0]["proposed"],
        "montage_models_below_half_valid": sum(r["validity"] < 0.5 for r in montages),
    }


def review_counts() -> dict:
    text = (OUT / "reviewed_top10.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*Result:\*\* (\d+) accepted, (\d+) retimed, (\d+) replaced", text)
    return {"review_accepted": int(m[1]), "review_retimed": int(m[2]), "review_replaced": int(m[3])}


def main():
    raw = {f.name: load(f) for f in sorted((OUT / "per_episode_promos_raw").glob("*.json"))}
    ranked = load(OUT / "ranked_promos.json")
    rows = ranked["ranked_promos"]
    checks = load(OUT / "validation_report.json")
    metas = [d["_meta"] for d in raw.values()]

    agree, overlaps = 0, []
    for name, d in raw.items():
        ref = load(REF / name)
        agree += bool(d["promos"]) == bool(ref["promos"])
        if d["promos"] and ref["promos"]:
            overlaps.append(max(iou(spans(d["promos"][0]), spans(p)) for p in ref["promos"]))

    same_episode_overlaps = 0
    by_episode = {}
    for r in rows:
        by_episode.setdefault(r["episode"], []).append([(s["start_s"], s["end_s"]) for s in r["segments"]])
    for clips in by_episode.values():
        for i in range(len(clips)):
            same_episode_overlaps += sum(iou(clips[i], other) >= 0.5 for other in clips[i + 1:])

    self_hooks = Counter(p["hook_strength"] for d in raw.values() for p in d["promos"])
    spoiler = Counter(r["spoiler_risk"] for r in rows)
    critic_hooks = [r["hook_strength"] for r in rows]
    too_long = [r["total_length_s"] for r in rows if r["total_length_s"] > 60]
    judge = next(r for r in load(OUT / "model_eval/summary.json") if r["model"] == JUDGE)

    stats = {
        "episodes": len(raw),
        "promos": len(rows),
        "empty_episodes": len(ranked["episodes_with_no_good_promo"]),
        "montages": len(load(OUT / "cross_episode_promos.json")["montages"]),
        "passed_checks": sum(c["status"] != "error" for c in checks),
        "episodes_with_retry": sum(bool(m["validation_retry"]) for m in metas),
        "picks_dropped": sum(m.get("dropped_after_validation", 0) for m in metas),
        "self_rated_hook_4_or_5": self_hooks[4] + self_hooks[5],
        "critic_hook_min": min(critic_hooks),
        "critic_hook_max": max(critic_hooks),
        "pick_or_skip_agreement": agree,
        "mean_time_overlap": round(sum(overlaps) / len(overlaps), 2),
        "consistency": judge["consistency"],
        "weak_hooks": sum(h <= 2 for h in critic_hooks),
        "need_backstory": sum(r["standalone_score"] <= 3 for r in rows),
        "same_episode_overlaps": same_episode_overlaps,
        "spoiler_high": spoiler["high"],
        "spoiler_low": spoiler["low"],
        "clips_over_60s": len(too_long),
        "longest_clip_s": max(r["total_length_s"] for r in rows),
        **review_counts(),
        "models": model_results(),
    }
    Path("demo/input_pack_stats.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
