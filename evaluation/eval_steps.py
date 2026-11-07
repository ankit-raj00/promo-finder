"""
Compares models for the ordering and montage steps, following EVAL_PLAN.md.
Run from a show's work folder, after rank.py:

  python -m evaluation.eval_steps ordering <model> [<model> ...]
  python -m evaluation.eval_steps montages <critic_model> <model> [<model> ...]

Writes outputs/step_eval/<step>/ (raw runs and summary.json). Finished runs are skipped.
"""
import json
import sys
from itertools import combinations
from pathlib import Path

from promo_finder.rank import montages as ce
from promo_finder.rank import post_first as gr
from promo_finder.llm import client as llm_client
from promo_finder.judge.critic import score_clip
from promo_finder.judge.validate import validate_montages

RUNS, TOP_N = 3, 15
BASE = Path("outputs/step_eval")


def relative(results, weights):
    done = [r for r in results if r.get("avg_seconds")]
    fastest = min((r["avg_seconds"] for r in done), default=1)
    lowest = min((r["avg_output_tokens"] for r in done if r["avg_output_tokens"]), default=1)
    for r in results:
        speed = fastest / r["avg_seconds"] if r.get("avg_seconds") else 0
        tokens = lowest / r["avg_output_tokens"] if r.get("avg_output_tokens") else 0
        r["score"] = round(sum(w * r.get(k, 0) for k, w in weights.items()) + r["_speed_w"] * speed + r["_tok_w"] * tokens, 3)
    results.sort(key=lambda r: (bool(r["gates_failed"]), -r["score"]))


def ordering(models):
    out = BASE / "ordering"
    rows = json.loads(Path("outputs/ranked_promos.json").read_text(encoding="utf-8"))["ranked_promos"][:TOP_N]
    by_key = {r["key"]: r for r in rows}
    prompt = gr.build_prompt(rows)
    results = []
    for m in models:
        folder = out / m.replace("/", "_")
        folder.mkdir(parents=True, exist_ok=True)
        runs, errors = [], []
        for i in range(1, RUNS + 1):
            path = folder / f"run{i}.json"
            if path.exists():
                runs.append(json.loads(path.read_text(encoding="utf-8")))
                continue
            try:
                parsed, meas = llm_client.complete_measured(gr.Order, prompt, m)
            except Exception as e:  # noqa: BLE001 - a failed call is a data point for this model
                errors.append(f"{type(e).__name__}: {e}"[:200])
                continue
            run = {"keys": [s.key for s in parsed.slots], "reasons": [s.reason for s in parsed.slots], **meas}
            path.write_text(json.dumps(run, indent=2), encoding="utf-8")
            runs.append(run)
        n = len(runs) or 1
        tops = [set(r["keys"][:10]) for r in runs]
        pairs = [len(a & b) / len(a | b) for a, b in combinations(tops, 2)]
        bad_slots = sum(len(r["keys"]) != 10 or len(set(r["keys"])) != 10 or any(k not in by_key for k in r["keys"]) for r in runs)
        risky = sum(by_key[k]["spoiler_risk"] == "high" or by_key[k]["platform_risk"] == "high"
                    for r in runs for k in r["keys"][:3] if k in by_key)
        s = {"model": m, "runs_done": len(runs), "bad_lists": bad_slots, "risky_in_top3": risky,
             "stability": round(sum(pairs) / len(pairs), 3) if pairs else 0.0,
             "avg_seconds": round(sum(r["seconds"] for r in runs) / n, 2),
             "avg_output_tokens": round(sum(r["output_tokens"] for r in runs) / n), "errors": errors,
             "_speed_w": 0.2, "_tok_w": 0.2}
        s["gates_failed"] = [g for g, bad in (("runs failed", len(runs) < RUNS), ("invalid list", bad_slots > 0), ("risky top 3", risky > 0)) if bad]
        results.append(s)
        print(m, {k: s[k] for k in ("runs_done", "bad_lists", "stability", "avg_seconds", "avg_output_tokens")}, errors[:1], flush=True)
    relative(results, {"stability": 0.6})
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("| Model | Gates | Score | Stability | Bad lists | Risky in top 3 | Avg sec | Avg output tokens |\n|---|---|---|---|---|---|---|---|")
    for r in results:
        gate = "FAIL: " + ", ".join(r["gates_failed"]) if r["gates_failed"] else "pass"
        print(f"| `{r['model']}` | {gate} | {r['score']} | {r['stability']} | {r['bad_lists']} | {r['risky_in_top3']} | {r['avg_seconds']} | {r['avg_output_tokens']} |")


def score_montage(m, critic_model):
    parts = []
    for s in m["segments"]:
        episode = json.loads((ce.PARSED / s["source_file"]).read_text(encoding="utf-8"))
        parts.append((episode, [s]))
    c = score_clip(parts, model=critic_model)
    return round((c["hook"] + c["cliffhanger"] + c["standalone"]) / 3, 2) if "hook" in c else None


def montages(critic_model, models):
    out = BASE / "montages"
    results = []
    for m in models:
        folder = out / m.replace("/", "_")
        folder.mkdir(parents=True, exist_ok=True)
        runs, errors = [], []
        for i in range(1, RUNS + 1):
            path = folder / f"run{i}.json"
            if path.exists():
                runs.append(json.loads(path.read_text(encoding="utf-8")))
                continue
            try:
                specs, meas = ce.propose_measured(TOP_N, m)
            except Exception as e:  # noqa: BLE001 - a failed call is a data point for this model
                errors.append(f"{type(e).__name__}: {e}"[:200])
                continue
            built, invalid = [], []
            for s in specs:
                try:
                    built.append(ce.build_montage(s))
                except (KeyError, StopIteration) as e:
                    invalid.append(f"{s['title']}: unknown reference {e}")
            tmp = folder / f"run{i}_built.json"
            tmp.write_text(json.dumps({"montages": built}, indent=2), encoding="utf-8")
            checks = validate_montages(tmp, ce.PARSED) if built else []
            valid = [b for b, c in zip(built, checks) if c["status"] != "error"]
            invalid += [f"{c['title']}: {'; '.join(c['errors'])}" for c in checks if c["status"] == "error"]
            for v in valid:
                v["critic_overall"] = score_montage(v, critic_model)
            run = {"proposed": len(specs), "valid": valid, "invalid": invalid, **meas}
            path.write_text(json.dumps(run, indent=2), encoding="utf-8")
            runs.append(run)
        n = len(runs) or 1
        proposed = sum(r["proposed"] for r in runs)
        valid_n = sum(len(r["valid"]) for r in runs)
        quality = [v["critic_overall"] for r in runs for v in r["valid"] if v.get("critic_overall")]
        s = {"model": m, "runs_done": len(runs), "proposed": proposed, "valid_count": valid_n,
             "validity": round(valid_n / proposed, 3) if proposed else 0.0,
             "critic_avg": round(sum(quality) / len(quality), 2) if quality else 0.0,
             "avg_seconds": round(sum(r["seconds"] for r in runs) / n, 2),
             "avg_output_tokens": round(sum(r["output_tokens"] for r in runs) / n), "errors": errors,
             "_speed_w": 0.1, "_tok_w": 0.1}
        s["quality"] = round(s["critic_avg"] / 5, 3)
        s["gates_failed"] = [g for g, bad in (("runs failed", len(runs) < RUNS), ("validity under 50%", s["validity"] < 0.5)) if bad]
        results.append(s)
        print(m, {k: s[k] for k in ("runs_done", "proposed", "valid_count", "critic_avg", "avg_seconds")}, errors[:1], flush=True)
    relative(results, {"validity": 0.4, "quality": 0.4})
    (out / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("| Model | Gates | Score | Validity | Valid/proposed | Critic avg (out of 5) | Avg sec | Avg output tokens |\n|---|---|---|---|---|---|---|---|")
    for r in results:
        gate = "FAIL: " + ", ".join(r["gates_failed"]) if r["gates_failed"] else "pass"
        print(f"| `{r['model']}` | {gate} | {r['score']} | {r['validity']} | {r['valid_count']}/{r['proposed']} | {r['critic_avg']} | {r['avg_seconds']} | {r['avg_output_tokens']} |")


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    if args[0] == "ordering":
        ordering(args[1:])
    elif args[0] == "montages":
        montages(args[1], args[2:])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
