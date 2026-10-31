# Failure mode: `set_underlist`

**Family:** Omission / partial set loss

A set field emits a strict subset of the evidenced members: real, stated members are dropped while the ones kept are correct.

## Prevalence (transcript-verified GENUINE episodes)

*From the older scalarfix catalogue (2026-08-28: four submissions incl. `mem0`, v1 prompts) — not the paper's numbers; see [`../FAILURE-MODES.md`](../FAILURE-MODES.md) for the released-key catalogue.*

- Total genuine episodes: **80**
- By SUT: `prompted` 24, `notebook` 29, `memory` 15, `mem0` 12
- By backbone: `gpt-5.6-luna` 30, `gemini-3.7-flash` 17, `deepseek-v4-flash-0731` 13, `deepseek-v4-pro-0813` 13, `glm-5.3-flash` 5, `glm-5.3` 2

## Examples

Each row is a transcript-confirmed instance. `emitted` is what the SUT put on the card; `gold` is what the transcript supports; the quote is the verbatim evidence.

### gen-033 · `prompted` / `gpt-5.6-luna` · checkpoint 0 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 5/5)
- **emitted:** `["peruvian"]`
- **gold:** `["dim_sum", "peruvian", "vegan_restaurant"]`
- **transcript:** “Peruvian, dim sum, a vegan place, any of those would do me.”
- **note:** same c0 drop of dim_sum & vegan_restaurant, both explicitly stated in the same breath as peruvian

### gen-031 · `notebook` / `gemini-3.7-flash` · checkpoint 12 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 5/5)
- **emitted:** `["caribbean", "dim_sum", "ethiopian"]`
- **gold:** `["ramen", "spanish", "caribbean", "vegan_restaurant", "dim_sum", "german", "ethiopian", "cajun"]`
- **transcript:** “I'd eat ramen quite happily, but I've had all four of those.”
- **note:** grade2 pinned from c12; emitted [caribbean,dim_sum,ethiopian] omits spanish and ramen (strong), later german. Persists to c24 as more accrue.

### gen-014 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 1 · field `cuisines_avoided`

- **field:** `cuisines_avoided`  (quality 4/5)
- **emitted:** `["turkish"]`
- **gold:** `["turkish", "seafood", "mexican"]`
- **transcript:** “Nothing with fish or shellfish in it, and no Turkish.”
- **note:** Dropped seafood, explicitly stated at c1; captured only turkish.

### gen-034 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 1 · field `event_types_liked`

- **field:** `event_types_liked`  (quality 4/5)
- **emitted:** `["live_jazz"]`
- **gold:** `["dance_night", "live_jazz", "film_screening"]`
- **transcript:** “what's coming up in the next few weeks by way of a dance night, or live jazz, or a film screening? Those are the ones I'd want to hear about”
- **note:** persists [live_jazz] through c11 despite all three types explicitly named by user at c5 and c7 (STRONG).

### gen-018 · `prompted` / `gpt-5.6-luna` · checkpoint 8 · field `event_types_liked`

- **field:** `event_types_liked`  (quality 4/5)
- **emitted:** `["market"]`
- **gold:** `["film_screening", "market", "art_opening"]`
- **transcript:** “They did a film in the yard behind it in August too, we took folding chairs and Priya brought a flask.”
- **note:** captures market but drops film_screening evidenced in the same session (c8)

### gen-033 · `prompted` / `gpt-5.6-luna` · checkpoint 2 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 4/5)
- **emitted:** `["peruvian", "portuguese"]`
- **gold:** `["dim_sum", "peruvian", "portuguese", "taiwanese", "vegan_restaurant"]`
- **transcript:** “Peruvian, dim sum, a vegan place, any of those would do me.”
- **note:** emits [peruvian,portuguese] at c2; drops dim_sum & vegan_restaurant (explicit c0) and taiwanese (c1)

### gen-033 · `notebook` / `gpt-5.6-luna` · checkpoint 2 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 4/5)
- **emitted:** `["peruvian", "taiwanese"]`
- **gold:** `["dim_sum", "peruvian", "portuguese", "taiwanese", "vegan_restaurant"]`
- **transcript:** “Peruvian, dim sum, a vegan place, any of those would do me.”
- **note:** emits [peruvian,taiwanese] at c2 but drops dim_sum & vegan_restaurant explicitly stated at c0

### gen-007 · `memory` / `deepseek-v4-flash-0731` · checkpoint 22 · field `event_types_liked`

- **field:** `event_types_liked`  (quality 4/5)
- **emitted:** `["open_mic"]`
- **gold:** `["art_opening", "open_mic", "food_festival"]`
- **transcript:** “I went to an opening in a print studio ... Ended up at the open mic upstairs afterwards too, which was the better half of the night”
- **note:** Only open_mic; missing art_opening and food_festival

### gen-011 · `mem0` / `deepseek-v4-pro-0813` · checkpoint 2 · field `event_types_liked`

- **field:** `event_types_liked`  (quality 4/5)
- **emitted:** `["museum_late"]`
- **gold:** `["live_rock", "wine_tasting", "pub_quiz", "museum_late"]`
- **transcript:** “A wine tasting I'd have said yes to on another night - that, or a quiz, or one of the museum late openings”
- **note:** stateful under-accumulates; at c19 (grade2) still only museum_late, misses wine_tasting/pub_quiz/live_rock

### gen-001 · `mem0` / `gpt-5.6-luna` · checkpoint 7 · field `event_types_liked`

- **field:** `event_types_liked`  (quality 4/5)
- **emitted:** `["live_jazz"]`
- **gold:** `["book_reading", "live_jazz", "market", "open_mic"]`
- **transcript:** “if anything like an open mic or a reading turns up round there later in the week, flag it”
- **note:** By c7 open_mic, book_reading and live_jazz all evidenced; SUT emitted only live_jazz, dropping the rest.

### gen-009 · `mem0` / `gpt-5.6-luna` · checkpoint 5 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 4/5)
- **emitted:** `["peruvian"]`
- **gold:** `["cajun", "caribbean", "filipino", "peruvian", "vietnamese"]`
- **transcript:** “if there's a Peruvian counter or a Filipino one I'll get this lot walking”
- **note:** Emitted only [peruvian], dropping firmly-stated filipino plus caribbean/cajun/vietnamese. Clear underlist.

### gen-004 · `prompted` / `deepseek-v4-flash-0731` · checkpoint 0 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `["italian"]`
- **gold:** `["italian", "brunch", "chinese"]`
- **transcript:** “there's a Sunday morning thing they do too apparently, eggs-free brunch board or whatever”
- **note:** misses brunch (c0/c1); gold weak-grade, lists chinese unevidenced until c7
