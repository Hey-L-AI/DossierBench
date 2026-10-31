# Failure mode: `ordinal_far`

**Family:** Value distortion / ordinal

An ordinal-scale field is emitted two or more levels from gold — frequently the opposite pole (e.g. `queue_is_part_of_it` read as `leaves_if_line`). Off-by-one is *not* counted; the scorer forgives it, so every episode here is a large mis-read.

## Prevalence (transcript-verified GENUINE episodes)

*From the older scalarfix catalogue (2026-08-28: four submissions incl. `mem0`, v1 prompts) — not the paper's numbers; see [`../FAILURE-MODES.md`](../FAILURE-MODES.md) for the released-key catalogue.*

- Total genuine episodes: **101**
- By SUT: `prompted` 22, `notebook` 13, `memory` 37, `mem0` 29
- By backbone: `deepseek-v4-flash-0731` 31, `gpt-5.6-luna` 30, `glm-5.3-flash` 20, `deepseek-v4-pro-0813` 15, `gemini-3.7-flash` 4, `glm-5.3` 1

## Examples

Each row is a transcript-confirmed instance. `emitted` is what the SUT put on the card; `gold` is what the transcript supports; the quote is the verbatim evidence.

### gen-019 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 16 · field `planning_style`

- **field:** `planning_style`  (quality 5/5)
- **emitted:** `'decides_same_day'`
- **gold:** `'books_weeks_ahead'`
- **transcript:** “I've stopped chancing it. Four of them are already in the calendar for the spring.”
- **note:** user books weeks/months ahead throughout (c11 Sept booked May, c22, c28); emitted decides_same_day is opposite extreme

### gen-024 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 3 · field `noise_preference`

- **field:** `noise_preference`  (quality 5/5)
- **emitted:** `'fine_with_lively'`
- **gold:** `'needs_quiet'`
- **transcript:** “you could hear the whole room at once — everyone shouting over each other, and the guy next to me had to repeat himself three times. Not that again.”
- **note:** user is textbook needs_quiet across c0/c2/c6/c8; emitted fine_with_lively is opposite direction

### gen-006 · `memory` / `gpt-5.6-luna` · checkpoint 4 · field `wait_tolerance`

- **field:** `wait_tolerance`  (quality 5/5)
- **emitted:** `'leaves_if_line'`
- **gold:** `'queue_is_part_of_it'`
- **transcript:** “got there about forty minutes before it opened and there were already six people ahead of me, so I'd want to be out the door earlier this time.”
- **note:** Emitted leaves_if_line is extreme opposite of queue_is_part_of_it (agreement 0.0). STRONG at c4.

### gen-007 · `memory` / `glm-5.3-flash` · checkpoint 3 · field `wait_tolerance`

- **field:** `wait_tolerance`  (quality 5/5)
- **emitted:** `'fifteen_min_max'`
- **gold:** `'queue_is_part_of_it'`
- **transcript:** “I’d rather just stand in the line like everyone else”
- **note:** Opposite end; persona repeatedly insists on queueing as part of the fun

### gen-002 · `prompted` / `deepseek-v4-flash-0731` · checkpoint 0 · field `solo_dining_comfort`

- **field:** `solo_dining_comfort`  (quality 4/5)
- **emitted:** `'takeaway_or_counter'`
- **gold:** `'enjoys_solo'`
- **transcript:** “c1 USER: 'sat at the counter with a salmon set and my book for about an hour' (session 1)”
- **note:** clear comfortable solo dining (book, hour-long, repeated 'just me'); takeaway_or_counter under-reads enjoys_solo by >=2. Near-universal misread across models.

### gen-021 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 15 · field `participation_vs_spectating`

- **field:** `participation_vs_spectating`  (quality 4/5)
- **emitted:** `'seeks_hands_on'`
- **gold:** `'joins_if_pushed'`
- **transcript:** “and then I was fine, I did the whole thing. Just wasn't going to put my hand up on my own.”
- **note:** STRONG joins_if_pushed (c1,c11); over-read bot-prompted class sign-up at c15 as seeks_hands_on

### gen-034 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 1 · field `formality_ceiling`

- **field:** `formality_ceiling`  (quality 4/5)
- **emitted:** `'counter_service'`
- **gold:** `'smart_casual'`
- **transcript:** “Somewhere with a bit of occasion to it, smart-casual, table booked properly rather than turning up.”
- **note:** emits counter_service through c11 despite user saying 'smart-casual' at c2; ceiling understated by >=2 steps.

### gen-040 · `prompted` / `gpt-5.6-luna` · checkpoint 4 · field `formality_ceiling`

- **field:** `formality_ceiling`  (quality 4/5)
- **emitted:** `'casual_sit_down'`
- **gold:** `'white_tablecloth'`
- **transcript:** “we did my dad's birthday last spring at one of those proper white-tablecloth places — linen, the little crumb scraper — and I enjoyed that a lot more than I thought I would.”
- **note:** By c4 model had explicit white_tablecloth enjoyment (c1); capped ceiling at casual_sit_down, 2 steps low. Genuine ceiling underestimate.

### gen-042 · `prompted` / `gpt-5.6-luna` · checkpoint 2 · field `formality_ceiling`

- **field:** `formality_ceiling`  (quality 4/5)
- **emitted:** `'counter_service'`
- **gold:** `'smart_casual'`
- **transcript:** “Nice enough that it feels like a proper sit-down, though, not a counter.”
- **note:** c1 shows a booked proper sit-down restaurant; model capped ceiling at counter_service, 2 steps below gold smart_casual.

### gen-049 · `prompted` / `gpt-5.6-luna` · checkpoint 5 · field `formality_ceiling`

- **field:** `formality_ceiling`  (quality 4/5)
- **emitted:** `'casual_sit_down'`
- **gold:** `'white_tablecloth'`
- **transcript:** “Proper white-tablecloth place, if something can take a table that size.”
- **note:** c2/c3 give explicit white-tablecloth/white-glove preference; model tracked c5's 'nothing formal' and capped at casual_sit_down, 2 steps low.

### gen-006 · `prompted` / `glm-5.3-flash` · checkpoint 0 · field `deal_seeking`

- **field:** `deal_seeking`  (quality 4/5)
- **emitted:** `'ignores_price'`
- **gold:** `'looks_for_deals'`
- **transcript:** “Once we've settled on the place I'll see if they've got anything on.”
- **note:** Emitted ignores_price is opposite end of looks_for_deals; user seeks offers and picks cheap spots. STRONG at c0.

### gen-018 · `notebook` / `gpt-5.6-luna` · checkpoint 9 · field `adventurousness`

- **field:** `adventurousness`  (quality 4/5)
- **emitted:** `'orders_unfamiliar'`
- **gold:** `'sticks_to_known'`
- **transcript:** “same thing I'd ordered there before, I wasn't about to start experimenting on a Tuesday”
- **note:** user strongly sticks_to_known; SUT emits opposite end orders_unfamiliar - likely conflates venue-novelty with dish adventurousness
