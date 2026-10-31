# Failure mode: `boolean_flip`

**Family:** Value distortion / boolean

The `alcohol_free_required` boolean is emitted with the wrong polarity.

## Prevalence (transcript-verified GENUINE episodes)

*From the older scalarfix catalogue (2026-08-28: four submissions incl. `mem0`, v1 prompts) — not the paper's numbers; see [`../FAILURE-MODES.md`](../FAILURE-MODES.md) for the released-key catalogue.*

- Total genuine episodes: **7**
- By SUT: `prompted` 0, `notebook` 0, `memory` 0, `mem0` 7
- By backbone: `deepseek-v4-flash-0731` 7

## Examples

Each row is a transcript-confirmed instance. `emitted` is what the SUT put on the card; `gold` is what the transcript supports; the quote is the verbatim evidence.

### gen-007 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 11 · field `alcohol_free_required`

- **field:** `alcohol_free_required`  (quality 4/5)
- **emitted:** `False`
- **gold:** `True`
- **transcript:** “I don’t want to be sat somewhere that’s serving drink at all — can you find me a place with no licence on it?”
- **note:** Emitted false (active flip), not merely missing; constraint clearly evidenced at c5

### gen-002 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 10 · field `alcohol_free_required`

- **field:** `alcohol_free_required`  (quality 3/5)
- **emitted:** `False`
- **gold:** `True`
- **transcript:** “c9 USER: 'nowhere with a bar. if the drinks list is half the menu it's not for me'; Kinzo 'don't serve any drink stronger than green tea' (session 9)”
- **note:** stateful SUT failed to capture clear c9 no-alcohol constraint at first exposure (memory recording miss). Single-pass SUTs not among failures.
