# Distribution fields: the score is near-blind to shape

*Measured 2026-08-26 on the first scored SUT emissions (`work/sut-emissions/`, the four
submissions × three models over the five pilot subjects `gen-000..004`). Diagnostic:
`python3 -m persona_twin.shape`. No model calls — this reads runs that already happened.*

## The claim

The three distribution fields — `price_band_distribution`, `party_size_distribution`,
`visit_time_distribution` — exist to measure *spread*. The field docs say so outright:
a distribution "not a mode … preserves bimodal spend that a point value would flatten"
(`fields.py`). But the metric that scores them, `1 − JSD`, is scale-compressed
(`metrics.jensen_shannon_divergence`, and the note in `metrics.weak_abstention_credit`):
a spike on the right mode scores almost as well as the true spread. So **the headline score
barely distinguishes a twin that produced a distribution from one that guessed the mode and
put all its mass there.** The mean 1 − JSD hides a difference in kind.

## The evidence

Pooled over all committed twin distributions (each twin's highest-checkpoint commit,
scored against the persona), n ≈ 48–50 per field:

| field | mean 1 − JSD | twin support | persona support | twin spike rate |
|---|---|---|---|---|
| `price_band` | 0.871 | **1.66** | 2.64 | **48 %** |
| `party_size` | 0.941 | 2.23 | 2.38 | 2 % |
| `visit_time` | 0.798 | 3.28 | 5.00 | 4 % |

*Support = bins carrying more than 2 % mass. Spike = one bin holds ≥ 90 %.*

On `price_band` the twins reach a mean 1 − JSD (0.871) **at parity with a perfect
occasion-counter** (~0.865 raw; see below) — yet they get there by collapsing to a single
bin **almost half the time**, mean support 1.66 against the persona's 2.64. Same mean,
categorically different object. The metric forgives it.

The three fields fail differently, and the shape view is what separates them:

- **`party_size`** — the twins reproduce the shape (support 2.23 ≈ 2.38, ~0 % spikes). It
  is a two-value field (1s and 2s); there is little spread to lose. A counter adds nothing.
- **`visit_time`** — the twins under-cover the tails (support 3.28 vs the persona's full 5),
  but rarely spike. They capture the mode and the second bin and drop the thin occasions.
- **`price_band`** — the twins collapse to a mode-spike. This is the real shape failure, and
  it is invisible in the mean.

### Why a spike is not free — the reading-rule tie-in

A spike puts `0.00` on the bins it abandons, and under
`fields.DISTRIBUTION_READING_RULE` a `0.00` bin is not "rare", it is "never". A twin whose
`price_band` reads `{<£15: 1.0}` is asserting the person **never** spends in the other three
bands — a claim a reader can contradict from a single session showing otherwise. So the
48 % spike rate is not only a fidelity gap the JSD score misses; it is latent
misread/leak risk. Shape is where the score's blindness has teeth.

## What we did about it — a shape diagnostic, not a counter

> **Amendment — 2026-08-26 (ADR-0063).**
> The SUTs **now do** build the distributions from a counted-occasion histogram. Nothing measured
> below is withdrawn — the ceiling numbers are exactly why the histogram is framed as a **shape**
> lever and not a score one. This note decided a counter is not worth adding *to move the score*;
> the histogram was then added *to fix the shape*, which is the axis this whole note is about. The two are the
> same finding read on its two axes: the counter cannot beat the model's prior on `1 − JSD` (so the
> headline is unchanged and unchased), and it can spread a distribution the one-shot estimate
> collapses (so the support/spike gap below is what it targets). The paragraph that follows records
> the reasoning *as of this measurement*; read it as the case the amendment answers, not as current
> behaviour.

We did **not** add an occasion-counter to the SUTs. A counter was measured against its own
ceiling first (`python3 -m persona_twin.shape` compares twins to gold; the counter ceiling
was computed separately): a *perfect* tally of the gold-labelled occasions scores 0.865 /
0.941 / 0.863 raw on the three fields — at or below what the twins already post — because
each transcript exposes a median of only 4–7 legible occasions per field. Counting a handful
of noisy draws cannot beat a model's prior, and where it helps the shape (a smoothed counter
reaches persona-level support) it does not move the JSD score, which is blind to support.

Instead we report shape **as a separate axis**, apart from the score:

- `metrics.distribution_shape(field_id, dist)` — a pure helper returning support, zero-bins,
  peak mass, entropy, and a spike flag for any one distribution. It is deliberately **not**
  consumed by `metrics.agreement`; the scored path is unchanged.
- `python3 -m persona_twin.shape` — an aggregate persona-vs-twin table (support, entropy,
  spike %, abstain %) over the cached emissions. Add `--subject gen-000` for per-bin ASCII
  histograms, `--run prompted/openai` to filter.

### Worked example (`gen-000`, prompted / gpt-5.6-luna)

```
price_band_distribution
         persona              prompted/openai/gpt-5.6-luna
<£15     █████████      0.62   ██████████████ 1.00  <- spike
£15-30   █████          0.34   ·              0.00
£30-50   █              0.04   ·              0.00
£50+     ·              0.00   ·              0.00
         support 3 vs 1  |  spike: yes  |  zero-bins 1 vs 3  |  1-JSD 0.78
```

The persona spreads across three price bands; the twin spikes on the cheapest and asserts
"never" on the other three. The `1 − JSD` of 0.78 barely registers the difference — which is
the whole point of this note.

## Non-goal

The **diagnostic** here changes no score, and is not meant to. It is a reading instrument: it lets
a twin's distributions be compared to the persona's on the axis the fields were built to
measure, separately from the JSD number that cannot see it. If measuring "can the twin build
a real distribution" ever becomes a *scored* goal, the lever is the **metric** (a support or
entropy term, or a less-compressed distance), not the SUT — the occasion histogram added later
targets the shape a reader sees, not the number the scorer reads, and does not change that.
