"""
Inter-judge agreement: did an independent, blind second judge pick the same
moments? Measured as temporal overlap (IoU) between promo time ranges.

  python agreement.py data/promos_final data/promos_blind
"""
import json
import sys
from pathlib import Path


def spans(promo):
    return [(s["start_s"], s["end_s"]) for s in promo["segments"]]


def iou(a, b):
    def length(xs):
        return sum(e - s for s, e in xs)
    inter = sum(max(0.0, min(e1, e2) - max(s1, s2)) for s1, e1 in a for s2, e2 in b)
    union = length(a) + length(b) - inter
    return inter / union if union > 0 else 0.0


def main():
    first, blind = Path(sys.argv[1]), Path(sys.argv[2])
    rows = []
    for bf in sorted(blind.glob("*.json")):
        b = json.loads(bf.read_text(encoding="utf-8"))
        a = json.loads((first / bf.name).read_text(encoding="utf-8"))
        ap, bp = a.get("promos", []), b.get("promos", [])
        if not ap and not bp:
            rows.append({"episode": a["episode_number"], "outcome": "both rejected", "best_iou": None, "overlap": True})
            continue
        if not ap or not bp:
            rows.append({"episode": a["episode_number"], "outcome": "one judge rejected", "best_iou": 0.0, "overlap": False})
            continue
        top_b = spans(bp[0])
        best = max(iou(top_b, spans(p)) for p in ap)
        rows.append({"episode": a["episode_number"], "outcome": "both picked",
                     "best_iou": round(best, 2), "overlap": best > 0.0, "strong": best >= 0.5})

    picked = [r for r in rows if r["outcome"] == "both picked"]
    decision_agree = sum(r["outcome"] != "one judge rejected" for r in rows)
    overlap = sum(r["overlap"] for r in picked)
    strong = sum(r.get("strong", False) for r in picked)
    mean_iou = sum(r["best_iou"] for r in picked) / len(picked) if picked else 0.0

    summary = {
        "episodes": len(rows),
        "pick_vs_reject_agreement": f"{decision_agree}/{len(rows)}",
        "blind_top_pick_overlaps_a_first_pass_promo": f"{overlap}/{len(picked)}",
        "blind_top_pick_iou_ge_0.5": f"{strong}/{len(picked)}",
        "mean_best_iou": round(mean_iou, 2),
        "per_episode": rows,
    }
    out = Path("outputs/agreement_report.json")
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    for r in rows:
        print(f"  {r['episode']:<11} {r['outcome']:<20} best IoU={r['best_iou']}")
    print({k: v for k, v in summary.items() if k != "per_episode"})


if __name__ == "__main__":
    main()
