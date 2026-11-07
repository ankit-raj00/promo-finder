"""
Parses Kuku microdrama screenplay .txt files into structured JSON.

Input format (one file per episode):
    EPISODE NUMBER: Episode N
    EPISODE LOGLINE: ...
    CONTINUITY NOTES: ...
    --- SCRIPT START ---
    SCENE 01
    Scene heading: INT. ... - DAY
    Scene start: MM:SS.ss
    Scene end: MM:SS.ss
    Actions and dialogue:
    **CAMERA:** / **ACTION:** / **SOUND FX:** / **SPEAKER (mood):**
    Start: (MM:SS.ss) | End: (MM:SS.ss)
    <text>
    ...
    --- SCRIPT END ---

Output: one JSON per episode with scenes -> elements, each element timestamped
in both "MM:SS.ss" form and absolute seconds (float) from episode start, so
downstream code can window across scene/episode boundaries without re-parsing
timestamp strings.
"""
import json
import re
import sys
from pathlib import Path

TIME_RE = re.compile(r"(\d+):(\d+(?:\.\d+)?)")
HEADER_RE = re.compile(r"^\*\*(.+?):\*\*$")
SCENE_START_RE = re.compile(r"^SCENE\s+\d+", re.IGNORECASE)


def time_to_seconds(t: str) -> float:
    m = TIME_RE.match(t.strip())
    if not m:
        raise ValueError(f"Bad timestamp: {t!r}")
    minutes, seconds = m.groups()
    return int(minutes) * 60 + float(seconds)


def classify_element(label: str):
    """Return (kind, speaker) for a '**LABEL:**' header line."""
    raw = label.strip()
    if raw.upper() == "CAMERA":
        return "camera", None
    if raw.upper() == "ACTION":
        return "action", None
    if raw.upper() in ("SOUND FX", "SFX"):
        return "sound_fx", None
    # Dialogue: "SPEAKER" or "SPEAKER (mood, tags)" or "SPEAKER (O.S.)" etc.
    m = re.match(r"^(.*?)(?:\s*\((.*)\))?$", raw)
    speaker = m.group(1).strip() if m else raw
    tags = m.group(2).strip() if m and m.group(2) else None
    return "dialogue", {"speaker": speaker, "tags": tags}


def parse_episode(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    episode = {
        "source_file": path.name,
        "episode_number": None,
        "logline": None,
        "continuity_notes": None,
        "scenes": [],
    }

    i = 0
    n = len(lines)

    def peek():
        return lines[i] if i < n else ""

    # --- header block ---
    while i < n and not lines[i].startswith("--- SCRIPT START ---"):
        line = lines[i]
        if line.startswith("EPISODE NUMBER:"):
            episode["episode_number"] = line.split(":", 1)[1].strip()
        elif line.startswith("EPISODE LOGLINE:"):
            episode["logline"] = line.split(":", 1)[1].strip()
        elif line.startswith("CONTINUITY NOTES:"):
            episode["continuity_notes"] = line.split(":", 1)[1].strip()
        i += 1
    i += 1  # skip "--- SCRIPT START ---"

    current_scene = None
    while i < n:
        line = lines[i].strip()

        if line.startswith("--- SCRIPT END ---"):
            break

        if SCENE_START_RE.match(line):
            if current_scene is not None:
                episode["scenes"].append(current_scene)
            current_scene = {
                "scene_id": line,
                "heading": None,
                "start": None,
                "end": None,
                "start_s": None,
                "end_s": None,
                "elements": [],
            }
            i += 1
            continue

        if current_scene is not None:
            low = line.lower()
            if low.startswith("scene heading:"):
                current_scene["heading"] = line.split(":", 1)[1].strip()
            elif low.startswith("scene start:"):
                v = line.split(":", 1)[1].strip()
                current_scene["start"] = v
                current_scene["start_s"] = time_to_seconds(v)
            elif low.startswith("scene end:"):
                v = line.split(":", 1)[1].strip()
                current_scene["end"] = v
                current_scene["end_s"] = time_to_seconds(v)
            else:
                m = HEADER_RE.match(line)
                if m:
                    kind, meta = classify_element(m.group(1))
                    # next non-blank line should be Start/End
                    j = i + 1
                    while j < n and lines[j].strip() == "":
                        j += 1
                    start_s = end_s = None
                    start_raw = end_raw = None
                    if j < n and lines[j].strip().startswith("Start:"):
                        se_line = lines[j].strip()
                        times = TIME_RE.findall(se_line)
                        if len(times) >= 2:
                            start_raw = f"{times[0][0]}:{times[0][1]}"
                            end_raw = f"{times[1][0]}:{times[1][1]}"
                            start_s = time_to_seconds(start_raw)
                            end_s = time_to_seconds(end_raw)
                        j += 1
                    # text is the next non-blank line(s) until blank line or next header
                    text_lines = []
                    while j < n and lines[j].strip() != "" and not HEADER_RE.match(lines[j].strip()) and not SCENE_START_RE.match(lines[j].strip()) and not lines[j].strip().startswith("--- SCRIPT END ---"):
                        text_lines.append(lines[j].strip())
                        j += 1
                    element = {
                        "kind": kind,
                        "start": start_raw,
                        "end": end_raw,
                        "start_s": start_s,
                        "end_s": end_s,
                        "text": " ".join(text_lines).strip('"'),
                    }
                    if kind == "dialogue":
                        element["speaker"] = meta["speaker"]
                        element["tags"] = meta["tags"]
                    current_scene["elements"].append(element)
                    i = j
                    continue
        i += 1

    if current_scene is not None:
        episode["scenes"].append(current_scene)

    # episode-level duration
    if episode["scenes"]:
        episode["duration_s"] = episode["scenes"][-1]["end_s"]
    else:
        episode["duration_s"] = None

    return episode


def main():
    in_dir = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(in_dir.glob("*.txt"))
    errors = []
    for f in files:
        try:
            parsed = parse_episode(f)
            out_name = f.stem + ".json"
            (out_dir / out_name).write_text(json.dumps(parsed, indent=2), encoding="utf-8")
        except Exception as e:
            errors.append((f.name, str(e)))

    print(f"Parsed {len(files) - len(errors)}/{len(files)} episodes into {out_dir}")
    if errors:
        print("Errors:")
        for name, err in errors:
            print(f"  {name}: {err}")


if __name__ == "__main__":
    main()
