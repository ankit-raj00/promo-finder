import React, { useMemo, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { SampleNotice } from "./Overview.jsx";

export async function downloadSheet(rows, filename) {
  const r = await fetch("/api/sheet", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rows }) });
  if (!r.ok) throw new Error("download failed");
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function SheetButton({ rows, filename, label }) {
  const [failed, setFailed] = useState(false);
  return (
    <>
      <button className="btn" onClick={() => { setFailed(false); downloadSheet(rows, filename).catch(() => setFailed(true)); }}>{label}</button>
      {failed && <span className="error-text"> The download failed. Please try again.</span>}
    </>
  );
}

function Rating({ label, value }) {
  if (value === undefined || value === null) return null;
  return (
    <span className="rating" aria-label={`${label} ${value} out of 5`}>
      {label} <b>{value}/5</b>
    </span>
  );
}

function Risk({ label, level }) {
  if (!level) return null;
  return <span className={`risk risk-${level}`}>{label}: {level}</span>;
}

export function PromoCard({ promo, heading }) {
  const times = promo.segments.map((s) => `${s.start}-${s.end}`).join(" + ");
  return (
    <article className="card promo">
      <div className="promo-head">
        <h3>{heading}{promo.episode}</h3>
        <code className="times">{times}</code>
        <span className="muted small">{promo.total_length_s.toFixed(1)}s</span>
      </div>
      <div className="chips">
        <Rating label="Hook" value={promo.hook_strength} />
        <Rating label="Cliffhanger" value={promo.cliffhanger_strength} />
        <Rating label="Standalone" value={promo.standalone_score} />
        <Risk label="Spoiler" level={promo.spoiler_risk} />
        <Risk label="Platform" level={promo.platform_risk} />
      </div>
      <div className="moments">
        <div>
          <p className="label">Hook <code>{promo.hook_timestamp}</code></p>
          <blockquote>{promo.hook_quote}</blockquote>
        </div>
        <div>
          <p className="label">Cliffhanger {promo.cliffhanger_timestamp && <code>{promo.cliffhanger_timestamp}</code>}</p>
          <blockquote>{promo.cliffhanger_quote}</blockquote>
        </div>
      </div>
      <p><b>Why it works:</b> {promo.why_it_works}</p>
      {promo.reason && <p className="slot-reason"><b>Why this slot:</b> {promo.reason}</p>}
      {promo.risk_note && <p className="muted small">Risk note: {promo.risk_note}</p>}
      {promo.errors?.length > 0 && <p className="error-text">Failed a rule check: {promo.errors.join("; ")}</p>}
    </article>
  );
}

function Montage({ montage }) {
  return (
    <article className="card promo">
      <div className="promo-head">
        <h3>{montage.title}</h3>
        <span className="muted small">{montage.total_length_s.toFixed(1)}s</span>
        <Risk label="Spoiler" level={montage.spoiler_risk} />
      </div>
      <p className="small">
        {montage.segments.map((s, i) => (
          <span key={i}>{i > 0 && " + "}{s.episode} <code>{s.start}-{s.end}</code></span>
        ))}
      </p>
      <div className="moments">
        <div><p className="label">Hook ({montage.hook.episode})</p><blockquote>{montage.hook.quote}</blockquote></div>
        <div><p className="label">Cliffhanger ({montage.cliffhanger.episode})</p><blockquote>{montage.cliffhanger.quote}</blockquote></div>
      </div>
      <p><b>Why it works:</b> {montage.why_it_works}</p>
    </article>
  );
}

export function PostFirst({ results }) {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Post these first</h1>
          <p className="lede">Code shortlists by score, then one AI step compares the clips side by side and sets the order. An editor makes the final call.</p>
        </div>
        <SheetButton rows={results.post_first} filename="post_first_editor_sheet.csv" label="Download editor sheet (CSV)" />
      </div>
      <SampleNotice results={results} />
      {results.review && (
        <details className="card"><summary>Review of this list: accepted, retimed or replaced</summary>
          <div className="markdown"><Markdown remarkPlugins={[remarkGfm]}>{results.review}</Markdown></div>
        </details>
      )}
      {results.post_first.map((p) => <PromoCard key={p.key} promo={p} heading={`${p.slot}. `} />)}
      {results.montages.length > 0 && (
        <>
          <h2>Montages across episodes</h2>
          {results.montages.map((m, i) => <Montage key={i} montage={m} />)}
        </>
      )}
    </>
  );
}

const number = (episode) => parseInt(String(episode).replace(/\D/g, ""), 10) || 0;

export function AllPromos({ results }) {
  const [view, setView] = useState("score");
  const [episode, setEpisode] = useState("all");
  const episodes = useMemo(() => [...new Set(results.ranked.map((r) => r.episode))].sort((a, b) => number(a) - number(b)), [results]);
  const slots = useMemo(() => Object.fromEntries(results.post_first.map((p) => [p.key, p.slot])), [results]);
  const rows = useMemo(() => {
    const picked = episode === "all" ? results.ranked : results.ranked.filter((r) => r.episode === episode);
    return view === "score" ? picked : [...picked].sort((a, b) => number(a.episode) - number(b.episode) || a.rank - b.rank);
  }, [results, view, episode]);
  const sheetRows = useMemo(() => results.ranked.map((r) => ({ ...r, slot: slots[r.key] })), [results, slots]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{results.ranked.length} promos</h1>
          <p className="lede">Every clip that passed the rule checks, with the score that ranked it.</p>
        </div>
        <SheetButton rows={sheetRows} filename="all_promos_editor_sheet.csv" label="Download editor sheet (CSV)" />
      </div>
      <SampleNotice results={results} />
      <div className="controls">
        <label>Sort by
          <select value={view} onChange={(e) => setView(e.target.value)}>
            <option value="score">Score</option>
            <option value="episode">Episode</option>
          </select>
        </label>
        <label>Episode
          <select value={episode} onChange={(e) => setEpisode(e.target.value)}>
            <option value="all">All episodes</option>
            {episodes.map((e) => <option key={e} value={e}>{e}</option>)}
          </select>
        </label>
      </div>
      {rows.map((r) => <PromoCard key={r.key} promo={r} heading={`#${r.rank} · score ${r.rank_score} · `} />)}
      {results.empty_episodes.length > 0 && (
        <details className="card"><summary>{results.empty_episodes.length} episodes with no good promo</summary>
          {results.empty_episodes.map((n) => <p key={n.episode}><b>{n.episode}:</b> {n.reason}</p>)}
        </details>
      )}
    </>
  );
}
