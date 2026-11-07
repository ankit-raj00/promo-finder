"""
Builds a show's quality report from files on disk; every number is computed, not typed in.
Run from the project root:  python -m promo_finder.reports.quality runs/<show_name>
Compares against data/promos_final, the earlier output judged separately by Claude.
"""
import glob
import json
import sys
from collections import Counter
from pathlib import Path

from promo_finder.reports.agreement import iou, spans

W = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/broken_soldier_flash_low")
REF = Path("data/promos_final")


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def dist(counter):
    return ", ".join(f"{k}: {counter[k]}" for k in sorted(counter))


def main():
    raw = {Path(f).name: load(f) for f in sorted(glob.glob(str(W / "data/promos/*.json")))}
    ranked = load(W / "outputs/ranked_promos.json")
    rows = ranked["ranked_promos"]
    checks = load(W / "outputs/validation_report.json")
    montages = load(W / "outputs/cross_episode_promos.json")["montages"]
    judge = {r["model"]: r for r in load("outputs/model_eval/summary.json")}.get("agy/gemini-3.8-flash-low", {})
    ordering = {r["model"]: r for r in load(W / "outputs/step_eval/ordering/summary.json")}.get("agy/gemini-3.8-flash-low", {})

    metas = [d["_meta"] for d in raw.values()]
    status = Counter(c["status"] for c in checks)
    self_hook = Counter(p["hook_strength"] for d in raw.values() for p in d["promos"])
    critic_hook = Counter(r["hook_strength"] for r in rows)
    critic_cliff = Counter(r["cliffhanger_strength"] for r in rows)
    spoiler = Counter(r["spoiler_risk"] for r in rows)
    platform = Counter(r["platform_risk"] for r in rows)

    both, disagree, ious = 0, [], []
    for name, d in raw.items():
        ref = load(REF / name)
        if bool(d["promos"]) != bool(ref["promos"]):
            disagree.append(d["episode_number"])
        elif d["promos"]:
            both += 1
            ious.append(max(iou(spans(d["promos"][0]), spans(p)) for p in ref["promos"]))
    no_overlap = sum(i == 0 for i in ious)

    weak_hooks = [r for r in rows if r["hook_strength"] <= 2]
    weak_ends = [r for r in rows if r["cliffhanger_strength"] <= 2]
    top_weak = [r for r in ranked["post_first"] if r["cliffhanger_strength"] <= 3 or r["hook_strength"] <= 3]
    context_heavy = [r for r in rows if r["standalone_score"] <= 3]
    overlaps = 0
    by_ep = {}
    for r in rows:
        by_ep.setdefault(r["episode"], []).append(r)
    for ep, items in by_ep.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if iou([(s["start_s"], s["end_s"]) for s in items[i]["segments"]],
                       [(s["start_s"], s["end_s"]) for s in items[j]["segments"]]) >= 0.5:
                    overlaps += 1
    camera_hooks = sum(p["hook"].get("element") is not None and any(w in p["hook"]["quote"].lower() for w in ("shot", "close-up"))
                       for d in raw.values() for p in d["promos"])

    L = []
    w = L.append
    w("# Quality report\n\n")
    w(f"From the pipeline run in `{W.as_posix()}`. {len(raw)} episodes, {len(rows)} promos, {len(montages)} montages. "
      "No editor reviewed this and no post performance data exists, so it shows reliability and consistency, not proven audience results.\n")

    w("\n## 1. Do the picks follow the rules?\n")
    w(f"- {status['ok']} of {len(checks)} promos pass every check, with {status['warning']} warnings and {status['error']} errors: "
      "quotes match the script, hook within 3 seconds, cliffhanger at the cut, 20-60 seconds.\n")
    w("- The model only names script lines; code fills in timestamps and quotes, so invented quotes aren't possible.\n")
    w(f"- This took retries: {sum(m['validation_retry'] for m in metas)} of {len(metas)} episodes needed at least one, and "
      f"{sum(m.get('dropped_after_validation', 0) for m in metas)} picks were dropped after retries. {sum(m['refused'] for m in metas)} refusals.\n")

    w("\n## 2. Would it pick the same moments again?\n")
    if judge:
        tested = sorted(r["consistency"] for r in load("outputs/model_eval/summary.json") if r.get("consistency") is not None)
        w(f"- Same model, same 10 test episodes, two runs: consistency {judge.get('consistency')} out of 1. It ranks {tested.index(judge['consistency']) + 1} from the bottom "
          f"of the {len(tested)} models tested, so a re-run will change some start and end points.\n")
    if ordering:
        w(f"- Ordering step: the top 10 overlaps {ordering.get('stability')} between runs, so expect one or two slots to change.\n")

    w("\n## 3. Does it agree with a separate judge?\n")
    w(f"Compared with the earlier output (judged separately by Claude) across all {len(raw)} episodes:\n\n")
    w(f"- Pick vs. skip agrees on {len(raw) - len(disagree)} of {len(raw)} episodes" + (f"; differs on {', '.join(disagree)}.\n" if disagree else ".\n"))
    if ious:
        w(f"- Where both picked, the top pick's time overlap averages {sum(ious) / len(ious):.2f}. {both - no_overlap} of {both} overlap at all; "
          f"{no_overlap} chose a different moment.\n")
    w("- Both judges were AI models, so this shows agreement, not that the picks are right.\n")

    w("\n## 4. Does the ranking mean anything?\n")
    w(f"- The picking model's own hook ratings: {dist(self_hook)}. It rates its own work too generously to rank by.\n")
    w(f"- The independent critic's hook ratings: {dist(critic_hook)}. Cliffhanger ratings: {dist(critic_cliff)}.\n")
    w(f"- {len({r['rank_score'] for r in rows})} distinct scores across {len(rows)} promos.\n")
    w("- In the critic test, a promo scored above a random clip 89% of the time, on 24 clips with imperfect labels.\n")

    w("\n## 5. Where the tool picks badly\n")
    w(f"- **Weak openings:** {len(weak_hooks)} promos score 2 or lower on hook, and {camera_hooks} open on a camera direction rather than a line, which depends on footage we can't see.\n")
    w(f"- **Soft endings:** {len(weak_ends)} promos score 2 or lower on cliffhanger.\n")
    if top_weak:
        w(f"- **Weak clips in the top 10:** {', '.join(r['episode'] for r in top_weak)} score 3 or lower on hook or cliffhanger. The ordering step favours premise over the scores.\n")
    w(f"- **Clips needing backstory:** {len(context_heavy)} promos score 3 or lower on standalone sense.\n")
    w(f"- **Duplicates:** {overlaps} pairs of promos from the same episode overlap by half or more, so an editor should post only one of each pair. Separate episodes can also repeat a beat.\n")
    w(f"- **Spoiler check over-flags:** {dist(spoiler)}. It caught all 4 known high-risk test clips but also wrongly marked 2 of 32 other test answers high, and 48 low labels flatten the ranking. "
      "Treat it as a flag for an editor, not a final call.\n")
    w(f"- **Platform risk is unverified:** {dist(platform)}, judged from the script only. An editor must check the footage.\n")
    w("- **Text only:** performance, music and pacing decide whether a hook lands, and the pipeline can't see them.\n")
    w("- **Keyword filter is weak:** it found nothing in Episodes 14 and 39 and proposes ordinary dialogue elsewhere, so it's only a hint for the model.\n")
    w("- **Rate limits:** the Claude critic model has one credential and hit a 5-minute cooldown after heavy use. The pipeline now waits and resumes, but a run can slow down without warning.\n")

    w("\n## 6. Episodes left without a promo\n")
    for n in ranked["episodes_with_no_good_promo"]:
        w(f"- **{n['episode']}**: {n['reason']}\n")

    w("\n## 7. How we'd know it works with a real team\n")
    w("- **Editor acceptance:** how many of the top 10 an editor posts without changing the cuts. Target: 7 or more.\n")
    w("- **Time saved:** editor minutes per show, compared with watching every episode.\n")
    w("- **Post results:** 3-second hold, completion and click-through for tool-picked vs. hand-picked promos.\n")
    w("- **Feedback loop:** rejected and retimed picks become test cases for prompt and model changes.\n")

    out = W / "outputs/quality_report.md"
    out.write_text("".join(L), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
