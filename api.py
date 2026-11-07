"""
Promo Finder web app: a small JSON API around the pipeline, plus the built React site.

Run: python -m uvicorn api:app --port 8000     then open http://localhost:8000

Judgment on a new episode uses a live model when a key is available, either on the
server or sent with the request; otherwise a keyword-only fallback, clearly labelled.
Real results in outputs/ quote confidential scripts, so a public copy sets
PROMO_FINDER_DEMO_ONLY=1 and only ever serves the sample show.
"""
import json
import os
import tempfile
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import llm_client
from editor_sheet import to_csv
from heuristics import generate_windows
from parser import parse_episode
from rank import score as rank_score
from validate import check_promo, elements as validation_elements

BASE = Path(__file__).parent
REAL_OUT = BASE / "outputs"
DEMO_OUT = BASE / "demo" / "outputs"
SAMPLES = BASE / "samples" / "demo_show"
SITE = BASE / "frontend" / "dist"

MAX_SCRIPT_CHARS = 1_000_000
MAX_EPISODES_ON_SHARED_KEY = 5  # per run, enforced by the site
SHARED_KEY_EPISODES_PER_HOUR = 30  # per visitor, enforced here, so one person can't use up a shared key
_recent = defaultdict(deque)
_recent_lock = threading.Lock()

app = FastAPI(title="Promo Finder", docs_url=None, redoc_url=None, openapi_url=None)


class Script(BaseModel):
    name: str = Field(max_length=200)
    text: str = Field(max_length=MAX_SCRIPT_CHARS)


class JudgeRequest(Script):
    context: str = Field(default="", max_length=200_000)  # one logline per episode, for spoiler checks
    base_url: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    critic_model: str = Field(default="", max_length=200)


class SheetRequest(BaseModel):
    rows: list[dict] = Field(max_length=1000)


def has_real_results() -> bool:
    return (REAL_OUT / "ranked_promos.json").exists() and not os.environ.get("PROMO_FINDER_DEMO_ONLY")


def has_server_key() -> bool:
    # Only an explicitly supplied key turns on live mode; ambient tokens from other tools don't.
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("PROMO_FINDER_API_KEY"))


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def read_text(path: Path):
    return path.read_text(encoding="utf-8") if path.exists() else None


def parse_script(name: str, text: str) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        tmp.write(text.encode("utf-8"))
        path = Path(tmp.name)
    try:
        episode = parse_episode(path)
    except Exception as e:  # noqa: BLE001 - a bad file should get a clear message, not a server error
        raise HTTPException(422, f"{name}: this file couldn't be read as a screenplay.") from e
    finally:
        path.unlink(missing_ok=True)
    if not episode["scenes"]:
        raise HTTPException(422, f"{name}: no scenes found. Check that it matches the format of the sample episodes.")
    episode["source_file"] = name
    episode["episode_number"] = episode["episode_number"] or Path(name).stem
    return episode


def fmt(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{seconds % 60:05.2f}"


def norm(p: dict) -> dict:
    """Flattens a promo from the judge into the same shape as a ranked row."""
    return {
        **p,
        "hook_quote": p["hook"]["quote"], "hook_timestamp": p["hook"]["timestamp"], "hook_why": p["hook"].get("why", ""),
        "cliffhanger_quote": p["cliffhanger"]["quote"], "cliffhanger_timestamp": p["cliffhanger"]["timestamp"],
        "cliffhanger_why": p["cliffhanger"].get("why", ""),
        "standalone_score": p["standalone"]["score"],
    }


def keyword_only(episode: dict) -> dict:
    """Zero-cost fallback: the keyword filter's top windows, clearly marked as unverified."""
    promos = []
    for c in generate_windows(episode)[:3]:
        if c.get("fallback"):
            continue
        start = fmt(c["start_s"])
        promos.append({
            "segments": [{"start": start, "end": fmt(c["end_s"]), "start_s": c["start_s"], "end_s": c["end_s"]}],
            "hook": {"quote": c["hook_line"], "timestamp": start, "why": "Keyword match only, not judged."},
            "cliffhanger": {"quote": c["cliff_line"], "timestamp": "", "why": "Keyword match only, not judged."},
            "standalone": {"score": 3, "reasoning": "Not assessed."},
            "why_it_works": "Unverified keyword pick. Add an API key for real judgment.",
            "total_length_s": c["length_s"], "confidence": "low",
        })
    return {"promos": promos, "no_good_promo_reason": None if promos else "The keyword filter found nothing."}


def judge_live(episode: dict, context: str, model: str | None, critic_model: str | None) -> dict:
    """Judge, then the independent critic and spoiler check. Same steps as the full pipeline."""
    from critic import score_clip
    from llm_scorer import score_episode
    from spoiler_check import check_clip

    result = score_episode(episode, {"candidates": generate_windows(episode)}, context, model=model, fallback_model=model)
    for p in result["promos"]:
        critic = score_clip([(episode, p["segments"])], model=critic_model)
        if "hook" in critic:  # the critic's view replaces the judge's ratings of its own work
            p.update(hook_strength=critic["hook"], cliffhanger_strength=critic["cliffhanger"])
            p["standalone"]["score"] = critic["standalone"]
        check = check_clip(episode, p["segments"], context, model=critic_model)
        if "spoiler_risk" in check:
            p["spoiler_risk"] = check["spoiler_risk"]
    return result


def within_shared_limit(visitor: str, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    with _recent_lock:
        seen = _recent[visitor]
        while seen and now - seen[0] > 3600:
            seen.popleft()
        if len(seen) >= SHARED_KEY_EPISODES_PER_HOUR:
            return False
        seen.append(now)
        return True


def visitor_of(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[-1].strip() or (request.client.host if request.client else "unknown")


@app.get("/api/config")
def config():
    return {
        "server_key": has_server_key(),
        "server_model": os.environ.get("PROMO_FINDER_MODEL", "") if has_server_key() else "",
        "default_model": os.environ.get("PROMO_FINDER_MODEL", ""),
        "has_real_results": has_real_results(),
        "max_episodes_shared": MAX_EPISODES_ON_SHARED_KEY,
    }


@app.get("/api/results")
def results():
    out = REAL_OUT if has_real_results() else DEMO_OUT
    ranked = read_json(out / "ranked_promos.json") or {}
    return {
        "is_sample": out == DEMO_OUT,
        "post_first": ranked.get("post_first", []),
        "ranked": ranked.get("ranked_promos", []),
        "empty_episodes": ranked.get("episodes_with_no_good_promo", []),
        "montages": (read_json(out / "cross_episode_promos.json") or {}).get("montages", []),
        "quality_report": read_text(out / "quality_report.md"),
        "review": read_text(out / "reviewed_top10.md"),
    }


@app.get("/api/samples")
def samples():
    saved = read_json(DEMO_OUT / "ranked_promos.json") or {"ranked_promos": []}
    out = []
    for f in sorted(SAMPLES.glob("*.txt")):
        rows = [r for r in saved["ranked_promos"] if Path(r["source_file"]).stem == f.stem]
        out.append({"name": f.name, "text": f.read_text(encoding="utf-8"), "saved": rows})
    return out


@app.post("/api/parse")
def parse(script: Script):
    e = parse_script(script.name, script.text)
    return {
        "name": script.name, "episode": e["episode_number"], "logline": e.get("logline") or "",
        "duration_s": e["duration_s"], "scenes": len(e["scenes"]),
        "lines": sum(len(s["elements"]) for s in e["scenes"]),
    }


@app.post("/api/judge")
def judge(req: JudgeRequest, request: Request):
    episode = parse_script(req.name, req.text)
    own_key = bool(req.api_key)
    live = own_key or has_server_key()
    if live and not own_key and not within_shared_limit(visitor_of(request)):
        raise HTTPException(429, "You've reached this site's hourly limit for the free AI key. "
                                 "Try again later, or add your own key under Use your own model.")
    try:
        if own_key:
            llm_client.use_session(req.base_url, req.api_key)
            result = judge_live(episode, req.context, req.model or None, req.critic_model or req.model or None)
        elif live:
            result = judge_live(episode, req.context, None, None)
        else:
            result = keyword_only(episode)
    except Exception as e:  # noqa: BLE001 - any provider failure becomes a plain message for this episode
        return {"episode": episode["episode_number"], "promos": [], "error": llm_client.friendly_error(e), "live": live}

    els = validation_elements(episode)
    rows = []
    for p in result["promos"]:
        row = {**norm(p), "episode": episode["episode_number"], "errors": check_promo(p, els, episode["duration_s"])[0]}
        if live:
            row["rank_score"] = rank_score(row)
        rows.append(row)
    return {"episode": episode["episode_number"], "promos": rows, "empty_reason": result.get("no_good_promo_reason"), "live": live}


@app.post("/api/sheet")
def sheet(req: SheetRequest):
    return Response(to_csv(req.rows), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="promo_editor_sheet.csv"'})


@app.get("/api/{rest:path}")
def unknown_api(rest: str):
    raise HTTPException(404, "Not found")


if SITE.exists():
    app.mount("/", StaticFiles(directory=SITE, html=True), name="site")
else:
    @app.get("/")
    def no_site():
        return PlainTextResponse("The site isn't built yet. Run: cd frontend && npm install && npm run build")
