# Failure mode: `set_partial_swap`

**Family:** Value distortion / set partial

A set field partially overlaps gold but also carries wrong members — a mix of a real hit and a substitution or inflation.

## Prevalence (transcript-verified GENUINE episodes)

*From the older scalarfix catalogue (2026-08-28: four submissions incl. `mem0`, v1 prompts) — not the paper's numbers; see [`../FAILURE-MODES.md`](../FAILURE-MODES.md) for the released-key catalogue.*

- Total genuine episodes: **42**
- By SUT: `prompted` 11, `notebook` 13, `memory` 12, `mem0` 6
- By backbone: `gpt-5.6-luna` 29, `deepseek-v4-pro-0813` 7, `deepseek-v4-flash-0731` 4, `gemini-3.7-flash` 1, `glm-5.3` 1

## Examples

Each row is a transcript-confirmed instance. `emitted` is what the SUT put on the card; `gold` is what the transcript supports; the quote is the verbatim evidence.

### gen-003 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 3 · field `accessibility_needs`

- **field:** `accessibility_needs`  (quality 5/5)
- **emitted:** `["step_free_access"]`
- **gold:** `[]`
- **transcript:** “No access requirements, stairs are fine.”
- **note:** step_free_access asserted despite explicit denial at c2 (gold=[] STRONG). Clean invented-constraint example.

### gen-018 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 21 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 4/5)
- **emitted:** `["burgers", "deli", "italian", "steakhouse"]`
- **gold:** `["mexican", "steakhouse"]`
- **transcript:** “What have you got on the Mexican side though — I'd lean that way myself if it's decent.”
- **note:** misses mexican (explicit like at c21) while inflating with burgers/deli/italian; gold=[mexican,steakhouse]

### gen-018 · `prompted` / `gemini-3.7-flash` · checkpoint 24 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 4/5)
- **emitted:** `["burgers", "mexican"]`
- **gold:** `["mexican", "steakhouse"]`
- **transcript:** “same thing I'd ordered there before, I wasn't about to start experimenting on a Tuesday”
- **note:** drops steakhouse (core like since c0) and adds burgers; gold=[mexican,steakhouse]

### gen-031 · `notebook` / `gpt-5.6-luna` · checkpoint 8 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 4/5)
- **emitted:** `["caribbean", "dim_sum", "ethiopian", "italian", "spanish"]`
- **gold:** `["ramen", "spanish", "caribbean", "vegan_restaurant", "dim_sum", "german", "ethiopian", "cajun"]`
- **transcript:** “Pass on that one. Pasta for twelve is a bit of a shrug, and if I'm spending that I'd rather it went on dim sum, or one of the Ethiopian places”
- **note:** italian included despite user rejecting pasta/italian at the same checkpoint (c8); clean example of a rejected cuisine emitted as liked.

### gen-046 · `notebook` / `gpt-5.6-luna` · checkpoint 14 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 4/5)
- **emitted:** `["chinese", "latin_american", "mexican", "peruvian", "taiwanese"]`
- **gold:** `["peruvian", "moroccan", "taiwanese", "malaysian", "cajun", "vietnamese", "vegan_restaurant"]`
- **transcript:** “my brother's other half is joining partway through for a drink and she won't eat chinese, so nothing that's basically a chinese place”
- **note:** gpt invents chinese (a companion's dislike) and mexican (never mentioned) as user's likes

### gen-046 · `notebook` / `gpt-5.6-luna` · checkpoint 26 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 4/5)
- **emitted:** `["chinese", "filipino", "latin_american", "malaysian", "mexican", "peruvian", "taiwanese", "vietnamese"]`
- **gold:** `["peruvian", "moroccan", "taiwanese", "malaysian", "cajun", "vietnamese", "vegan_restaurant"]`
- **transcript:** “my brother's other half is joining partway through for a drink and she won't eat chinese, so nothing that's basically a chinese place”
- **note:** grade STRONG; gpt still lists chinese/mexican/filipino as likes - real over-inflation, not timing

### gen-010 · `memory` / `deepseek-v4-pro-0813` · checkpoint 2 · field `cuisines_avoided`

- **field:** `cuisines_avoided`  (quality 4/5)
- **emitted:** `["ethiopian", "steakhouse"]`
- **gold:** `["moroccan", "steakhouse", "vegan_restaurant"]`
- **transcript:** “the friend I'm going with can't eat Ethiopian, she got badly ill off it years ago and won't go near it”
- **note:** added companion's ethiopian aversion to user; dropped user's own moroccan

### gen-007 · `memory` / `gpt-5.6-luna` · checkpoint 17 · field `event_types_liked`

- **field:** `event_types_liked`  (quality 4/5)
- **emitted:** `["market", "open_mic"]`
- **gold:** `["art_opening", "open_mic", "food_festival"]`
- **transcript:** “I went to an opening in a print studio ... Ended up at the open mic upstairs afterwards too, which was the better half of the night”
- **note:** open_mic right, market fabricated; missing art_opening and food_festival

### gen-007 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 16 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 4/5)
- **emitted:** `["chinese", "korean"]`
- **gold:** `["bbq", "chinese"]`
- **transcript:** “If there’s a barbecue place that’ll do a long table, or Chinese, that’d land better with me.”
- **note:** Kept chinese but swapped bbq->korean (korean fabricated from a booking)

### gen-010 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 11 · field `event_types_liked`

- **field:** `event_types_liked`  (quality 3/5)
- **emitted:** `["cooking_class", "film_screening"]`
- **gold:** `["cooking_class", "market", "art_opening"]`
- **transcript:** “we're not eating till after ten, film first”
- **note:** added film_screening over-read from one incidental 'film first' mention

### gen-014 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 10 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `["japanese", "malaysian", "peruvian", "thai"]`
- **gold:** `["peruvian", "vietnamese", "japanese", "taiwanese", "thai", "indian", "mediterranean", "ethiopian"]`
- **transcript:** “I want the clatter, tables jammed in, people shouting over each other.”
- **note:** Added malaysian (Kembali chosen for noise not cuisine) and dropped vietnamese.

### gen-019 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 13 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `["mexican", "middle_eastern"]`
- **gold:** `["mexican", "italian"]`
- **transcript:** “Mexican or Italian, either suits me.”
- **note:** mexican captured; italian missed (evidenced c24); middle_eastern is a carried-over fabrication from c10
