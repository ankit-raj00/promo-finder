import React, { useEffect, useMemo, useState } from "react";
import { PromoCard, SheetButton } from "./Results.jsx";

const ALL = "__all__";
const MAX_FILE_BYTES = 1024 * 1024;

async function postJson(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The server couldn't handle that request. Please try again.");
  return data;
}

export default function TryIt({ config }) {
  const [mode, setMode] = useState("sample");
  const [samples, setSamples] = useState([]);
  const [sample, setSample] = useState(ALL);
  const [pasted, setPasted] = useState("");
  const [uploads, setUploads] = useState([]);
  const [uploadError, setUploadError] = useState("");
  const [runSampleLive, setRunSampleLive] = useState(false);
  const [own, setOwn] = useState({ base_url: "", api_key: "", model: "", critic_model: "" });
  const [progress, setProgress] = useState(null);
  const [run, setRun] = useState(null);
  const [failure, setFailure] = useState("");

  useEffect(() => {
    fetch("/api/samples").then((r) => r.json()).then(setSamples).catch(() => setFailure("The sample episodes couldn't load. Please refresh the page."));
  }, []);

  const chosenSamples = useMemo(() => (sample === ALL ? samples : samples.filter((s) => s.name === sample)), [samples, sample]);
  const scripts = useMemo(() => {
    if (mode === "sample") return chosenSamples.map(({ name, text }) => ({ name, text }));
    if (mode === "paste") return pasted.trim() ? [{ name: "pasted-script.txt", text: pasted }] : [];
    return uploads;
  }, [mode, chosenSamples, pasted, uploads]);

  const ownKey = Boolean(own.api_key.trim());
  const live = ownKey || config.server_key;
  const sharedKey = live && !ownKey;
  const useSaved = mode === "sample" && (!live || (sharedKey && !runSampleLive));
  const overLimit = sharedKey && !useSaved && scripts.length > config.max_episodes_shared;
  const busy = progress !== null;

  const onFiles = async (fileList) => {
    setUploadError("");
    setRun(null);
    const files = [...fileList];
    const tooBig = files.filter((f) => f.size > MAX_FILE_BYTES);
    if (tooBig.length) setUploadError(`Skipped ${tooBig.map((f) => f.name).join(", ")}: each file must be under 1 MB.`);
    const kept = files.filter((f) => f.size <= MAX_FILE_BYTES);
    setUploads(await Promise.all(kept.map(async (f) => ({ name: f.name, text: await f.text() }))));
  };

  const runIt = async () => {
    setFailure("");
    setRun(null);
    if (useSaved) {
      const rows = chosenSamples.flatMap((s) => s.saved).sort((a, b) => a.rank - b.rank).map((r, i) => ({ ...r, rank: i + 1 }));
      setRun({ title: "Saved results from a real AI run", rows, problems: [] });
      return;
    }
    const rows = [];
    const problems = [];
    try {
      setProgress({ done: 0, total: scripts.length, label: "Reading the scripts" });
      const parsed = [];
      for (const s of scripts) {
        try {
          parsed.push({ script: s, info: await postJson("/api/parse", s) });
        } catch (e) {
          problems.push(e.message);
        }
      }
      const context = parsed.map((p) => `${p.info.episode}: ${p.info.logline}`).join("\n");
      let isLive = live;
      for (let i = 0; i < parsed.length; i++) {
        const { script, info } = parsed[i];
        setProgress({ done: i, total: parsed.length, label: `Judging ${info.episode}` });
        try {
          const body = { ...script, context, ...(ownKey ? own : {}) };
          const out = await postJson("/api/judge", body);
          isLive = out.live;
          if (out.error) problems.push(`${info.episode}: ${out.error}`);
          else if (!out.promos.length) problems.push(`No good promo in ${info.episode}: ${out.empty_reason || "none found"}`);
          rows.push(...out.promos);
        } catch (e) {
          problems.push(`${info.episode}: ${e.message}`);
        }
      }
      if (isLive) rows.sort((a, b) => b.rank_score - a.rank_score);
      rows.forEach((r, i) => { r.rank = i + 1; r.key = `${r.episode}-${i}`; });
      setRun({ title: isLive ? "Live AI run" : "Keyword-only picks, not AI judgment", rows, problems });
    } finally {
      setProgress(null);
    }
  };

  const switchMode = (m) => { setMode(m); setRun(null); setFailure(""); };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Try it</h1>
          <p className="lede">Choose an input and press Find promos. No login needed. This page runs steps 1-7 of the pipeline. The top 10 order and montages come from the full-show command, <code>run_show.py</code>.</p>
        </div>
      </div>

      <div className="segmented" role="radiogroup" aria-label="Input type">
        {[["sample", "Sample episode"], ["paste", "Paste script text"], ["upload", "Upload .txt files"]].map(([id, label]) => (
          <button key={id} role="radio" aria-checked={mode === id} className={mode === id ? "seg active" : "seg"} onClick={() => switchMode(id)}>{label}</button>
        ))}
      </div>

      {mode === "sample" && useSaved && <div className="notice"><b>No key needed.</b> These sample episodes have saved results from a real AI run, so they appear instantly.</div>}
      {mode !== "sample" && !live && <div className="notice warn"><b>No AI key is set, so these will be keyword-only picks, not AI judgment.</b> Add a key under Use your own model for the real pipeline.</div>}
      {!useSaved && sharedKey && (
        <div className="notice"><b>Live AI is on, with no key needed.</b> It uses a free Gemini key, so it can run out for a while, and it takes about 20 seconds per episode. Your text goes to Google, which may use it to improve its products, so <b>don't paste confidential scripts</b>.</div>
      )}
      {!useSaved && ownKey && <div className="notice"><b>Live AI is on,</b> using your key.</div>}

      <section className="card">
        {mode === "sample" && (
          <>
            <label className="field">Sample episode
              <select value={sample} onChange={(e) => { setSample(e.target.value); setRun(null); }}>
                <option value={ALL}>All 3 sample episodes</option>
                {samples.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
              </select>
            </label>
            {sharedKey && (
              <label className="check"><input type="checkbox" checked={runSampleLive} onChange={(e) => { setRunSampleLive(e.target.checked); setRun(null); }} /> Run the AI live instead of showing saved results</label>
            )}
            <details><summary>Read the script</summary>
              {chosenSamples.map((s) => <pre key={s.name} className="script">{s.text}</pre>)}
            </details>
          </>
        )}
        {mode === "paste" && (
          <label className="field">Paste one episode's screenplay
            <textarea rows={12} maxLength={200000} value={pasted} onChange={(e) => { setPasted(e.target.value); setRun(null); }} placeholder="Use the same format as the sample episodes, which you can read under Sample episode." />
          </label>
        )}
        {mode === "upload" && (
          <label className="field">Upload screenplay .txt files, one episode or a whole show
            <input type="file" accept=".txt,text/plain" multiple onChange={(e) => onFiles(e.target.files)} />
            {uploads.length > 0 && <span className="muted small">{uploads.length} file(s) ready</span>}
            {uploadError && <span className="error-text">{uploadError}</span>}
          </label>
        )}

        <details className="own-model"><summary>Use your own model (optional)</summary>
          <p className="muted small">Any OpenAI-compatible endpoint, or leave Base URL empty for Anthropic's API with a Claude model ID. Your key is only sent with these requests and is never saved.</p>
          <div className="grid two">
            <label className="field">Base URL<input value={own.base_url} onChange={(e) => setOwn({ ...own, base_url: e.target.value })} placeholder="https://your-endpoint/v1" /></label>
            <label className="field">API key<input type="password" autoComplete="off" value={own.api_key} onChange={(e) => setOwn({ ...own, api_key: e.target.value })} /></label>
            <label className="field">Judge model<input value={own.model} onChange={(e) => setOwn({ ...own, model: e.target.value })} placeholder="Model ID" /></label>
            <label className="field">Critic model<input value={own.critic_model} onChange={(e) => setOwn({ ...own, critic_model: e.target.value })} placeholder="Same as judge if empty" /></label>
          </div>
          {ownKey && !own.model.trim() && <p className="error-text">Enter a judge model ID to use your own key.</p>}
        </details>

        {overLimit && <p className="error-text">The free key runs up to {config.max_episodes_shared} episodes at a time. Remove some files, or add your own key.</p>}
        <button className="btn primary run" disabled={busy || !scripts.length || overLimit || (ownKey && !own.model.trim())} onClick={runIt}>
          {busy ? "Working..." : "Find promos"}
        </button>
      </section>

      {busy && (
        <div className="card" role="status">
          <p>{progress.label} ({Math.min(progress.done + 1, progress.total)} of {progress.total})</p>
          <div className="bar"><div style={{ width: `${(progress.done / Math.max(progress.total, 1)) * 100}%` }} /></div>
        </div>
      )}
      {failure && <div className="notice error">{failure}</div>}

      {run && (
        <section aria-live="polite">
          <div className="page-head">
            <h2 id="run-title">{run.title}: {run.rows.length} promo{run.rows.length === 1 ? "" : "s"}</h2>
            {run.rows.length > 0 && <SheetButton rows={run.rows} filename="promo_editor_sheet.csv" label="Download editor sheet (CSV)" />}
          </div>
          {run.problems.map((p, i) => <div className="notice warn" key={i}>{p}</div>)}
          {run.rows.map((r) => (
            <PromoCard key={r.key} promo={r} heading={r.rank_score !== undefined ? `#${r.rank} · score ${r.rank_score} · ` : `${r.rank}. `} />
          ))}
        </section>
      )}
    </>
  );
}
