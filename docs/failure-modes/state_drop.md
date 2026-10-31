# Failure mode: `state_drop`

**Family:** Omission / state loss

A stateful SUT emits `unknown` or a stale value for a field that was clearly evidenced in an *earlier* session. The fact reached the memory bank once and was lost or overwritten; the single-pass systems, which re-read the window each checkpoint, never show this.

## Prevalence (transcript-verified GENUINE episodes)

*From the older scalarfix catalogue (2026-08-28: four submissions incl. `mem0`, v1 prompts) — not the paper's numbers; see [`../FAILURE-MODES.md`](../FAILURE-MODES.md) for the released-key catalogue.*

- Total genuine episodes: **15**
- By SUT: `prompted` 0, `notebook` 0, `memory` 1, `mem0` 14
- By backbone: `deepseek-v4-flash-0731` 13, `gpt-5.6-luna` 2

## Examples

Each row is a transcript-confirmed instance. `emitted` is what the SUT put on the card; `gold` is what the transcript supports; the quote is the verbatim evidence.

### gen-002 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 11 · field `alcohol_free_required`

- **field:** `alcohol_free_required`  (quality 5/5)
- **emitted:** `'unknown'`
- **gold:** `True`
- **transcript:** “c9 USER: 'nowhere with a bar. if the drinks list is half the menu it's not for me'; Kinzo 'don't serve any drink stronger than green tea' (session 9)”
- **note:** no-alcohol constraint established c9; stateful SUT emits unknown by c11+ (lost across sessions). Only stateful SUTs fail this; single-pass re-read and get it r

### gen-007 · `memory` / `gpt-5.6-luna` · checkpoint 26 · field `alcohol_free_required`

- **field:** `alcohol_free_required`  (quality 4/5)
- **emitted:** `'unknown'`
- **gold:** `True`
- **transcript:** “I don’t want to be sat somewhere that’s serving drink at all — can you find me a place with no licence on it?”
- **note:** Stateful SUT reverted to unknown on constraint evidenced earlier at c5

### gen-002 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 10 · field `cuisines_avoided`

- **field:** `cuisines_avoided`  (quality 4/5)
- **emitted:** `[]`
- **gold:** `["spanish"]`
- **transcript:** “c8 USER: 'No, Spanish isn't it.' (Casa Duero, session 8)”
- **note:** stateful SUT dropped spanish that became evidenced at c8; lost across sessions (state loss).

### gen-004 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 6 · field `event_types_liked`

- **field:** `event_types_liked`  (quality 4/5)
- **emitted:** `[]`
- **gold:** `["dance_night", "museum_late", "tasting_menu"]`
- **transcript:** “a museum thing that stayed open till midnight last month, and a dance floor after ... put me down for anything like the set menu”
- **note:** stateful SUT dropped events evidenced at c3 (museum_late/dance_night/tasting_menu)

### gen-004 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 4 · field `dietary`

- **field:** `dietary`  (quality 4/5)
- **emitted:** `[]`
- **gold:** `["vegan"]`
- **transcript:** “It needs to be vegan though - all of it, no dairy in the pasta, no butter in the sauces”
- **note:** stateful SUT dropped vegan evidenced at c1

### gen-004 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 5 · field `dietary`

- **field:** `dietary`  (quality 4/5)
- **emitted:** `'unknown'`
- **gold:** `["vegan"]`
- **transcript:** “It needs to be vegan though - all of it, no dairy in the pasta, no butter in the sauces”
- **note:** stateful SUT dropped vegan evidenced at c1
