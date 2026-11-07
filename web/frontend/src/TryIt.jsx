import React, { useEffect, useMemo, useState } from "react";
import { Montage, PromoCard, SheetButton } from "./Results.jsx";

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
  const [savedExtras, setSavedExtras] = useState({ post_first: [], montages: [] });
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
    fetch("/api/samples").then((r) => r.json()).then((d) => { setSamples(d.samples); setSavedExtras(d); }).catch(() => setFailure("The sample episodes couldn't load. Please refresh the page."));
  }, []);

  const chosenSamples = useMemo(() => (sample === ALL ? samples : samples.filter((s) => s.name === sample)), [samples, sample]);
  const scripts = useMemo(() => {
    if (mode === "sample") return chosenSamples.map(({ name, text }) => ({ name, text }));
    if (mode === "paste") return pasted.trim() ? [{ name: "pasted-script.txt", text: pasted }] : [];
    return uploads;
  }, [mode, chosenSamples, pasted, uploads]);

  // A key only counts with a model, so a browser autofilling the box can't override the server's setup.
  const ownKey = Boolean(own.api_key.trim() && own.model.trim());
  const needsModel = Boolean(own.api_key.trim() && !own.model.trim() && !config.server_key);
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
      const whole = sample === ALL;
      setRun({ title: "Saved results from a real AI run", rows, problems: [], postFirst: whole ? savedExtras.post_first : [], montages: whole ? savedExtras.montages : [] });
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
      let extras = { post_first: [], montages: [] };
      if (isLive && rows.length >= 2) {
        setProgress({ done: parsed.length, total: parsed.length, label: "Choosing what to post first, and joining moments" });
        try {
          extras = await postJson("/api/finish", { scripts: parsed.map((p) => p.script), rows, ...(ownKey ? { base_url: own.base_url, api_key: own.api_key, model: own.model } : {}) });
          problems.push(...extras.problems);
          if (extras.montages_dropped) problems.push(`${extras.montages_dropped} proposed montage(s) failed a code check and were dropped.`);
        } catch (e) {
          problems.push(`The top 10 order and montages weren't created: ${e.message}`);
        }
      }
      setRun({ title: isLive ? "Live AI run" : "Keyword-only picks, not AI judgment", rows, problems, postFirst: extras.post_first, montages: extras.montages });
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
          <p className="lede">Choose an input and press Find promos. No login needed. A live run goes through every AI and code step, then an editor makes the final call.</p>
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
        config.free_gemini_key
          ? <div className="notice"><b>Live AI is on, with no key needed.</b> It uses a free Gemini key, so it can run out for a while, and a run usually takes under a minute. These models can pick differently from the 50-episode run. Your text goes to Google, which may use it to improve its products, so <b>don't paste confidential scripts</b>.</div>
          : <div className="notice"><b>Live AI is on,</b> using this copy's model settings.</div>
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
            <label className="field">API key<input type="password" autoComplete="new-password" name="custom-model-api-key" value={own.api_key} onChange={(e) => setOwn({ ...own, api_key: e.target.value })} /></label>
            <label className="field">Judge model<input value={own.model} onChange={(e) => setOwn({ ...own, model: e.target.value })} placeholder="Model ID" /></label>
            <label className="field">Critic model<input value={own.critic_model} onChange={(e) => setOwn({ ...own, critic_model: e.target.value })} placeholder="Same as judge if empty" /></label>
          </div>
        </details>

        {needsModel && <p className="error-text">Find promos is off because you entered an API key with no model. Enter a Judge model under Use your own model, or clear the key.</p>}

        {overLimit && <p className="error-text">The free key runs up to {config.max_episodes_shared} episodes at a time. Remove some files, or add your own key.</p>}
        <button className="btn primary run" disabled={busy || !scripts.length || overLimit || needsModel} onClick={runIt}>
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
          {run.postFirst?.length > 0 && (
            <>
              <h2>Post these first</h2>
              {run.postFirst.map((p) => <PromoCard key={`first-${p.key}`} promo={p} heading={`${p.slot}. `} />)}
            </>
          )}
          {run.montages?.length > 0 && (
            <>
              <h2>Montages across episodes</h2>
              {run.montages.map((m, i) => <Montage key={i} montage={m} />)}
            </>
          )}
          {(run.postFirst?.length > 0 || run.montages?.length > 0) && <h2>All promos by score</h2>}
          {run.rows.map((r) => (
            <PromoCard key={r.key} promo={r} heading={r.rank_score !== undefined ? `#${r.rank} · score ${r.rank_score} · ` : `${r.rank}. `} />
          ))}
        </section>
      )}
    </>
  );
}
