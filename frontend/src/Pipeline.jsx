import React, { useState } from "react";

const STEPS = {
  read: { kind: "code", n: 1, title: "Read the script", short: "Scenes and timed lines", file: "parser.py",
    what: "Turns each screenplay file into scenes and lines, with a start and end time for every line.",
    why: "Scripts are regular enough that rules handle this exactly and for free. A model could only add mistakes.",
    out: "Every line, with its speaker, text and timestamps." },
  hints: { kind: "code", n: 2, title: "Find keyword hints", short: "Possible moments", file: "heuristics.py",
    what: "Looks for words that often signal drama, and suggests possible moments to the judge.",
    why: "It's only a hint, because keywords alone pick badly: it found nothing in two episodes and flagged ordinary dialogue in others.",
    out: "A short list of candidate windows." },
  judge: { kind: "ai", n: 3, title: "Pick the clips", short: "The judge", file: "llm_scorer.py",
    what: "A model reads the whole episode, with a one-line summary of every other episode, and chooses the best moments. It may also decide that an episode has no good promo.",
    why: "Whether a moment is gripping is a judgment call, so this is real AI work. The model answers with line IDs, never with text, so it can't invent a quote or a timestamp.",
    out: "Line IDs for each clip's start, end, hook and cliffhanger. Code turns them into exact times and words." },
  check: { kind: "code", n: 4, title: "Check every pick", short: "Rules against the script", file: "validate.py",
    what: "Is it 20-60 seconds? Is the hook in the first 3 seconds, and the cliffhanger at the cut? Do the quotes match the script?",
    why: "These rules have a right answer, so code checks them every time. On the 50-episode run, 32 episodes needed at least one retry, and 4 picks were dropped.",
    out: "A pass, or the exact error to send back to the model." },
  critic: { kind: "ai", n: 5, title: "Get a second opinion", short: "The critic", file: "critic.py",
    what: "A separate step sees only the clip's text, like a stranger would, and scores the hook, the cliffhanger and whether it makes sense alone.",
    why: "The judge rated 77 of its own 78 hooks 4 or 5 out of 5, so its ratings can't rank anything. The critic's hook scores spread from 2 to 5.",
    out: "Scores from 1 to 5, which replace the judge's own ratings." },
  spoiler: { kind: "ai", n: 6, title: "Check for spoilers", short: "Does it give away a twist?", file: "spoiler_check.py",
    what: "Given a summary of every episode, the model flags clips that reveal a late twist.",
    why: "A promo recruits new viewers, so it shouldn't give away the payoff. It flags too much, so it's a prompt for an editor, not a verdict.",
    out: "A risk level: none, low or high." },
  score: { kind: "code", n: 7, title: "Score and rank", short: "A formula anyone can read", file: "rank.py",
    what: "35% hook + 30% cliffhanger + 20% makes sense alone + 15% good length, minus a penalty for spoiler or platform risk.",
    why: "Arithmetic should be code. An editor can see exactly why a clip ranked where it did.",
    out: "Every promo in a ranked list." },
  order: { kind: "ai", n: 8, title: "Choose what to post first", short: "Side-by-side comparison", file: "global_rank.py",
    what: "A model compares the top 15 clips side by side, then orders the top 10 and writes a reason for each slot.",
    why: "A score can't weigh a whole list against itself. Ranking answers \"what's strongest\", and this answers \"what should a stranger see first\".",
    out: "A top 10, with a reason per slot." },
  montage: { kind: "ai", n: 9, title: "Join moments across episodes", short: "Checked by code", file: "cross_episode.py",
    what: "A model proposes clips that join moments from different episodes. Code checks the length and story order, and drops anything invalid.",
    why: "The brief allows promos that join moments. One proposed montage on the real run failed a check and was dropped.",
    out: "A few validated montages." },
  editor: { kind: "person", n: 10, title: "An editor decides", short: "The final call", file: "editor_sheet.py",
    what: "An editor works from the exported sheet, approves or changes the picks, and cuts the footage.",
    why: "The tool reads text, so it can't judge acting, music or pacing. It removes \"watch every episode\", not the editor. Rejected picks become new test cases.",
    out: "Promos ready to post." },
};

const KIND = { code: "Code", ai: "AI", person: "Person" };

const MODELS = [
  ["Judge", "Gemini 3.8 Flash Low", "5 models, 10 episodes, 2 runs each", "Tied with Flash Lite on quality (3.58 vs 3.62 of 5); won on speed, the tie-break set in advance"],
  ["Critic", "Claude Sonnet 4.6", "12 real promos vs 12 random clips, 2 runs", "Scored a promo above a random clip 89% of the time"],
  ["Spoiler check", "Gemini 3.8 Flash Low", "20 labelled clips, 2 runs", "Caught all 4 high-risk clips, with 2 false alarms"],
  ["Post-first order", "Gemini 3.8 Flash Low", "Same list, 3 runs", "Top 10s overlapped by 0.88 between runs; twice as fast as the other leader"],
  ["Montages", "Gemini 3.8 Flash Low", "3 runs of 3 montages", "9 of 9 valid; two other models failed"],
];

function Node({ id, selected, onSelect }) {
  const s = STEPS[id];
  return (
    <button className={`node node-${s.kind}${selected === id ? " selected" : ""}`} aria-pressed={selected === id} onClick={() => onSelect(id)}>
      <span className="node-n">{s.n}</span>
      <span className="node-text"><b>{s.title}</b><span>{s.short}</span></span>
      <span className={`tag tag-${s.kind}`}>{KIND[s.kind]}</span>
    </button>
  );
}

const Arrow = () => <div className="arrow" aria-hidden="true" />;

function Detail({ id }) {
  const s = STEPS[id];
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

export default function Pipeline() {
  const [selected, setSelected] = useState("judge");
  const select = (id) => {
    setSelected(id);
    // On a phone the explanation sits below the flowchart, so bring it into view.
    if (window.innerWidth <= 760) {
      requestAnimationFrame(() => document.querySelector(".detail")?.scrollIntoView({ behavior: "smooth", block: "nearest" }));
    }
  };
  const node = (id) => <Node id={id} selected={selected} onSelect={select} />;
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
        <div className="detail-col"><Detail id={selected} /></div>
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
          <tbody>{MODELS.map((m) => <tr key={m[0]}>{m.map((c, i) => <td key={i}>{c}</td>)}</tr>)}</tbody>
        </table>
      </div>
      <p className="muted small">
        Quality in the judge's final round, and the test labels for the critic and spoiler check, came from Claude, not a person, so that evidence is weaker. New runs use Flash Low for the critic too, because the winner hit long rate limits. This site's live runs use the free Gemini Flash Lite for speed, a model outside this comparison.
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
