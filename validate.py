"""
Deterministic checks on every judged promo against the source screenplay.
The LLM proposes; this code verifies. Nothing reaches an editor unmarked.

Checks per promo:
  - segments lie inside the episode, are ordered, and total 20-60s
  - hook and cliffhanger quotes exist in the screenplay:
      exact      verbatim inside one line
      merged     verbatim, but joins adjacent lines          (warning)
      paraphrase right moment at the claimed time, reworded   (warning, repairable)
      missing    no matching line near the claimed time      (error)
  - the quoted line sits inside the clip
  - hook lands within the first 3s of the clip
  - cliffhanger lands at the cut

Usage:
  python validate.py [promos_dir] [parsed_dir] [report_path]
  python validate.py --repair <out_dir> [promos_dir] [parsed_dir]
      writes copies with quotes/timestamps snapped to the verbatim script line
      (raw judge output is left untouched, as the brief asks for unedited outputs)
  python validate.py --montages outputs/cross_episode_promos.json [parsed_dir]
Exit code 1 if any promo has an error.
"""
import difflib
import json
import re
import sys
from pathlib import Path

from transcript import fmt_ts

MIN_LEN, MAX_LEN, LEN_TOL = 20.0, 60.0, 0.5
HOOK_WINDOW_S = 3.0
CLIFF_WINDOW_S = 6.0
TS_TOL_S = 1.0
PARAPHRASE_WINDOW_S = 2.0
PARAPHRASE_MIN_RATIO = 0.55


def norm(s: str) -> str:
    s = (s or "").lower().replace("’", "'").replace("—", " ").replace("–", " ")
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def to_s(ts: str):
    m = re.match(r"(\d+):(\d+(?:\.\d+)?)", ts or "")
    return int(m.group(1)) * 60 + float(m.group(2)) if m else None


def elements(episode: dict):
    els = [e for s in episode["scenes"] for e in s["elements"] if e["start_s"] is not None]
    els.sort(key=lambda e: e["start_s"])
    return els


def _nearest(cands, claimed_s):
    if claimed_s is None:
        return cands[0]
    return min(cands, key=lambda e: abs(e["start_s"] - claimed_s))


def locate(quote: str, els: list, claimed_s):
    """Return (status, element)."""
    q = norm(quote)
    if not q:
        return "missing", None

    exact = [e for e in els if q in norm(e["text"])]
    if exact:
        return "exact", _nearest(exact, claimed_s)

    # quote joins 2-3 lines; try consecutive lines of the same kind (e.g. two
    # dialogue lines with a camera cue between them), then raw adjacency
    merged = []
    for pool in ([e for e in els if e["kind"] == "dialogue"], els):
        for i in range(len(pool)):
            for span in (2, 3):
                window = pool[i:i + span]
                if len(window) < span:
                    continue
                joined = norm(" ".join(e["text"] for e in window))
                if q in joined or (len(q) > 20 and joined in q):
                    merged.append(window[0])
        if merged:
            return "merged", _nearest(merged, claimed_s)

    if claimed_s is not None:
        near = [e for e in els if abs(e["start_s"] - claimed_s) <= PARAPHRASE_WINDOW_S]
        best, best_ratio = None, 0.0
        for e in near:
            t = norm(e["text"])
            ratio = difflib.SequenceMatcher(None, q, t[: max(len(q), 1) + 40]).ratio()
            if ratio > best_ratio:
                best, best_ratio = e, ratio
        if best is not None and best_ratio >= PARAPHRASE_MIN_RATIO:
            return "paraphrase", best
    return "missing", None


def check_promo(p: dict, els: list, duration: float):
    errors, warnings, located = [], [], {}
    segs = p["segments"]
    total, prev_end = 0.0, -1.0
    for s in segs:
        if s["start_s"] >= s["end_s"]:
            errors.append(f"segment {s['start']}-{s['end']} has start >= end")
        if s["start_s"] < -0.01 or s["end_s"] > duration + 0.5:
            errors.append(f"segment {s['start']}-{s['end']} outside episode (0-{duration:.1f}s)")
        if s["start_s"] < prev_end:
            errors.append("segments overlap or are out of order")
        prev_end = s["end_s"]
        total += s["end_s"] - s["start_s"]
    if not (MIN_LEN - LEN_TOL <= total <= MAX_LEN + LEN_TOL):
        errors.append(f"total length {total:.1f}s outside 20-60s")
    if abs(total - p.get("total_length_s", total)) > 1.0:
        warnings.append(f"total_length_s={p['total_length_s']} but segments sum to {total:.1f}s")

    def inside(t):
        return any(s["start_s"] - TS_TOL_S <= t <= s["end_s"] + TS_TOL_S for s in segs)

    for key in ("hook", "cliffhanger"):
        claimed = to_s(p[key].get("timestamp"))
        status, el = locate(p[key]["quote"], els, claimed)
        located[key] = (status, el)
        if status == "missing":
            errors.append(f"{key} quote not found in screenplay: {p[key]['quote'][:70]!r}")
            continue
        if status == "merged":
            warnings.append(f"{key} quote joins adjacent lines")
        if status == "paraphrase":
            warnings.append(f"{key} quote is reworded; verbatim line at {el['start']}: {el['text'][:60]!r}")
        if claimed is not None and abs(el["start_s"] - claimed) > TS_TOL_S + 0.5:
            warnings.append(f"{key} timestamp {p[key]['timestamp']} but line is at {el['start']}")
        if not inside(el["start_s"]):
            errors.append(f"{key} line at {el['start']} is outside the clip")
        if key == "hook" and el["start_s"] - segs[0]["start_s"] > HOOK_WINDOW_S:
            errors.append(f"hook lands {el['start_s'] - segs[0]['start_s']:.1f}s in (rule: within 3s)")
        if key == "cliffhanger" and segs[-1]["end_s"] - el["end_s"] > CLIFF_WINDOW_S:
            warnings.append(f"cliffhanger line ends {segs[-1]['end_s'] - el['end_s']:.1f}s before the cut")
    return errors, warnings, located


def validate(promos_dir: Path, parsed_dir: Path, repair_dir: Path | None = None):
    results = []
    if repair_dir:
        repair_dir.mkdir(parents=True, exist_ok=True)
    for f in sorted(promos_dir.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        ep = json.loads((parsed_dir / f.name).read_text(encoding="utf-8"))
        els = elements(ep)
        for i, p in enumerate(d.get("promos", [])):
            errs, warns, located = check_promo(p, els, ep["duration_s"])
            results.append({
                "episode": d["episode_number"], "source_file": f.name, "promo_index": i,
                "status": "error" if errs else ("warning" if warns else "ok"),
                "errors": errs, "warnings": warns,
            })
            if repair_dir:
                repairs = []
                for key in ("hook", "cliffhanger"):
                    status, el = located[key]
                    if el is not None and status in ("merged", "paraphrase"):
                        repairs.append({"field": key, "was": p[key]["quote"], "now": el["text"]})
                        p[key]["quote"] = el["text"]
                        p[key]["timestamp"] = el["start"]
                total = round(sum(s["end_s"] - s["start_s"] for s in p["segments"]), 2)
                if abs(total - p.get("total_length_s", total)) > 0.05:
                    repairs.append({"field": "total_length_s", "was": p.get("total_length_s"), "now": total})
                    p["total_length_s"] = total
                p["repairs"] = repairs
        if repair_dir:
            (repair_dir / f.name).write_text(json.dumps(d, indent=2), encoding="utf-8")
    return results


def validate_montages(path: Path, parsed_dir: Path):
    """Cross-episode promos: each segment names its own source_file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    cache = {}
    results = []
    for i, m in enumerate(data["montages"]):
        for s in m["segments"]:
            if s["source_file"] not in cache:
                ep = json.loads((parsed_dir / s["source_file"]).read_text(encoding="utf-8"))
                cache[s["source_file"]] = (ep, elements(ep))
        results.append({"montage": i, **check_montage(m, cache)})
    return results


def check_montage(m: dict, cache: dict) -> dict:
    """cache maps each segment's source_file to (episode, elements)."""
    errs, warns = [], []
    total = 0.0
    for s in m["segments"]:
        ep, _ = cache[s["source_file"]]
        if s["start_s"] >= s["end_s"] or s["end_s"] > ep["duration_s"] + 0.5:
            errs.append(f"bad segment {s['source_file']} {s['start']}-{s['end']}")
        total += s["end_s"] - s["start_s"]
    if not (MIN_LEN - LEN_TOL <= total <= MAX_LEN + LEN_TOL):
        errs.append(f"total length {total:.1f}s outside 20-60s")
    order = [(s["source_file"], s["start_s"]) for s in m["segments"]]
    if order != sorted(order):
        errs.append("segments are not in story order")
    for key, seg in (("hook", m["segments"][0]), ("cliffhanger", m["segments"][-1])):
        _, els = cache[seg["source_file"]]
        status, el = locate(m[key]["quote"], els, to_s(m[key]["timestamp"]))
        if status != "exact":
            (errs if status == "missing" else warns).append(f"{key} quote {status}")
        elif not (seg["start_s"] - TS_TOL_S <= el["start_s"] <= seg["end_s"] + TS_TOL_S):
            errs.append(f"{key} line at {el['start']} outside its segment")
        elif key == "hook" and el["start_s"] - seg["start_s"] > HOOK_WINDOW_S:
            errs.append("hook not within first 3s")
    return {"title": m.get("title"), "total_length_s": round(total, 2),
            "status": "error" if errs else ("warning" if warns else "ok"),
            "errors": errs, "warnings": warns}


def _print(results, label_key):
    counts = {k: sum(r["status"] == k for r in results) for k in ("ok", "warning", "error")}
    print(f"Validated {len(results)}: {counts['ok']} ok, {counts['warning']} warnings, {counts['error']} errors")
    for r in results:
        name = r.get(label_key) if label_key != "episode" else f"{r['episode']} #{r['promo_index']}"
        for msg in r["errors"]:
            print(f"  ERROR {name}: {msg}")
        for msg in r["warnings"]:
            print(f"  warn  {name}: {msg}")
    return counts


def main():
    args = sys.argv[1:]
    if args and args[0] == "--montages":
        path = Path(args[1])
        parsed_dir = Path(args[2] if len(args) > 2 else "data/parsed")
        results = validate_montages(path, parsed_dir)
        counts = _print(results, "title")
        sys.exit(1 if counts["error"] else 0)

    repair_dir = None
    if args and args[0] == "--repair":
        repair_dir = Path(args[1])
        args = args[2:]
    promos_dir = Path(args[0] if len(args) > 0 else "data/promos")
    parsed_dir = Path(args[1] if len(args) > 1 else "data/parsed")
    report = Path(args[2] if len(args) > 2 else "outputs/validation_report.json")

    results = validate(promos_dir, parsed_dir, repair_dir)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(results, indent=2), encoding="utf-8")
    counts = _print(results, "episode")
    print(f"Report -> {report}")
    sys.exit(1 if counts["error"] else 0)


if __name__ == "__main__":
    main()
