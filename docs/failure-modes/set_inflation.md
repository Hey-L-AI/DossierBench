# Failure mode: `set_inflation`

**Family:** Commission / set over-assertion

A set field emits every evidenced member *plus* unsupported extras — often venue types the user merely visited read as cuisines they *like* (visit-to-like conflation). Seen only on single-pass systems.

## Prevalence (transcript-verified GENUINE episodes)

*From the older scalarfix catalogue (2026-08-28: four submissions incl. `mem0`, v1 prompts) — not the paper's numbers; see [`../FAILURE-MODES.md`](../FAILURE-MODES.md) for the released-key catalogue.*

- Total genuine episodes: **15**
- By SUT: `prompted` 7, `notebook` 8, `memory` 0, `mem0` 0
- By backbone: `deepseek-v4-pro-0813` 9, `gpt-5.6-luna` 6

## Examples

Each row is a transcript-confirmed instance. `emitted` is what the SUT put on the card; `gold` is what the transcript supports; the quote is the verbatim evidence.

### gen-025 · `notebook` / `deepseek-v4-pro-0813` · checkpoint 2 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 4/5)
- **emitted:** `["burgers", "italian", "steakhouse"]`
- **gold:** `["burgers"]`
- **transcript:** “I've been after a burger ever since — nobody'll argue with that.”
- **note:** gold burgers only; SUT added italian/steakhouse from booked/grill venues, not stated likes

### gen-018 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 6 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `["burgers", "deli", "steakhouse"]`
- **gold:** `["steakhouse"]`
- **transcript:** “came back to the burger, because everything else I couldn't have told you what was in it”
- **note:** adds burgers+deli to likes; gold=steakhouse; user picks familiar plain food, not a burger/deli preference

### gen-018 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 12 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `["burgers", "deli", "italian", "steakhouse"]`
- **gold:** `["steakhouse"]`
- **transcript:** “I'd rather sit down and order something I've had before and know I like.”
- **note:** burgers+deli+italian inflation of steakhouse-only gold; venues eaten treated as preferences

### gen-025 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 4 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `["burgers", "italian", "pizza", "steakhouse"]`
- **gold:** `["burgers"]`
- **transcript:** “I've been after a burger ever since — nobody'll argue with that.”
- **note:** gold burgers only; SUT added italian/pizza/steakhouse from chosen venues, not stated likes

### gen-018 · `prompted` / `gpt-5.6-luna` · checkpoint 21 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `["burgers", "deli", "italian", "mexican", "steakhouse"]`
- **gold:** `["mexican", "steakhouse"]`
- **transcript:** “came back to the burger, because everything else I couldn't have told you what was in it”
- **note:** correct core (mexican,steakhouse) inflated with three visited venues burgers/deli/italian

### gen-014 · `notebook` / `deepseek-v4-pro-0813` · checkpoint 10 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `["japanese", "malaysian", "peruvian", "thai", "vietnamese"]`
- **gold:** `["peruvian", "vietnamese", "japanese", "taiwanese", "thai", "indian", "mediterranean", "ethiopian"]`
- **transcript:** “I want the clatter, tables jammed in, people shouting over each other.”
- **note:** Added malaysian; user picked Kembali for noise, not Malay cuisine. All evidenced cuisines present, so inflation not swap.

### gen-018 · `notebook` / `deepseek-v4-pro-0813` · checkpoint 1 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `["burgers", "mexican", "steakhouse"]`
- **gold:** `["steakhouse"]`
- **transcript:** “same thing I'd ordered there before, I wasn't about to start experimenting on a Tuesday”
- **note:** adds burgers+mexican from one Camberton Yard visit; gold=steakhouse only; conflates venue visited with liked cuisine

### gen-018 · `notebook` / `deepseek-v4-pro-0813` · checkpoint 2 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `["burgers", "deli", "mexican", "steakhouse"]`
- **gold:** `["steakhouse"]`
- **transcript:** “came back to the burger, because everything else I couldn't have told you what was in it”
- **note:** adds deli (Marlow's Deli) & burgers as likes; user eats burgers as safe default, not preference

### gen-018 · `notebook` / `deepseek-v4-pro-0813` · checkpoint 12 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `["burgers", "deli", "italian", "mexican", "steakhouse"]`
- **gold:** `["steakhouse"]`
- **transcript:** “I'd rather sit down and order something I've had before and know I like.”
- **note:** adds italian (Nonna Rosa, chosen reluctantly as 'plainer'), deli, burgers, mexican; gold=steakhouse at c12

### gen-018 · `notebook` / `gpt-5.6-luna` · checkpoint 12 · field `cuisines_liked`

- **field:** `cuisines_liked`  (quality 3/5)
- **emitted:** `["deli", "italian", "steakhouse"]`
- **gold:** `["steakhouse"]`
- **transcript:** “I'd rather sit down and order something I've had before and know I like.”
- **note:** adds deli+italian as likes; italian was the reluctant 'plainer' pick; gold=steakhouse
