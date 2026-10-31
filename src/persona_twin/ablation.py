"""Abstention-incentive analysis: what does the scoring rule actually pay for? (spec-v0 §11)

This module is the instrument that produced the **2026-08-10 rescoring** (ADR-0001, -0002,
-0003). It ran against the previous rule and found four defects; it now verifies the
properties the replacement rule was chosen to have. Both halves matter — the findings are the
reasoning behind the ADRs, and the verifications are what stops the rule drifting back.

## What it found in the previous rule

1. **§11's criterion was and remains unfalsifiable as written.** It asks that a system
   abstaining in `U(t)` beat the same system guessing there. A commit in `U` earns nothing in
   `A(t)` (`scorer.py`, the `Grade.UNEVIDENCED` branch) and costs the fabrication term, so the
   delta is mechanical on every dataset for every system. **The rescoring did not fix this and
   was not meant to** — it is a property of the `U`-band rule itself, which is conceded prior
   art (§0.1) and stays. What the rescoring fixed is that the *weak* band, where §0.1's
   surviving claim actually lives, now has a falsifiable criterion of its own (see 4).
2. **The fabrication penalty was a rate over `U(t)`,** so each fabrication got cheaper the
   less the transcript evidenced. The exchange rate between "missed an evidenced field" and
   "fabricated" ran from 1.24 in the sparse tier to 0.21 in rich — crossing 1, meaning the
   scorer paid an unsure system to guess in exactly the tier §7.4 builds so abstention is the
   defensible move. Fixed by ADR-0002: fabrications are priced on the same denominator as
   agreement, making the exchange rate `a_S/λ` and independent of the band mix.
3. **λ = 1 sat on the clamp floor.** Still partly true, and still reported — see
   `clamp_kills_gradient`. `f_raw` exists so every comparison here can be made on the
   incentive-bearing number rather than the clamped one.
4. **Weak-band abstention was not credited, only dropped from the average.** Committing there
   paid iff `a_W > c_S·a_S` — a comparison against the system's own accuracy elsewhere, so two
   systems with identical weak-band behaviour on identical evidence got opposite verdicts, and
   blanket abstention was optimal for every competent system. Fixed by ADR-0001: committing on
   weak now pays iff `a_W > WEAK_CREDIT`, which is a comparison against the evidence.

## What it verifies about the current rule

* **`exchange_rate` is `a_S/λ`** — constant across tiers. `test_ablation.py` pins this.
* **`break_even_detection`** is now `p* = n_S·a_S / (λ·n_U + n_S·a_S)`. `n_U` appears, and in
  the right direction: more unevidenced fields to protect makes attempting detection pay
  sooner. Under the old rule `n_U` was absent entirely.
* **`weak_band_commit_pays`** depends only on `a_W` vs the credit.
* **`abstain_everything_floor`** — crediting weak abstention gives a do-nothing card a
  non-zero score, which is the cost of ADR-0001 and the reason `f_skill` is reported.

`NoisyDetector` reads the evidence map and ground truth directly, as `baselines.EvidenceOracle`
does. Like the oracle it is an **instrument for testing the scorer, never a published
baseline** — it exists to put a controlled system on the other side of the metric so the
metric's incentives can be measured without a dataset or a model.

    python -m persona_twin.ablation
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Sequence

from . import fields, metrics, scorer
# Reused rather than re-implemented: a second plausible-value sampler would drift from the
# one the baselines use, and then the two would disagree about what a plausible card is.
from .baselines import _random_value
from .card import PersonaCard
from .evidence import EvidenceMap, Grade
from .fields import UNKNOWN
from .subject import Subject

#: Fidelity is computed over the 20 preference fields only (spec §8.2), so every band size
#: here is a partition of those 20 and the 4 constraints never appear.
N_PREFERENCE: int = len(fields.PREFERENCE_IDS)

#: The distribution fields the scorer's low-N carve-out (§8.1a) treats specially. Kept out of
#: the ablation's WEAK band by `synthetic_subject`; shared with the scorer's own set.
_DISTRIBUTION_ID_SET = scorer._DISTRIBUTION_ID_SET

#: λ values the spec requires reporting at (§8.3, §11), post-rescoring.
REPORTED_LAMBDAS: tuple[float, ...] = scorer.REPORTED_LAMBDAS


# --- band mixes -------------------------------------------------------------


@dataclass(frozen=True)
class BandMix:
    """A partition of the 20 preference fields into S / W / U at one checkpoint.

    This is the realized evidence-grade profile of spec §7.4, expressed as counts. §7.4
    declares its per-tier targets qualitatively ("sparse — predominantly weak"), so the
    tier mixes in `TIER_MIXES` are **illustrative readings of that prose, not declared
    targets**. Re-run this module against the real profiles once they are fixed.
    """

    strong: int
    weak: int
    label: str = ""

    def __post_init__(self) -> None:
        if self.strong < 0 or self.weak < 0:
            raise ValueError("band sizes cannot be negative")
        if self.strong + self.weak > N_PREFERENCE:
            raise ValueError(
                f"{self.strong} strong + {self.weak} weak exceeds the "
                f"{N_PREFERENCE} preference fields"
            )

    @property
    def unevidenced(self) -> int:
        return N_PREFERENCE - self.strong - self.weak

    @property
    def evidenced(self) -> int:
        """`A(t)`'s denominator. Since ADR-0001 this is fixed by the evidence map rather than
        by SUT behaviour, which is what makes every closed form here exact."""
        return self.strong + self.weak

    @property
    def name(self) -> str:
        return self.label or f"S{self.strong}/W{self.weak}/U{self.unevidenced}"


#: Illustrative per-tier profiles read off §7.4's prose. See `BandMix`.
TIER_MIXES: tuple[BandMix, ...] = (
    BandMix(2, 10, "sparse"),          # predominantly weak, strong confined to a few pins
    BandMix(5, 8, "medium-early"),     # rising strong fraction, persistent weak band
    BandMix(11, 5, "medium-late"),
    BandMix(15, 3, "rich"),            # strong on most scheduled fields, U genuinely unevidenced
)


# --- the closed form --------------------------------------------------------


@dataclass(frozen=True)
class Policy:
    """A band-detection policy: commit probability per band, plus value quality.

    ``commit_*`` are the SUT's decisions — `commit_unevidenced` is its fabrication rate and
    `commit_strong` its recall on genuinely-strong fields. ``exact_rate_*`` is the
    probability it emits the gold value when it does commit; the *realized* agreement is
    higher than that, since a near-miss ordinal still earns partial credit, and is measured
    rather than assumed (see `Measured.a_strong`).
    """

    commit_strong: float = 1.0
    commit_weak: float = 1.0
    commit_unevidenced: float = 1.0
    exact_rate_strong: float = 1.0
    exact_rate_weak: float = 1.0
    label: str = ""


@dataclass(frozen=True)
class Expectation:
    """Closed-form expected scores for one (mix, policy, λ)."""

    a: float | None
    fabrication_count: float
    penalty: float
    f: float | None
    f_raw: float | None


def expected(
    mix: BandMix,
    *,
    commit_strong: float,
    commit_weak: float,
    commit_unevidenced: float,
    a_strong: float,
    a_weak: float,
    lam: float,
    weak_credit: float = metrics.WEAK_CREDIT,
) -> Expectation:
    """Expected `A`, penalty and `F` under the scorer's aggregation rules.

    **Exact for any fractional commit rate**, unlike the pre-rescoring version. That is a
    direct consequence of ADR-0001: abstaining on a weak field no longer removes it from the
    denominator, so the denominator is the deterministic `mix.evidenced` and `E[A]` is a ratio
    of an expectation to a constant rather than of two random variables.

    `f` is clamped exactly as the scorer clamps it; `f_raw` is reported next to it because the
    clamp is where the incentive gradient dies — see `clamp_kills_gradient`.
    """
    denominator = mix.evidenced
    if denominator <= 0:
        # The evidence map grades nothing here: no yardstick to price a fabrication against.
        return Expectation(
            a=None,
            fabrication_count=commit_unevidenced * mix.unevidenced,
            penalty=0.0, f=None, f_raw=None,
        )

    numerator = (
        commit_strong * mix.strong * a_strong
        + commit_weak * mix.weak * a_weak
        + (1.0 - commit_weak) * mix.weak * weak_credit
    )
    a = numerator / denominator
    fabrications = commit_unevidenced * mix.unevidenced
    penalty = lam * fabrications / denominator
    raw = a - penalty
    return Expectation(
        a=a, fabrication_count=fabrications, penalty=penalty,
        f=_clamp(raw), f_raw=raw,
    )


def break_even_detection(mix: BandMix, lam: float, a_strong: float) -> float | None:
    """Detector accuracy at which detect-and-abstain ties always-commit.

    The comparison is between two systems identical except in how they treat the bands they
    cannot see:

    * **always-commit** — commits everywhere: `c_S = 1`, `c_U = 1`.
    * **detect-and-abstain** — a detector of accuracy `p`: commits on a strong field with
      probability `p`, and wrongly commits on an unevidenced one with probability `1 − p`.

    Both share the same weak-band policy, so it cancels. The unclamped difference is
    ``[λ·n_U·p − (1−p)·n_S·a_S] / n_evidenced``, giving

        p* = n_S·a_S / (λ·n_U + n_S·a_S)

    **`n_U` appears, and in the right direction** — the more of the card the transcript leaves
    unevidenced, the more there is to protect and the sooner attempting detection pays. Under
    the pre-rescoring rate-based penalty `n_U` cancelled out entirely, which was the tell that
    the penalty was normalized wrongly (ADR-0002).

    Returns ``None`` when the comparison is empty: nothing strong to miss, or nothing
    unevidenced to protect.
    """
    if mix.evidenced <= 0 or not mix.unevidenced or not mix.strong:
        return None
    strong_stake = mix.strong * a_strong
    return strong_stake / (lam * mix.unevidenced + strong_stake)


def marginal_costs(
    mix: BandMix, lam: float, a_strong: float
) -> tuple[float | None, float | None]:
    """`F` cost of one extra abstention error vs one extra fabrication.

    Returns ``(cost_of_one_missed_strong, cost_of_one_fabrication)``. Both are now priced on
    the same denominator — `a_S/n_evidenced` and `λ/n_evidenced` — which is the whole point of
    ADR-0002 and why their ratio no longer depends on the band mix.
    """
    if mix.evidenced <= 0:
        return None, None
    return a_strong / mix.evidenced, lam / mix.evidenced


def exchange_rate(mix: BandMix, lam: float, a_strong: float) -> float | None:
    """How many fabrications one wrongly-withheld commit is worth: **`a_S/λ`**.

    The cleanest statement of the incentive, and the number ADR-0002 was chosen to make
    well-behaved. Above 1.0 the scorer pays an unsure system to guess; below 1.0 it pays it to
    abstain. Because both errors are priced on the same denominator the band mix cancels
    entirely, so this is a property of λ and of how well the system reads strong evidence —
    not of which tier it happens to be scored in.

    Under the previous rule this was `a_S·n_U/(λ·(n_S+n_W))`, which ran 1.24 → 0.21 across the
    tiers and crossed 1. ``mix`` is still taken so callers read the same way and so the
    mix-independence is testable rather than assumed.
    """
    if lam <= 0 or mix.evidenced <= 0 or not mix.unevidenced:
        return None
    return a_strong / lam


def weak_band_commit_pays(
    mix: BandMix, *, a_weak: float, weak_credit: float = metrics.WEAK_CREDIT
) -> float:
    """`A` gained by committing across the whole weak band rather than abstaining across it.

    Since ADR-0001 this is ``n_W·(a_W − WEAK_CREDIT)/n_evidenced``: committing pays **iff the
    system's weak-band accuracy beats what the evidence licenses**, and nothing else enters.
    In particular `c_S` and `a_S` have dropped out, which is the defect being fixed — under
    the previous rule the comparison was against `c_S·a_S`, so a strong system was paid to
    abstain and a weak one paid to guess for identical behaviour on identical evidence.

    `fab` is untouched by the weak band, so the sign of this is the whole decision.
    """
    if mix.evidenced == 0:
        return 0.0
    return mix.weak * (a_weak - weak_credit) / mix.evidenced


def abstain_everything_floor(
    mix: BandMix, weak_credit: float = metrics.WEAK_CREDIT
) -> float | None:
    """`A` (and `F`, since it fabricates nothing) for a card that abstains on every field.

    This is the cost of ADR-0001 and the reason `f_skill` exists: a do-nothing system now
    collects the credit on every weak field, so in a weak-heavy tier its floor is high —
    `WEAK_CREDIT·n_W/n_evidenced`. Publishing the floor beside the score is not optional; a
    reader who does not know it cannot tell 0.70 in the sparse tier from 0.70 in rich.
    """
    if mix.evidenced <= 0:
        return None
    return weak_credit * mix.weak / mix.evidenced


def clamp_kills_gradient(
    mix: BandMix, lam: float, a_strong: float, a_weak: float
) -> bool:
    """True when `F` floors at 0 for both a full fabricator and an almost-full one.

    `F = clamp(A − penalty, 0, 1)`. A system deep enough in the floor scores 0 whether it
    fabricates on every unevidenced field or all but one, so the abstention incentive vanishes
    for exactly the weak systems the benchmark most wants to move. Reporting `f_raw` is the
    mitigation; removing the clamp entirely is a v1 candidate (ADR-0002, Consequences).
    """
    if not mix.unevidenced:
        return False
    shared = dict(
        commit_strong=1.0, commit_weak=1.0, a_strong=a_strong, a_weak=a_weak, lam=lam,
    )
    always = expected(mix, commit_unevidenced=1.0, **shared)
    nearly = expected(
        mix, commit_unevidenced=max(0.0, 1.0 - 1.0 / mix.unevidenced), **shared
    )
    if always.f is None or nearly.f is None:
        return False
    return always.f == 0.0 and nearly.f == 0.0


# --- evidence density: how far the unbounded terms actually reach ------------
#
# ADR-0002 records as debt that `F_raw` is unbounded below and therefore "not safely
# averageable", on the evidence of −8.7 on the toy subjects. That number is a function of
# how much the *toys* evidence (2–3 of 20 fields), not of the scoring rule, and the toys
# were written to exercise the scorer rather than to resemble a tier. These four functions
# separate the two so the v1 decision is made on the right number.


def penalty_ceiling(mix: BandMix, lam: float) -> float | None:
    """The largest penalty any system can incur at this mix: `λ·n_U/n_evidenced`.

    Every unbounded term in the composite is a multiple of this, because the penalty is
    the only term without a natural cap — `A` is bounded in [0, 1] by construction. It
    grows without bound as the evidenced band thins, which is the whole of ADR-0002's
    concern, and it is *flat in the card size*: what matters is the ratio of unevidenced
    to evidenced fields, not how many fields there are.
    """
    if mix.evidenced <= 0:
        return None
    return lam * mix.unevidenced / mix.evidenced


def worst_raw(mix: BandMix, lam: float) -> float | None:
    """The most negative `F_raw` reachable at this mix — a system that fabricates on every
    unevidenced field and earns nothing at all on the evidenced ones.

    This is the true floor of the reported number, so it is the figure the "safely
    averageable" question is actually about. `A ≥ 0` always, so it is exactly
    `−penalty_ceiling`.
    """
    ceiling = penalty_ceiling(mix, lam)
    return None if ceiling is None else -ceiling


def skill_amplification(mix: BandMix, weak_credit: float = metrics.WEAK_CREDIT) -> float | None:
    """`1/(1 − a_floor)` — the factor `f_skill` multiplies every raw score by.

    `f_skill = (F_raw − a_floor)/(1 − a_floor)`, so a high do-nothing floor does not merely
    shift the scale, it *stretches* it. The floor is highest exactly where the weak band is
    widest (ADR-0003), which is the sparse tier — so skill's magnitudes blow out fastest in
    the tier whose numbers are already hardest to read. This is the argument against
    promoting `f_skill` to the headline that ADR-0003 left open, and it is arithmetic rather
    than judgement.
    """
    floor = abstain_everything_floor(mix, weak_credit)
    if floor is None or floor >= 1.0:
        return None
    return 1.0 / (1.0 - floor)


def min_evidenced_for(bound: float, lam: float, n_fields: int = N_PREFERENCE) -> int | None:
    """Smallest evidenced-band size keeping the penalty ceiling at or under `bound`.

    Solves `λ·(n − n_e)/n_e ≤ bound` for `n_e`, i.e. `n_e ≥ λ·n/(λ + bound)`. This is the
    gate the density analysis argues for: instead of redesigning the composite to be bounded
    by construction, refuse to average checkpoints whose evidenced band is too thin to price
    a fabrication against — which §7.4's tier targets already imply but do not enforce.
    """
    if bound < 0:
        return None
    needed = lam * n_fields / (lam + bound)
    candidate = int(-(-needed // 1))  # ceiling division without importing math
    return candidate if candidate <= n_fields else None


#: The toy subjects, as a band mix. They evidence 2–3 of 20 fields — written to exercise
#: the scorer's edges, not to resemble any tier §7.4 would accept. Included in the sweep
#: purely so the contrast with the tier mixes is visible in one table.
TOY_MIX = BandMix(1, 2, "toy (as built)")


# --- the empirical arm: a controlled SUT run through the real scorer ---------


def synthetic_subject(
    mix: BandMix, *, seed: int, tier: str = "medium", checkpoints: int = 1
) -> Subject:
    """A subject whose evidence map realizes ``mix`` exactly.

    Band membership is **shuffled per seed** rather than assigned by field order. Without
    that, the strong band would always hold the same fields and `a_strong` would be an
    artifact of which metrics those fields happen to use — Jaccard on a 39-member vocabulary
    behaves nothing like a 4-level ordinal under corruption.

    The 4 hard constraints are graded `UNEVIDENCED` and the instrument abstains on them, so
    the gating track of §8.2 stays out of the measurement.
    """
    rng = random.Random(f"ablation-subject:{seed}:{mix.strong}:{mix.weak}")
    values: dict[str, Any] = {
        spec.field_id: _random_value(spec, rng) for spec in fields.ALL_FIELDS
    }
    gold = PersonaCard(f"abl-{seed}", values)

    # The weak band is drawn from non-distribution fields only, so the low-N distribution
    # carve-out (scorer §8.1a) never fires here: this closed form is count-based and has no
    # distribution concept, so a WEAK distribution — which the scorer drops from both terms —
    # would desync `mix.weak` from what the scorer actually scores. Distribution fields stay
    # eligible for the strong band (scored normally there), which keeps a_strong a per-seed
    # draw over the metric mix as the docstring intends. The BandMix invariant strong+weak <=
    # N_PREFERENCE guarantees the strong band still fills once weak has taken its non-dist
    # share (17 non-distribution fields; the widest tier asks for 10 weak).
    non_dist = [f for f in fields.PREFERENCE_IDS if f not in _DISTRIBUTION_ID_SET]
    rng.shuffle(non_dist)
    if mix.weak > len(non_dist):
        raise ValueError(
            f"weak band {mix.weak} exceeds the {len(non_dist)} non-distribution preference "
            "fields; the ablation cannot place a distribution in the weak band"
        )
    weak_ids = set(non_dist[: mix.weak])
    # Distributions are deprioritized for the strong band too: the non-distribution leftovers
    # fill it first, and a distribution is drawn in only when the band is wider than the
    # non-distribution pool can supply (the rich / no-unevidenced tiers). This keeps the small
    # tiers distribution-free so their measured a_strong is a clean ordinal/set/boolean draw —
    # the ablation deliberately brackets the JSD carve-out out — while the BandMix invariant
    # still guarantees the band fills. `non_dist` is already per-seed shuffled, so which
    # metrics land in strong still varies with the seed, as the docstring requires.
    strong_pool = non_dist[mix.weak :] + [
        f for f in fields.PREFERENCE_IDS if f in _DISTRIBUTION_ID_SET
    ]
    strong_ids = set(strong_pool[: mix.strong])

    per_checkpoint: dict[str, Grade] = {}
    for field_id in fields.ALL_FIELDS_IDS:
        if field_id in strong_ids:
            per_checkpoint[field_id] = Grade.STRONG
        elif field_id in weak_ids:
            per_checkpoint[field_id] = Grade.WEAK
        else:
            per_checkpoint[field_id] = Grade.UNEVIDENCED

    subject = Subject(
        subject_id=f"abl-{seed}",
        gold=gold,
        evidence=EvidenceMap(f"abl-{seed}", [dict(per_checkpoint) for _ in range(checkpoints)]),
        tier=tier,
        simulator_model="ablation-instrument",
    )
    subject.validate()
    return subject


@dataclass
class NoisyDetector:
    """A system with a controllable band detector. **Instrument, not a baseline.**

    Reads the evidence map and ground truth directly, as `baselines.EvidenceOracle` does,
    and for the same reason: to put a known system on the other side of the scorer. The
    oracle fixes the ceiling; this fixes the *incentive gradient*, by making the SUT's
    commit/abstain decision and its value accuracy independently dialable.
    """

    subject: Subject
    policy: Policy
    seed: int = 0
    _checkpoint: int = 0

    def reset(self, subject_id: str) -> None:
        self._checkpoint = 0

    def observe(self, sessions: Sequence[Any]) -> PersonaCard:
        card = self.emit(self._checkpoint)
        self._checkpoint += 1
        return card

    def emit(self, checkpoint: int) -> PersonaCard:
        gold = self.subject.gold_at(checkpoint)

        values: dict[str, Any] = {}
        for spec in fields.ALL_FIELDS:
            if spec.is_constraint:
                values[spec.field_id] = UNKNOWN
                continue
            # A fresh stream per field, not one stream for the card. This is what makes two
            # policies differing only in `commit_unevidenced` **exactly paired**: the strong
            # and weak fields then draw identical values in both arms, so the F delta is the
            # fabrication penalty and nothing else. With a single shared stream the extra
            # draws taken on U fields shift every later field and the delta picks up noise
            # of the same order as the effect being measured.
            rng = random.Random(f"{self.seed}:{checkpoint}:{spec.field_id}")
            grade = self.subject.evidence.grade(checkpoint, spec.field_id)
            commit_p, exact_p = self._parameters(grade)
            if rng.random() >= commit_p:
                values[spec.field_id] = UNKNOWN
                continue
            gold_value = gold.get(spec.field_id)
            values[spec.field_id] = (
                gold_value if rng.random() < exact_p else _corrupt(spec, gold_value, rng)
            )
        return PersonaCard(self.subject.subject_id, values)

    def _parameters(self, grade: Grade) -> tuple[float, float]:
        if grade == Grade.STRONG:
            return self.policy.commit_strong, self.policy.exact_rate_strong
        if grade == Grade.WEAK:
            return self.policy.commit_weak, self.policy.exact_rate_weak
        # In U there is no gold to be right about as far as the scorer is concerned — the
        # commit is a fabrication whatever its value. The value still has to be schema-valid,
        # so it is drawn as a plausible guess.
        return self.policy.commit_unevidenced, 0.0

    def carried_state_bytes(self) -> int:
        return 0

    def carried_state_tokens(self) -> int:
        return 0


def _corrupt(spec: fields.Field, value: Any, rng: random.Random) -> Any:
    """A wrong but schema-valid value, so agreement lands below 1.0 without being degenerate."""
    if spec.metric == fields.METRIC_ORDINAL:
        alternatives = [level for level in spec.levels if level != value]
        return rng.choice(alternatives)
    if spec.metric == fields.METRIC_EXACT:
        return not value
    if spec.metric == fields.METRIC_JACCARD:
        members = set(value)
        outside = [level for level in spec.levels if level not in members]
        if members and rng.random() < 0.5:
            members.discard(rng.choice(sorted(members)))
        if outside:
            members.add(rng.choice(outside))
        return sorted(members)
    if spec.metric == fields.METRIC_JSD:
        return _random_value(spec, rng)
    raise ValueError(f"unhandled metric {spec.metric!r}")


@dataclass(frozen=True)
class Measured:
    """Monte Carlo results, with the realized agreements the closed form takes as input.

    ``f`` is the mean of the scorer's clamped `F(t)`; ``f_raw`` is the mean before the clamp.
    Both are reported because they answer different questions: `f` is what a leaderboard would
    print, `f_raw` is what carries the *incentive*, and they diverge exactly where a system is
    bad enough to sit on the floor. Strategy comparisons belong on `f_raw`.

    ``f_skill`` is `f_raw` with the do-nothing floor divided out — the number that stays
    comparable across tiers now that weak abstention is credited (ADR-0003).
    """

    a: float | None
    fabrication_count: float
    penalty: float
    f: float | None
    f_raw: float | None
    f_skill: float | None
    a_floor: float | None
    a_strong: float
    a_weak: float
    scorable_replicates: int
    replicates: int


def simulate(
    mix: BandMix,
    policy: Policy,
    *,
    lam: float,
    replicates: int = 400,
    seed: int = 0,
) -> Measured:
    """Run `NoisyDetector` through the real `score_checkpoint` and average.

    Deliberately routed through the scorer rather than reimplementing its arithmetic: the
    whole point is to measure what the shipped scoring rule rewards, so a second
    implementation of `A` and the penalty here would be measuring the wrong thing the moment
    the two drifted.
    """
    collected: dict[str, list[float]] = {
        "a": [], "f": [], "f_raw": [], "f_skill": [], "a_floor": [],
        "penalty": [], "fabrications": [], "strong": [], "weak": [],
    }

    for index in range(replicates):
        subject = synthetic_subject(mix, seed=seed * 100_000 + index)
        detector = NoisyDetector(subject, policy, seed=seed * 100_000 + index)
        detector.reset(subject.subject_id)
        emitted = detector.emit(0)
        result = scorer.score_checkpoint(subject, 0, emitted, lam=lam)

        collected["fabrications"].append(float(result.fabrication_count))
        collected["penalty"].append(result.penalty)
        for key, value in (
            ("a", result.a), ("f", result.f), ("f_raw", result.f_raw),
            ("f_skill", result.f_skill), ("a_floor", result.a_floor),
        ):
            if value is not None:
                collected[key].append(value)

        for outcome in result.outcomes:
            # Committed only: an abstained weak field now carries the credit as its
            # "agreement", and averaging that into a_weak would measure the scorer's own
            # convention rather than the system's accuracy.
            if not outcome.committed or outcome.agreement is None:
                continue
            if outcome.grade == Grade.STRONG:
                collected["strong"].append(outcome.agreement)
            elif outcome.grade == Grade.WEAK:
                collected["weak"].append(outcome.agreement)

    return Measured(
        a=_mean_or_none(collected["a"]),
        fabrication_count=_mean_or_none(collected["fabrications"]) or 0.0,
        penalty=_mean_or_none(collected["penalty"]) or 0.0,
        f=_mean_or_none(collected["f"]),
        f_raw=_mean_or_none(collected["f_raw"]),
        f_skill=_mean_or_none(collected["f_skill"]),
        a_floor=_mean_or_none(collected["a_floor"]),
        a_strong=_mean_or_none(collected["strong"]) or 0.0,
        a_weak=_mean_or_none(collected["weak"]) or 0.0,
        scorable_replicates=len(collected["a"]),
        replicates=replicates,
    )


def guess_instead_of_abstain(
    mix: BandMix, policy: Policy, *, lam: float, replicates: int = 400, seed: int = 0
) -> tuple[Measured, Measured]:
    """The spec §11 ablation, run as specified — and still mechanical.

    Returns ``(abstaining, guessing)`` where the two differ *only* in `commit_unevidenced`.
    The `f_raw` delta is always exactly `λ · Δn_fab / n_evidenced`, because a commit in `U`
    contributes nothing to `A`. The 2026-08-10 rescoring changed how that penalty is
    normalized; it did not and could not make this ablation informative. The falsifiable
    criterion is the weak-band one — see `weak_band_commit_pays`.
    """
    def arm(commit_unevidenced: float, suffix: str) -> Measured:
        return simulate(
            mix, Policy(
                commit_strong=policy.commit_strong,
                commit_weak=policy.commit_weak,
                commit_unevidenced=commit_unevidenced,
                exact_rate_strong=policy.exact_rate_strong,
                exact_rate_weak=policy.exact_rate_weak,
                label=f"{policy.label} ({suffix})",
            ),
            lam=lam, replicates=replicates, seed=seed,
        )

    return arm(0.0, "abstains in U"), arm(1.0, "guesses in U")


# --- helpers ----------------------------------------------------------------


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _mean_or_none(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _fmt(value: float | None, width: int = 7, places: int = 3) -> str:
    return f"{'n/a':>{width}}" if value is None else f"{value:>{width}.{places}f}"


# --- report -----------------------------------------------------------------


def _section(title: str) -> None:
    print()
    print(title)
    print("-" * max(len(title), 78))


def main() -> int:
    reference = Policy(
        commit_strong=1.0, commit_weak=1.0, commit_unevidenced=1.0,
        exact_rate_strong=0.85, exact_rate_weak=0.55, label="reference",
    )
    print(f"Scoring rule as of the 2026-08-10 rescoring. WEAK_CREDIT = "
          f"{metrics.WEAK_CREDIT:.4f}, default lambda = {scorer.DEFAULT_LAMBDA}.")

    _section("1. Exchange rate: which error does the scorer treat as worse?")
    print("How many fabrications one wrongly-withheld commit is worth. Both errors are now")
    print("priced on the same denominator, so this is a_S/lambda and the band mix cancels.")
    print(f"{'mix':<13} {'a_S':>6} " + " ".join(
        f"{'rate @ l=' + f'{l:.1f}':>14}" for l in REPORTED_LAMBDAS))
    for mix in TIER_MIXES:
        measured = simulate(mix, reference, lam=scorer.DEFAULT_LAMBDA, replicates=200)
        cells = [_fmt(exchange_rate(mix, lam, measured.a_strong), 14, 2)
                 for lam in REPORTED_LAMBDAS]
        print(f"{mix.name:<13} {measured.a_strong:>6.3f} " + " ".join(cells))
    print()
    print("Constant down each column — the tier-dependent inversion the previous rate-based")
    print("penalty produced (1.24 in sparse, 0.21 in rich, crossing 1) is gone. At the default")
    print("lambda=1.0 the rate sits just below 1: fabrication is the marginally worse error,")
    print("uniformly, which is the thesis without a thumb on the scale.")

    _section("2. Break-even band-detection accuracy")
    print("A detector committing on a strong field with probability p and wrongly committing")
    print("on an unevidenced one with probability 1-p. Below p*, always-commit wins.")
    print(f"{'mix':<13} {'a_S':>6} " + " ".join(
        f"{'p* @ l=' + f'{l:.1f}':>13}" for l in REPORTED_LAMBDAS))
    for mix in TIER_MIXES:
        measured = simulate(mix, reference, lam=scorer.DEFAULT_LAMBDA, replicates=200)
        cells = [_fmt(break_even_detection(mix, lam, measured.a_strong), 13)
                 for lam in REPORTED_LAMBDAS]
        print(f"{mix.name:<13} {measured.a_strong:>6.3f} " + " ".join(cells))
    print()
    print("p* = n_S*a_S/(lambda*n_U + n_S*a_S). n_U now appears and pushes the threshold down:")
    print("the more of the card is unevidenced, the more a detector protects and the sooner")
    print("attempting detection pays. Under the previous rule n_U cancelled out entirely.")

    _section("3. The weak band — where §0.1's surviving claim lives")
    print("A gained by committing across all of W(t) rather than abstaining across it.")
    print(f"Committing pays iff a_W > WEAK_CREDIT = {metrics.WEAK_CREDIT:.3f}, and nothing")
    print("else enters — not the system's strong-band accuracy, not the tier.")
    print(f"{'mix':<13} {'strong-band quality':>20} {'a_W':>6} {'delta A':>9} {'pays to':>9}")
    for mix in TIER_MIXES:
        for exact_strong, exact_weak in ((0.95, 0.35), (0.35, 0.35), (0.95, 0.95)):
            policy = Policy(
                commit_strong=1.0, commit_weak=1.0, commit_unevidenced=0.0,
                exact_rate_strong=exact_strong, exact_rate_weak=exact_weak,
            )
            measured = simulate(mix, policy, lam=scorer.DEFAULT_LAMBDA, replicates=200)
            delta = weak_band_commit_pays(mix, a_weak=measured.a_weak)
            verdict = "commit" if delta > 0 else ("abstain" if delta < 0 else "tie")
            print(f"{mix.name:<13} {measured.a_strong:>20.3f} {measured.a_weak:>6.3f} "
                  f"{_fmt(delta, 9)} {verdict:>9}")
    print()
    print("Rows 1 and 2 of each tier have identical weak-band accuracy and very different")
    print("strong-band accuracy, and now get the same verdict. Under the previous rule they")
    print("got opposite ones, which is the defect ADR-0001 fixes.")

    _section("4. The cost of crediting weak abstention: a non-zero do-nothing floor")
    print("A card that abstains everywhere now collects the credit on every weak field.")
    print(f"{'mix':<13} {'abstain-all F':>14} {'oracle F':>9} {'headroom':>9} "
          f"{'skill of abstain-all':>21}")
    for mix in TIER_MIXES:
        floor = abstain_everything_floor(mix)
        measured = simulate(
            mix, Policy(commit_strong=0.0, commit_weak=0.0, commit_unevidenced=0.0),
            lam=scorer.DEFAULT_LAMBDA, replicates=100,
        )
        headroom = None if floor is None else 1.0 - floor
        print(f"{mix.name:<13} {_fmt(floor, 14)} {1.0:>9.3f} {_fmt(headroom, 9)} "
              f"{_fmt(measured.f_skill, 21)}")
    print()
    print("This is why f_skill is reported beside F: it divides the floor out, so a do-nothing")
    print("system scores 0 by construction and the §11 abstain-everything sanity check keeps")
    print("its meaning. Publishing F alone in the sparse tier would show 0.69 for a system")
    print("that emitted nothing at all.")

    _section("5. Where the clamp still destroys the incentive")
    print("F = clamp(A - penalty, 0, 1). Once a system is deep enough in the floor,")
    print("fabricating more costs nothing. Mitigated by reporting f_raw, not yet removed.")
    print(f"{'mix':<13} {'lambda':>7} {'a_S':>6} {'gradient':>10}")
    for mix in TIER_MIXES:
        for lam in REPORTED_LAMBDAS:
            measured = simulate(mix, Policy(exact_rate_strong=0.2, exact_rate_weak=0.15),
                                lam=lam, replicates=200)
            dead = clamp_kills_gradient(mix, lam, measured.a_strong, measured.a_weak)
            print(f"{mix.name:<13} {lam:>7.1f} {measured.a_strong:>6.3f} "
                  f"{'dead' if dead else 'live':>10}")

    _section("6. The §11 U-band ablation is still mechanical")
    print("Unchanged by the rescoring, and unchangeable: a commit in U(t) earns nothing in")
    print("A(t), so the delta is always lambda*dn_fab/n_evidenced.")
    print(f"{'mix':<13} {'lambda':>7} {'raw delta':>10} {'predicted':>10}")
    for mix in TIER_MIXES:
        for lam in REPORTED_LAMBDAS:
            abstaining, guessing = guess_instead_of_abstain(
                mix, reference, lam=lam, replicates=100
            )
            delta = (
                None if abstaining.f_raw is None or guessing.f_raw is None
                else abstaining.f_raw - guessing.f_raw
            )
            predicted = (
                lam * (guessing.fabrication_count - abstaining.fabrication_count)
                / mix.evidenced if mix.evidenced else None
            )
            print(f"{mix.name:<13} {lam:>7.1f} {_fmt(delta, 10)} {_fmt(predicted, 10)}")
    print()
    print("NoisyDetector is an instrument, not a baseline: it reads the evidence map and")
    print("ground truth directly, which no real SUT may do.")

    _section("7. Evidence density: is F_raw actually unsafe to average?")
    print("ADR-0002 records -8.7 on the toys and concludes F_raw is unbounded below. The")
    print("bound is lambda*n_U/n_evidenced, so it is a statement about how much the item")
    print("evidences, not about the rule. The toy row is the same scorer on a mix no tier")
    print("would be allowed to have.")
    print(f"{'mix':<15} {'n_ev':>5} {'worst F_raw':>12} {'floor':>7} "
          f"{'skill x':>8} {'worst skill':>12}")
    for mix in (TOY_MIX,) + TIER_MIXES:
        raw = worst_raw(mix, scorer.DEFAULT_LAMBDA)
        floor = abstain_everything_floor(mix)
        amp = skill_amplification(mix)
        worst_skill = None if raw is None or amp is None else (raw - floor) * amp
        print(f"{mix.name:<15} {mix.evidenced:>5} {_fmt(raw, 12)} {_fmt(floor, 7)} "
              f"{_fmt(amp, 8, 2)} {_fmt(worst_skill, 12)}")
    print()
    print("Two things fall out. F_raw's range on any tier mix is roughly [-0.7, 1] — bounded,")
    print("narrow, and safely averageable; the -8.7 is a property of the toys alone. And")
    print("f_skill is the *less* bounded number, not the more: it multiplies by 1/(1-floor),")
    print("which is largest in the sparse tier, so promoting it to the headline (ADR-0003's")
    print("open question) would import the magnitude problem it was meant to avoid.")

    _section("8. The density gate that replaces the bounded composite")
    print("If the unbounded term is only reachable at thin evidence, the cheaper fix is to")
    print("refuse to average those checkpoints rather than to redesign the composite.")
    print(f"{'penalty ceiling at most':>24} " + " ".join(
        f"{'n_ev @ l=' + f'{l:.1f}':>14}" for l in REPORTED_LAMBDAS))
    for bound in (0.5, 1.0, 2.0, 4.0):
        cells = [f"{str(min_evidenced_for(bound, lam)):>14}" for lam in REPORTED_LAMBDAS]
        print(f"{bound:>24.1f} " + " ".join(cells))
    print()
    print("At the default lambda=1.0, requiring 7 of 20 fields evidenced caps any single")
    print("checkpoint's penalty at 2.0 and its F_raw at -2.0; requiring 14 caps it at 0.43.")
    print("Every tier mix in section 7 already clears both. The gate is therefore a")
    print("scorable-checkpoint precondition, not a change to the scoring rule — and unlike")
    print("a bounded composite it does not alter the marginal cost of a fabrication, which")
    print("is the property ADR-0002 chose the count-based penalty to get.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
