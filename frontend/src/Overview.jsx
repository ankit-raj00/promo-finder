import React from "react";

export function SampleNotice({ results }) {
  if (!results.is_sample) return null;
  return (
    <div className="notice">
      <b>You're viewing a sample show I wrote.</b> Kuku's scripts are confidential, so the results from its 50 episodes are
      in the raw outputs linked on the submission form, not on this public site.
    </div>
  );
}

export default function Overview({ results, go }) {
  return (
    <>
      <section className="hero">
        <p className="eyebrow">From screenplay to ranked promos</p>
        <h1>Find the best promo clips without watching every episode.</h1>
        <p className="lede">
          An editor watches every episode to pick promos, and new shows ship every week. This tool reads the screenplays and
          suggests 20-60 second clips, ranked, with exact timestamps and a reason for each. An editor then makes the final call.
        </p>
        <div className="actions">
          <button className="btn primary" onClick={() => go("try")}>Try it on a script</button>
          <button className="btn" onClick={() => go("post-first")}>See what to post first</button>
          <button className="btn" onClick={() => go("how")}>See how it works</button>
        </div>
      </section>

      <SampleNotice results={results} />

      <h2>What makes a good promo</h2>
      <div className="grid three">
        <div className="card"><h3>Opens on a hook</h3><p>Something in the first 3 seconds makes a scrolling viewer stop.</p></div>
        <div className="card"><h3>Ends on a cliffhanger</h3><p>It cuts at an unresolved moment, so the viewer needs to know what happens next.</p></div>
        <div className="card"><h3>Makes sense alone</h3><p>Someone who has never seen the show can still follow it.</p></div>
      </div>

      <h2>Results on Kuku's input pack</h2>
      <div className="stats">
        <div className="stat"><b>50</b><span>episodes read</span></div>
        <div className="stat"><b>78</b><span>promos found</span></div>
        <div className="stat"><b>3</b><span>episodes left empty on purpose</span></div>
        <div className="stat"><b>3</b><span>montages across episodes</span></div>
      </div>
      <p className="muted small">All 78 pass the code checks against the script. No editor has reviewed them yet, and the Quality tab shows where the tool picks badly.</p>

      <h2>The idea behind it</h2>
      <div className="grid two">
        <div className="card ai-card"><h3>AI makes the judgment calls</h3><p>Which moment is gripping, whether a stranger would follow it, and what to post first.</p></div>
        <div className="card code-card"><h3>Code does what can be checked</h3><p>Reading the script, exact timestamps and quotes, the 20-60 second and 3-second rules, and the score.</p></div>
      </div>

      <h2>Where to go next</h2>
      <div className="grid two">
        <button className="card link-card" onClick={() => go("post-first")}><h3>Post first →</h3><p>The top picks, each with a reason for its slot.</p></button>
        <button className="card link-card" onClick={() => go("try")}><h3>Try it →</h3><p>Pick a sample, paste a script or upload files. No login needed.</p></button>
        <button className="card link-card" onClick={() => go("how")}><h3>How it works →</h3><p>A flowchart of every step, and why each one exists.</p></button>
        <button className="card link-card" onClick={() => go("quality")}><h3>Quality →</h3><p>How I checked the results, and where the tool fails.</p></button>
      </div>
    </>
  );
}
