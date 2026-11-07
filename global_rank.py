"""
Side-by-side ordering pass: one call sees the shortlist together and decides
the "post these first" order with a reason per slot. Scores from separate
per-episode calls aren't on one scale; this pass is the calibration step.

Run after rank.py (which builds the shortlist), then run rank.py again to attach the order.
  python global_rank.py [shortlist_size]
Needs model credentials (see llm_client.py). Writes editor_order into outputs/global_calibration.json.
"""
import json
import os
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

import llm_client

RANKED = Path("outputs/ranked_promos.json")
CALIBRATION = Path("outputs/global_calibration.json")
MODEL = os.environ.get("PROMO_FINDER_RANK_MODEL") or os.environ.get("PROMO_FINDER_MODEL", "claude-opus-5-5")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Slot(Strict):
    key: str
    reason: str


class Order(Strict):
    slots: list[Slot]


def build_prompt(rows: list) -> str:
    listing = "\n".join(
        f"{r['key']} | {r['episode']} | {r['total_length_s']:.0f}s | hook {r['hook_strength']} cliff {r['cliffhanger_strength']} "
        f"standalone {r['standalone_score']} | spoiler {r['spoiler_risk']} platform {r['platform_risk']}\n"
        f"   hook: {r['hook_quote']}\n   cliffhanger: {r['cliffhanger_quote']}\n   why: {r['why_it_works']}"
        for r in rows
    )
    return (
        "You're deciding which promos a microdrama app posts first to recruit viewers who have never seen the show. "
        "Order the 10 best from this shortlist. Lead with clips that sell the show's premise and need no context; "
        "push spoilers and platform-risky clips later; avoid two clips of the same beat back to back. "
        "Give one sentence per slot saying why it sits there. Use the keys exactly as written.\n\n" + listing
    )


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    rows = json.loads(RANKED.read_text(encoding="utf-8"))["ranked_promos"][:n]
    order = llm_client.complete(Order, build_prompt(rows), MODEL)
    valid = {r["key"] for r in rows}
    slots = [s for s in order.slots if s.key in valid]

    calib = json.loads(CALIBRATION.read_text(encoding="utf-8")) if CALIBRATION.exists() else {"scores": {}}
    calib["editor_order"] = [{"key": s.key, "reason": s.reason} for s in slots]
    calib["ordered_by"] = MODEL
    CALIBRATION.write_text(json.dumps(calib, indent=2), encoding="utf-8")
    print(f"Ordered {len(slots)} slots -> {CALIBRATION}. Now re-run: python rank.py")


if __name__ == "__main__":
    main()
