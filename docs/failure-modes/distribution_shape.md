# Failure mode: `distribution_shape`

**Family:** Value distortion / distribution

A distribution field (price band, party size, visit time) is emitted with the wrong shape — most often collapsed to a single-bucket point mass, frozen on the first or latest session rather than aggregated across the history.

## Prevalence (transcript-verified GENUINE episodes)

*From the older scalarfix catalogue (2026-08-28: four submissions incl. `mem0`, v1 prompts) — not the paper's numbers; see [`../FAILURE-MODES.md`](../FAILURE-MODES.md) for the released-key catalogue.*

- Total genuine episodes: **48**
- By SUT: `prompted` 3, `notebook` 3, `memory` 27, `mem0` 15
- By backbone: `gpt-5.6-luna` 17, `glm-5.3-flash` 11, `deepseek-v4-pro-0813` 10, `deepseek-v4-flash-0731` 9, `glm-5.3` 1

## Examples

Each row is a transcript-confirmed instance. `emitted` is what the SUT put on the card; `gold` is what the transcript supports; the quote is the verbatim evidence.

### gen-006 · `prompted` / `glm-5.3-flash` · checkpoint 0 · field `visit_time_distribution`

- **field:** `visit_time_distribution`  (quality 4/5)
- **emitted:** `{"weekday-lunch": 0.0, "weekday-evening": 0.0, "weekend-day": 0.0, "weekend-evening": 1.0, "late-night": 0.0}`
- **gold:** `{"weekday-lunch": 0.06, "weekday-evening": 0.2, "weekend-day": 0.06, "weekend-evening": 0.26, "late-night": 0.42}`
- **transcript:** “Late — we wouldn't be sitting down till about ten.”
- **note:** Mass collapsed to weekend-evening=1.0; misses dominant late-night and lunch visits. STRONG.

### gen-001 · `memory` / `deepseek-v4-flash-0731` · checkpoint 2 · field `party_size_distribution`

- **field:** `party_size_distribution`  (quality 4/5)
- **emitted:** `{"1": 0.0, "2": 1.0, "3-5": 0.0, "6+": 0.0}`
- **gold:** `{"1": 0.0, "2": 0.09, "3-5": 0.51, "6+": 0.4}`
- **transcript:** “Need somewhere for tonight, four of us.”
- **note:** c0 and c1 both party of 4 (STRONG); SUT emitted 2=1.0, missing dominant 3-5 bucket. Memory collapsed to latest session.

### gen-005 · `memory` / `deepseek-v4-flash-0731` · checkpoint 5 · field `party_size_distribution`

- **field:** `party_size_distribution`  (quality 4/5)
- **emitted:** `{"1": 0.0, "2": 1.0, "3-5": 0.0, "6+": 0.0}`
- **gold:** `{"1": 0.0, "2": 0.05, "3-5": 0.54, "6+": 0.41}`
- **transcript:** “Looking for somewhere for tonight, four of us”
- **note:** STRONG grade; four 4-tops seen c0-c3 (3-5 bucket) plus later 8s/9s. Emitted {2:1.0} is genuinely wrong shape.

### gen-005 · `memory` / `deepseek-v4-flash-0731` · checkpoint 8 · field `party_size_distribution`

- **field:** `party_size_distribution`  (quality 4/5)
- **emitted:** `{"1": 0.5, "2": 0.5, "3-5": 0.0, "6+": 0.0}`
- **gold:** `{"1": 0.0, "2": 0.05, "3-5": 0.54, "6+": 0.41}`
- **transcript:** “there'll be eight of us”
- **note:** STRONG grade; parties of 4/8/9 seen. Emitted {1:0.5,2:0.5} genuinely wrong shape. Persists to c13.

### gen-007 · `memory` / `deepseek-v4-flash-0731` · checkpoint 8 · field `party_size_distribution`

- **field:** `party_size_distribution`  (quality 4/5)
- **emitted:** `{"1": 0.0, "2": 1.0, "3-5": 0.0, "6+": 0.0}`
- **gold:** `{"1": 0.0, "2": 0.12, "3-5": 0.48, "6+": 0.4}`
- **transcript:** “eight of us, sit-down dinner”
- **note:** One-hot on recent 'table for two' (c8) despite repeated large-group bookings of 8-11

### gen-009 · `memory` / `deepseek-v4-flash-0731` · checkpoint 5 · field `price_band_distribution`

- **field:** `price_band_distribution`  (quality 4/5)
- **emitted:** `{"<\u00a315": 0.0, "\u00a315-30": 0.0, "\u00a330-50": 1.0, "\u00a350+": 0.0}`
- **gold:** `{"<\u00a315": 0.55, "\u00a315-30": 0.37, "\u00a330-50": 0.08, "\u00a350+": 0.0}`
- **transcript:** “under a tenner each ideally”
- **note:** Collapsed all mass to 30-50 (only the c5 £30-40 session), ignoring cheap history (<a tenner c1/c3, £15-25 c2). Failed to aggregate.

### gen-011 · `memory` / `deepseek-v4-pro-0813` · checkpoint 1 · field `visit_time_distribution`

- **field:** `visit_time_distribution`  (quality 4/5)
- **emitted:** `{"weekday-lunch": 0.0, "weekday-evening": 1.0, "weekend-day": 0.0, "weekend-evening": 0.0, "late-night": 0.0}`
- **gold:** `{"weekday-lunch": 0.05, "weekday-evening": 0.19, "weekend-day": 0.05, "weekend-evening": 0.24, "late-night": 0.47}`
- **transcript:** “we won't be starting till gone ten at night”
- **note:** stateful froze on weekday-evening (first c1 visit); ignores late-night dominance through c19 (grade2)

### gen-005 · `mem0` / `gpt-5.6-luna` · checkpoint 5 · field `visit_time_distribution`

- **field:** `visit_time_distribution`  (quality 4/5)
- **emitted:** `{"weekday-lunch": 0.6666666666666666, "weekday-evening": 0.0, "weekend-day": 0.0, "weekend-evening": 0.0, "late-night": 0.3333333333333333}`
- **gold:** `{"weekday-lunch": 0.05, "weekday-evening": 0.1, "weekend-day": 0.27, "weekend-evening": 0.49, "late-night": 0.09}`
- **transcript:** “Looking for somewhere for tonight, four of us”
- **note:** STRONG grade; visits are mostly weekend (Sat) lunches/eves. Weekday-heavy emit is genuinely wrong shape.

### gen-005 · `mem0` / `gpt-5.6-luna` · checkpoint 6 · field `visit_time_distribution`

- **field:** `visit_time_distribution`  (quality 4/5)
- **emitted:** `{"weekday-lunch": 0.5, "weekday-evening": 0.5, "weekend-day": 0.0, "weekend-evening": 0.0, "late-night": 0.0}`
- **gold:** `{"weekday-lunch": 0.05, "weekday-evening": 0.1, "weekend-day": 0.27, "weekend-evening": 0.49, "late-night": 0.09}`
- **transcript:** “Looking for somewhere for tonight, four of us”
- **note:** STRONG grade; weekday-heavy emit wrong vs weekend-dominant gold.

### gen-005 · `mem0` / `gpt-5.6-luna` · checkpoint 7 · field `visit_time_distribution`

- **field:** `visit_time_distribution`  (quality 4/5)
- **emitted:** `{"weekday-lunch": 0.4, "weekday-evening": 0.4, "weekend-day": 0.0, "weekend-evening": 0.0, "late-night": 0.2}`
- **gold:** `{"weekday-lunch": 0.05, "weekday-evening": 0.1, "weekend-day": 0.27, "weekend-evening": 0.49, "late-night": 0.09}`
- **transcript:** “Looking for somewhere for tonight, four of us”
- **note:** STRONG grade; weekday-skewed emit wrong vs weekend gold.

### gen-005 · `mem0` / `gpt-5.6-luna` · checkpoint 8 · field `visit_time_distribution`

- **field:** `visit_time_distribution`  (quality 4/5)
- **emitted:** `{"weekday-lunch": 0.3, "weekday-evening": 0.5, "weekend-day": 0.0, "weekend-evening": 0.0, "late-night": 0.2}`
- **gold:** `{"weekday-lunch": 0.05, "weekday-evening": 0.1, "weekend-day": 0.27, "weekend-evening": 0.49, "late-night": 0.09}`
- **transcript:** “Looking for somewhere for tonight, four of us”
- **note:** STRONG grade; weekday-heavy emit wrong vs weekend gold.

### gen-005 · `mem0` / `gpt-5.6-luna` · checkpoint 10 · field `visit_time_distribution`

- **field:** `visit_time_distribution`  (quality 4/5)
- **emitted:** `{"weekday-lunch": 0.4, "weekday-evening": 0.5, "weekend-day": 0.0, "weekend-evening": 0.0, "late-night": 0.1}`
- **gold:** `{"weekday-lunch": 0.05, "weekday-evening": 0.1, "weekend-day": 0.27, "weekend-evening": 0.49, "late-night": 0.09}`
- **transcript:** “Looking for somewhere for tonight, four of us”
- **note:** STRONG grade; weekday-heavy emit wrong vs weekend-dominant gold.
