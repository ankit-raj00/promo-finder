"""
Cross-episode promos ("join a few moments ... across several episodes").

A montage is a list of (episode file, start element, end element) segments,
plus a hook element (in the first segment) and a cliffhanger element (in the
last). As with single-episode promos, the model only names element IDs; code
resolves them to timestamps and verbatim quotes, then validate.py checks them.

  python -m promo_finder.rank.montages build     # build montages from MONTAGE_SPECS, if you've added any -> outputs/cross_episode_promos.json
  python -m promo_finder.rank.montages propose   # ask the model to propose montages from the ranked pool (needs an API key)
"""
import json
import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from promo_finder.llm import client as llm_client
from promo_finder.ingest.transcript import indexed_elements, label, fmt_ts
from promo_finder.judge.validate import validate_montages

PARSED = Path("data/parsed")
OUT = Path("outputs/cross_episode_promos.json")

# Fill in to build montages by hand; `propose` asks the model instead.
MONTAGE_SPECS = []

_cache: dict[str, tuple[str, dict, dict]] = {}


def load(ep_prefix: str):
    if ep_prefix not in _cache:
        f = next(PARSED.glob(f"episode-{ep_prefix}-*.json"))
        ep = json.loads(f.read_text(encoding="utf-8"))
        _cache[ep_prefix] = (f.name, ep, {e["id"]: e for e in indexed_elements(ep)})
    return _cache[ep_prefix]


def build_montage(spec: dict, lookup=None) -> dict:
    lookup = lookup or load
    segments = []
    for ep_prefix, a, b in spec["segments"]:
        fname, ep, els = lookup(ep_prefix)
        segments.append({"episode": ep["episode_number"], "source_file": fname,
                         "start": els[a]["start"], "end": els[b]["end"],
                         "start_s": els[a]["start_s"], "end_s": els[b]["end_s"]})

    def quote(ref):
        _, ep, els = lookup(ref[0])
        e = els[ref[1]]
        return {"episode": ep["episode_number"], "quote": e["text"], "timestamp": e["start"], "element": e["id"]}

    return {
        "title": spec["title"], "segments": segments,
        "hook": quote(spec["hook"]), "cliffhanger": quote(spec["cliffhanger"]),
        "total_length_s": round(sum(s["end_s"] - s["start_s"] for s in segments), 2),
        "why_it_works": spec["why_it_works"], "spoiler_risk": spec["spoiler_risk"], "note": spec.get("note", ""),
    }


# --- API path: the model proposes specs; code builds and validates them ---------------------------

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SegRef(Strict):
    episode_prefix: str  # zero-padded, e.g. "003"
    start_element: str
    end_element: str


class Proposal(Strict):
    title: str
    segments: list[SegRef]
    hook_element: str
    cliffhanger_element: str
    why_it_works: str
    spoiler_risk: Literal["none", "low", "high"]
    note: str


class Proposals(Strict):
    montages: list[Proposal]


DEFAULT_MODEL = os.environ.get("PROMO_FINDER_MONTAGE_MODEL") or os.environ.get("PROMO_FINDER_MODEL", "claude-opus-5-5")


def propose(top_n: int = 15, model: str = DEFAULT_MODEL):
    return propose_measured(top_n, model)[0]


def propose_measured(top_n: int, model: str):
    ranked = json.loads(Path("outputs/ranked_promos.json").read_text(encoding="utf-8"))["ranked_promos"][:top_n]
    return propose_from([(r, r["source_file"].split("-")[1]) for r in ranked], load, model)


def propose_from(ranked: list, lookup, model: str):
    """ranked is a list of (promo row, episode prefix); lookup finds an episode by prefix."""
    blocks = []
    for r, prefix in ranked:
        _, ep, els = lookup(prefix)
        lo = r["segments"][0]["start_s"] - 10
        hi = r["segments"][-1]["end_s"] + 10
        lines = [f"{e['id']} [{fmt_ts(e['start_s'])}-{fmt_ts(e['end_s'])}] {label(e)}: {e['text']}"
                 for e in els.values() if lo <= e["start_s"] <= hi]
        blocks.append(f"### episode_prefix {prefix} ({ep['episode_number']}): {r['why_it_works']}\n" + "\n".join(lines))

    prompt = (
        "Propose 3 cross-episode promo montages for social media, joining moments from 2-5 of the excerpts below. "
        "Each montage: 20-60s total, segments in story order, hook_element (in the first segment, within its first 3s) "
        "and cliffhanger_element (in the last segment, at the cut). Refer to elements only by ID and episode_prefix. "
        "Favor montages that sell the show's premise to someone who has never seen it; mark spoiler_risk honestly.\n\n"
        + "\n\n".join(blocks)
    )
    proposals, measured = llm_client.complete_measured(Proposals, prompt, model)
    specs = []
    for p in proposals.montages:
        segs = [(s.episode_prefix, s.start_element, s.end_element) for s in p.segments]
        specs.append({"title": p.title, "segments": segs,
                      "hook": (segs[0][0], p.hook_element), "cliffhanger": (segs[-1][0], p.cliffhanger_element),
                      "why_it_works": p.why_it_works, "spoiler_risk": p.spoiler_risk, "note": p.note})
    return specs, measured


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "build"
    specs = propose() if mode == "propose" else MONTAGE_SPECS
    montages = []
    for s in specs:
        try:
            montages.append(build_montage(s))
        except (KeyError, StopIteration) as e:  # the model named a line or episode that doesn't exist
            print(f"Dropped montage {s['title']!r}: unknown reference {e}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"montages": montages}, indent=2), encoding="utf-8")
    # Only validated montages stay in the output.
    results = validate_montages(OUT, PARSED)
    for r in results:
        if r["status"] == "error":
            print(f"Dropped montage {r['title']!r}: {'; '.join(r['errors'])}")
    montages = [m for m, r in zip(montages, results) if r["status"] != "error"]
    OUT.write_text(json.dumps({"montages": montages}, indent=2), encoding="utf-8")
    for m in montages:
        path = " + ".join(f"{s['episode'].replace('Episode ', 'E')} {s['start']}-{s['end']}" for s in m["segments"])
        print(f"{m['title']} ({m['total_length_s']}s): {path}")
    print(f"-> {OUT}. Validate with: python -m promo_finder.judge.validate --montages {OUT}")


if __name__ == "__main__":
    main()
