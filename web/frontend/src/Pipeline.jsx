import React, { useState } from "react";

const buildSteps = (s) => ({
  read: { kind: "code", n: 1, title: "Read the script", short: "Scenes and timed lines", file: "promo_finder/ingest/parser.py",
    what: "Turns each screenplay file into scenes and lines, with a start and end time for every line.",
    why: "Scripts are regular enough that rules handle this exactly and for free. A model could only add mistakes.",
    out: "Every line, with its speaker, text and timestamps." },
  hints: { kind: "code", n: 2, title: "Find keyword hints", short: "Possible moments", file: "promo_finder/ingest/heuristics.py",
    what: "Looks for words that often signal drama, and suggests possible moments to the judge.",
    why: "It's only a hint, because keywords alone pick badly: it found nothing in two episodes and flagged ordinary dialogue in others.",
    out: "A short list of candidate windows." },
  judge: { kind: "ai", n: 3, title: "Pick the clips", short: "The judge", file: "promo_finder/judge/scorer.py",
    what: "A model reads the whole episode, with a one-line summary of every other episode, and chooses the best moments. It may also decide that an episode has no good promo.",
    why: "Whether a moment is gripping is a judgment call, so this is real AI work. The model answers with line IDs, never with text, so it can't invent a quote or a timestamp.",
    out: "Line IDs for each clip's start, end, hook and cliffhanger. Code turns them into exact times and words." },
  check: { kind: "code", n: 4, title: "Check every pick", short: "Rules against the script", file: "promo_finder/judge/validate.py",
    what: "Is it 20-60 seconds? Is the hook in the first 3 seconds, and the cliffhanger at the cut? Do the quotes match the script?",
    why: `These rules have a right answer, so code checks them every time. On the 50-episode run, ${s.episodes_with_retry} episodes needed at least one retry, and ${s.picks_dropped} picks were dropped.`,
    out: "A pass, or the exact error to send back to the model." },
  critic: { kind: "ai", n: 5, title: "Get a second opinion", short: "The critic", file: "promo_finder/judge/critic.py",
    what: "A separate step sees only the clip's text, like a stranger would, and scores the hook, the cliffhanger and whether it makes sense alone.",
    why: `The judge rated ${s.self_rated_hook_4_or_5} of its own ${s.promos} hooks 4 or 5 out of 5, so its ratings can't rank anything. The critic's hook scores spread from ${s.critic_hook_min} to ${s.critic_hook_max}.`,
    out: "Scores from 1 to 5, which replace the judge's own ratings." },
  spoiler: { kind: "ai", n: 6, title: "Check for spoilers", short: "Does it give away a twist?", file: "promo_finder/judge/spoiler.py",
    what: "Given a summary of every episode, the model flags clips that reveal a late twist.",
    why: "A promo recruits new viewers, so it shouldn't give away the payoff. It flags too much, so it's a prompt for an editor, not a verdict.",
    out: "A risk level: none, low or high." },
  score: { kind: "code", n: 7, title: "Score and rank", short: "A formula anyone can read", file: "promo_finder/rank/ranker.py",
    what: "35% hook + 30% cliffhanger + 20% makes sense alone + 15% good length, minus a penalty for spoiler or platform risk.",
    why: "Arithmetic should be code. An editor can see exactly why a clip ranked where it did.",
    out: "Every promo in a ranked list." },
  order: { kind: "ai", n: 8, title: "Choose what to post first", short: "Side-by-side comparison", file: "promo_finder/rank/post_first.py",
    what: "A model compares the top 15 clips side by side, then orders the top 10 and writes a reason for each slot.",
    why: "A score can't weigh a whole list against itself. Ranking answers \"what's strongest\", and this answers \"what should a stranger see first\".",
    out: "A top 10, with a reason per slot." },
  montage: { kind: "ai", n: 9, title: "Join moments across episodes", short: "Checked by code", file: "promo_finder/rank/montages.py",
    what: "A model proposes clips that join moments from different episodes. Code checks the length and story order, and drops anything invalid.",
    why: "The brief allows promos that join moments. One proposed montage on the real run failed a check and was dropped.",
    out: "A few validated montages." },
  editor: { kind: "person", n: 10, title: "An editor decides", short: "The final call", file: "promo_finder/export/editor_sheet.py",
    what: "An editor works from the exported sheet, approves or changes the picks, and cuts the footage.",
    why: "The tool reads text, so it can't judge acting, music or pacing. It removes \"watch every episode\", not the editor. Rejected picks become new test cases.",
    out: "Promos ready to post." },
});

const KIND = { code: "Code", ai: "AI", person: "Person" };

const modelRows = (m) => [
  ["Judge", m.judge_winner, `${m.judge_models_compared} models, 10 episodes, 2 runs each`, `Tied with ${m.judge_runner_up} on quality (${m.judge_winner_quality} vs ${m.judge_runner_up_quality} of 5); won on speed, the tie-break set in advance`],
  ["Critic", m.critic_winner, "12 real promos vs 12 random clips, 2 runs", `Scored a promo above a random clip ${m.critic_separation_pct}% of the time`],
  ["Spoiler check", m.spoiler_winner, "20 labelled clips, 2 runs", `Caught all ${m.spoiler_high_caught} high-risk clips, with ${m.spoiler_false_alarms} false alarms`],
  ["Post-first order", m.order_winner, "Same list, 3 runs", `Top 10s overlapped by ${m.order_stability} between runs; ${m.order_speed_vs_next} times as fast as the other leader`],
  ["Montages", m.montage_winner, "3 runs of 3 montages", `${m.montage_valid} of ${m.montage_proposed} valid; ${m.montage_models_below_half_valid} other models fell below half valid`],
];

function Node({ steps, id, selected, onSelect }) {
  const s = steps[id];
  return (
    <button className={`node node-${s.kind}${selected === id ? " selected" : ""}`} aria-pressed={selected === id} onClick={() => onSelect(id)}>
      <span className="node-n">{s.n}</span>
      <span className="node-text"><b>{s.title}</b><span>{s.short}</span></span>
      <span className={`tag tag-${s.kind}`}>{KIND[s.kind]}</span>
    </button>
  );
}

const Arrow = () => <div className="arrow" aria-hidden="true" />;

function Detail({ steps, id }) {
  const s = steps[id];
  return (
    <div className="card detail" aria-live="polite">
      <span className={`tag tag-${s.kind}`}>{KIND[s.kind]}</span>
      <h3>{s.n}. {s.title}</h3>
      <h4>What it does</h4><p>{s.what}</p>
      <h4>Why it's done this way</h4><p>{s.why}</p>
      <h4>What comes out</h4><p>{s.out}</p>
      <p className="muted small">Code: <code>{s.file}</code></p>
    </div>
  );
}

export default function Pipeline({ stats }) {
  const steps = buildSteps(stats);
  const [selected, setSelected] = useState("judge");
  const select = (id) => {
    setSelected(id);
    // On a phone the explanation sits below the flowchart, so bring it into view.
    if (window.innerWidth <= 760) {
      requestAnimationFrame(() => document.querySelector(".detail")?.scrollIntoView({ behavior: "smooth", block: "nearest" }));
    }
  };
  const node = (id) => <Node steps={steps} id={id} selected={selected} onSelect={select} />;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>How it works</h1>
          <p className="lede">AI makes the judgment calls, and code does anything that can be checked. Select a step to see what it does and why.</p>
        </div>
      </div>

      <div className="legend" aria-label="Legend">
        <span><i className="dot dot-code" />Code</span>
        <span><i className="dot dot-ai" />AI</span>
        <span><i className="dot dot-person" />Person</span>
      </div>

      <div className="flow-layout">
        <div className="flow">
          <div className="endpoint">A screenplay .txt file</div>
          <Arrow />
          {node("read")}
          <Arrow />
          {node("hints")}
          <Arrow />
          <div className="loop">
            <p className="loop-label">Retry: if a pick fails, the errors go back to the AI, up to 2 times. After that, it's dropped.</p>
            {node("judge")}
            <Arrow />
            {node("check")}
          </div>
          <Arrow />
          <div className="fork-label">Each clip that passes gets:</div>
          <div className="pair">
            {node("critic")}
            {node("spoiler")}
          </div>
          <Arrow />
          {node("score")}
          <Arrow />
          <div className="fork-label">The ranked list feeds two steps:</div>
          <div className="pair">
            {node("order")}
            {node("montage")}
          </div>
          <Arrow />
          {node("editor")}
          <Arrow />
          <div className="endpoint end">Promos posted</div>
        </div>
        <div className="detail-col"><Detail steps={steps} id={selected} /></div>
      </div>

      <h2>Why the AI can't invent a quote</h2>
      <p>The model never writes a timestamp or a line of dialogue. It sees numbered lines and answers with the numbers, and code looks up the rest.</p>
      <div className="grid two">
        <div className="card"><h4>What the model sees</h4><pre className="mono">e12 [00:17.00-00:19.00] JULIAN (O.S.) (Cold): Put it down.</pre></div>
        <div className="card"><h4>What the model answers</h4><pre className="mono">{"hook_element: \"e12\""}</pre><p className="muted small">Code fills in 00:17.00 and "Put it down." from the script.</p></div>
      </div>
      <p>This came from a real failure. In an early version, the model wrote its own quotes and said they were all word for word, after a check of just 4. A full check found 12 of 138 merged or reworded.</p>

      <h2>How the model for each AI step was chosen</h2>
      <p>For each step, I wrote down what "good" means before the full comparison, gave every model the same test, removed unreliable ones, and ranked the rest.</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Step</th><th>Winner</th><th>Test</th><th>Result</th></tr></thead>
          <tbody>{modelRows(stats.models).map((m) => <tr key={m[0]}>{m.map((c, i) => <td key={i}>{c}</td>)}</tr>)}</tbody>
        </table>
      </div>
      <p className="muted small">
        A Claude model served as the LLM judge for the judge's final round and supplied the test labels for the critic and spoiler check, not a person, so that evidence is weaker. New runs use Flash Low for the critic too, because the winner hit long rate limits. This site's live runs use free Gemini models for speed. The judge is Gemini 3.1 Flash Lite, the finalist above. The other AI steps use Gemini's current Flash Lite, which wasn't in these tests.
      </p>

      <h2>Scale and cost</h2>
      <div className="grid three">
        <div className="card"><h3>About $0.10 to $2</h3><p>Estimated AI cost per 50-episode show, judge step only, at Claude Haiku 5.5 and Sonnet 5.5 list prices. It's an estimate: the run used a subscription login, so there's no real bill.</p></div>
        <div className="card"><h3>About 8 minutes</h3><p>Estimated judge time per show, one episode at a time, at about 9 seconds per episode in tests. An editor would watch about 100 minutes of footage.</p></div>
        <div className="card"><h3>One setting per model</h3><p>Any OpenAI-compatible API or Anthropic's API works. A failed episode doesn't stop the run, and a re-run picks up where it stopped.</p></div>
      </div>
    </>
  );
}
