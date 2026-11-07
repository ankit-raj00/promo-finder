"""
Runs the model comparison defined in EVAL_PLAN.md.

  python -m evaluation.eval_models <model> [<model> ...] [--episodes 001,006,...] [--runs 2]
  python -m evaluation.eval_models --blind <model_a> <model_b>     # makes the blind human review sheet

Writes outputs/model_eval/<model>/run<N>/*.json (raw picks) and summary.{json,md}.
Finished episodes are skipped, so a re-run resumes.
"""
import csv
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from promo_finder.judge import scorer as ls
from promo_finder.reports.agreement import iou, spans
from promo_finder.ingest.heuristics import generate_windows
from promo_finder.ingest.transcript import series_context

PARSED, REF, OUT = Path("data/parsed"), Path("data/promos_final"), Path("outputs/model_eval")
DEFAULT_EPISODES = "001,006,009,014,017,019,027,036,039,046"
WORKERS = 3
BLIND_SEED = 20261106

GATES = {"min_completed": 1.0, "max_refused": 0, "min_decision": 0.8, "max_dropped": 0.10}
WEIGHTS = {"overlap": 0.40, "consistency": 0.25, "first_try": 0.15, "speed": 0.10, "tokens": 0.10}


def folder(model: str, run: int) -> Path:
    return OUT / model.replace("/", "_") / f"run{run}"


def run_episode(model: str, run: int, prefix: str, context: str) -> dict:
    parsed = next(PARSED.glob(f"episode-{prefix}-*.json"))
    episode = json.loads(parsed.read_text(encoding="utf-8"))
    ref = json.loads((REF / parsed.name).read_text(encoding="utf-8"))
    row = {"episode": episode["episode_number"], "prefix": prefix, "run": run}
    path = folder(model, run) / parsed.name

    if path.exists():
        out = json.loads(path.read_text(encoding="utf-8"))
    else:
        start = time.time()
        try:
            out = ls.score_episode(episode, {"candidates": generate_windows(episode)}, context, model=model)
        except Exception as e:  # noqa: BLE001 - a failed call is a data point for this model
            row.update(status="failed", error=f"{type(e).__name__}: {e}"[:300])
            return row
        out["_meta"]["seconds"] = round(time.time() - start, 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    promos, meta = out["promos"], out["_meta"]
    row.update(status="ok", seconds=meta["seconds"], picks=len(promos), retried=meta["validation_retry"],
               dropped=meta.get("dropped_after_validation", 0), refused=meta["refused"],
               decision_agrees=bool(promos) == bool(ref["promos"]), source_file=parsed.name)
    if promos and ref["promos"]:
        row["best_iou"] = round(max(iou(spans(promos[0]), spans(p)) for p in ref["promos"]), 3)
    return row


def consistency(model: str, rows: list, runs: int) -> float | None:
    """Agreement between run 1 and run 2: both skip = 1, one skips = 0, both pick = top-pick overlap."""
    if runs < 2:
        return None
    scores = []
    for r in [x for x in rows if x["run"] == 1 and x["status"] == "ok"]:
        a = json.loads((folder(model, 1) / r["source_file"]).read_text(encoding="utf-8"))["promos"]
        other = folder(model, 2) / r["source_file"]
        if not other.exists():
            continue
        b = json.loads(other.read_text(encoding="utf-8"))["promos"]
        scores.append(1.0 if not a and not b else 0.0 if not a or not b else iou(spans(a[0]), spans(b[0])))
    return round(sum(scores) / len(scores), 3) if scores else None


def summarize(model: str, rows: list, runs: int, usage: dict) -> dict:
    ok = [r for r in rows if r["status"] == "ok"]
    ious = [r["best_iou"] for r in ok if "best_iou" in r]
    picks = sum(r["picks"] for r in ok)
    dropped = sum(r["dropped"] for r in ok)
    n = len(ok) or 1
    s = {
        "model": model, "calls": len(rows), "completed": len(ok),
        "completed_rate": round(len(ok) / len(rows), 3),
        "refused": sum(r["refused"] for r in ok),
        "decision_agreement": round(sum(r["decision_agrees"] for r in ok) / n, 3),
        "dropped_rate": round(dropped / (picks + dropped), 3) if picks + dropped else 0.0,
        "overlap": round(sum(ious) / len(ious), 3) if ious else 0.0,
        "consistency": consistency(model, rows, runs),
        "first_try": round(sum(not r["retried"] for r in ok) / n, 3),
        "avg_seconds": round(sum(r["seconds"] for r in ok) / n, 1),
        "avg_output_tokens": usage["by_model"].get(model, {}).get("output", 0) // n or None,
        "valid_promos": picks, "rows": rows,
    }
    s["gates_failed"] = [name for name, failed in (
        ("completed", s["completed_rate"] < GATES["min_completed"]),
        ("refusals", s["refused"] > GATES["max_refused"]),
        ("pick vs. skip", s["decision_agreement"] < GATES["min_decision"]),
        ("dropped picks", s["dropped_rate"] > GATES["max_dropped"]),
    ) if failed]
    return s


def add_scores(results: list):
    fastest = min((r["avg_seconds"] for r in results if r["avg_seconds"]), default=1)
    tokens = [r["avg_output_tokens"] for r in results if r["avg_output_tokens"]]
    lowest = min(tokens) if tokens else None
    for r in results:
        speed = fastest / r["avg_seconds"] if r["avg_seconds"] else 0
        tok = lowest / r["avg_output_tokens"] if lowest and r["avg_output_tokens"] else 0
        r["score"] = round(WEIGHTS["overlap"] * r["overlap"] + WEIGHTS["consistency"] * (r["consistency"] or 0)
                           + WEIGHTS["first_try"] * r["first_try"] + WEIGHTS["speed"] * speed + WEIGHTS["tokens"] * tok, 3)
    results.sort(key=lambda r: (bool(r["gates_failed"]), -r["score"]))


def to_markdown(results: list) -> str:
    head = ("| Model | Gates | Score | Overlap | Consistency | First try | Pick vs. skip | Dropped | Avg sec | Avg output tokens |\n"
            "|---|---|---|---|---|---|---|---|---|---|\n")
    lines = [f"| `{r['model']}` | {'FAIL: ' + ', '.join(r['gates_failed']) if r['gates_failed'] else 'pass'} | {r['score']} | {r['overlap']} | "
             f"{r['consistency']} | {r['first_try']} | {r['decision_agreement']} | {r['dropped_rate']} | {r['avg_seconds']} | {r['avg_output_tokens']} |"
             if r["completed"] else f"| `{r['model']}` | not tested (request rejected) | - | - | - | - | - | - | - | - |"
             for r in results]
    return ("# Model comparison\n\nRules fixed beforehand in EVAL_PLAN.md. The overlap reference is our existing output, not editor ground truth.\n\n"
            + head + "\n".join(lines) + "\n")


def blind_sheet(model_a: str, model_b: str):
    """Top pick per episode from each finalist, shuffled, with the model names kept in a separate key file."""
    items = []
    for model in (model_a, model_b):
        for f in sorted(folder(model, 1).glob("*.json")):
            promos = json.loads(f.read_text(encoding="utf-8"))["promos"]
            if promos:
                p = promos[0]
                items.append({"model": model, "episode": f.name.split("-")[1],
                              "clip": " + ".join(f"{s['start']}-{s['end']}" for s in p["segments"]),
                              "hook": p["hook"]["quote"], "cliffhanger": p["cliffhanger"]["quote"]})
    random.Random(BLIND_SEED).shuffle(items)
    with open(OUT / "blind_review.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "episode", "clip", "hook", "cliffhanger", "hook_1to5", "cliffhanger_1to5", "standalone_1to5"])
        for i, it in enumerate(items, 1):
            w.writerow([i, it["episode"], it["clip"], it["hook"], it["cliffhanger"], "", "", ""])
    (OUT / "blind_key_do_not_open.json").write_text(json.dumps({i: it["model"] for i, it in enumerate(items, 1)}, indent=2), encoding="utf-8")
    print(f"{len(items)} clips -> {OUT / 'blind_review.csv'}")


def main():
    args = sys.argv[1:]
    if args[:1] == ["--blind"]:
        return blind_sheet(args[1], args[2])
    episodes, runs = DEFAULT_EPISODES, 2
    if "--episodes" in args:
        i = args.index("--episodes")
        episodes = args[i + 1]
        del args[i:i + 2]
    if "--runs" in args:
        i = args.index("--runs")
        runs = int(args[i + 1])
        del args[i:i + 2]
    if not args:
        sys.exit(__doc__)
    prefixes = episodes.split(",")
    context = series_context(PARSED)
    OUT.mkdir(parents=True, exist_ok=True)

    # Keep earlier models' results, so a candidate added later joins the same table.
    summary = OUT / "summary.json"
    results = [r for r in json.loads(summary.read_text(encoding="utf-8")) if r["model"] not in args] if summary.exists() else []
    for model in args:
        ls.USAGE = ls.UsageLog()
        ls.FALLBACK_MODEL = model  # no fallback here: a refusal counts against this model
        print(f"\n== {model}", flush=True)
        jobs = [(run, p) for run in range(1, runs + 1) for p in prefixes]
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            rows = list(pool.map(lambda j: run_episode(model, j[0], j[1], context), jobs))
        for r in rows:
            detail = r.get("error") or f"picks={r['picks']} iou={r.get('best_iou')} retried={r['retried']} {r['seconds']}s"
            print(f"  run{r['run']} {r['episode']}: {r['status']} {detail}", flush=True)
        results.append(summarize(model, rows, runs, ls.USAGE.summary()))
        add_scores(results)
        (OUT / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        (OUT / "summary.md").write_text(to_markdown(results), encoding="utf-8")
    print("\n" + to_markdown(results))


if __name__ == "__main__":
    main()
