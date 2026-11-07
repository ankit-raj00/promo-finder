# How we chose the models

The pipeline has five LLM steps, and each was tested separately under rules fixed in `EVAL_PLAN.md`.

| Step | What it does | Chosen model | Why |
|---|---|---|---|
| Judge | Picks the clips | `agy/gemini-3.8-flash-low` | Tied on quality with Flash Lite, won the tie-break on speed |
| Critic | Scores each clip from its text alone | `agy/claude-sonnet-4-6` | Best overall score: separates promos from random clips, consistent, fast |
| Ordering | Sets the post-first top 10 | `agy/gemini-3.8-flash-low` | Tied for most stable, and twice as fast as the other |
| Montages | Joins moments across episodes | `agy/gemini-3.8-flash-low` | 9 of 9 valid in the test, highest critic score |
| Spoiler check | Flags clips that give away twists | `agy/gemini-3.8-flash-low` | The only model to catch all 4 high-risk test clips |

**Confidence:** moderate. These are small tests on one show, and no editor or real audience data was involved. Details for the judge come first; the other four are at the end.

# Step 1: The judge

## Why it matters

The script gives the time of every line. The model decides which lines a clip starts and ends on, which lines are the hook and cliffhanger, and why the clip works. Code then looks up the exact timestamps and quotes.

## The steps

### 1. Wrote the rules first
Candidates, test set, gates, score weights and the final decision rule went into `EVAL_PLAN.md` before the full comparison. Only a 2-episode test of the setup had run. This stops a winner being picked first and justified afterwards.

### 2. Fixed the test
- **Episodes:** 10, covering the season and known hard cases
  - Spread across the season: 1, 9, 19, 27, 36, 46
  - Quiet, reference says no promo: 6, 17
  - Keyword filter found nothing: 14, 39
- **Same conditions:** every model got the same prompt and validator, with no per-model tuning.
- **Repeat runs:** each model ran the set twice, 20 calls per model, to measure consistency.
- **Reference:** `data/promos_final`, our earlier validated output.

### 3. Set pass/fail gates
| Gate | Threshold |
|---|---|
| Calls completed | 100% |
| Refusals | 0 |
| Pick vs. skip agrees with the reference | at least 80% |
| Picks dropped by the validator | at most 10% |

"Pick vs. skip" asks whether the model made the same basic call as the reference on an episode: find a promo there, or skip it.

### 4. Set the score weights
| Measure | Weight | What it means |
|---|---|---|
| Overlap | 40% | Time overlap between the model's top pick and the best reference promo |
| Consistency | 25% | Agreement between the model's two runs |
| First-try validity | 15% | Share of calls needing no retry |
| Speed | 10% | Seconds per episode, relative to the fastest |
| Tokens | 10% | Output tokens, relative to the lowest |

### 5. Ran the models
| Rank | Model | Gates | Score | Overlap | Consistency | First try | Pick vs. skip | Dropped | Avg sec | Avg output tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `gemini-3.8-flash-low` | pass | 0.753 | 0.729 | 0.685 | 60% | 100% | 0% | 8.8 | 712 |
| 2 | `gemini-3.1-flash-lite` | pass | 0.731 | 0.640 | 0.835 | 100% | 100% | 0% | 9.5 | 3,073 |
| 3 | `gemini-3.8-flash-high` | pass | 0.680 | 0.793 | 0.793 | 100% | 95% | 0% | 195.5 | 7,367 |
| 4 | `gemini-3.8-flash-medium` | pass | 0.650 | 0.703 | 0.840 | 95% | 100% | 0% | 118.9 | 7,934 |
| 5 | `claude-sonnet-4-6` | pass | 0.553 | 0.629 | 0.538 | 80% | 95% | 6.1% | 51.5 | 2,356 |

Model IDs start with `agy/`. Three more candidates could not be tested, so these results say nothing about their quality:
- `gemini-3.1-pro-low` and `claude-sonnet-5-5-medium`: the gateway lists them but refused them as unavailable.
- `gpt-oss-120b-medium`: it rejected the JSON request format our evaluator sends.

### 6. Shortlisted two
The plan sends the top 2 passing models to a blind review: Flash Low and Flash Lite.

### 7. Ran a blind review
1. Code took each finalist's top pick per episode from run 1, giving 16 clips, 8 from each model.
2. Code shuffled the clips and kept the model names in a separate key file.
3. An LLM judge (Claude Opus 5.5) read each clip's script text and scored it from 1 to 5.
4. The scores were saved to `outputs/model_eval/blind_scores_llm_judge.csv` before the key was opened.

| Average score (out of 5) | Flash Lite | Flash Low |
|---|---|---|
| Hook | 3.62 | 3.00 |
| Cliffhanger | 3.75 | 3.75 |
| Standalone sense | 3.50 | 4.00 |
| **Overall** | **3.62** | **3.58** |

### 8. Applied the decision rule
The rule, set in advance: the higher average wins, but if the gap is under 0.3 points, the faster model wins. The gap was 0.04, so the faster model won: Flash Low, at 8.8 seconds against 9.5.

## Changes made along the way

All of these are also recorded in `EVAL_PLAN.md`. The rules, weights and gates did not change.

| Change | When | Why |
|---|---|---|
| Added Flash Lite | During the first run, before any scores | It's the lightest Gemini option |
| Added Flash Medium, GPT-OSS and Claude Sonnet 4.6 | After Flash High and Flash Low scores were visible | Two original candidates were unavailable; chosen only on availability |
| LLM judge instead of a human reviewer | Final step | Requested by the project owner |

## Limits

- **The tie-break decided it.** The speed gap is under one second. Flash Lite has better hooks, higher consistency and needed no retries; Flash Low has better standalone sense and uses fewer tokens.
- **The weights affect the finalists.** Ignoring speed and tokens, the order is Flash High, Flash Medium, Flash Lite, Flash Low, Claude Sonnet 4.6. Flash High may be the stronger choice if waiting about 22 times longer is acceptable.
- **Overlap measures similarity, not quality.** The reference came from Claude models and isn't editor ground truth, which can favour Claude models.
- **The judge isn't neutral.** It's one Claude model reading text, not footage. It also helped create the reference and had already worked with these episodes.
- **The sample is small.** Ten episodes from one show, so small differences aren't meaningful.
- **No dollar comparison.** These models ran through an Antigravity login via OmniRoute, so tokens stand in for cost. OmniRoute's own notes say Antigravity's terms prohibit access through a proxy. The official Gemini API would need its own rerun and pricing.

# Step 2: The critic

**Why it exists:** the judge rated its own picks too generously. In its first pass, 73 of 74 hooks scored 4 or 5, and weak hooks reached the top.

**Test:** 24 clips scored twice: 12 strong promos and 12 random control clips, drawn with a fixed seed. A good critic scores the promos higher. Separation is the chance that a promo beats a control, where 0.5 is guessing and 1.0 is perfect.

| Rank | Model | Gates | Score | Separation | Promo avg | Control avg | Consistency | Avg sec | Avg output tokens |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `claude-sonnet-4-6` | pass | 0.940 | 0.889 | 3.99 | 3.04 | 0.993 | 2.21 | 40 |
| 2 | `gemini-3.8-flash-low` | pass | 0.892 | 0.858 | 3.62 | 2.96 | 0.986 | 3.02 | 39 |
| 3 | `gemini-3.8-flash-medium` | pass | 0.747 | 0.906 | 3.72 | 2.90 | 0.976 | 6.94 | 493 |
| 4 | `gemini-3.1-flash-lite` | pass | 0.728 | 0.823 | 3.81 | 3.11 | 0.931 | 3.57 | 722 |

**Checked:** consistency looked too high, so we checked for a response cache. Run 2's text and token counts differed from run 1's, and the gateway reported a cache miss on a test request, so the numbers stand.

**Result on all 78 promos:** hook scores now spread across 2 to 5 (9, 23, 37 and 9 promos for scores 2, 3, 4 and 5).

**Change after testing:** the test didn't measure rate limits. Later, a full-command test on 3 episodes stalled for over 30 minutes on Claude Sonnet 4.6 cooldowns, and episodes failed. The submitted 78 promos keep their Sonnet scores, but new runs default to the runner-up, `gemini-3.8-flash-low`, which has a separate quota. Reliability should have been a gate; it would be for the next test.

**Limits:** the promos' labels came from Claude, which may favour the Claude model. Flash Medium actually separated best (0.906) and lost on speed and tokens. With only 24 clips, these separation gaps are small.

# Step 3: Ordering

**Test:** the same top 15 list, 3 runs per model. Stability is the average overlap between runs' top 10s.

| Rank | Model | Gates | Score | Stability | Avg sec | Avg output tokens |
|---|---|---|---|---|---|---|
| 1 | `gemini-3.8-flash-low` | pass | 0.927 | 0.879 | 7.28 | 374 |
| 2 | `claude-sonnet-4-6` | pass | 0.765 | 0.879 | 14.76 | 539 |
| 3 | `gemini-3.1-flash-lite` | pass | 0.680 | 0.768 | 7.60 | 2,745 |
| 4 | `gemini-3.8-flash-medium` | pass | 0.492 | 0.717 | 33.85 | 3,984 |

**Limit:** this measures stable, rule-following answers, not whether the order is right. It also can't judge fit: the critic undervalues reveals that depend on context, so a human still reviews the final top 10.

# Step 4: Montages

**Test:** the same promo list, 3 runs per model, each asked for 3 montages. Valid montages were then scored by the critic.

| Rank | Model | Gates | Score | Valid | Critic avg | Avg sec | Avg output tokens |
|---|---|---|---|---|---|---|---|
| 1 | `gemini-3.8-flash-low` | pass | 0.932 | 9 of 9 | 4.15 | 9.69 | 742 |
| 2 | `claude-sonnet-4-6` | pass | 0.766 | 7 of 9 | 4.09 | 21.39 | 897 |
| 3 | `gemini-3.8-flash-medium` | FAIL: validity | 0.468 | 3 of 9 | 4.00 | 144.19 | 9,756 |
| 4 | `gemini-3.1-flash-lite` | FAIL: validity | 0.417 | 1 of 9 | 3.33 | 11.11 | 3,819 |

**Limit:** 9 of 9 doesn't mean it never fails. In the final run after the test, Flash Low proposed 3 montages and 1 was dropped because its hook wasn't in the first 3 seconds. The validator caught it.

# Step 5: Spoiler check

**Why it exists:** the judge rated Episode 47's villain punishment as no spoiler risk, and that clip reached the top 10.

**Test:** 20 clips checked twice, including all 4 labelled high risk. Labels came from the earlier calibration pass.

| Rank | Model | Gates | Score | High-risk recall | High clips caught | Accuracy | Consistency | False highs | Avg sec | Avg output tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `gemini-3.8-flash-low` | pass | 0.833 | 1.0 | 4 of 4 | 0.75 | 1.0 | 2 | 3.41 | 101 |
| 2 | `gemini-3.1-flash-lite` | pass | 0.638 | 0.625 | 3 of 4 | 0.75 | 0.8 | 2 | 3.31 | 862 |
| 3 | `claude-sonnet-4-6` | FAIL: high recall | 0.770 | 0.5 | 2 of 4 | 0.90 | 1.0 | 0 | 2.63 | 31 |

**Limits:** Claude Sonnet 4.6 was more accurate overall but missed half the high-risk clips, and the gate puts catching spoilers first. On all 78 promos, the winner marked 12 high and 48 low, which looks too cautious, so treat it as a flag for an editor. The labels are one Claude judge's opinion.

## What would make this stronger

1. An editor scores the same blind clips.
2. Compare picks against promos Kuku's editors actually chose.
3. Re-run on another show.
4. Compare models on real post results, such as 3-second hold and click-through.

## Files

| File | What it holds |
|---|---|
| `EVAL_PLAN.md` | The rules and every change to them |
| `eval_models.py` | The comparison code |
| `outputs/model_eval/summary.md`, `summary.json` | The score table |
| `outputs/model_eval/<model>/run1`, `run2` | Raw picks for each model |
| `outputs/model_eval/blind_review.csv` | The 16 shuffled clips |
| `outputs/model_eval/blind_scores_llm_judge.csv` | The judge's scores |
| `outputs/model_eval/blind_key_do_not_open.json` | Which model made each clip |
| `outputs/model_eval/blind_result.json` | The unblinded averages |
| `outputs/step_eval/` | Test sets, raw runs and summaries for the critic, ordering, montage and spoiler tests |
| `critic.py`, `spoiler_check.py`, `eval_critic.py`, `eval_steps.py`, `eval_spoiler.py` | The steps and the code for these tests |
