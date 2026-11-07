"""
Compares models for the critic step, following EVAL_PLAN.md.
  python -m evaluation.eval_critic <model> [<model> ...]
Writes outputs/step_eval/critic/ (test set, raw scores, summary). Finished calls are skipped.
"""
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path

from promo_finder.judge.critic import score_clip
from promo_finder.ingest.transcript import fmt_ts, indexed_elements

OUT = Path("outputs/step_eval/critic")
PARSED, REF = Path("data/parsed"), Path("data/promos_final")
SEED, PER_CLASS, RUNS, WORKERS = 20261106, 12, 2, 4


def episode(fname):
    return json.loads((PARSED / fname).read_text(encoding="utf-8"))


def key_for(ep_number, i):
    return f"E{''.join(c for c in ep_number if c.isdigit())}#{i}"


def build_test_set():
    path = OUT / "test_set.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    rng = random.Random(SEED)
    scores = json.loads(Path("outputs/earlier_hand_judged/global_calibration.json").read_text(encoding="utf-8"))["scores"]
    pos, promo_spans = [], {}
    for f in sorted(REF.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        promo_spans[f.name] = [(s["start_s"], s["end_s"]) for p in d["promos"] for s in p["segments"]]
        for i, p in enumerate(d["promos"]):
            s = scores.get(key_for(d["episode_number"], i))
            if s and s["hook_strength"] + s["cliffhanger_strength"] >= 8:
                pos.append({"label": "promo", "source_file": f.name, "segments": p["segments"]})
    rng.shuffle(pos)
    neg, files = [], [f.name for f in sorted(REF.glob("*.json"))]
    rng.shuffle(files)
    for fname in files:
        if len(neg) == PER_CLASS:
            break
        els = indexed_elements(episode(fname))
        for _ in range(50):
            a = rng.choice(els)
            want = rng.uniform(30, 45)
            ends = [e for e in els if a["start_s"] + 25 <= e["end_s"] <= a["start_s"] + want]
            if not ends:
                continue
            b = ends[-1]
            if all(b["end_s"] <= s or a["start_s"] >= e for s, e in promo_spans[fname]):
                neg.append({"label": "control", "source_file": fname, "segments": [
                    {"start": fmt_ts(a["start_s"]), "end": fmt_ts(b["end_s"]), "start_s": a["start_s"], "end_s": b["end_s"]}]})
                break
    clips = pos[:PER_CLASS] + neg
    rng.shuffle(clips)
    for i, c in enumerate(clips):
        c["id"] = i
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clips, indent=2), encoding="utf-8")
    return clips


def overall(s):
    return (s["hook"] + s["cliffhanger"] + s["standalone"]) / 3


def auc(pos, neg):
    if not pos or not neg:
        return 0.0
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def run_model(model, clips):
    folder = OUT / model.replace("/", "_")
    folder.mkdir(parents=True, exist_ok=True)

    def one(job):
        run, clip = job
        tags = {"id": clip["id"], "run": run, "label": clip["label"]}
        path = folder / f"run{run}_clip{clip['id']}.json"
        if path.exists():
            return {**json.loads(path.read_text(encoding="utf-8")), **tags}
        try:
            r = score_clip([(episode(clip["source_file"]), clip["segments"])], model=model)
        except Exception as e:  # noqa: BLE001 - a failed call is a data point for this model
            return {"failed": f"{type(e).__name__}: {e}"[:200], **tags}
        path.write_text(json.dumps(r, indent=2), encoding="utf-8")
        return {**r, **tags}

    jobs = [(run, c) for run in range(1, RUNS + 1) for c in clips]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        rows = list(pool.map(one, jobs))
    ok = [r for r in rows if "failed" not in r and not r.get("refused")]
    means = {}
    for r in ok:
        means.setdefault(r["id"], []).append(overall(r))
    label = {c["id"]: c["label"] for c in clips}
    avg = {i: sum(v) / len(v) for i, v in means.items()}
    pos = [v for i, v in avg.items() if label[i] == "promo"]
    neg = [v for i, v in avg.items() if label[i] == "control"]
    gaps = []
    for i in means:
        gaps += [abs(a - b) for a, b in combinations(means[i], 2)]
    n = len(ok) or 1
    s = {
        "model": model, "calls": len(rows), "completed_rate": round(len(ok) / len(rows), 3),
        "refused": sum(bool(r.get("refused")) for r in rows),
        "first_try": round(sum(r["attempts"] == 1 for r in ok) / n, 3),
        "separation": auc(pos, neg),
        "promo_mean": round(sum(pos) / len(pos), 2) if pos else None,
        "control_mean": round(sum(neg) / len(neg), 2) if neg else None,
        "consistency": round(1 - (sum(gaps) / len(gaps)) / 4, 3) if gaps else 0.0,
        "avg_seconds": round(sum(r["seconds"] for r in ok) / n, 2),
        "avg_output_tokens": round(sum(r["output_tokens"] for r in ok) / n),
        "errors": sorted({r["failed"] for r in rows if "failed" in r})[:3],
    }
    s["gates_failed"] = [g for g, bad in (("completed", s["completed_rate"] < 1), ("refusals", s["refused"] > 0),
                                          ("first try", s["first_try"] < 0.9)) if bad]
    return s


def add_scores(results):
    done = [r for r in results if r["avg_seconds"]]
    fastest = min((r["avg_seconds"] for r in done), default=1)
    lowest = min((r["avg_output_tokens"] for r in done if r["avg_output_tokens"]), default=1)
    for r in results:
        speed = fastest / r["avg_seconds"] if r["avg_seconds"] else 0
        tok = lowest / r["avg_output_tokens"] if r["avg_output_tokens"] else 0
        r["score"] = round(0.5 * r["separation"] + 0.25 * r["consistency"] + 0.125 * speed + 0.125 * tok, 3)
    results.sort(key=lambda r: (bool(r["gates_failed"]), -r["score"]))


def main():
    models = sys.argv[1:]
    if not models:
        sys.exit(__doc__)
    clips = build_test_set()
    print(f"{len(clips)} clips: {sum(c['label'] == 'promo' for c in clips)} promos, "
          f"{sum(c['label'] == 'control' for c in clips)} controls", flush=True)
    results = []
    for m in models:
        print("==", m, flush=True)
        results.append(run_model(m, clips))
        add_scores(results)
        (OUT / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("| Model | Gates | Score | Separation | Promo avg | Control avg | Consistency | First try | Avg sec | Avg output tokens |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        gate = "FAIL: " + ", ".join(r["gates_failed"]) if r["gates_failed"] else "pass"
        print(f"| `{r['model']}` | {gate} | {r['score']} | {r['separation']} | {r['promo_mean']} | {r['control_mean']} | "
              f"{r['consistency']} | {r['first_try']} | {r['avg_seconds']} | {r['avg_output_tokens']} |")
        if r["errors"]:
            print("  errors:", r["errors"])


if __name__ == "__main__":
    main()
