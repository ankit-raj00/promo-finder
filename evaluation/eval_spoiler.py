"""
Compares models for the spoiler check, following EVAL_PLAN.md. Run from the project root.
  python -m evaluation.eval_spoiler <model> [<model> ...]
Writes outputs/step_eval/spoiler/. Finished calls are skipped.
"""
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from promo_finder.judge.spoiler import check_clip
from promo_finder.ingest.transcript import series_context

OUT = Path("outputs/step_eval/spoiler")
PARSED, REF = Path("data/parsed"), Path("data/promos_final")
SEED, RUNS, WORKERS = 20261106, 2, 2


def load(fname):
    return json.loads((PARSED / fname).read_text(encoding="utf-8"))


def build_test_set():
    path = OUT / "test_set.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    labels = json.loads(Path("outputs/earlier_hand_judged/global_calibration.json").read_text(encoding="utf-8"))["scores"]
    pools = {"high": [], "low": [], "none": []}
    for f in sorted(REF.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        num = "".join(c for c in d["episode_number"] if c.isdigit())
        for i, p in enumerate(d["promos"]):
            s = labels.get(f"E{num}#{i}")
            if s:
                pools[s["spoiler_risk"]].append({"key": f"E{num}#{i}", "label": s["spoiler_risk"], "source_file": f.name, "segments": p["segments"]})
    rng = random.Random(SEED)
    clips = pools["high"] + rng.sample(pools["low"], 8) + rng.sample(pools["none"], 8)
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clips, indent=2), encoding="utf-8")
    return clips


def run_model(model, clips, context):
    folder = OUT / model.replace("/", "_")
    folder.mkdir(parents=True, exist_ok=True)

    def one(job):
        run, c = job
        path = folder / f"run{run}_{c['key'].replace('#', '_')}.json"
        tags = {"key": c["key"], "label": c["label"], "run": run}
        if path.exists():
            return {**json.loads(path.read_text(encoding="utf-8")), **tags}
        try:
            r = check_clip(load(c["source_file"]), c["segments"], context, model=model)
        except Exception as e:  # noqa: BLE001 - a failed call is a data point for this model
            return {"failed": f"{type(e).__name__}: {e}"[:200], **tags}
        path.write_text(json.dumps(r, indent=2), encoding="utf-8")
        return {**r, **tags}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        rows = list(pool.map(one, [(r, c) for r in range(1, RUNS + 1) for c in clips]))
    ok = [r for r in rows if "failed" not in r]
    high = [r for r in ok if r["label"] == "high"]
    answers = {}
    for r in ok:
        answers.setdefault(r["key"], []).append(r["spoiler_risk"])
    pairs = [v for v in answers.values() if len(v) == 2]
    caught = {r["key"] for r in high if r["spoiler_risk"] == "high"}
    n = len(ok) or 1
    s = {"model": model, "calls": len(rows), "completed": len(ok),
         "high_recall": round(sum(r["spoiler_risk"] == "high" for r in high) / len(high), 3) if high else 0.0,
         "high_clips_caught": len(caught),
         "accuracy": round(sum(r["spoiler_risk"] == r["label"] for r in ok) / n, 3),
         "consistency": round(sum(v[0] == v[1] for v in pairs) / len(pairs), 3) if pairs else 0.0,
         "false_high": sum(r["spoiler_risk"] == "high" and r["label"] != "high" for r in ok),
         "avg_seconds": round(sum(r["seconds"] for r in ok) / n, 2),
         "avg_output_tokens": round(sum(r["output_tokens"] for r in ok) / n),
         "errors": sorted({r["failed"] for r in rows if "failed" in r})[:2]}
    s["gates_failed"] = [g for g, bad in (("completed", len(ok) < len(rows)), ("high recall", len(caught) < 3)) if bad]
    return s


def main():
    models = sys.argv[1:]
    if not models:
        sys.exit(__doc__)
    clips = build_test_set()
    context = series_context(PARSED)
    print(f"{len(clips)} clips, labels: high {sum(c['label'] == 'high' for c in clips)}", flush=True)
    results = [run_model(m, clips, context) for m in models]
    fastest = min((r["avg_seconds"] for r in results if r["avg_seconds"]), default=1)
    lowest = min((r["avg_output_tokens"] for r in results if r["avg_output_tokens"]), default=1)
    for r in results:
        speed = fastest / r["avg_seconds"] if r["avg_seconds"] else 0
        tokens = lowest / r["avg_output_tokens"] if r["avg_output_tokens"] else 0
        r["score"] = round(0.4 * r["high_recall"] + 0.3 * r["accuracy"] + 0.1 * r["consistency"] + 0.1 * speed + 0.1 * tokens, 3)
    results.sort(key=lambda r: (bool(r["gates_failed"]), -r["score"]))
    (OUT / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("| Model | Gates | Score | High recall | High clips caught | Accuracy | Consistency | False highs | Avg sec | Avg output tokens |\n|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        gate = "FAIL: " + ", ".join(r["gates_failed"]) if r["gates_failed"] else "pass"
        print(f"| `{r['model']}` | {gate} | {r['score']} | {r['high_recall']} | {r['high_clips_caught']}/4 | {r['accuracy']} | {r['consistency']} | {r['false_high']} | {r['avg_seconds']} | {r['avg_output_tokens']} |")
        if r["errors"]:
            print("  errors:", r["errors"])


if __name__ == "__main__":
    main()
