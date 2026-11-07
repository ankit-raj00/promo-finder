import React from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function Quality({ results, stats: s }) {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Quality</h1>
          <p className="lede">These numbers come from the run on all 50 episodes of Kuku's input pack.</p>
        </div>
      </div>

      <div className="notice warn"><b>The biggest gap: no editor has checked these picks.</b> The checks below show the tool is reliable and consistent. They don't prove the promos would perform well with real viewers.</div>

      <h2>What I checked</h2>
      <div className="grid three">
        <div className="card"><p className="big">{s.passed_checks} of {s.promos}</p><h3>Pass the rules</h3><p>Every promo passes every check against the script. It took work: {s.episodes_with_retry} of {s.episodes} episodes needed a retry, and {s.picks_dropped} picks were dropped.</p></div>
        <div className="card"><p className="big">{s.pick_or_skip_agreement} of {s.episodes}</p><h3>Agree on pick or skip</h3><p>An earlier run, judged separately by a Claude model, made the same pick-or-skip choice, with a time overlap of {s.mean_time_overlap}. Both are AI, so this shows agreement, not that the picks are right.</p></div>
        <div className="card"><p className="big">{s.consistency}</p><h3>Consistency out of 1</h3><p>Two runs of the judge on the test episodes. The moments are often similar, but the start and end points move.</p></div>
      </div>

      <h2>Where it picks badly</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Problem</th><th>How much</th><th>What it means</th></tr></thead>
          <tbody>
            <tr><td>Weak hooks</td><td>{s.weak_hooks} of {s.promos} scored 2 or lower</td><td>The opening may not stop a stranger, so an editor should check these first</td></tr>
            <tr><td>Needs backstory</td><td>{s.need_backstory} of {s.promos} scored 3 or lower</td><td>The clip may confuse someone new to the show</td></tr>
            <tr><td>Overlapping clips</td><td>{s.same_episode_overlaps} same-episode pairs</td><td>Post only one from each pair</td></tr>
            <tr><td>Spoiler check flags too much</td><td>{s.spoiler_high} high and {s.spoiler_low} low</td><td>Treat it as a prompt for an editor, not a verdict</td></tr>
            <tr><td>Missed moments</td><td>At least 1</td><td>It didn't pick a scene the earlier run ranked first, so strong moments can be skipped</td></tr>
            <tr><td>Slightly long clip</td><td>{s.clips_over_60s} clip at {s.longest_clip_s}s</td><td>The check allows half a second of slack for script timing, so it needs a small trim</td></tr>
            <tr><td>Re-runs differ</td><td>See consistency above</td><td>Some start and end points change between runs</td></tr>
            <tr><td>Text only</td><td>Every clip</td><td>It can't judge acting, music or pacing</td></tr>
          </tbody>
        </table>
      </div>

      <h2>Top 10 review</h2>
      <p>In place of an editor, a Claude model reviewed the top 10 from the script text: <b>{s.review_accepted} accepted, {s.review_retimed} retimed, {s.review_replaced} replaced</b>. No person has reviewed them, and that's weaker evidence than a real editor's check.</p>

      <h2>How I'd know it works with a real team</h2>
      <div className="grid three">
        <div className="card"><h3>Editor acceptance</h3><p>How many of the top 10 get posted unchanged. Target: 7 or more.</p></div>
        <div className="card"><h3>Time saved</h3><p>Editor minutes per show, compared with watching every episode.</p></div>
        <div className="card"><h3>Viewer results</h3><p>3-second hold and click-through for tool-picked vs hand-picked promos.</p></div>
      </div>
      <p>Rejected and retimed picks become test cases for the next prompt or model change. The editor sheet has columns ready for that.</p>

      {!results.is_sample && results.quality_report && (
        <details className="card"><summary>Full quality report</summary>
          <div className="markdown"><Markdown remarkPlugins={[remarkGfm]}>{results.quality_report}</Markdown></div>
        </details>
      )}
    </>
  );
}
