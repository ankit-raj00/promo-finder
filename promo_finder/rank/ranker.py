"""
Ranks every promo and answers "which would you post first, and why".

Two layers:
  1. A transparent score, so an editor can see why something ranked where it did:
       quality = 0.35*hook + 0.30*cliffhanger + 0.20*standalone + 0.15*length_fit   (all on 1-5)
       minus penalties: spoiler high -1.0 / low -0.25, platform high -1.0 / low -0.25
  2. An editorial "post first" order: one judge compares the top candidates side by side
     (outputs/global_calibration.json, produced by global_rank.py or by hand) and writes a reason
     for each slot. The score shortlists; the side-by-side pass decides the order.

Scores come from the promo itself (new llm_scorer.py output) or, for promos judged
before those fields existed, from global_calibration.json.

Usage: python -m promo_finder.rank.ranker [promos_dir] [out_json]
"""
import json
import sys
from pathlib import Path

PENALTY = {"none": 0.0, "low": 0.25, "high": 1.0}
CALIBRATION = Path("outputs/global_calibration.json")


def length_fit(total: float) -> float:
    """1.0 inside 30-45s, falling off toward the 20s/60s limits."""
    if 30 <= total <= 45:
        return 1.0
    if 20 <= total <= 60:
        dist = 30 - total if total < 30 else total - 45
        return max(0.4, 1.0 - dist / 25)
    return 0.0


def key_for(episode_number: str, index: int) -> str:
    return f"E{''.join(c for c in episode_number if c.isdigit())}#{index}"


def score(p: dict) -> float:
    quality = (0.35 * p["hook_strength"] + 0.30 * p["cliffhanger_strength"]
               + 0.20 * p["standalone_score"] + 0.15 * 5 * length_fit(p["total_length_s"]))
    return round(quality - PENALTY[p["spoiler_risk"]] - PENALTY[p["platform_risk"]], 3)


def rank_all(promos_dir: Path):
    calib = json.loads(CALIBRATION.read_text(encoding="utf-8")) if CALIBRATION.exists() else {"scores": {}, "editor_order": []}
    rows, no_good, uncalibrated = [], [], []
    for f in sorted(promos_dir.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if not d.get("promos"):
            no_good.append({"episode": d["episode_number"], "reason": d.get("no_good_promo_reason")})
            continue
        for i, p in enumerate(d["promos"]):
            key = key_for(d["episode_number"], i)
            fields = {k: p[k] for k in ("hook_strength", "cliffhanger_strength", "spoiler_risk", "platform_risk", "risk_note") if k in p}
            if len(fields) < 4:
                if key not in calib["scores"]:
                    uncalibrated.append(key)
                    continue
                fields = calib["scores"][key]
            row = {
                "key": key, "episode": d["episode_number"], "source_file": d["source_file"],
                "segments": p["segments"], "total_length_s": p["total_length_s"],
                "hook_quote": p["hook"]["quote"], "hook_timestamp": p["hook"]["timestamp"],
                "cliffhanger_quote": p["cliffhanger"]["quote"], "cliffhanger_timestamp": p["cliffhanger"]["timestamp"],
                "why_it_works": p["why_it_works"], "standalone_score": p["standalone"]["score"],
                "confidence": p["confidence"], **fields,
            }
            # The independent critic replaces the picking model's ratings of its own work.
            critic = p.get("critic") or {}
            if "hook" in critic:
                row.update(hook_strength=critic["hook"], cliffhanger_strength=critic["cliffhanger"],
                           standalone_score=critic["standalone"], viewer_question=critic["viewer_question"])
            check = p.get("spoiler_check") or {}
            if "spoiler_risk" in check:
                row.update(spoiler_risk=check["spoiler_risk"], twist_revealed=check["twist_revealed"])
            row["rank_score"] = score(row)
            rows.append(row)

    rows.sort(key=lambda r: -r["rank_score"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    by_key = {r["key"]: r for r in rows}
    post_first = []
    for slot, item in enumerate(calib.get("editor_order", []), 1):
        if item["key"] in by_key:
            post_first.append({"slot": slot, "reason": item["reason"], **by_key[item["key"]]})
    return rows, post_first, no_good, uncalibrated


def to_markdown(rows, post_first, no_good) -> str:
    def fmt(r):
        times = " + ".join(f"{s['start']}-{s['end']}" for s in r["segments"])
        risk = "" if r["spoiler_risk"] == r["platform_risk"] == "none" else \
            f"  \n  Risk: spoiler {r['spoiler_risk']}, platform {r['platform_risk']}. {r.get('risk_note', '')}"
        return (f"**{r['episode']}** | {times} ({r['total_length_s']:.1f}s) | hook {r['hook_strength']}/5, "
                f"cliffhanger {r['cliffhanger_strength']}/5, standalone {r['standalone_score']}/5\n"
                f"  Hook ({r['hook_timestamp']}): \"{r['hook_quote']}\"  \n"
                f"  Cliffhanger ({r['cliffhanger_timestamp']}): \"{r['cliffhanger_quote']}\"  \n"
                f"  Why: {r['why_it_works']}{risk}\n")
    out = ["# Promos: post these first\n"]
    for r in post_first:
        out.append(f"{r['slot']}. {fmt(r)}  Order reason: {r['reason']}\n")
    out.append(f"\n# All {len(rows)} promos by score\n")
    for r in rows:
        out.append(f"{r['rank']}. (score {r['rank_score']}) {fmt(r)}")
    out.append("\n# Episodes with no good promo\n")
    for n in no_good:
        out.append(f"- **{n['episode']}**: {n['reason']}\n")
    return "\n".join(out)


def main():
    promos_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "data/promos_final")
    out_path = Path(sys.argv[2] if len(sys.argv) > 2 else "outputs/ranked_promos.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows, post_first, no_good, uncalibrated = rank_all(promos_dir)
    out_path.write_text(json.dumps({
        "total_promos": len(rows), "post_first": post_first,
        "episodes_with_no_good_promo": no_good, "ranked_promos": rows,
    }, indent=2), encoding="utf-8")
    out_path.with_suffix(".md").write_text(to_markdown(rows, post_first, no_good), encoding="utf-8")

    print(f"Ranked {len(rows)} promos -> {out_path} (+ .md)")
    if uncalibrated:
        print(f"WARNING: {len(uncalibrated)} promos skipped (no strength scores): {uncalibrated}")
    ties = len(rows) - len({r["rank_score"] for r in rows})
    print(f"Distinct scores: {len({r['rank_score'] for r in rows})}/{len(rows)}")
    print("\nPost first:")
    for r in post_first:
        print(f"  {r['slot']:>2}. [{r['episode']}] score {r['rank_score']} (#{r['rank']} by score) -- {r['hook_quote'][:50]}")
    print("\nTop 10 by score:")
    for r in rows[:10]:
        print(f"  #{r['rank']:>2} [{r['episode']}] {r['rank_score']}  H{r['hook_strength']} C{r['cliffhanger_strength']} "
              f"S{r['standalone_score']} {r['total_length_s']:.0f}s spoiler={r['spoiler_risk']}")


if __name__ == "__main__":
    main()
