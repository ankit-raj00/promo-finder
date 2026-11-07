"""
Kuku Promo Finder - Streamlit prototype.

Run: streamlit run app.py

Tabs:
  1. Post first   - the editorial top 10 with a reason per slot, plus cross-episode montages
  2. All promos   - every validated promo, by score or by episode
  3. New episodes - upload one or more screenplay .txt files and get one ranked list
  4. Quality      - validation, agreement and known failure modes

Judgment on a new episode uses a live model when a key is set, in .env or the
sidebar; otherwise a keyword-only fallback, clearly labelled. A fresh clone shows
the demo results for the sample show in samples/demo_show.
"""
import hmac
import json
import os
import tempfile
from pathlib import Path

import streamlit as st

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
# On a hosted copy, real results quote confidential scripts, so this password guards them.
VIEW_PASSWORD = os.environ.get("PROMO_FINDER_VIEW_PASSWORD", "")
MAX_EPISODES_ON_SHARED_KEY = 5  # limits what visitors can spend if a hosted copy has its own key

st.set_page_config(page_title="Kuku Promo Finder", layout="wide")
st.title("Kuku Promo Finder")
st.caption("Finds 20-60s promo clips in a microdrama screenplay: a hook in the first 3 seconds, "
           "a cliffhanger at the cut, and a clip that makes sense to a stranger.")

has_real = (REAL_OUT / "ranked_promos.json").exists()
unlocked = has_real and not VIEW_PASSWORD
wrong_password = False
if has_real and VIEW_PASSWORD:
    with st.sidebar:
        st.header("Full 50-episode results")
        entered = st.text_input("Password", value="", type="password",
                                help="Results from Kuku's input pack. The password is on the submission form.")
    unlocked = hmac.compare_digest(entered.encode(), VIEW_PASSWORD.encode())
    wrong_password = bool(entered) and not unlocked

OUT = REAL_OUT if unlocked else DEMO_OUT
IS_DEMO = not unlocked
RANKED = OUT / "ranked_promos.json"
MONTAGES = OUT / "cross_episode_promos.json"
QUALITY = OUT / "quality_report.md"
REVIEW = OUT / "reviewed_top10.md"

if wrong_password:
    st.error("Wrong password. Showing the sample show.")
if IS_DEMO and has_real:
    st.warning("Showing an original 3-episode sample show. Enter the password in the sidebar to see "
               "the 78 promos from Kuku's 50 episodes.")
elif IS_DEMO:
    st.warning("Showing results for an original 3-episode sample show. Kuku's scripts aren't in this repo, "
               "so its results are shared separately.")

with st.sidebar:
    st.header("Model settings")
    st.caption("Optional. Any OpenAI-compatible endpoint, or leave Base URL empty to use Anthropic's API with a "
               "Claude model ID. Used only for this session and never saved.")
    side_url = st.text_input("Base URL", value=os.environ.get("PROMO_FINDER_BASE_URL", ""),
                             placeholder="https://your-endpoint/v1")
    side_key = st.text_input("API key", value="", type="password")
    side_model = st.text_input("Judge model", value=os.environ.get("PROMO_FINDER_MODEL", "agy/gemini-3.8-flash-low"))
    side_critic = st.text_input("Critic model", value=os.environ.get("PROMO_FINDER_CRITIC_MODEL", "agy/gemini-3.8-flash-low"))


def has_live_key() -> bool:
    # Only an explicitly supplied key turns on live mode; ambient tokens from other tools don't.
    return bool(side_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("PROMO_FINDER_API_KEY"))


def norm(p: dict) -> dict:
    """Ranked rows and raw promos use slightly different shapes; render both."""
    if "hook_quote" in p:
        return p
    return {
        **p,
        "hook_quote": p["hook"]["quote"], "hook_timestamp": p["hook"]["timestamp"], "hook_why": p["hook"].get("why", ""),
        "cliffhanger_quote": p["cliffhanger"]["quote"], "cliffhanger_timestamp": p["cliffhanger"]["timestamp"],
        "cliffhanger_why": p["cliffhanger"].get("why", ""),
        "standalone_score": p["standalone"]["score"],
    }


def badge(level: str, label: str) -> str:
    color = {"none": "green", "low": "orange", "high": "red"}.get(level, "gray")
    return f":{color}[{label}: {level}]"


def render(p: dict, heading: str = ""):
    p = norm(p)
    times = " + ".join(f"{s['start']}-{s['end']}" for s in p["segments"])
    with st.container(border=True):
        title = f"{heading}{p.get('episode', '')}".strip()
        st.markdown(f"**{title}** | `{times}` | {p['total_length_s']:.1f}s")
        scores = []
        for k, name in (("hook_strength", "hook"), ("cliffhanger_strength", "cliffhanger"), ("standalone_score", "standalone")):
            if k in p:
                scores.append(f"{name} {p[k]}/5")
        risks = [badge(p[k], k.split("_")[0]) for k in ("spoiler_risk", "platform_risk") if k in p]
        st.markdown("  ".join(scores + risks))
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Hook** `{p['hook_timestamp']}`")
            st.write(f"“{p['hook_quote']}”")
            if p.get("hook_why"):
                st.caption(p["hook_why"])
        with c2:
            st.markdown(f"**Cliffhanger** `{p['cliffhanger_timestamp']}`")
            st.write(f"“{p['cliffhanger_quote']}”")
            if p.get("cliffhanger_why"):
                st.caption(p["cliffhanger_why"])
        st.markdown(f"**Why it works:** {p['why_it_works']}")
        if p.get("risk_note"):
            st.caption(f"Risk note: {p['risk_note']}")
        if p.get("reason"):
            st.info(f"Why this slot: {p['reason']}")


def heuristic_only(episode: dict) -> dict:
    """Zero-cost fallback: the keyword pre-filter's top windows, clearly marked as unverified."""
    promos = []
    for c in generate_windows(episode)[:3]:
        if c.get("fallback"):
            continue
        promos.append({
            "segments": [{"start": f"{int(c['start_s'] // 60):02d}:{c['start_s'] % 60:05.2f}",
                          "end": f"{int(c['end_s'] // 60):02d}:{c['end_s'] % 60:05.2f}",
                          "start_s": c["start_s"], "end_s": c["end_s"]}],
            "hook": {"quote": c["hook_line"], "timestamp": f"{int(c['start_s'] // 60):02d}:{c['start_s'] % 60:05.2f}",
                     "why": "Keyword match only, not judged."},
            "cliffhanger": {"quote": c["cliff_line"], "timestamp": "", "why": "Keyword match only, not judged."},
            "standalone": {"score": 3, "reasoning": "Not assessed."},
            "why_it_works": "Unverified keyword pick. Set an API key for real judgment.",
            "total_length_s": c["length_s"], "confidence": "low",
        })
    return {"promos": promos, "no_good_promo_reason": None if promos else "Keyword filter found nothing."}


def judge_live(episode: dict, context: str) -> dict:
    """Judge, then the independent critic and spoiler check, then the same score the full pipeline uses."""
    from critic import score_clip
    from llm_scorer import score_episode
    from spoiler_check import check_clip

    result = score_episode(episode, {"candidates": generate_windows(episode)}, context, model=side_model, fallback_model=side_model)
    for p in result["promos"]:
        critic = score_clip([(episode, p["segments"])], model=side_critic)
        if "hook" in critic:  # the critic's view replaces the judge's ratings of its own work
            p.update(hook_strength=critic["hook"], cliffhanger_strength=critic["cliffhanger"])
            p["standalone"]["score"] = critic["standalone"]
        check = check_clip(episode, p["segments"], context, model=side_critic)
        if "spoiler_risk" in check:
            p["spoiler_risk"] = check["spoiler_risk"]
    return result


def run_episodes(episodes: list[dict]) -> dict:
    """Runs every episode and returns one ranked list. One failed episode doesn't stop the rest."""
    live = has_live_key()
    if live and side_key:
        llm_client.use_session(side_url, side_key)
    context = "\n".join(f"{e['episode_number']}: {e.get('logline') or ''}" for e in episodes)
    rows, empty, failed = [], [], []
    bar = st.progress(0.0, text="Starting...")
    for i, episode in enumerate(episodes):
        bar.progress(i / len(episodes), text=f"Judging {episode['episode_number']} ({i + 1} of {len(episodes)})")
        try:
            result = judge_live(episode, context) if live else heuristic_only(episode)
        except Exception as e:  # noqa: BLE001 - show any API failure and keep going
            failed.append(f"{episode['episode_number']}: {llm_client.friendly_error(e)}")
            continue
        if not result["promos"]:
            empty.append({"episode": episode["episode_number"], "reason": result.get("no_good_promo_reason")})
        els = validation_elements(episode)
        for p in result["promos"]:
            row = {**norm(p), "episode": episode["episode_number"], "errors": check_promo(p, els, episode["duration_s"])[0]}
            row["key"] = f"{row['episode']}#{len(rows)}"
            if live:
                row["rank_score"] = rank_score(row)
            rows.append(row)
    bar.empty()
    if live:
        rows.sort(key=lambda r: -r["rank_score"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"rows": rows, "empty": empty, "failed": failed, "mode": "live model" if live else "keyword-only fallback"}


def saved_sample_run(names: list[str]) -> dict:
    """Saved results from a real pipeline run on the sample show, so nobody needs a key to see real output."""
    stems = {Path(n).stem for n in names}
    saved = json.loads((DEMO_OUT / "ranked_promos.json").read_text(encoding="utf-8"))
    rows = [dict(r) for r in saved["ranked_promos"] if Path(r["source_file"]).stem in stems]
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"rows": rows, "empty": [], "failed": [], "mode": "saved results from a real AI run"}


ranked = json.loads(RANKED.read_text(encoding="utf-8")) if RANKED.exists() else None
tab1, tab2, tab3, tab4 = st.tabs(["Post first", "All promos", "Run on new episodes", "Quality"])

with tab1:
    if not ranked:
        st.info("Run `python rank.py` first.")
    else:
        st.subheader("Post these first")
        st.caption("Shortlisted by score, then ordered by one judge comparing them side by side.")
        st.download_button("Download top 10 as an editor sheet (CSV)", to_csv(ranked["post_first"]),
                           file_name="post_first_editor_sheet.csv", mime="text/csv")
        if REVIEW.exists():
            with st.expander("Review of this top 10: accepted, retimed or replaced"):
                st.markdown(REVIEW.read_text(encoding="utf-8"))
        for r in ranked["post_first"]:
            render(r, heading=f"{r['slot']}. ")
        if MONTAGES.exists():
            st.subheader("Cross-episode montages")
            for m in json.loads(MONTAGES.read_text(encoding="utf-8"))["montages"]:
                with st.container(border=True):
                    path = " + ".join(f"{s['episode'].replace('Episode ', 'E')} `{s['start']}-{s['end']}`" for s in m["segments"])
                    st.markdown(f"**{m['title']}** | {m['total_length_s']:.1f}s  {badge(m['spoiler_risk'], 'spoiler')}")
                    st.markdown(path)
                    st.write(f"Hook ({m['hook']['episode']}): “{m['hook']['quote']}”")
                    st.write(f"Cliffhanger ({m['cliffhanger']['episode']}): “{m['cliffhanger']['quote']}”")
                    st.markdown(f"**Why it works:** {m['why_it_works']}")
                    if m.get("note"):
                        st.caption(m["note"])

with tab2:
    if ranked:
        rows = ranked["ranked_promos"]
        n_eps = len({r["episode"] for r in rows}) + len(ranked["episodes_with_no_good_promo"])
        st.subheader(f"{ranked['total_promos']} validated promos across {n_eps} episodes")
        slots = {r["key"]: r["slot"] for r in ranked["post_first"]}
        st.download_button("Download all promos as an editor sheet (CSV)", to_csv(rows, slots),
                           file_name="all_promos_editor_sheet.csv", mime="text/csv")
        view = st.radio("View", ["By score", "By episode"], horizontal=True)
        if view == "By score":
            n = st.slider("Show top", 5, len(rows), min(15, len(rows))) if len(rows) > 5 else len(rows)
            for r in rows[:n]:
                render(r, heading=f"#{r['rank']} (score {r['rank_score']}) ")
        else:
            eps = sorted({r["episode"] for r in rows}, key=lambda e: int("".join(c for c in e if c.isdigit())))
            choice = st.selectbox("Episode", eps)
            for r in rows:
                if r["episode"] == choice:
                    render(r, heading=f"#{r['rank']} ")
        with st.expander(f"{len(ranked['episodes_with_no_good_promo'])} episodes with no good promo"):
            for n in ranked["episodes_with_no_good_promo"]:
                st.write(f"**{n['episode']}**: {n['reason']}")

with tab3:
    SAMPLE_MODE, PASTE_MODE, UPLOAD_MODE = "Sample episode", "Paste script text", "Upload .txt files"
    mode = st.radio("Choose an input", [SAMPLE_MODE, PASTE_MODE, UPLOAD_MODE], horizontal=True)
    live = has_live_key()
    shared_key = live and not side_key
    if shared_key and mode == SAMPLE_MODE:
        st.info("**No key needed.** The sample show has saved results from a real AI run, so they appear instantly. "
                "You can also run the AI live below.")
    elif shared_key:
        st.info("**Live AI is on, with no key needed.** It uses a free Gemini key, so it can run out for a while. "
                "Your text goes to Google, which may use it to improve its products, so **don't paste confidential scripts**.")
    elif live:
        st.info("**Live AI is on,** using your key. Find promos will judge this input now.")
    elif mode == SAMPLE_MODE:
        st.info("**No key needed.** The sample show has saved results from a real AI run, so they appear instantly. "
                "To run the AI live, add a key in the sidebar.")
    else:
        st.warning("**No API key set, so these are keyword-only picks, not AI judgment.** "
                   "Add a key in the sidebar for the real pipeline, or try a sample episode.")

    files = []
    if mode == SAMPLE_MODE:
        samples = sorted(SAMPLES.glob("*.txt"))
        ALL_SAMPLES = "All 3 sample episodes"
        sample = st.selectbox("Sample episode", [ALL_SAMPLES] + [s.name for s in samples])
        chosen = samples if sample == ALL_SAMPLES else [SAMPLES / sample]
        files = [(s.name, s.read_bytes()) for s in chosen]
        with st.expander("Read the script"):
            for s in chosen:
                st.text(s.read_text(encoding="utf-8"))
    elif mode == PASTE_MODE:
        pasted = st.text_area("Paste one episode's screenplay", height=250, max_chars=200_000,
                              placeholder="Same format as the input pack. Choose Sample episode, then Read the script, to see it.")
        if pasted.strip():
            files = [("pasted-script.txt", pasted.encode("utf-8"))]
    else:
        uploads = st.file_uploader("Upload screenplay .txt files, one or a whole show", type="txt", accept_multiple_files=True)
        files = [(u.name, u.getvalue()) for u in uploads or []]

    episodes = []
    for name, content in files:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(content)
            path = Path(tmp.name)
        try:
            episode = parse_episode(path)
        except Exception as e:  # noqa: BLE001 - a bad file shouldn't take down the page
            st.error(f"{name}: couldn't read this file ({type(e).__name__}).")
            continue
        finally:
            path.unlink(missing_ok=True)
        if not episode["scenes"]:
            st.error(f"{name}: no scenes found. Check that it matches the format of the sample episodes.")
            continue
        episode["source_file"] = name
        episode["episode_number"] = episode["episode_number"] or Path(name).stem
        episodes.append(episode)

    input_id = (mode, tuple((name, hash(content)) for name, content in files))
    if episodes:
        lines = sum(len(s["elements"]) for e in episodes for s in e["scenes"])
        st.write(f"Read **{len(episodes)} episode(s)**: {sum(e['duration_s'] for e in episodes):.0f}s of story, {lines} script lines.")
        run_sample_live = shared_key and mode == SAMPLE_MODE and st.checkbox("Run the AI live instead of showing saved results")
        if shared_key and len(episodes) > MAX_EPISODES_ON_SHARED_KEY:
            st.error(f"This server's shared key runs up to {MAX_EPISODES_ON_SHARED_KEY} episodes at a time. "
                     "Add your own key in the sidebar for more.")
        elif st.button("Find promos", type="primary"):
            if mode == SAMPLE_MODE and (not live or (shared_key and not run_sample_live)):
                st.session_state["run"] = saved_sample_run([e["source_file"] for e in episodes])
            else:
                st.session_state["run"] = run_episodes(episodes)
            st.session_state["run_for"] = input_id

    run = st.session_state.get("run") if st.session_state.get("run_for") == input_id else None
    if run:  # kept across reruns, so a download click doesn't clear the results
        if run["rows"] or not run["failed"]:
            st.success(f"Done ({run['mode']}): {len(run['rows'])} promos.")
        for failure in run["failed"]:
            st.error(f"Failed, and the run carried on. {failure}")
        for n in run["empty"]:
            st.warning(f"No good promo in {n['episode']}: {n['reason']}")
        if run["rows"]:
            st.download_button("Download as an editor sheet (CSV)", to_csv(run["rows"]),
                               file_name="new_episodes_editor_sheet.csv", mime="text/csv")
        for r in run["rows"]:
            heading = f"#{r['rank']} (score {r['rank_score']}) " if "rank_score" in r else f"{r['rank']}. "
            render(r, heading=heading)
            if r.get("errors"):
                st.error("Validation: " + "; ".join(r["errors"]))

with tab4:
    if QUALITY.exists():
        st.markdown(QUALITY.read_text(encoding="utf-8"))
    elif IS_DEMO:
        st.info("The quality report covers Kuku's 50 episodes, so it isn't shown for the 3-episode sample show. "
                "`quality_report.py` builds one from any full run.")
    else:
        st.info("Run `python quality_report.py` first.")
