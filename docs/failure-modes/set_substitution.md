# Failure mode: `set_substitution`

**Family:** Value distortion / set swap

A set field emits members disjoint from gold — the evidenced members are replaced wholesale with wrong ones from the same vocabulary.

## Prevalence (transcript-verified GENUINE episodes)

*From the older scalarfix catalogue (2026-08-28: four submissions incl. `mem0`, v1 prompts) — not the paper's numbers; see [`../FAILURE-MODES.md`](../FAILURE-MODES.md) for the released-key catalogue.*

- Total genuine episodes: **28**
- By SUT: `prompted` 4, `notebook` 3, `memory` 12, `mem0` 9
- By backbone: `gpt-5.6-luna` 12, `deepseek-v4-pro-0813` 6, `deepseek-v4-flash-0731` 5, `glm-5.3-flash` 4, `glm-5.3` 1

## Examples

Each row is a transcript-confirmed instance. `emitted` is what the SUT put on the card; `gold` is what the transcript supports; the quote is the verbatim evidence.

### gen-014 · `prompted` / `glm-5.3` · checkpoint 13 · field `event_types_liked`

- **field:** `event_types_liked`  (quality 4/5)
- **emitted:** `["tasting_menu"]`
- **gold:** `["book_reading"]`
- **transcript:** “oh, that I'd go to. Put me down for one.”
- **note:** Missed clearly-liked book_reading (author evening booked at c13); carried over the tasting_menu fabrication instead.

### gen-022 · `notebook` / `deepseek-v4-pro-0813` · checkpoint 10 · field `event_types_liked`

- **field:** `event_types_liked`  (quality 4/5)
- **emitted:** `["market"]`
- **gold:** `["art_opening"]`
- **transcript:** “yes — I'll go to the opening first and walk down after”
- **note:** At c10 art_opening clearly evidenced (gallery opening, stayed talking to artist an hour); SUT kept fabricated 'market' and missed the real liked event.

### gen-008 · `memory` / `gpt-5.6-luna` · checkpoint 9 · field `event_types_avoided`

- **field:** `event_types_avoided`  (quality 4/5)
- **emitted:** `["tasting_menu"]`
- **gold:** `["film_screening"]`
- **transcript:** “My sister dragged me to one of theirs back in the spring and I was out of that seat well before the end of it.”
- **note:** film_screening avoidance evidenced c6; SUT substituted tasting_menu (a dining-format dislike, wrong item/category).

### gen-007 · `memory` / `glm-5.3-flash` · checkpoint 5 · field `event_types_avoided`

- **field:** `event_types_avoided`  (quality 4/5)
- **emitted:** `["tasting_menu"]`
- **gold:** `["live_rock"]`
- **transcript:** “the birthday one we did at that rock venue — I sat through the whole set counting the songs left”
- **note:** Substituted tasting_menu for the evidenced live_rock avoidance

### gen-004 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 9 · field `event_types_avoided`

- **field:** `event_types_avoided`  (quality 3/5)
- **emitted:** `["wine_tasting"]`
- **gold:** `["live_rock"]`
- **transcript:** “I went to one of those [band] there in February for a friend's birthday and spent most of it checking the time”
- **note:** missed evidenced live_rock; emitted wine_tasting has c0 grounding but not gold

### gen-010 · `prompted` / `gpt-5.6-luna` · checkpoint 11 · field `event_types_liked`

- **field:** `event_types_liked`  (quality 3/5)
- **emitted:** `["film_screening"]`
- **gold:** `["cooking_class", "market", "art_opening"]`
- **transcript:** “we're not eating till after ten, film first”
- **note:** dropped evidenced cooking_class, emitted only invented film_screening (agreement 0)

### gen-001 · `memory` / `deepseek-v4-flash-0731` · checkpoint 7 · field `cuisines_avoided`

- **field:** `cuisines_avoided`  (quality 3/5)
- **emitted:** `["mexican"]`
- **gold:** `["latin_american"]`
- **transcript:** “did a Venezuelan place for a birthday last spring and picked at it the whole night, and I was the same at the Mexican one”
- **note:** User disliked BOTH Venezuelan and Mexican; gold latin_american. mexican-only misses Venezuelan (under-generalization).

### gen-004 · `memory` / `deepseek-v4-pro-0813` · checkpoint 3 · field `event_types_liked`

- **field:** `event_types_liked`  (quality 3/5)
- **emitted:** `["cooking_class"]`
- **gold:** `["dance_night", "museum_late", "tasting_menu"]`
- **transcript:** “a museum thing that stayed open till midnight last month, and a dance floor after ... put me down for anything like the set menu”
- **note:** missed 3 events pinned at c3; cooking_class from c0 pasta-making not in gold

### gen-008 · `memory` / `glm-5.3-flash` · checkpoint 0 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `["seafood"]`
- **gold:** `["bbq"]`
- **transcript:** “There's Smokehouse Yard - brisket and ribs by the plate ... Let's do it - Smokehouse Yard.”
- **note:** User actively chose bbq place (brisket/ribs); seafood only a passing past 'fish place' mention; WEAK grade.

### gen-004 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 4 · field `event_types_liked`

- **field:** `event_types_liked`  (quality 3/5)
- **emitted:** `["art_opening", "supper_club"]`
- **gold:** `["dance_night", "museum_late", "tasting_menu"]`
- **transcript:** “a museum thing that stayed open till midnight last month, and a dance floor after ... put me down for anything like the set menu”
- **note:** missed 3 events pinned at c3; fabricated members

### gen-008 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 5 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `["american"]`
- **gold:** `["bbq"]`
- **transcript:** “Something smoky - brisket, ribs, that sort of thing would do me.”
- **note:** Clear bbq signal (smoky/brisket/ribs); 'american' is an over-broad mislabel of the pick.
