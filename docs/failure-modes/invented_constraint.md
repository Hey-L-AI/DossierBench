# Failure mode: `invented_constraint`

**Family:** Commission / invented constraint

A hard constraint is asserted with no support for the *user* — usually a companion's stated need misattributed to the user, a dish feature read as a personal restriction, or a soft avoidance escalated into a hard constraint.

## Prevalence (transcript-verified GENUINE episodes)

*From the older scalarfix catalogue (2026-08-28: four submissions incl. `mem0`, v1 prompts) — not the paper's numbers; see [`../FAILURE-MODES.md`](../FAILURE-MODES.md) for the released-key catalogue.*

- Total genuine episodes: **16**
- By SUT: `prompted` 6, `notebook` 3, `memory` 2, `mem0` 5
- By backbone: `deepseek-v4-pro-0813` 11, `deepseek-v4-flash-0731` 4, `glm-5.3` 1

## Examples

Each row is a transcript-confirmed instance. `emitted` is what the SUT put on the card; `gold` is what the transcript supports; the quote is the verbatim evidence.

### gen-018 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 5 · field `accessibility_needs`

- **field:** `accessibility_needs`  (quality 5/5)
- **emitted:** `["seating_required"]`
- **gold:** `[]`
- **transcript:** “No access requirements, steps are fine.”
- **note:** invents seating_required from a stylistic table preference; gold [] is STRONG (user denied any access need)

### gen-019 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 5 · field `allergies`

- **field:** `allergies`  (quality 5/5)
- **emitted:** `["shellfish"]`
- **gold:** `[]`
- **transcript:** “ramen and the shellfish are out for me, so one of the other two.”
- **note:** clean case: 'shellfish out for me' is a preference, invented as an allergy; gold allergies=[]

### gen-001 · `memory` / `deepseek-v4-pro-0813` · checkpoint 2 · field `allergies`

- **field:** `allergies`  (quality 5/5)
- **emitted:** `["sesame", "shellfish"]`
- **gold:** `["shellfish"]`
- **transcript:** “no shellfish for me. And he can't have sesame”
- **note:** Sesame is the friend's restriction, not the user's; SUT wrongly attributed it to the user. Clean misattribution example.

### gen-003 · `mem0` / `deepseek-v4-flash-0731` · checkpoint 3 · field `accessibility_needs`

- **field:** `accessibility_needs`  (quality 5/5)
- **emitted:** `["step_free_access"]`
- **gold:** `[]`
- **transcript:** “No access requirements, stairs are fine.”
- **note:** step_free_access asserted despite explicit denial at c2 (gold=[] STRONG). Clean invented-constraint example.

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

### gen-010 · `prompted` / `deepseek-v4-pro-0813` · checkpoint 10 · field `accessibility_needs`

- **field:** `accessibility_needs`  (quality 3/5)
- **emitted:** `["accessible_restroom", "seating_required"]`
- **gold:** `["accessible_restroom"]`
- **transcript:** “Someone once tried to get me waved in through a side door and I went back round and stood in the line properly”
- **note:** seating_required has zero basis; contradicts counter/queue persona

### gen-010 · `notebook` / `deepseek-v4-pro-0813` · checkpoint 10 · field `accessibility_needs`

- **field:** `accessibility_needs`  (quality 3/5)
- **emitted:** `["accessible_restroom", "seating_required", "step_free_access"]`
- **gold:** `["accessible_restroom"]`
- **transcript:** “Someone once tried to get me waved in through a side door and I went back round and stood in the line properly”
- **note:** seating_required has zero basis; contradicts counter/queue persona

### gen-003 · `memory` / `deepseek-v4-pro-0813` · checkpoint 0 · field `accessibility_needs`

- **field:** `accessibility_needs`  (quality 3/5)
- **emitted:** `["seating_required"]`
- **gold:** `[]`
- **transcript:** “Need somewhere that can take a table that size.”
- **note:** seating_required asserted with no basis; genuine invented constraint on a hard-constraint field.
