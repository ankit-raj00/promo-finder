# Model selection plan

The pipeline has four LLM steps, and each gets its own test and model. The first section below covers the judge. The other three are at the end of this file.

Fixed before any full comparison run. Only a 2-episode smoke test of the harness had run at this point.

## Decision
Choose the model for the judging step (per-episode promo selection).

## Candidates
- `agy/gemini-3.8-flash-high`
- `agy/gemini-3.8-flash-low`
- `agy/gemini-3.1-pro-low`
- `agy/claude-sonnet-5-5-medium`
- `agy/gemini-3.1-flash-lite` (added while the first four were running, before any scores existed; the rules, weights and gates did not change)

### Change after the first run
`agy/gemini-3.1-pro-low` and `agy/claude-sonnet-5-5-medium` could not be tested: the gateway lists them, but refuses them as "not available in the active live catalog". A one-line availability check was then run on 13 other listed models. Three worked and were added: `agy/gemini-3.8-flash-medium`, `agy/gpt-oss-120b-medium` and `agy/claude-sonnet-4-6`.

These were added after the scores for the Flash High and Flash Low runs were visible, chosen only on availability. The rules, weights and gates did not change.

`agy/gpt-oss-120b-medium` was then not tested either: it rejected the JSON request format the evaluator sends. That is a limit of the test harness, not a verdict on the model.

## Test set
10 fixed episodes, chosen to cover the season and known hard cases:
1, 9, 19, 27, 36, 46 (spread across the season), 6, 17 (quiet, reference says no promo), 14, 39 (keyword filter found nothing).

Every model gets the same prompt, the same validator and no per-model tuning. Each runs the set twice.

## Reference
`data/promos_final`, our existing judged output (Claude models, checked by the validator). It's a baseline, not editor ground truth, and it may favour Claude models. So it can shortlist models but doesn't decide alone.

## Gates (fail any one and the model is out)
| Gate | Threshold |
|---|---|
| Completed calls | 100% |
| Refusals | 0 |
| Pick vs. skip agrees with the reference | at least 80% |
| Picks dropped by the validator | at most 10% |

## Score (ranks the models that pass)
| Metric | Weight | Meaning |
|---|---|---|
| Overlap with reference | 40% | Mean time overlap (IoU) of the top pick with the best reference promo |
| Consistency | 25% | Agreement between the model's two runs |
| First-try validity | 15% | Share of calls needing no retry |
| Speed | 10% | Average seconds per episode, relative to the fastest |
| Token use | 10% | Average output tokens, relative to the lowest |

The weights are a judgment call: quality first, then reliability, then efficiency.

## Final decision
1. The top 2 models that pass go to a blind human review. Their clips are shuffled and labelled A/B, and a person scores each from 1 to 5 on hook, cliffhanger and standalone sense.
2. The higher average human score wins. If the gap is under 0.3 points, the faster model wins.

### Change to the final review
The blind review was done by an LLM judge (Claude Opus 5.5) instead of a person, at the project owner's request. The judge saw the clips' script text without model names and saved its scores before the key was opened. This is weaker than human review: Claude models also made the reference data, and the judge reads text, not footage.

## Limits
Ten episodes from one show is a small sample, so small score differences aren't meaningful. One reviewer isn't an editor panel. Results should be rechecked on a new show and against editors' choices.

---

# Other LLM steps

Fixed before these tests ran. Steps run in this order, because each uses the previous winner's output.

Candidates for all three: `agy/gemini-3.1-flash-lite`, `agy/gemini-3.8-flash-low`, `agy/gemini-3.8-flash-medium`, `agy/claude-sonnet-4-6`. These are the models that worked in the judge test, leaving out `agy/gemini-3.8-flash-high` to keep usage light, since it was the slowest there by far (195.5 seconds per episode).

Shared rules: a model must pass every gate. The highest score wins. If the top two are within 0.03, the faster one wins.

## Step: Critic (scores a clip from its text alone)

**Test set:** 24 clips, each scored twice. 12 are promos from `data/promos_final` that the calibration pass rated at least 8 out of 10 on hook plus cliffhanger. 12 are control clips: random 30-45 second windows that don't overlap any promo. Both are drawn with a fixed seed.

**Gates:** 100% of calls complete, 0 refusals, at least 90% valid on the first try.

| Measure | Weight | Meaning |
|---|---|---|
| Separation | 50% | Chance that a promo scores above a control (AUC; 0.5 is guessing, 1.0 is perfect) |
| Consistency | 25% | How closely the two runs' scores match |
| Speed | 12.5% | Seconds per call, relative to the fastest |
| Tokens | 12.5% | Output tokens, relative to the lowest |

**Limit:** the labels are imperfect. A random window can be good by chance, and the promos' ratings came from Claude.

## Step: Ordering (sets the post-first top 10)

**Test:** the same top 15 list, 3 runs per model.

**Gates:** every run returns 10 valid, distinct promos, with no high-risk spoiler or platform clip in the top 3.

| Measure | Weight | Meaning |
|---|---|---|
| Stability | 60% | Average overlap between runs' top 10s (Jaccard) |
| Speed | 20% | Relative to the fastest |
| Tokens | 20% | Relative to the lowest |

**Limit:** this measures stable, rule-following answers, not whether the order is right. That needs real post results.

## Step: Montages (joins moments across episodes)

**Test:** the same list of promos, 3 runs per model, each asked for 3 montages.

**Gates:** every run completes, and at least 50% of proposed montages pass the validator.

| Measure | Weight | Meaning |
|---|---|---|
| Validity | 40% | Share of proposed montages that pass the validator |
| Quality | 40% | Average critic score of the valid montages, out of 5 |
| Speed | 10% | Relative to the fastest |
| Tokens | 10% | Relative to the lowest |

## Step: Spoiler check (marks clips that give away twists)

Added after the final ranking let a likely spoiler through (Episode 47, the villain's punishment, rated no risk by the judge).

**Candidates:** `agy/claude-sonnet-4-6`, `agy/gemini-3.8-flash-low`, `agy/gemini-3.1-flash-lite`.

**Test set:** 20 promos from `data/promos_final`, each checked twice: all 4 labelled high risk, plus 8 low and 8 none drawn with a fixed seed. The labels come from the earlier calibration pass (`outputs/global_calibration.json`).

**Gates:** 100% of calls complete, 0 refusals, and at least 3 of the 4 high-risk clips caught.

| Measure | Weight | Meaning |
|---|---|---|
| High-risk recall | 40% | Share of high-risk clips marked high, across both runs |
| Accuracy | 30% | Share of all clips matching the label exactly |
| Consistency | 10% | Share of clips given the same answer in both runs |
| Speed | 10% | Relative to the fastest |
| Tokens | 10% | Relative to the lowest |

**Limit:** the labels are one Claude judge's opinion, and only 4 clips are high risk, so this is a small check, not proof.
