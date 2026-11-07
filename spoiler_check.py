"""
Separate spoiler check: given every episode logline and a clip, does the clip give away a twist?
The judge's own spoiler ratings missed clear late-series payoffs.

  python spoiler_check.py <promos_dir>     # adds a "spoiler_check" field to each promo, in place
Set PROMO_FINDER_SPOILER_MODEL to choose the model. Promos already checked are skipped.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

import llm_client
from critic import clip_text
from transcript import series_context

MODEL = os.environ.get("PROMO_FINDER_SPOILER_MODEL") or os.environ.get("PROMO_FINDER_MODEL", "claude-opus-5-5")


class SpoilerCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spoiler_risk: Literal["none", "low", "high"]
    twist_revealed: str


RULES = """You decide whether a promo clip spoils a serial drama for viewers who haven't started it.
Below are the show's episode loglines, the clip's episode number and the clip's script.

high: the clip gives away how a major storyline ends: a villain is exposed, arrested or punished, the couple's final victory, or a late payoff the story builds toward.
low: the clip reveals a mid-story twist, such as someone surviving or a hidden plot, but not how it ends.
none: the clip shows the premise or a setup and reveals nothing the show withholds. The show's title and first episodes define the premise, which is never a spoiler.

twist_revealed: one short sentence naming what is given away, or "none".

SHOW:
"""


def check_clip(episode: dict, segments: list, context: str, model: str | None = None) -> dict:
    prompt = f"{RULES}{context}\n\nCLIP FROM {episode['episode_number']}:\n{clip_text([(episode, segments)])}"
    parsed, measured = llm_client.complete_measured(SpoilerCheck, prompt, model or MODEL)
    return {**parsed.model_dump(), **measured}


def main():
    promos_dir, parsed_dir = Path(sys.argv[1]), Path("data/parsed")
    context = series_context(parsed_dir)
    start = time.time()
    failures = []

    def run(f: Path):
        d = json.loads(f.read_text(encoding="utf-8"))
        todo = [p for p in d.get("promos", []) if "spoiler_check" not in p]
        if not todo:
            return
        episode = json.loads((parsed_dir / f.name).read_text(encoding="utf-8"))
        try:
            for p in todo:
                p["spoiler_check"] = check_clip(episode, p["segments"], context)
        except Exception as e:  # noqa: BLE001 - one episode must not lose the rest; a re-run retries it
            failures.append(f.name)
            print(f"  FAILED {f.name}: {type(e).__name__}: {str(e)[:150]}", flush=True)
            return
        f.write_text(json.dumps(d, indent=2), encoding="utf-8")

    with ThreadPoolExecutor(max_workers=int(os.environ.get("PROMO_FINDER_CONCURRENCY", "4"))) as pool:
        list(pool.map(run, sorted(promos_dir.glob("*.json"))))
    if failures:
        sys.exit(f"{len(failures)} episodes not checked. Re-run the same command to retry them.")
    print(f"Spoiler check done in {time.time() - start:.0f}s -> {promos_dir}")


if __name__ == "__main__":
    main()
