import React, { useEffect, useState } from "react";
import Overview from "./Overview.jsx";
import { PostFirst, AllPromos } from "./Results.jsx";
import TryIt from "./TryIt.jsx";
import Pipeline from "./Pipeline.jsx";
import Quality from "./Quality.jsx";

const TABS = [
  ["overview", "Overview"],
  ["post-first", "Post first"],
  ["all", "All promos"],
  ["try", "Try it"],
  ["how", "How it works"],
  ["quality", "Quality"],
];

function tabFromHash() {
  const hash = window.location.hash.slice(1);
  return TABS.some(([id]) => id === hash) ? hash : "overview";
}

export default function App() {
  const [tab, setTab] = useState(tabFromHash);
  const [results, setResults] = useState(null);
  const [config, setConfig] = useState(null);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    Promise.all([fetch("/api/results"), fetch("/api/config")])
      .then(async ([r, c]) => {
        if (!r.ok || !c.ok) throw new Error("bad response");
        setResults(await r.json());
        setConfig(await c.json());
      })
      .catch(() => setLoadError(true));
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = (id) => {
    window.location.hash = id;
    window.scrollTo(0, 0);
  };

  return (
    <>
      <header className="topbar">
        <div className="wrap topbar-inner">
          <a className="brand" href="#overview">
            <span className="brand-mark" aria-hidden="true">▶</span> Promo Finder
          </a>
          <a className="repo-link" href="https://github.com/ankit-raj00/promo-finder">Code on GitHub</a>
        </div>
        <nav className="wrap tabs" role="tablist" aria-label="Sections">
          {TABS.map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "tab active" : "tab"} onClick={() => go(id)}>
              {label}
            </button>
          ))}
        </nav>
      </header>

      <main className="wrap main">
        {loadError && <div className="notice error">The site couldn't load its data. Please refresh the page.</div>}
        {!loadError && !results && <p className="muted" role="status">Loading...</p>}
        {results && config && (
          <>
            {tab === "overview" && <Overview results={results} go={go} />}
            {tab === "post-first" && <PostFirst results={results} />}
            {tab === "all" && <AllPromos results={results} />}
            {tab === "try" && <TryIt config={config} />}
            {tab === "how" && <Pipeline />}
            {tab === "quality" && <Quality results={results} />}
          </>
        )}
      </main>

      <footer className="wrap footer">
        Built for Problem 1 of Kuku's AI Generalist assignment. Scripts are read as text only, and an editor makes the final call.
      </footer>
    </>
  );
}
