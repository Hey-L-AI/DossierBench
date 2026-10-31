# Failure mode: `missed_constraint`

**Family:** Omission / constraint recall

An active hard constraint (allergy, dietary need, accessibility need, alcohol-free) is explicitly stated in the transcript but the emitted card leaves the field `unknown` — a recall miss on a safety-relevant field.

## Prevalence (transcript-verified GENUINE episodes)

*From the older scalarfix catalogue (2026-08-28: four submissions incl. `mem0`, v1 prompts) — not the paper's numbers; see [`../FAILURE-MODES.md`](../FAILURE-MODES.md) for the released-key catalogue.*

- Total genuine episodes: **53**
- By SUT: `prompted` 6, `notebook` 4, `memory` 16, `mem0` 27
- By backbone: `deepseek-v4-flash-0731` 17, `gpt-5.6-luna` 17, `deepseek-v4-pro-0813` 12, `glm-5.3-flash` 6, `glm-5.3` 1

## Examples

Each row is a transcript-confirmed instance. `emitted` is what the SUT put on the card; `gold` is what the transcript supports; the quote is the verbatim evidence.

### gen-005 · `prompted` / `deepseek-v4-flash-0731` · checkpoint 2 · field `dietary`

- **field:** `dietary`  (quality 5/5)
- **emitted:** `'unknown'`
- **gold:** `["keto"]`
- **transcript:** “I'm off carbs at the moment, so I need something I can eat without the bread or the rice”
- **note:** keto STRONG at c2; persists to c13.

### gen-032 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 7 · field `alcohol_free_required`

- **field:** `alcohol_free_required`  (quality 5/5)
- **emitted:** `'unknown'`
- **gold:** `True`
- **transcript:** “It has to be a place that doesn't serve alcohol — no bar in it, no licence.”
- **note:** hard constraint stated explicitly and STRONG (grade 2) at c7; emitted 'unknown' misses it across 18 checkpoints

### gen-020 · `prompted` / `gpt-5.6-luna` · checkpoint 4 · field `allergies`

- **field:** `allergies`  (quality 5/5)
- **emitted:** `'unknown'`
- **gold:** `["wheat"]`
- **transcript:** “flag no wheat for me”
- **note:** user states own wheat allergy at c4; SUT emitted unknown; clean hard-constraint miss

### gen-011 · `notebook` / `gpt-5.6-luna` · checkpoint 5 · field `accessibility_needs`

- **field:** `accessibility_needs`  (quality 5/5)
- **emitted:** `'unknown'`
- **gold:** `["seating_required"]`
- **transcript:** “I need an actual seat, chair or bench, whatever, but something to sit on”
- **note:** seating_required STRONG (grade2) from c5, reiterated c9; emits unknown despite re-read

### gen-002 · `memory` / `deepseek-v4-flash-0731` · checkpoint 9 · field `alcohol_free_required`

- **field:** `alcohol_free_required`  (quality 5/5)
- **emitted:** `'unknown'`
- **gold:** `True`
- **transcript:** “c9 USER: 'nowhere with a bar. if the drinks list is half the menu it's not for me'; Kinzo 'don't serve any drink stronger than green tea' (session 9)”
- **note:** stateful SUT failed to capture clear c9 no-alcohol constraint at first exposure (memory recording miss). Single-pass SUTs not among failures.

### gen-007 · `memory` / `deepseek-v4-pro-0813` · checkpoint 5 · field `alcohol_free_required`

- **field:** `alcohol_free_required`  (quality 5/5)
- **emitted:** `'unknown'`
- **gold:** `True`
- **transcript:** “I don’t want to be sat somewhere that’s serving drink at all — can you find me a place with no licence on it?”
- **note:** Missed hard alcohol-free constraint at first evidence (c5)

### gen-008 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 0 · field `accessibility_needs`

- **field:** `accessibility_needs`  (quality 5/5)
- **emitted:** `'unknown'`
- **gold:** `["braille_menu"]`
- **transcript:** “do they do a braille menu? I'd rather not have someone read the board out to me while a queue of people listens”
- **note:** Hard accessibility constraint clearly stated at c0 (STRONG); SUT emitted unknown.

### gen-001 · `mem0` / `deepseek-v4-pro-0813` · checkpoint 2 · field `allergies`

- **field:** `allergies`  (quality 5/5)
- **emitted:** `'unknown'`
- **gold:** `["shellfish"]`
- **transcript:** “Yeah, no shellfish for me.”
- **note:** Shellfish stated explicitly (STRONG) at c2; SUT emitted unknown, dropping an active hard constraint.

### gen-010 · `mem0` / `deepseek-v4-pro-0813` · checkpoint 3 · field `accessibility_needs`

- **field:** `accessibility_needs`  (quality 5/5)
- **emitted:** `'unknown'`
- **gold:** `["accessible_restroom"]`
- **transcript:** “I need the loo to actually be usable, step-free, room to turn. Can you check that before I commit to it?”
- **note:** stateful emitted unknown on STRONG hard accessibility constraint stated at c3; safety-relevant

### gen-034 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 11 · field `accessibility_needs`

- **field:** `accessibility_needs`  (quality 4/5)
- **emitted:** `'unknown'`
- **gold:** `["braille_menu"]`
- **transcript:** “And I'll need the menu in braille.”
- **note:** user explicitly states own braille-menu need at c11; SUT emits accessibility_needs=unknown. distinct from companion's guide dog (c2, correctly excluded).

### gen-013 · `prompted` / `gpt-5.6-luna` · checkpoint 11 · field `allergies`

- **field:** `allergies`  (quality 4/5)
- **emitted:** `'unknown'`
- **gold:** `["wheat"]`
- **transcript:** “Just nothing with wheat in it for me, I can't have it.”
- **note:** emits allergies=unknown through c29 despite explicit user wheat allergy at c15 (STRONG). safety-relevant persistent miss.

### gen-005 · `notebook` / `gpt-5.6-luna` · checkpoint 6 · field `dietary`

- **field:** `dietary`  (quality 4/5)
- **emitted:** `[]`
- **gold:** `["keto"]`
- **transcript:** “I'm off carbs at the moment, so I need something I can eat without the bread or the rice”
- **note:** Duplicate of collapse row; keto dropped.
