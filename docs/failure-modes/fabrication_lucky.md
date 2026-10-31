# Failure mode: `fabrication_lucky`

**Family:** (rare) commission that matched

A commit on an unevidenced field that happened to equal gold. Kept only for completeness; almost always a scoring artifact, not a failure.

## Prevalence (transcript-verified GENUINE episodes)

*From the older scalarfix catalogue (2026-08-28: four submissions incl. `mem0`, v1 prompts) — not the paper's numbers; see [`../FAILURE-MODES.md`](../FAILURE-MODES.md) for the released-key catalogue.*

- Total genuine episodes: **4**
- By SUT: `prompted` 1, `notebook` 0, `memory` 0, `mem0` 3
- By backbone: `glm-5.3-flash` 3, `gpt-5.6-luna` 1

## Examples

Each row is a transcript-confirmed instance. `emitted` is what the SUT put on the card; `gold` is what the transcript supports; the quote is the verbatim evidence.

### gen-009 · `prompted` / `glm-5.3-flash` · checkpoint 0 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `["filipino"]`
- **gold:** `[]`
- **transcript:** “if there's a Peruvian counter or a Filipino one I'll get this lot walking”
- **note:** c0 grade0, no cuisine liking stated; bot merely suggested a Filipino counter for logistics. Filipino is in eventual gold -> lucky, not guess.

### gen-000 · `mem0` / `glm-5.3-flash` · checkpoint 3 · field `novelty_vs_loyalty`

- **field:** `novelty_vs_loyalty`  (quality 3/5)
- **emitted:** `'almost_never_repeats'`
- **gold:** `'almost_never_repeats'`
- **note:** novelty unevidenced throughout (grade 0); committed almost_never_repeats which happens to match gold. Genuine fabrication that got lucky.

### gen-009 · `mem0` / `glm-5.3-flash` · checkpoint 2 · field `novelty_vs_loyalty`

- **field:** `novelty_vs_loyalty`  (quality 3/5)
- **emitted:** `'almost_never_repeats'`
- **gold:** `'almost_never_repeats'`
- **transcript:** “Did Rowan Yard for someone's leaving do last year ... I've had it. I'd rather put the money somewhere none of us have set foot in.”
- **note:** c2 grade0, no venue-repeat evidence yet (that arrives c5). Committed almost_never_repeats which matches eventual gold -> lucky.

### gen-006 · `mem0` / `gpt-5.6-luna` · checkpoint 1 · field `noise_preference`

- **field:** `noise_preference`  (quality 2/5)
- **emitted:** `'needs_quiet'`
- **gold:** `'needs_quiet'`
- **note:** UNEVIDENCED field; committed specific value needs_quiet that happened to match gold. Lucky overcommit, weak example.
