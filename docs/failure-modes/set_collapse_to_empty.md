# Failure mode: `set_collapse_to_empty`

**Family:** Omission / set loss

A set field that was evidenced is emitted as empty/`unknown` — the whole set is dropped, not just trimmed.

## Prevalence (transcript-verified GENUINE episodes)

*From the older scalarfix catalogue (2026-08-28: four submissions incl. `mem0`, v1 prompts) — not the paper's numbers; see [`../FAILURE-MODES.md`](../FAILURE-MODES.md) for the released-key catalogue.*

- Total genuine episodes: **8**
- By SUT: `prompted` 0, `notebook` 1, `memory` 0, `mem0` 7
- By backbone: `deepseek-v4-flash-0731` 7, `gpt-5.6-luna` 1

## Examples

Each row is a transcript-confirmed instance. `emitted` is what the SUT put on the card; `gold` is what the transcript supports; the quote is the verbatim evidence.

### gen-005 · `notebook` / `gpt-5.6-luna` · checkpoint 6 · field `dietary`

- **field:** `dietary`  (quality 4/5)
- **emitted:** `[]`
- **gold:** `["keto"]`
- **transcript:** “I'm off carbs at the moment, so I need something I can eat without the bread or the rice”
- **note:** Duplicate of collapse row; keto dropped.

### gen-003 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 6 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 4/5)
- **emitted:** `[]`
- **gold:** `["greek"]`
- **transcript:** “Greek is exactly the sort of thing I was hoping you'd say.”
- **note:** greek explicitly stated at c6 (gold=[greek]); SUT emits []. Drops a just-evidenced preference to empty.

### gen-005 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 5 · field `allergies`

- **field:** `allergies`  (quality 4/5)
- **emitted:** `[]`
- **gold:** `["peanuts"]`
- **transcript:** “I need to know there's no peanut in what I'm eating, so it has to be somewhere the counter can actually tell me”
- **note:** Duplicate of the collapse row; peanuts dropped. missed_constraint fits.

### gen-007 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 6 · field `cuisines_avoided`

- **field:** `cuisines_avoided`  (quality 4/5)
- **emitted:** `[]`
- **gold:** `["soul_food"]`
- **transcript:** “Not soul food, sorry. Last time a group of us went to one ... I sat there pushing it round the plate.”
- **note:** Dropped well-evidenced soul_food avoidance to empty

### gen-002 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 8 · field `cuisines_avoided`

- **field:** `cuisines_avoided`  (quality 3/5)
- **emitted:** `[]`
- **gold:** `["spanish"]`
- **transcript:** “c8 USER: 'No, Spanish isn't it.' (Casa Duero, session 8)”
- **note:** dropped/omitted spanish exactly when it became evidenced at c8 (WEAK).

### gen-002 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 8 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `[]`
- **gold:** `["sushi", "american"]`
- **transcript:** “c3 USER: 'salmon and avocado rolls, which is what I got the last three times I was in there' (session 3)”
- **note:** stateful SUT collapsed an evidenced set (sushi+american) to empty; sushi evidenced across many prior sessions.

### gen-005 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 5 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `[]`
- **gold:** `["french", "italian"]`
- **transcript:** “the little French place off Newington Green... That's the direction I'd rather go in tonight”
- **note:** French chosen repeatedly by c5; collapse to [] drops a clearly-liked cuisine. Grade WEAK but direction unambiguous.
