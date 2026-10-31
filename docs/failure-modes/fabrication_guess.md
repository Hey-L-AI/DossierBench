# Failure mode: `fabrication_guess`

**Family:** Commission / premature commitment

The SUT commits a positive, non-default value on a field the transcript has not yet evidenced — guessing instead of abstaining. The benchmark prices every such commit as a fabrication regardless of whether it later turns out right.

## Prevalence (transcript-verified GENUINE episodes)

*From the older scalarfix catalogue (2026-08-28: four submissions incl. `mem0`, v1 prompts) — not the paper's numbers; see [`../FAILURE-MODES.md`](../FAILURE-MODES.md) for the released-key catalogue.*

- Total genuine episodes: **206**
- By SUT: `prompted` 50, `notebook` 36, `memory` 46, `mem0` 74
- By backbone: `glm-5.3-flash` 81, `deepseek-v4-flash-0731` 42, `deepseek-v4-pro-0813` 37, `gpt-5.6-luna` 32, `glm-5.3` 12, `gemini-3.7-flash` 2

## Examples

Each row is a transcript-confirmed instance. `emitted` is what the SUT put on the card; `gold` is what the transcript supports; the quote is the verbatim evidence.

### gen-000 · `mem0` / `deepseek-v4-pro-0813` · checkpoint 2 · field `cuisines_avoided`

- **field:** `cuisines_avoided`  (quality 5/5)
- **emitted:** `["filipino"]`
- **gold:** `["cajun", "turkish", "korean"]`
- **transcript:** “he doesn't eat Filipino food, so nothing from that place”
- **note:** filipino is the brother's dislike, not the user's; SUT misattributes a third party's constraint to the user. cuisines_avoided unevidenced (grade 0).

### gen-007 · `prompted` / `deepseek-v4-flash-0731` · checkpoint 12 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 4/5)
- **emitted:** `["korean"]`
- **gold:** `[]`
- **transcript:** “If there’s a barbecue place that’ll do a long table, or Chinese, that’d land better with me.”
- **note:** Fabricated 'korean' from a single Korean booking; real likes are bbq/chinese

### gen-000 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 0 · field `deal_seeking`

- **field:** `deal_seeking`  (quality 4/5)
- **emitted:** `'notices_value'`
- **gold:** `'plans_around_specials'`
- **note:** deal_seeking unevidenced at c0 (grade 0); only a price cap given. Positive commit is a genuine guess.

### gen-023 · `notebook` / `deepseek-v4-pro-0813` · checkpoint 1 · field `event_types_liked`

- **field:** `event_types_liked`  (quality 4/5)
- **emitted:** `["tasting_menu"]`
- **gold:** `[]`
- **transcript:** “There's Ardley House — a five-course tasting menu, lamb and beef shin, a cheese course before dessert.”
- **note:** No event pref stated at c1; model invented event_types_liked from a restaurant menu-format token (tasting menu). Field evidenced later, so not hidden-prior.

### gen-006 · `memory` / `deepseek-v4-flash-0731` · checkpoint 2 · field `cuisines_avoided`

- **field:** `cuisines_avoided`  (quality 4/5)
- **emitted:** `["spanish"]`
- **gold:** `["ramen"]`
- **note:** spanish is brother's one-off aversion, not user's standing pref; misattributed companion constraint. Field unevidenced.

### gen-000 · `memory` / `glm-5.3-flash` · checkpoint 0 · field `deal_seeking`

- **field:** `deal_seeking`  (quality 4/5)
- **emitted:** `'looks_for_deals'`
- **gold:** `'plans_around_specials'`
- **note:** Emitted looks_for_deals. deal_seeking unevidenced at c0 (grade 0); only a price cap given. Positive commit is a genuine guess.

### gen-007 · `memory` / `glm-5.3-flash` · checkpoint 6 · field `participation_vs_spectating`

- **field:** `participation_vs_spectating`  (quality 4/5)
- **emitted:** `'seeks_hands_on'`
- **gold:** `'joins_if_pushed'`
- **transcript:** “I’m not going to volunteer myself, but I’m also useless at saying no when someone asks me straight.”
- **note:** Fabricated proactive value on unevidenced field; persona only joins if pushed, won't volunteer

### gen-007 · `memory` / `glm-5.3-flash` · checkpoint 12 · field `participation_vs_spectating`

- **field:** `participation_vs_spectating`  (quality 4/5)
- **emitted:** `'joins_readily'`
- **gold:** `'joins_if_pushed'`
- **transcript:** “I’m not going to volunteer myself, but I’m also useless at saying no when someone asks me straight.”
- **note:** Fabricated proactive value on unevidenced field; persona only joins if pushed, won't volunteer

### gen-000 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 2 · field `event_types_avoided`

- **field:** `event_types_avoided`  (quality 4/5)
- **emitted:** `["live_rock"]`
- **gold:** `["live_jazz"]`
- **note:** No genre ever stated; user rejected a 'band' venue for noise reasons. live_rock is invented. Field unevidenced at c2 (grade 0 until c5).

### gen-004 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 0 · field `allergies`

- **field:** `allergies`  (quality 4/5)
- **emitted:** `["eggs"]`
- **gold:** `[]`
- **transcript:** “No allergies, nothing else to work around.”
- **note:** invented eggs allergy; misread c0 'eggs-free brunch board' (a dish), user states no allergies

### gen-009 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 5 · field `dietary`

- **field:** `dietary`  (quality 4/5)
- **emitted:** `["dairy_free"]`
- **gold:** `[]`
- **transcript:** “one of the four can't have dairy, so it needs to actually work for her”
- **note:** Attributed a companion's dairy-free need to the user; gold user dietary is []. Clean invented_constraint.

### gen-006 · `prompted` / `deepseek-v4-flash-0731` · checkpoint 2 · field `noise_preference`

- **field:** `noise_preference`  (quality 3/5)
- **emitted:** `'prefers_low_hum'`
- **gold:** `'needs_quiet'`
- **note:** UNEVIDENCED field; committed prefers_low_hum (adjacent to gold needs_quiet). Overcommit fabrication.
