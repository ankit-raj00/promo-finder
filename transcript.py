"""
Compact, ID-addressed transcript of a parsed episode.

The model sees lines like
    e12 [00:17.00-00:19.00] JULIAN (O.S.) (Cold): Put it down.
and answers with element IDs ("e12"), never with timestamps or quotes. Code
then resolves IDs back to exact timestamps and verbatim text, so the model
cannot invent a timestamp or paraphrase a line. It's also about 45% fewer
tokens than sending the raw parsed JSON.
"""
import json
from pathlib import Path


def fmt_ts(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{seconds % 60:05.2f}"


def indexed_elements(episode: dict) -> list[dict]:
    flat = []
    for scene in episode["scenes"]:
        for el in scene["elements"]:
            if el["start_s"] is None or el["end_s"] is None:
                continue
            flat.append({**el, "scene_id": scene["scene_id"]})
    flat.sort(key=lambda e: e["start_s"])  # stable: ties keep script order
    for i, el in enumerate(flat):
        el["id"] = f"e{i}"
    return flat


def label(el: dict) -> str:
    if el["kind"] == "dialogue":
        return f"{el['speaker']} ({el['tags']})" if el.get("tags") else el["speaker"]
    return {"camera": "CAMERA", "action": "ACTION", "sound_fx": "SFX"}.get(el["kind"], el["kind"].upper())


def render(episode: dict, els: list[dict] | None = None) -> str:
    els = els if els is not None else indexed_elements(episode)
    head = [
        f"{episode['episode_number']} | duration {fmt_ts(episode['duration_s'])}",
        f"Logline: {episode.get('logline') or ''}",
        "",
    ]
    body = [f"{e['id']} [{fmt_ts(e['start_s'])}-{fmt_ts(e['end_s'])}] {label(e)}: {e['text']}" for e in els]
    return "\n".join(head + body)


def series_context(parsed_dir: Path) -> str:
    """One line per episode logline -- lets the judge reason about spoilers and arc position."""
    lines = []
    for f in sorted(Path(parsed_dir).glob("*.json")):
        ep = json.loads(f.read_text(encoding="utf-8"))
        lines.append(f"{ep['episode_number']}: {ep.get('logline') or ''}")
    return "\n".join(lines)
