import React from "react";

export function SampleNotice({ results }) {
  if (!results.is_sample) return null;
  return (
    <div className="notice">
      This is a sample show I wrote. Kuku's scripts are confidential, so the results from its 50 episodes are in the raw
      outputs linked on the submission form, not on this public site.
    </div>
  );
}

export default function Overview({ results, go }) {
  return (
    <>
      <h1>Promo Finder</h1>
      <p className="lede">
        Today an editor watches every episode to pick promos, and new shows ship every week. This tool reads the screenplays
        and suggests 20-60 second clips, ranked, with timestamps and a reason for each. An editor makes the final call.
      </p>
      <div className="actions">
        <button className="btn primary" onClick={() => go("try")}>Try it on a script</button>
        <button className="btn" onClick={() => go("post-first")}>See the top picks</button>
        <button className="btn" onClick={() => go("how")}>How it works</button>
      </div>

      <SampleNotice results={results} />

      <h2>What it looks for</h2>
      <ol className="plain">
        <li>A hook in the first 3 seconds that makes a scrolling viewer stop.</li>
        <li>A cliffhanger at the cut, so the viewer needs to know what happens next.</li>
        <li>A clip that makes sense to someone who has never seen the show.</li>
      </ol>

      <h2>Results on Kuku's input pack</h2>
      <table className="numbers">
        <tbody>
          <tr><td>50</td><td>episodes read</td></tr>
          <tr><td>78</td><td>promos found</td></tr>
          <tr><td>3</td><td>episodes left empty on purpose</td></tr>
          <tr><td>3</td><td>montages across episodes</td></tr>
        </tbody>
      </table>
      <p>All 78 pass the code checks against the script. No editor has reviewed them yet. The Quality tab shows where the tool picks badly.</p>

      <h2>What's AI and what's code</h2>
      <div className="grid two">
        <div className="card"><h3>AI</h3><p>Which moment is gripping, whether a stranger would follow it, and what to post first.</p></div>
        <div className="card"><h3>Code</h3><p>Reading the script, exact timestamps and quotes, the 20-60 second and 3-second rules, and the score.</p></div>
      </div>
    </>
  );
}
