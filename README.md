# Promo Finder

Reads a microdrama screenplay and suggests the best 20-60 second promo clips, ranked. A good promo opens on a hook in the first 3 seconds, ends on a cliffhanger, and makes sense to someone who has never seen the show.

Built for Problem 1 of Kuku's AI Generalist assignment.

**Live app: https://13-206-20-22.sslip.io**

The app shows a sample show I wrote, so anyone can try it. Kuku's scripts are confidential, so neither they nor the results that quote them are in this repo or on the hosted site. Those results are in the raw outputs linked on the submission form.

To browse the 50-episode results in the app, unzip the raw outputs so the `outputs` folder sits in the repo's main folder, then run the app on your computer.

## Try it

1. Open the live app and look at **Post first**: the top 10 clips, each with a reason for its slot.
2. Download the editor sheet. It's a CSV with every timestamp, hook and cliffhanger, plus empty columns for an editor's decision and notes.
3. Open **Try it**, pick a sample episode, paste a script, or upload `.txt` files, then press **Find promos**.
4. Open **How it works** for a flowchart of the pipeline, and **Quality** for where it fails.

No key is needed on the live app. Sample episodes show saved results from a real AI run, and pasted or uploaded scripts get live AI judgment through a free Gemini key (`gemini-flash-lite-latest`). It can run out for a while, and that model wasn't part of the comparison in `docs/MODEL_SELECTION.md`, so its picks can differ from the submitted results. Don't paste confidential scripts, because they go to Google.

Running it yourself with no key, pasted or uploaded scripts get keyword-only picks, labelled as such. To use your own model, enter these under **Use your own model** on the Try it tab. They're used for your session only and are never saved.

| To use | Base URL | Models |
|---|---|---|
| Anthropic | leave empty | a Claude model ID |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | a Gemini model ID |
| Anything OpenAI-compatible | your endpoint | that endpoint's model ID |

## Run it on your computer

Needs Python 3.10 or newer.

```bash
pip install -r requirements.txt
python tests/test_pipeline.py     # offline tests, no key needed
python -m uvicorn web.api:app --port 8000     # then open http://localhost:8000
```

To run a whole show from the command line:

```bash
cp .env.example .env        # then fill in your endpoint and key
python run_show.py "<folder of .txt episodes>" runs/<show_name>
```

Results land in `runs/<show_name>/outputs/`, starting with `ranked_promos.md`. If an episode fails the others continue, a re-run picks up where it stopped, and rate limits are waited out.

## Project layout

```
promo_finder/           the pipeline, one folder per stage
  ingest/               read scripts, line IDs, keyword hints
  judge/                judge, critic, spoiler check, rule checks
  rank/                 score, post-first order, montages
  export/               the editor sheet
  reports/              quality report, agreement, site stats
  llm/                  one place for model calls
  pipeline.py           runs every step on a show
web/                    the site: api.py and frontend/ (React)
evaluation/             the model comparison tests
tests/                  offline tests
docs/                   evaluation rules and model results
samples/, demo/         the sample show and its results
deploy/                 server configuration
run_show.py             the command to run a whole show
```

To add a pipeline step, add a module in the matching folder and one line in `pipeline.py`. To swap a provider, change only `llm/client.py`.

## How it works

The rule behind the design: **AI makes the judgment calls, and code does anything that can be checked.**

| Step | Who | File | What happens |
|---|---|---|---|
| 1. Read | Code | `ingest/parser.py` | Turns a script into scenes and timed lines, and copes with format quirks |
| 2. Hints | Code | `ingest/heuristics.py` | Finds keyword matches. They're only hints, because keywords alone pick badly |
| 3. Pick | AI | `judge/scorer.py` | The judge reads the episode and answers with line IDs |
| 4. Check | Code | `judge/validate.py` | Checks the length, hook and cliffhanger rules against the script |
| 5. Second opinion | AI | `judge/critic.py` | Scores the clip from its text alone, like a stranger would |
| 6. Spoilers | AI | `judge/spoiler.py` | Asks whether the clip gives away a twist |
| 7. Score | Code | `rank/ranker.py` | Applies a formula anyone can read |
| 8. Post first | AI | `rank/post_first.py` | Compares the top 15 side by side and gives a reason for each top 10 slot |
| 9. Montages | AI + code | `rank/montages.py` | Joins moments across episodes, and code drops invalid ones |
| 10. Decide | Person | `export/editor_sheet.py` | An editor approves the picks from the exported sheet and cuts the footage |

A live run on the site's **Try it** tab goes through steps 1-9, the same as `run_show.py` on a whole show. The site's headline numbers come from `demo/input_pack_stats.json`, which `reports/stats.py` computes from the result files.

Why some of these choices were made:

- **Line IDs instead of text (step 3).** The model sees `e12 [00:17.00-00:19.00] JULIAN (O.S.) (Cold): Put it down.` and answers `e12`. Code then looks up the time and the words, so a quote or timestamp can't be invented. An early version let the model write quotes, and a full check found 12 of 138 merged or reworded.
- **Retries with the error attached (step 4).** A pick that breaks a rule goes back to the model with the reason, up to twice, and is then dropped.
- **A separate critic (step 5).** The judge rated nearly all its own hooks 4 or 5 out of 5, which is useless for ranking.
- **The score (step 7).** It's 35% hook + 30% cliffhanger + 20% makes sense alone + 15% good length, minus a penalty for spoiler or platform risk.

## How the models were chosen

Each AI step has its own test, with the rules written down before the full comparison. See `docs/EVAL_PLAN.md` for the rules and every change made along the way, and `docs/MODEL_SELECTION.md` for the results and how to read them. The test scripts are in `evaluation/`.

## Where it picks badly

The tool reads text, so it can't judge acting, music or pacing. It can miss a great moment, choose clips that need backstory, return overlapping clips from one episode, and flag too many spoilers. A re-run also moves some start and end points. No editor has checked its picks yet, which is the biggest gap. `reports/quality.py` measures these from any run.

## Hosting

The live app runs on one small AWS server, using `deploy/promo-finder.service` for the app and `deploy/Caddyfile` for HTTPS. Its free Gemini key is in a root-only file on the server, never in this repo, and the site limits it to 5 episodes per run and 30 per visitor per hour.

It sets `PROMO_FINDER_DEMO_ONLY=1`, so a public copy serves only the sample show, even if `outputs/` exists.

The site is React (`web/frontend/`) on a small FastAPI backend (`web/api.py`). The built site is committed in `web/frontend/dist`, so running it needs only Python. To change it, run `npm install` and `npm run build` in `web/frontend/`.

## How the submitted results were produced

Kuku's results came from running this pipeline on all 50 episodes, using the models in `.env.example`, except that the critic was `agy/claude-sonnet-4-6` (`docs/MODEL_SELECTION.md` explains why that default changed). The models ran through OmniRoute on an Antigravity login. OmniRoute's own notes say Antigravity's terms prohibit proxy access, so a production version should use an official API key.

In this repo, `demo/outputs/` holds the sample show's results. `outputs/` is ignored by git, so the raw results can't be committed by accident.
