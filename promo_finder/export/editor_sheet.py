"""
Turns ranked promos into a CSV an editor can work from in Excel or Google Sheets.

The last two columns are blank on purpose. An editor fills them in, and rejected or
retimed picks become test cases for the next prompt or model change.
"""
import csv
import io

COLUMNS = ["post_first_slot", "rank", "score", "episode", "segments", "length_s",
           "hook_at", "hook", "cliffhanger_at", "cliffhanger", "why_it_works",
           "hook_rating", "cliffhanger_rating", "standalone_rating",
           "spoiler_risk", "platform_risk", "editor_decision", "editor_notes"]


def _safe(value):
    """Stops a script line that starts with = + - @ from running as a spreadsheet formula."""
    return f"'{value}" if isinstance(value, str) and value[:1] in ("=", "+", "-", "@") else value


def to_csv(rows: list[dict], slots: dict | None = None) -> bytes:
    """rows use the ranked-promo shape. slots maps a promo's key to its post-first slot."""
    slots = slots or {}
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS)
    writer.writeheader()
    for r in rows:
        writer.writerow({k: _safe(v) for k, v in {
            "post_first_slot": r.get("slot") or slots.get(r.get("key"), ""),
            "rank": r.get("rank", ""),
            "score": r.get("rank_score", ""),
            "episode": r.get("episode", ""),
            "segments": " + ".join(f"{s['start']}-{s['end']}" for s in r["segments"]),
            "length_s": round(r["total_length_s"], 2),
            "hook_at": r.get("hook_timestamp", ""),
            "hook": r.get("hook_quote", ""),
            "cliffhanger_at": r.get("cliffhanger_timestamp", ""),
            "cliffhanger": r.get("cliffhanger_quote", ""),
            "why_it_works": r.get("why_it_works", ""),
            "hook_rating": r.get("hook_strength", ""),
            "cliffhanger_rating": r.get("cliffhanger_strength", ""),
            "standalone_rating": r.get("standalone_score", ""),
            "spoiler_risk": r.get("spoiler_risk", ""),
            "platform_risk": r.get("platform_risk", ""),
            "editor_decision": "",
            "editor_notes": "",
        }.items()})
    return out.getvalue().encode("utf-8-sig")  # the BOM makes Excel read it as UTF-8
