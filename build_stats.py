"""
Computes the numbers the website shows about the 50-episode run, from the result files.
The output holds counts only, with no script text, so it's safe in the public repo.

Run from the project root:  python build_stats.py    -> input_pack_stats.json
"""
import json
from collections import Counter
from pathlib import Path

from agreement import iou, spans

OUT = Path("outputs")
REF = Path("data/promos_final")  # the earlier run, used for comparison
JUDGE = "agy/gemini-3.8-flash-low"


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


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
    }
    Path("input_pack_stats.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
