"""Session authoring: the §6 prompts, and the deterministic checks that bound them.

Step 5's deliverable. The prompts are here rather than in a probe runner because
`probe_sessions/` measured three §6.1 collisions and two outright authoring defects, and the
fixes belong to the shipped author, not to the harness that found them.

**The split is ADR-0005's and ADR-0009's, for the third time.** A prose instruction in an
authoring prompt is advice; what a rule can check, a rule checks, and a session that fails is
re-authored rather than shipped. The evidence for insisting on this is in the probe: the
distractor brief said *in capitals* that the session carried no persona signal and the author
put `travel_radius` in it anyway, and the grading prompt that produced 35% invention already
forbade exactly that. Two prose safeguards, two failures.

What is bounded here, and what is not:

* `reaction` written as a habit statement — **partly bounded**. `SELF_SUMMARY_PATTERNS`
  catches the quantified forms ("as far ahead as I ever", "I always", "that's just how I am").
  It does not catch an unquantified present-tense generalization, and `check_session` does not
  pretend to; the residue stays with the §7.2 leakage audit. The rate of each is published by
  `probe_reauthor/`.
* the distractor session leaking — **bounded structurally, but not lexically.** The leak was
  *"i'm not going to cross the river on a weeknight"*, which shares no token with any card
  value, so no word list would have caught it. What produces it is that the user was still
  deciding where to go. `DISTRACTOR_SCENARIOS` removes the decision from the session instead
  of asking the author not to answer it.
* a distribution enumerated in one turn — **bounded**. The signature is two or more bins of
  one field in a single user turn under a relative-frequency marker, which is what a leakage
  auditor calls a filled-in profile, and it is a shape a regex can see.

Nothing here calls a model. `probe_reauthor/` supplies the model and the retry loop.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from . import fields
# The move name is the scheduler's, not a second copy of the string: a disclosure beat
# is written there and read here, and a drifting literal would silently disable the
# check rather than fail it.
from .schedule import DISCLOSURE as DISCLOSURE_MOVE
from .schedule import THIRD_PARTY
# The map credits `planning_style` on exactly the sessions the horizon line below writes it,
# so the condition is imported rather than restated. See `continuity_for`.
from .schedule import horizon_lands
from .schedule import USER as OWN

# --- the system prompt -------------------------------------------------------

AUTHOR_SYSTEM = """\
You write realistic transcripts of conversations between a person and a restaurant / cafe / \
bar / event recommendation bot, for a research dataset.

Hard rules:

1. THE BOT IS A PROP. It is generic and only mildly helpful. It asks bland questions and \
gives unremarkable suggestions. A bot that gives good recommendations makes the user's \
reactions too informative, which ruins the dataset.
2. THE USER NEVER RECITES A PREFERENCE AS A CATEGORY. No trait vocabulary, no scale \
language, no self-summaries. Preferences surface the way people actually show them: what \
they choose, what they reject, what they react to, what they complain about, stories about \
what happened.
3. THE USER NEVER GENERALIZES ABOUT THEMSELVES. This is the most common way rule 2 gets \
broken while looking like it is being kept. A line about what the user *tends to* do is a \
self-summary wearing conversational clothes, and it is the single defect the audit caught \
most often in the last run. Banned outright: "I always", "I never", "I usually", "I tend \
to", "as a rule", "that's just how I am", "I'm the kind of person who", "as far ahead as I \
ever", "any time I", "every time I", "I'm not really a X person". The user says what \
happened this time, or what they want right now. A reader infers the tendency; the user \
never states it.
3b. AND THE USER NEVER RATES TODAY AGAINST THEIR OWN NORM. The same defect reaches the page \
sideways, attached to a choice rather than to the person: "same as usual", "the usual", \
"nicer than normal", "more than we'd normally spend", "a rare treat", "the sort of place we \
always end up at". These are what makes a *distribution* field contradictable at all — a \
card whose spend is mostly £50+ is not contradicted by one £30-50 lunch, but it is \
contradicted by calling that lunch typical, and equally by calling it a step up. Name what \
is happening today and stop there.
4. WRITE ONLY WHAT YOU ARE ASKED TO EVIDENCE. THE BEATS ARE THE WHOLE JOB. Every brief \
names a small number of beats, and those beats are the complete list of things this session \
is for. A field not named by a beat must not be inferable from the session — not settled, \
not narrowed, not hinted at. Do not round out the character.
4b. AND KNOWING SOMETHING IS NOT PERMISSION TO WRITE IT. The brief may show you the whole \
truth about this person. That is background for staying in character, and it is not a list \
of material. The failure mode it creates is not an author writing something false — it is an \
author writing something TRUE about a field no beat named, which reads as ordinary good \
writing and is the exact measurement this dataset takes, spoiled. When a detail would be apt \
and no beat asked for it, leave it out.
5. Sessions are short and ordinary. Real people are indirect, digressive, and often say \
nothing informative for several turns.
6. NUMBER EVERY MESSAGE. Each message begins with its number in square brackets, then the \
speaker label: `[0] USER:`, `[1] BOT:`, `[2] USER:`. Start at 0 and add exactly one each \
time, counting both speakers in the one sequence. These numbers are how the rest of the \
pipeline points at a message, so a gap, a repeat, or a start other than 0 makes the session \
unusable.

Output JSON only: {"transcript": "[0] USER: ...\\n[1] BOT: ...\\n[2] USER: ..."}

If — and only if — the brief contains a SCRIPTED SELF-DESCRIPTION, add one more key giving \
that line back verbatim, exactly as it appears in the transcript: \
{"transcript": "...", "self_report": "the sentence the user says about themselves"}. \
That line is the one place the user is allowed to generalize about themselves, and the \
checks downstream have no way to tell it apart from the mistake it looks like unless you \
say which line it is."""


#: The `reaction` move, restated. The old definition — "the user responds to something and
#: their response reveals a leaning, without settling it" — is satisfied by a habit
#: statement, and that is how it kept being written: a third of the leakage flags in
#: `probe_sessions/` were reactions realized as *"as far ahead as I ever plan this stuff"*.
#: What makes a reaction narrow-without-pinning is that it is about **one specific thing the
#: bot just said**, so a reader learns the direction of one judgment and has to guess how
#: general it is. That property is lost the moment the user supplies the generality.
MOVE_DEFINITIONS: dict[str, str] = {
    "request": "the user asks for something that names or excludes what they want",
    "rejection": "the user turns down a specific suggestion, for a reason that shows the "
                 "trait",
    "reaction": "the user responds to THE SPECIFIC THING THE BOT JUST NAMED — this place, "
                "this time, this suggestion — and their response shows which way they lean "
                "on it. It must not be a statement about what they do in general. "
                "'Half seven is pushing it for a Tuesday' is a reaction; 'I never eat late "
                "on weeknights' is a self-summary and is banned by rule 3. Same information, "
                "and only the first one leaves the reader still inferring.",
    "anecdote": "the user tells a short story about one past occasion",
    "disclosure": "THE BOT ASKS and the user answers in the negative — they have none of "
                  "this. The only move the bot initiates. It has to be, because a "
                  "preference can be volunteered and an absence cannot: a user who "
                  "spontaneously announces 'I don't have any allergies' is reciting a "
                  "profile, which rule 3 bans. Asked, the same information is an ordinary "
                  "answer to an ordinary question.",
}


#: What the bot asks about, per hard constraint, for a `disclosure` beat — and the word its
#: question has to carry.
#:
#: The generic instruction was measured failing. Authored without this table, `gen-000`'s
#: three disclosure beats — `dietary`, `allergies`, `alcohol_free_required` — came back as
#: three near-identical questions: *"is there anything I should know about what you eat?"*
#: twice verbatim for two different fields, and *"what you eat or drink"* for the third. The
#: move was correct every time (the bot asked, the user did not volunteer) and the *record*
#: was still wrong: the derived map credits `allergies` STRONG at that checkpoint while the
#: page cannot be told apart from the `dietary` one, and `alcohol_free_required` rests on the
#: two words "or drink". Under ADR-0034 the schedule is the map, so a beat that does not name
#: its field is the map asserting a cell the transcript does not carry — and the same generic
#: question read as evidencing a *second* constraint is the cross-field leak §8.1 punishes a
#: twin for acting on.
#:
#: `marker` is the substring the check reads, and it is deliberately narrow. A wider set —
#: "don't eat" for `dietary`, "drinks" for `alcohol_free_required` — matches ordinary user
#: turns ("I never eat late on weeknights") and would fire on the construction rather than
#: the defect. Narrow markers miss some paraphrases; that is the direction a gate should err.
DISCLOSURE_PROBES: dict[str, tuple[str, tuple[str, ...], str]] = {
    "dietary": (
        "The bot must ask about a DIET and name two examples — 'do you follow any "
        "particular diet, vegetarian or halal or anything like that?'. It must NOT say "
        "'dietary requirements', 'anything the kitchen should know' or 'anything I should "
        "know about what you eat': those are the umbrella question in ordinary English, they "
        "are answered for allergies at the same time, and they settle neither field. Naming "
        "two diets is what closes the scope — a question that lists vegetarian and halal "
        "cannot be read as screening for peanuts.",
        ("particular diet", "specific diet", "any diet", "diet you follow"),
        "'No, no particular diet' — never a bare 'nothing like that'.",
    ),
    "allergies": (
        "The bot must use the word 'allergies' — asking whether there are any allergies the "
        "kitchen should know about. Naming the word is what separates this beat from the "
        "dietary one; a question about 'what you eat' does not settle allergies.",
        ("allerg",),
        "'No allergies' — never a bare 'nothing to flag'.",
    ),
    "accessibility_needs": (
        "The bot must name access explicitly — step-free access, stairs, whether the venue "
        "needs to meet any access requirements. A vague 'anything I should know' does not "
        "settle this field.",
        ("access", "step-free", "step free", "wheelchair", "stairs"),
        "'No access requirements' or 'no, stairs are fine' — never a bare "
        "'nothing like that'.",
    ),
    "alcohol_free_required": (
        "The bot must use the word 'alcohol' — asking whether the place needs to be "
        "alcohol-free, or whether a bar on site is a problem. 'What you eat or drink' does "
        "not settle this field.",
        ("alcohol",),
        "'No, it doesn't need to be alcohol-free' — never a bare 'nothing to "
        "worry about'.",
    ),
}


#: Scenarios for the §5 distractor session. It is a library rather than a brief because the
#: brief was measured failing: `probe_sessions/` cell 13 found the author writing
#: `travel_radius` into a session whose instructions said in capitals that it carried none,
#: and the strong count rose across the zero-signal checkpoint in 11 of 20 cases.
#:
#: The common property is that **the user is not choosing where or when to go.** That is the
#: structural part. A user deciding on a venue has to reveal something to decide with; a user
#: chasing a lost booking reference has nothing to leak, however chatty the author makes them.
DISTRACTOR_SCENARIOS: tuple[str, ...] = (
    "The user is trying to recover a booking reference for a reservation someone else made. "
    "The bot cannot look it up. They go back and forth about what the confirmation email "
    "might have been called.",
    "The user is asking on behalf of a visiting relative who has already chosen where they "
    "are going, and only needs to know how to get there on public transport.",
    "The app logged the user out and lost a saved list. They want to know whether it can be "
    "restored. The bot is not helpful about this.",
    "A plan for this evening has just been cancelled by someone else. The user tells the bot "
    "it is cancelled, declines to make another, and the conversation drifts to the weather "
    "and the state of the high street.",
    # The booking is somebody else's on purpose. Owned by the user it states a lead time —
    # a named future day, a table already held — and lead time is a graded field, so the
    # scenario leaked `planning_style` into a session with no beats to check it against.
    # `gen-001-s4` is the case: "I've got a table booked at the Lamb & Flag on Saturday" on a
    # `decides_same_day` card, a blocking contradiction in a session that carries no map at
    # all. The structural property this list is built on — the user is not choosing where or
    # when to go — was already the fix; this entry just did not hold it.
    "The user is checking whether a place someone else has booked, for a group the user is "
    "joining, is accessible by a particular bus route, and whether the bot knows if the road "
    "works have finished.",
    "The user is chasing a lost-property enquiry — they left a scarf somewhere last week and "
    "want to know how to contact the venue. They do not say which venue, or what they were "
    "doing there.",
    "The user wants to know why the app keeps sending them notifications, and how to turn "
    "them off. The bot walks through settings. No plan is made.",
    "Someone else is organising a leaving do and has asked the user to forward a link. The "
    "user cannot find the link and wants help searching their history for it.",
)

#: **Not** an app-support catalogue. v0's distractor list drifted into delivery complaints and
#: password resets — a *different product's* conversations, which teaches a reader nothing
#: about this one and reads as filler. These are things a person says to a recommendations app
#: while not choosing anywhere to go: lost property, notification settings, someone else's
#: booking. The structural property is preserved (the user is not choosing where or when to
#: go, so there is nothing to leak) without leaving the product.


# --- the deterministic checks ------------------------------------------------

#: Quantified self-generalization. Every one of these was written by an author in the probe
#: run or is the direct form of one that was. Matched against USER turns only — the bot is a
#: prop and may say anything.
SELF_SUMMARY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bi (?:always|never|usually|generally|normally|tend to|prefer to)\b",
     "quantified habit"),
    (r"\bas (?:a rule|far (?:ahead )?as i ever)\b", "quantified habit"),
    (r"\b(?:any|every) time i\b", "quantified habit"),
    # Narrow: an early draft matched `that's (how|what) I` and flagged "that's what I
    # thought", which is a reply about the bot's suggestion and the opposite of a
    # self-summary. The construction being banned needs its predicate.
    (r"\bthat'?s (?:just )?how i (?:am|do|work|operate|roll)\b", "self-characterization"),
    (r"\bthat'?s (?:just )?what i (?:do|like|go for|always)\b", "self-characterization"),
    (r"\bi'?m (?:just )?(?:not )?(?:really )?(?:a|the|that) (?:kind|sort|type) of\b",
     "self-characterization"),
    (r"\bi'?m (?:not )?(?:really )?(?:a|an) \w+ person\b", "self-characterization"),
    (r"\bi'?m (?:the )?(?:sort|kind) who\b", "self-characterization"),
)

#: Relative-frequency markers. Harmless alone; the §6.1 collision is one of these joined to
#: two or more bins of the same distribution field in one turn, which is the only way a user
#: conveys a *shape* and is exactly what an auditor reads as a filled-in profile.
FREQUENCY_MARKERS = (
    "mostly", "usually", "sometimes", "occasionally", "now and then", "now and again",
    "every now and then", "rarely", "hardly ever", "most of the time", "the big one",
    "more often", "less often", "once in a while", "here and there", "typically",
)

#: Rating today against the person's own norm. A *different* defect from `FREQUENCY_MARKERS`
#: joined to two bins: this one needs only **one** bin, because it does not convey a shape,
#: it asserts where the mode is. `gen-004-s8` carried it in both directions across an edit —
#: *"bit of a nicer one than normal"* (mode below this bin) and then, after a repair,
#: *"thirty to fifty each, same as usual"* (mode at this bin) — on a card whose mode is
#: neither. Every pattern here was written by an author or an editor in the pilot, or is the
#: direct form of one that was; `it's been a while` is deliberately absent, because
#: `session_brief` asks for exactly that phrase about the gap since the last conversation.
NORM_COMPARISON_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:same )?as usual\b", "today rated against the norm"),
    # The lookahead is measured, not defensive: bare `the usual` fired on `gen-002-s4`'s
    # *"not the usual thing I use this for"*, which is about the app and not about how this
    # person eats. It was the only false positive in 114 session-readings.
    (r"\bthe usual\b(?!\s+(?:thing|way|kind|sort|routine|business))",
     "today rated against the norm"),
    (r"\bthan (?:normal|usual)\b", "today rated against the norm"),
    (r"\bthan (?:we|i)(?:'d| would)? (?:normally|usually|tend to)\b",
     "today rated against the norm"),
    (r"\b(?:don'?t|do not|doesn'?t) often\b", "today rated against the norm"),
    (r"\brare treat\b", "today rated against the norm"),
    (r"\bstep up from\b", "today rated against the norm"),
)

#: Surface forms that put a turn in a distribution's bin, as regexes (a plain phrase is one).
#: Deliberately narrow: a token that fires on ordinary conversation would make the check
#: unusable, and this check exists to be run automatically over every authored session.
BIN_SURFACE: dict[str, dict[str, tuple[str, ...]]] = {
    "visit_time_distribution": {
        "weekday-lunch": ("weekday lunch", "lunchtime", "lunch break", "midweek lunch"),
        "weekday-evening": ("weeknight", "weekday evening", "week night", "after work"),
        "weekend-day": ("weekend afternoon", "saturday afternoon", "sunday afternoon",
                        "weekend lunch", "sunday lunch", "brunch"),
        "weekend-evening": ("saturday evening", "saturday night", "friday night",
                            "weekend evening"),
        "late-night": ("late night", "late-night", "after midnight", "small hours"),
    },
    "party_size_distribution": {
        # "just me" needs the negative lookahead: "just me and Ruth" is a party of two, and
        # without it the bin fired on an anecdote about the wrong bin entirely.
        "1": ("on my own", "by myself", r"just me\b(?!\s+(?:and|&|,))", "solo"),
        "2": ("just the two of us", "the two of us", "table for two"),
        "3-5": ("four of us", "five of us", "three of us", "table for four"),
        "6+": ("big group", "eight of us", "ten of us", "the whole group", "large group"),
    },
    # Bins are concrete per-head amounts (ADR-0045), not symbolic tiers, so the gate reads
    # actual numbers first and the old qualitative words second — a turn can recite two bins
    # numerically ("under £15 most nights, but £60 for her birthday") just as easily as it
    # can with words.
    "price_band_distribution": {
        "<£15": ("cheap", "a tenner", "under a tenner", "budget",
                 r"£[1-9]\b", r"£1[0-4]\b"),
        "£15-30": ("mid-range", "reasonable", "nothing fancy",
                   r"£1[5-9]\b", r"£2\d\b"),
        "£30-50": ("proper dinner", "somewhere nicer", "a bit special",
                   r"£3\d\b", r"£4\d\b"),
        "£50+": ("blowout", "splash out", "expensive place", "the full works",
                 r"£(?:5\d|[6-9]\d|\d{3,})\b"),
    },
}


#: Bin surfaces for **reading** a transcript, as opposed to `BIN_SURFACE`'s job of catching a
#: profile being recited. The two want opposite things and must not be one table.
#:
#: `BIN_SURFACE` feeds `_distribution_problems`, a rule that re-authors a session when it
#: fires, so it is deliberately narrow — a phrase that matched ordinary conversation would
#: make the gate unusable. `DELIVERY_SURFACE` feeds `verify._incidental`, which asks the
#: opposite question: did this session carry the bin the schedule drew for it? Narrow surfaces
#: answered "no" 12 times out of 12 on an authoring run where the author had in fact delivered
#: every one — *"table for one"*, *"we're not counting the bill"*, *"no ceiling today"*,
#: *"Saturday lunch, two of us"*. A note that is wrong every time it fires is noise, and at
#: 612 sessions it would be about a thousand lines of it.
#: The three distribution fields ADR-0037 schedules as occasions, in the order they are
#: reported. The read path is per field — party size and visit time are structured, price is
#: a phrase list — so the field list has to be its own name rather than the keys of whichever
#: table happens to still be a table.
OCCASION_FIELDS: tuple[str, ...] = (
    "visit_time_distribution", "party_size_distribution", "price_band_distribution",
)


DELIVERY_SURFACE: dict[str, dict[str, tuple[str, ...]]] = {
    # `visit_time_distribution` used to sit here as a phrase list and is now read by
    # `_read_visit_time` instead — it is the one field where a phrase cannot answer the
    # question. "lunchtime" names a daypart and no day class, so the list filed it under
    # `weekday-lunch` and read *"saturday around lunchtime"* as a weekday; keeping the list
    # alongside the structured reader put that wrong bin back into the answer set, which is
    # how a set-valued probe stops being able to report a mismatch at all. Every phrase the
    # list carried is reachable from the daypart cues and the day names — pinned by
    # `test_the_old_visit_time_phrases_are_all_still_read`.
    "party_size_distribution": {
        # A bare "myself" was here and it is removed. English uses it reflexively far more
        # often than it uses it to mean "alone": across the 67-session pilot, nine user turns
        # contained the word, **one** was someone dining alone — and that one says "by myself",
        # which is matched anyway. The other eight are "fanning myself", "ring them myself",
        # "dig it out myself", "made the thing myself". So the surface had zero unique true
        # positives and eight false ones, and one of them blocked the audit: a party of 1 read
        # out of a distractor session about a lost booking link, on a subject whose card gives
        # that bin 0.00. See ADR-0038's verification section.
        #
        # Bare "for one" is narrowed the same way, not removed: at n=10 it has real unique
        # recall — "book me in for one, half twelve" and "half eight for one at Pashan" are
        # both party-of-1 and neither is caught by "table for one" or by `_read_party_size`'s
        # structured reader (its `book...for {n}` pattern has no slot for "book me **in** for
        # one"). But bare it also matched *"asking me for one"* (a document, not a seat) and
        # *"for one of those"* (an event, not a party) — 2 false positives to 2 unique true
        # ones on a 7-hit sample. The exclusions are the two false shapes actually observed,
        # not a guess at the space of them.
        "1": ("on my own", "by myself", r"just me\b(?!\s+(?:and|&|,))", "solo",
              "table for one", r"(?<!asking me )\bfor one\b(?!\s+of\b)",
              "eating alone", "just myself"),
        "2": ("just the two of us", "the two of us", "table for two", "two of us",
              "me and my", "for two", "the pair of us", "and one other"),
        "3-5": ("four of us", "five of us", "three of us", "table for four",
                "table for three", "table for five", "for four", "for three",
                "small group"),
        "6+": ("big group", "eight of us", "ten of us", "the whole group", "large group",
               "six of us", "seven of us", "nine of us", "table for eight",
               "table for six"),
    },
    # Widened from the reader arm's own 142 quotes over this store (ADR-0043). The
    # ambiguous phrases are listed under BOTH bins they could mean rather than assigned to
    # one: `gen-003` says *"at the upper end"* for `£30-50` and `£50+` alike, so the honest
    # reading is that this corpus measures price to one bin of resolution at the top of the
    # scale, not two. `read_occasion` returns a set and the drawn bin is delivered if it is
    # inside it.
    #
    # ADR-0045 renamed the bins from symbolic tiers to concrete per-head amounts, and the
    # qualitative phrases below stay: a page saying "money's not the issue" is still evidence
    # even when it names no number, and `_read_price_amount` (below) is what reads a stated
    # figure directly rather than through a word.
    "price_band_distribution": {
        "<£15": ("cheap", "a tenner", "under a tenner", "budget", "not spend much",
                 "cheap end", "inexpensive", "keep it cheap", "nothing expensive",
                 "not looking to spend much", "don't want to spend much",
                 "do not want to spend much", "nothing pricey", "nothing dear",
                 "nowhere expensive", "skint", "broke till payday", "nobody's flush",
                 "not paying for the privilege", "a tenner a head", "cheap is the main thing",
                 "counting what's left", "paying off"),
        # Bare "middling" is gone: *"Food reviews are middling"* is a judgement about the
        # kitchen, not the bill, and it put a blocking contradiction on `gen-004-s2`.
        "£15-30": ("mid-range", "reasonable", "nothing fancy", "not too dear",
                   "middle of the road", "nothing steep", "middling price",
                   "middling on price"),
        "£30-50": ("proper dinner", "somewhere nicer", "a bit special", "a proper meal",
                   "somewhere good", "upper end", "top of the range", "higher end",
                   "smarter end", "high side", "money's not the issue",
                   "not looking at prices", "not counting the pennies",
                   "spending that kind of money", "don't mind what it costs",
                   "properly good", "feeding the lot of them properly",
                   "an actual occasion", "the good stuff"),
        "£50+": ("blowout", "splash out", "expensive place", "the full works",
                 "not counting the bill", "no ceiling", "money no object",
                 "whatever it costs", "spend what it costs", "tasting menu",
                 "most expensive", "upper end", "top of the range", "higher end",
                 "smarter end", "high side", "money's not the issue",
                 "not looking at prices", "not counting the pennies",
                 "spending that kind of money", "don't mind what it costs",
                 "don't care what it comes to", "this is the one we spend on"),
    },
}



# --- reading an occasion off the page (ADR-0043) ----------------------------
#
# `DELIVERY_SURFACE` above is the phrase layer, and on its own it read 73 of 128 scheduled
# occasions in the pilot where a model reader read 121. The 48-cell gap was not a shortage
# of synonyms. Two of the three fields are not phrase-shaped at all:
#
#   * a party size is a **number** in a person-context — *"seven confirmed"*, *"eight,
#     maybe nine if my brother's partner comes"*, *"put me down for one at half twelve"* —
#     and no phrase list reaches those without listing every number twice;
#   * a visit time is a **daypart and a day class**, and the day class is usually not on
#     the page at all: the user says *"lunch today"* and the day comes from the calendar.
#     The session's own date is on disk and the probe was not reading it.
#
# So the phrase layer stays and two structured readers are unioned onto it. Three rules
# hold the result honest:
#
#   1. **the answer is a set.** *"at the upper end"* cannot separate `£30-50` from `£50+`, and
#      a probe that picks one is inventing a resolution the page does not carry. A drawn bin
#      inside the set is delivered; the set is reported when it is not.
#   2. **union, never replacement.** Every cell the phrase layer already found is still
#      found, so widening cannot quietly lose a cell — it is checked by a test.
#   3. **the calendar is only consulted for "today".** Where the page names its own day
#      (*"the 28th, that's a Saturday"*) the page wins. The session date is used only when
#      the user says the outing is today or tonight, where the two are the same day by
#      construction.

#: Number words a party size is written with. Digits are read too; the words are what the
#: authors actually use, and *"eight, maybe nine"* is two candidates rather than a range.
_NUMBER_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "eighteen": 18, "twenty": 20,
    "a couple": 2, "a dozen": 12,
}

#: What has to sit next to a number before it counts as a head count. Without this the
#: probe reads *"twelve thirty, just me"* as a party of twelve and *"book us for
#: five-forty-five"* as a party of five — both measured on the pilot before the guard.
_PARTY_CONTEXTS: tuple[str, ...] = (
    r"(?:{n})\s+(?:of us|people|persons?|adults|confirmed|covers|heads?)\b",
    r"\btable\s+for\s+(?:{n})\b",
    # The negative lookbehind is not decoration: *"the booking one — if I turn that off"*
    # is a distractor session about a notification toggle, and without it the probe read a
    # party of 1 out of it and called `gen-003-s4` a contradiction of a card that gives the
    # bin 0.00. Third bare-token false positive of this shape after "myself" and "dry".
    r"(?<!the )\bbook(?:ing)?(?:\s+(?:it|us|me|them))?(?:\s*,)?\s+(?:for\s+)?(?:{n})\b",
    r"\bparty\s+of\s+(?:{n})\b",
    r"\bput\s+me\s+down\s+for\s+(?:{n})\b",
    r"\bfor\s+(?:{n})\s+(?:of us|people)\b",
    r"\b(?:it'?s|we'?re|there'?s|there are)\s+(?:{n})\s+of\s+us\b",
)

#: A number reads as a clock time, not a head count, when one of these follows it.
_TIME_TAIL = r"(?:\s*[:.]\d{2}|\s*(?:o'?clock|ish|am|pm|thirty|fifteen|forty|forty-five|" \
             r"twenty|ten|five)\b|-)"

#: Meal and daypart words, and the late-night cues that override both. `late-night` is a
#: day-class-free bin (`schedule.WEEKEND_BINS` deliberately excludes it), so a late cue
#: answers the whole question on its own.
_DAY_CUES: tuple[str, ...] = (
    "lunch", "lunchtime", "brunch", "midday", "afternoon", "daytime", "middle of the day",
    "a gap in the middle", "matinee",
)
_EVENING_CUES: tuple[str, ...] = (
    "dinner", "evening", "tonight", "this evening", "supper", "after work", "night out",
    # Bare "night" is safe here only because the late cues are checked first and return
    # outright: "saturday night" is an evening, "late night" and "gone eleven" are not.
    "night", "weeknight", "week night", "school night",
)
_LATE_CUES: tuple[str, ...] = (
    "late night", "late-night", r"\bthis late\b", r"\bit'?s late\b", r"\bgone eleven\b",
    # "it'd be late, we've only just worked out we're free" — the conditional is how a
    # same-day plan states the hour before the hour is settled, and the probe missed
    # `gen-001-s6` on exactly that (measured, ADR-0043).
    r"\bit'?(?:d|ll) be late\b", r"\bwould be late\b", r"\bbe late\b",
    r"\bgone half ten\b", r"\bgone ten\b", r"\bafter eleven\b", r"\bafter midnight\b",
    r"\bnearly eleven\b", r"\bpast eleven\b", "midnight", "small hours", "still serving",
    r"\bnothing before ten\b", r"\bnothing (?:round here|open)'?s? (?:serving|shut)\b",
    r"\blast seating\b", r"\bstill out\b", r"\bthis hour\b", r"\bopen late\b",
)

_WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday")
_WEEKEND_NAMES = ("saturday", "sunday")

#: The user saying the outing is today. Only here is the session's own date consulted —
#: see rule 3 above.
_TODAY_CUES = (
    r"\btoday\b", r"\btonight\b", r"\bthis evening\b", r"\bthis afternoon\b",
    r"\bright now\b", r"\bwe'?re out\b", r"\bi'?m out\b", r"\bstill out\b",
    r"\bnow then\b", r"\bthis lunchtime\b",
)


def _number_pattern() -> str:
    words = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
    return rf"(?:\d{{1,2}}|{words})"


def _as_number(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


def _party_bin(size: int) -> str | None:
    if size <= 0 or size > 40:      # a party of 60 is a coach trip, not this dataset
        return None
    if size == 1:
        return "1"
    if size == 2:
        return "2"
    return "3-5" if size <= 5 else "6+"


def _read_party_size(turns: Sequence[str]) -> tuple[set[str], str]:
    """Every head count the user states, as bins. Set-valued: *"eight, maybe nine"* is one
    bin, *"ten seats is two of us stood at the wall"* is two, and reporting both is more
    honest than picking."""
    number = _number_pattern()
    found: set[str] = set()
    quote = ""
    for turn in turns:
        lowered = turn.lower()
        for template in _PARTY_CONTEXTS:
            pattern = template.format(n=number)
            for match in re.finditer(pattern, lowered):
                token = next((g for g in match.groups() if g), None)
                if token is None:
                    token = re.search(number, match.group(0)).group(0)
                tail = lowered[match.end():match.end() + 12]
                if re.match(_TIME_TAIL, tail):
                    continue
                size = _as_number(token)
                name = _party_bin(size) if size is not None else None
                if name:
                    found.add(name)
                    quote = quote or turn[:90]
    return found, quote


def _clock_hours(lowered: str) -> set[int]:
    """Hours named in a turn, as 1–12 clock faces. Deliberately not resolved to 24h — the
    corpus writes *"eight o'clock"* for dinner and *"half twelve"* for lunch, and the
    daypart is decided below with the meal words rather than by guessing am/pm."""
    number = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
    hours: set[int] = set()
    for match in re.finditer(rf"\b(?:half|quarter past|quarter to)\s+(\d{{1,2}}|{number})\b",
                             lowered):
        value = _as_number(match.group(1))
        if value:
            hours.add(value)
    for match in re.finditer(r"\b(\d{1,2})[:.](\d{2})\b", lowered):
        hours.add(int(match.group(1)))
    for match in re.finditer(rf"\b(\d{{1,2}}|{number})\s*(?:o'?clock|ish|am|pm)\b", lowered):
        value = _as_number(match.group(1))
        if value:
            hours.add(value)
    for match in re.finditer(
        rf"\b(\d{{1,2}}|{number})[\s-](?:thirty|fifteen|forty-five|forty five)\b", lowered
    ):
        value = _as_number(match.group(1))
        if value:
            hours.add(value)
    return {h for h in hours if 1 <= h <= 12}


def _dayparts(lowered: str) -> set[str]:
    """`day`, `evening`, `late`, or several. A late cue wins outright; otherwise the meal
    words and the clock face are unioned, because a page saying *"lunch"* and *"one
    o'clock"* is saying the same thing twice and a page saying neither says nothing."""
    if any(re.search(cue, lowered) for cue in _LATE_CUES):
        return {"late"}
    parts: set[str] = set()
    if any(re.search(r"\b" + cue + r"\b", lowered) for cue in _DAY_CUES):
        parts.add("day")
    if any(re.search(r"\b" + cue + r"\b", lowered) for cue in _EVENING_CUES):
        parts.add("evening")
    if not parts:
        for hour in _clock_hours(lowered):
            if hour in (11, 12, 1, 2, 3, 4):
                parts.add("day")
            elif hour in (5, 6, 7, 8, 9, 10):
                parts.add("evening")
    return parts


def _day_classes(lowered: str, on_date: "date | None") -> set[str]:
    """`weekday`, `weekend`, or both when the page does not say.

    Friday is a **weekday** here, matching `schedule.WEEKEND_BINS`, which is what the map
    counts against. The phrase layer used to file "friday night" under `weekend-evening`
    and that was the probe disagreeing with the schedule it audits.
    """
    classes: set[str] = set()
    if any(re.search(r"\b" + name + r"s?\b", lowered) for name in _WEEKEND_NAMES):
        classes.add("weekend")
    if any(re.search(r"\b" + name + r"s?\b", lowered) for name in _WEEKDAY_NAMES):
        classes.add("weekday")
    if re.search(r"\bweekend\b", lowered):
        classes.add("weekend")
    if re.search(r"\b(?:weeknight|week night|school night|weekday)\b", lowered):
        classes.add("weekday")
    if not classes and on_date is not None and any(
        re.search(cue, lowered) for cue in _TODAY_CUES
    ):
        classes.add("weekend" if on_date.weekday() >= 5 else "weekday")
    return classes


def _read_visit_time(
    turns: Sequence[str], on_date: "date | None"
) -> tuple[set[str], str]:
    """The visit-time bins the page is consistent with, from a daypart and a day class."""
    found: set[str] = set()
    quote = ""
    for turn in turns:
        lowered = turn.lower()
        parts = _dayparts(lowered)
        if not parts:
            continue
        classes = _day_classes(lowered, on_date) or {"weekday", "weekend"}
        bins = set()
        if "late" in parts:
            bins.add("late-night")
        for day_class in classes:
            if "day" in parts:
                bins.add("weekend-day" if day_class == "weekend" else "weekday-lunch")
            if "evening" in parts:
                bins.add("weekend-evening" if day_class == "weekend" else "weekday-evening")
        if bins:
            found |= bins
            quote = quote or turn[:90]
    return found, quote


def _read_phrases(field_id: str, turns: Sequence[str]) -> tuple[set[str], str]:
    """The original phrase layer, kept whole, but collecting **every** bin that matches
    rather than the first in alphabetical order. First-match-wins is what made
    *"saturday around lunchtime"* read as `weekday-lunch`: the day was never checked."""
    found: set[str] = set()
    quote = ""
    for turn in turns:
        lowered = turn.lower()
        for name, surfaces in DELIVERY_SURFACE.get(field_id, {}).items():
            if any(
                re.search(s if "\\" in s else r"\b" + re.escape(s) + r"\b", lowered)
                for s in surfaces
            ):
                found.add(name)
                quote = quote or turn[:90]
    return found, quote


#: A per-head figure, however it's said: "£42", "42 quid", "42 pounds a head".
_PRICE_AMOUNT: str = r"£\s?(\d+(?:\.\d+)?)|\b(\d+)\s*(?:quid|pounds?)\b"


def _price_bin_for_amount(amount: float) -> str:
    """Where a stated figure falls. Boundaries sit on the bin they name: exactly `15` is
    already `£15-30`, exactly `50` is already `£50+` — `<£15` is the one bin that is a
    strict less-than, because it is the one bin named that way."""
    if amount < 15:
        return "<£15"
    if amount < 30:
        return "£15-30"
    if amount < 50:
        return "£30-50"
    return "£50+"


def _read_price_amount(turns: Sequence[str]) -> tuple[set[str], str]:
    """Every per-head figure the page states, as bins. ADR-0045 made this bin genuinely
    numeric, so — unlike the other two distribution fields, which were always read from a
    daypart or a head count rather than a word — price now has a structured reader of its
    own alongside the phrase list `_read_phrases` still runs, because a page can settle
    price without ever naming a figure (*"nothing fancy"* commits no number)."""
    found: set[str] = set()
    quote = ""
    for turn in turns:
        for match in re.finditer(_PRICE_AMOUNT, turn.lower()):
            amount = float(match.group(1) or match.group(2))
            found.add(_price_bin_for_amount(amount))
            quote = quote or turn[:90]
    return found, quote


def read_occasion(
    field_id: str, transcript_text: str, *, on_date: "date | None" = None,
    speakers: str = "page",
) -> tuple[frozenset[str], str]:
    """Which bins of `field_id` the user's turns are consistent with, and one quote.

    An empty set means the probe found nothing, which after ADR-0043 is a much stronger
    statement than it used to be — but still a note, not a finding: the reader remains the
    arbiter, and the six cells both arms call empty are the ones worth reading.
    """
    # Price is the one field read from BOTH speakers, and the asymmetry is measured rather
    # than stylistic: the user states a party size and a time outright, but a price band is
    # proposed by the bot — *"Are you happy for me to look at the upper end?"* — and
    # assented to with *"yeah"*. Reading only the user loses 20 of the 32 price occasions in
    # the pilot, every one of which the model reader counts as delivered.
    both = field_id == "price_band_distribution" and speakers == "page"
    turns = (
        [t for _, t in speaker_turns(transcript_text)]
        if both
        else user_turns(transcript_text)
    )
    found, quote = _read_phrases(field_id, turns)
    if field_id == "party_size_distribution":
        extra, extra_quote = _read_party_size(turns)
    elif field_id == "visit_time_distribution":
        extra, extra_quote = _read_visit_time(turns, on_date)
    elif field_id == "price_band_distribution":
        extra, extra_quote = _read_price_amount(turns)
    else:
        extra, extra_quote = set(), ""
    found |= extra
    return frozenset(found), quote or extra_quote



#: What a bot's intake question is asking *about*, per field. Read by two callers that must
#: not drift: `_intake_problems` below rejects the turn at authoring time, and `verify`'s
#: `_unbriefed_intake` reports it off the stored page.
#:
#: A denial only contradicts the card when the card carries the field the question named —
#: `gen-004-s11` asks about step-free entry and gets a truthful "no, nothing like that" from
#: someone whose only constraint is `vegan`, and reporting that as a denied constraint would
#: be the gate crying wolf on a correct page. The generic asks belong to every field, because
#: a question that names no field is answered for all of them at once — which is the §8.1
#: cross-field leak ADR-0035 already had to fix once on the authoring side.
INTAKE_FIELD_PATTERNS: dict[str, str] = {
    # Diet-naming only. The umbrella phrasings this used to carry — `dietar`, "anything the
    # kitchen", "what you eat" — moved to `INTAKE_UMBRELLA`, because each of them reaches
    # `allergies` as surely as it reaches this field. What is left has to be a question about
    # a *diet*: `\bdiet\b` alone would fire on "a diet coke", so the word is only read in the
    # frames that put it to the person.
    "dietary": (r"(?:particular|specific|any|special)\s+diets?\b"
                r"|diets?\s+(?:you|they)\s+follow"
                r"|follow(?:ing)?\s+(?:any|a)\s+(?:particular\s+|specific\s+)?diets?\b"),
    "allergies": r"allerg",
    "accessibility_needs": r"step-free|step free|wheelchair|access requirement|access needs",
    # `dry\b` was here and matched no alcohol-free question in any store: 10 hits across
    # three pilot stores, every one of them "The Foundry" or "dry-fried" — three of them
    # raised `unbriefed_intake` on `gen-000-s1`, a venue name. Zero unique true positives,
    # so the sense is carried by words that only mean the drink.
    "alcohol_free_required": r"alcohol|teetotal|soft drinks|dry january|going dry",
}

#: Preference fields the bot screens for, measured rather than guessed. ADR-0041.
#:
#: Same defect as `INTAKE_FIELD_PATTERNS` on a field the intake gate cannot reach: the bot asks
#: something the session did not brief, the user must answer, and the answer is invented. The
#: reader arm found it twice as a *contradiction* — both `travel_radius`, both from *"Any part
#: of town?"* against a card that says `own_neighborhood` — and a lexical sweep of the bot's
#: turns found the ask itself on **39 of 67 sessions in the current store** (25, 36 and 10 in
#: the three earlier ones). The reader saw two because it sampled 24 sessions and only the
#: answers that landed against the card read as findings; the rest are leaks, not contradictions.
#:
#: The carve-out differs from the intake gate's and the difference matters. A hard constraint is
#: only ever asked for by a `disclosure` beat, so that is the only exemption there. A preference
#: is reached by `request`, `rejection` or `reaction` just as legitimately, so **any** beat on
#: the field exempts the ask. All 45 matches in the current store were read: 39 unbriefed, 6
#: briefed and correctly exempt, zero false positives.
#:
#: `price_sensitivity`-shaped asks (*"what sort of budget?"*) measured 2-4 per store and are
#: **deliberately absent**: the answer is briefed by the scenery whenever a price band was
#: drawn, so the carve-out would have to read the occasion rather than the beats. Small, and
#: its own piece of work. The same holds for the time and party-size asks a wider sweep turns
#: up (9 and 3 in the pre-re-author store) — THIS PARTICULAR OUTING states when and who is
#: going, so those questions have a briefed answer to land on and invent nothing.
#:
#: `noise_preference` is here on one measured match, not on principle. A 17-field sweep of
#: every bot question in four stores (280 sessions) found exactly one unbriefed ask this table
#: could not already reach — `gen-004-s2`'s *"somewhere quiet, or somewhere with a bit more
#: atmosphere?"* — with zero briefed matches and zero false positives. `formality_ceiling`
#: fired **nothing** in all four, so it stays out: a gate with no measured target is a guess,
#: and the cost of guessing here is retries spent on sessions nothing is wrong with.
SCREENING_FIELD_PATTERNS: dict[str, str] = {
    "travel_radius": (
        r"part of town|part of the city|what area|which area|any area|whereabouts"
        r"|how far (?:are you |do you )?(?:willing|want)|area (?:you'?d|to) (?:like|stay)"
        r"|stay (?:in|near)"
    ),
    "noise_preference": (
        r"somewhere quiet|quiet or|lively or|livelier or|buzzy or|quiet(?:er)? (?:place|spot)"
        r"|bit more atmosphere"
    ),
}


#: An ask that names no field at all. Answered for every constraint at once, which is why it
#: is never exempted by a beat: a `disclosure` beat requires the bot to *name* its field
#: (`DISCLOSURE_PROBES`), so a question this vague cannot be the one the schedule scheduled.
INTAKE_GENERIC = (
    r"anything i should (?:note|flag|know)|anything to (?:note|flag)"
    r"|anything (?:you|they) avoid|anything else i should know"
)

#: A bot turn screening the user for a preference field. BOT turns only, like `INTAKE_ASK`.
SCREENING_ASK = re.compile("|".join(SCREENING_FIELD_PATTERNS.values()), re.I)


#: An ask that names a topic spanning both food constraints. Narrower than `INTAKE_GENERIC`
#: — it does name a subject — and still not a question about one field: *"any dietary
#: requirements?"* is the umbrella term in ordinary English, and a person who answers it in
#: the negative has denied allergies too, whatever the schema thinks. That reading is the
#: reader's own verdict on `gen-001-s1`, measured against the corpus by
#: `probe_constraint_denial.py`, and it used to live as a local patch in `verify` because
#: widening it here would have flagged the then-correct disclosure phrasing — *"any dietary
#: requirements?"* for a `dietary` beat — as screening `allergies` unbriefed.
#:
#: `DISCLOSURE_PROBES["dietary"]` no longer prescribes that phrasing; it forbids it, and asks
#: for a diet named by example instead. So the objection is gone and the umbrella belongs
#: here, in the one table both readers read.
INTAKE_UMBRELLA = r"dietar|anything the kitchen|what (?:you|they) eat"

#: What the umbrella reaches. Both, always — that is what makes it the umbrella.
UMBRELLA_FIELDS: frozenset[str] = frozenset({"dietary", "allergies"})

#: A bot turn screening the user for a hard constraint. Matched against BOT turns only.
INTAKE_ASK = re.compile(
    "|".join(list(INTAKE_FIELD_PATTERNS.values()) + [INTAKE_UMBRELLA, INTAKE_GENERIC]), re.I
)


def asked_intake_fields(line: str) -> set[str]:
    """Which constraint fields this question puts to the user."""
    if re.search(INTAKE_GENERIC, line, re.I):
        return set(fields.CONSTRAINT_IDS)
    reached = {
        fid for fid, pattern in INTAKE_FIELD_PATTERNS.items()
        if re.search(pattern, line, re.I)
    }
    if re.search(INTAKE_UMBRELLA, line, re.I):
        reached |= UMBRELLA_FIELDS
    return reached


def unbriefed_intake_fields(line: str, briefed: Iterable[str]) -> set[str]:
    """Which of them nothing in this session briefed — the gate's actual question.

    **A beat exempts a field; it does not exempt a phrasing.** The carve-out exists because
    `DISCLOSURE_PROBES` requires a scheduled ask to *name its field*, so a question that names
    no field, or names two at once, cannot be the one the schedule scheduled. `_intake_problems`
    stated that rule in its docstring and then subtracted `briefed` from the generic case
    anyway, which meant one disclosure beat quietly licensed *"anything I should note?"* on the
    other three fields' behalf.

    So generic and umbrella asks are reported whole. `gen-001-s1` is what it costs otherwise:
    a `dietary` disclosure beat, an umbrella ask, a blanket denial, and a card carrying
    shellfish — the page stating the negation of ground truth on a field nothing scheduled.
    """
    reached = asked_intake_fields(line)
    if re.search(INTAKE_GENERIC, line, re.I):
        return reached
    if re.search(INTAKE_UMBRELLA, line, re.I):
        return (reached - set(briefed)) | set(UMBRELLA_FIELDS)
    return reached - set(briefed)


@dataclass(frozen=True)
class Problem:
    """One deterministic finding in an authored session."""

    kind: str        # "self_summary" | "distribution_enumeration" | "norm_comparison"
                     # | "disclosure_volunteered"
                     # | "blanket_denial"
                     # | "unbriefed_intake" | "alternation" | "third_party_*"
    turn: int        # index of the user turn, 0-based over user turns
    detail: str
    quote: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"[{self.kind}] user turn {self.turn}: {self.detail} — {self.quote!r}"


#: One message label, with the v0.4 message number in front of it.
#:
#: The number is **0-based on purpose**: a message number is therefore the same integer as
#: `speaker_turns()`'s index and `Locator.turn_index`, so a number a reader cites, a number
#: an editor splices at, and a number this module counts with are one numbering with no
#: translation between them. An off-by-one here would be invisible and would mis-target every
#: repair.
#:
#: The number is *optional in the pattern* and mandatory in `_numbering_problems`. Parsing
#: has to stay tolerant because unnumbered transcripts still exist and must remain readable —
#: every pre-v0.4 store (`runs/authored-pilot-pre-edit/`) and most test fixtures — but
#: nothing newly authored is allowed to be missing them. Tolerant reader, strict gate.
TURN_LABEL: str = r"(?im)^[ \t]*(?:\[(?P<num>\d+)\][ \t]*)?(?P<speaker>USER|BOT)[ \t]*:"


def user_turns(transcript: str) -> list[str]:
    """The USER lines of a transcript, in order.

    Split on the speaker labels the author is told to emit. A transcript that carries none is
    returned as a single turn rather than silently as zero, so a malformed session fails the
    checks loudly instead of passing them vacuously — the same reason `probe_sessions/`'s
    grade parser raises rather than defaulting.
    """
    if not re.search(TURN_LABEL, transcript):
        return [transcript]
    return [text for _n, speaker, text, _s, _e in _turn_spans(transcript)
            if speaker == "USER"]


def speaker_turns(transcript: str) -> list[tuple[str, str]]:
    """Every turn as `(speaker, text)` in order, speaker upper-cased.

    `user_turns` drops the bot, which is right for the self-summary rules — they are about
    what the *user* says. The disclosure rule is about **who speaks first**, so it needs both
    sides in one sequence. Same malformed-transcript convention: a transcript carrying no
    speaker labels comes back as a single USER turn rather than as zero, so the checks fail
    loudly rather than vacuously.

    v0.4: all three readers (`user_turns`, this, `_turn_spans`) now share `TURN_LABEL` and
    one implementation. They used to carry three copies of the same regex, which was three
    places for a message-number prefix to be understood differently.
    """
    if not re.search(TURN_LABEL, transcript):
        return [("USER", transcript)]
    return [(speaker, text) for _n, speaker, text, _s, _e in _turn_spans(transcript)]


def message_numbers(transcript: str) -> list[int | None]:
    """The declared `[n]` of each message, `None` where the message carries no number."""
    if not re.search(TURN_LABEL, transcript):
        return [None]
    return [number for number, _sp, _t, _s, _e in _turn_spans(transcript)]


def _excerpt(text: str, match: re.Match[str] | int, width: int = 60) -> str:
    start = match.start() if isinstance(match, re.Match) else match
    lo = max(0, start - width // 3)
    return text[lo:lo + width].replace("\n", " ").strip()


@dataclass(frozen=True)
class Locator:
    """Where a `Finding.quote` sits in a transcript: which turn, and its exact span.

    `turn_index` lines up with `speaker_turns()` — both are derived from the same speaker
    split, just kept separately rather than one refactored into the other, so nothing that
    already depends on `speaker_turns()`'s existing return shape can shift under this change.
    """

    turn_index: int
    speaker: str
    start: int
    end: int


def _turn_spans(transcript: str) -> list[tuple[int | None, str, str, int, int]]:
    """`(number, speaker, text, start, end)` per turn, offsets into the raw string.

    The single parser the other readers delegate to. `finditer` rather than `re.split`
    because `re.split` throws the match positions away, and `.strip()`ping a turn after the
    fact loses exactly the offset a splice needs back.

    `start`/`end` bound the **body only**, after the `[n] SPEAKER:` label. That is what keeps
    `splice_turn` mechanical: replacing a message rewrites its text and never touches its
    number or its speaker, so a repair cannot renumber the page underneath a finding that
    already cited it.
    """
    matches = list(re.finditer(TURN_LABEL, transcript))
    if not matches:
        return [(None, "USER", transcript.strip(), 0, len(transcript))]
    spans = []
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(transcript)
        body = transcript[body_start:body_end]
        stripped = body.strip()
        lead = len(body) - len(body.lstrip())
        start = body_start + lead
        declared = match.group("num")
        spans.append((
            int(declared) if declared is not None else None,
            match.group("speaker").strip().upper(),
            stripped, start, start + len(stripped),
        ))
    return spans


def _find_span(text: str, needle: str) -> tuple[int, int] | None:
    """`needle`'s span in `text` — exact first, then whitespace-normalized.

    A reader is told to "quote verbatim", not to preserve exact spacing, and a paraphrase of
    a line break or a double space is still the same turn. Search stays in `text`'s own
    coordinates throughout — a collapsed copy would need its offsets mapped back, which is not
    1:1 through arbitrary whitespace — so the fallback turns `needle`'s own whitespace into
    `\\s+` and searches the original string directly.
    """
    idx = text.find(needle)
    if idx >= 0:
        return idx, idx + len(needle)
    tokens = needle.split()
    if not tokens:
        return None
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    match = re.search(pattern, text)
    return (match.start(), match.end()) if match else None


def locate_quote(transcript: str, quote: str) -> Locator | None:
    """The turn `quote` came from, or `None` when it cannot be found there.

    Most `Finding.quote`s are one reader's or one rule's excerpt of a single turn, and those
    resolve directly. `verify._unbriefed_intake`'s `denied_constraint` quote is the one shape
    that spans two turns (`"{bot ask} / {user reply}"`) — unfindable as one string by
    construction — so the fallback splits on that separator and locates the second half, the
    user's denial, which is the half an edit would actually change.

    Returns `None` rather than a guess: nothing downstream may patch a turn this could not
    verify the quote came from.
    """
    quote = (quote or "").strip()
    if not quote:
        return None
    span = _find_span(transcript, quote)
    if span is None and " / " in quote:
        quote = quote.rsplit(" / ", 1)[-1].strip()
        span = _find_span(transcript, quote) if quote else None
    if span is None:
        return None
    start, _ = span
    for index, (_num, speaker, _text, turn_start, turn_end) in enumerate(
        _turn_spans(transcript)
    ):
        if turn_start <= start < turn_end:
            return Locator(index, speaker, turn_start, turn_end)
    return None


def locate_message(transcript: str, number: int) -> Locator | None:
    """The message numbered `number`, or `None` if the page has no such message.

    The counterpart to `locate_quote`, and the one a v0.4 finding should prefer: a quote can
    be paraphrased, span two speakers, or appear twice, and `locate_quote` has to guess. A
    message number is what the reader was actually looking at.
    """
    spans = _turn_spans(transcript)
    for index, (declared, speaker, _text, start, end) in enumerate(spans):
        if (declared if declared is not None else index) == number:
            return Locator(index, speaker, start, end)
    return None


def splice_turn(transcript: str, locator: Locator, new_text: str) -> str:
    """`transcript` with the turn at `locator` replaced by `new_text`.

    Purely mechanical — the speaker label and every other turn are untouched — because the
    model supplying `new_text` is never asked to reproduce the rest of the session, only this
    one turn; asking it to and trusting it to leave everything else byte-identical is the risk
    this function exists to avoid.
    """
    return transcript[:locator.start] + new_text.strip() + transcript[locator.end:]


def splice_turns(
    transcript: str, edits: Sequence[tuple[Locator, str]]
) -> str:
    """`transcript` with several turns replaced at once.

    Applied **last message first**, because every `Locator` holds absolute offsets into the
    string it was computed from: rewriting message 6 moves every offset after it, so splicing
    forwards would leave message 9's locator pointing at the wrong span. Going backwards, the
    only offsets that move are ones already spent.

    This exists because the two defects the pilot's editor could not repair were both
    multi-message by nature — a bot's unbriefed question plus the user's answer to it, and a
    rejection in one message answered by an acceptance three messages later. Neither is
    expressible as a single-turn patch, and rewriting one half at a time cannot converge:
    each half on its own still reads as the defect.
    """
    out = transcript
    for locator, new_text in sorted(edits, key=lambda e: e[0].start, reverse=True):
        out = out[:locator.start] + new_text.strip() + out[locator.end:]
    return out


def renumber(transcript: str) -> str:
    """`transcript` with every `[n]` relabelled contiguously from 0.

    Only ever needed after a **deletion** — a replacement keeps the message count, so
    `splice_turns` leaves numbering correct without touching it. Renumbering is the price of
    removing a message, and it is not free: every number anyone else already cited for this
    session moves. `apply_message_edits` returns the remap so callers can follow it, and
    `editor.remap_finding` is what follows it.
    """
    spans = _turn_spans(transcript)
    if not spans or spans[0][1] is None:                  # pragma: no cover - defensive
        return transcript
    return "\n".join(
        f"[{index}] {speaker}: {text}"
        for index, (_num, speaker, text, _s, _e) in enumerate(spans)
    ) + "\n"


def apply_message_edits(
    transcript: str, edits: Sequence[tuple[Locator, str | None]]
) -> tuple[str, dict[int, int | None]]:
    """`transcript` with messages replaced, removed, or both, plus the number remap.

    A `None` replacement **deletes** the message outright — label and body. Some defects are
    not a badly written message but a message that should not be there: `gen-001-s1`'s bot
    asks an intake question the session never briefed, and because the field is UNEVIDENCED
    there is no answer to it that is both true to the card and permitted by the map. Rewriting
    either half cannot fix that; removing the exchange can.

    Returns `(text, remap)` where `remap` maps every old message number to its new one, or to
    `None` if it was deleted. With no deletions the remap is the identity and the text is
    spliced byte-for-byte (`splice_turns`); with deletions the page is rebuilt and renumbered,
    which normalises label spacing on every message, not only the edited ones.

    Two things this deliberately does **not** check, because `check_session` already does and
    doing it twice would mean two rules that can disagree:

    * **alternation.** Deleting a bot's question but not the user's answer to it leaves two
      consecutive USER messages, which `_alternation_problems` gates. That is why a deletion
      is usually a *pair*.
    * **numbering.** The rebuilt page is renumbered here and re-checked there.
    """
    if not edits:
        return transcript, {}
    replaced: dict[int, str | None] = {loc.turn_index: new for loc, new in edits}
    if all(new is not None for new in replaced.values()):
        spliced = splice_turns(transcript, [(loc, new) for loc, new in edits
                                            if new is not None])
        numbers = message_numbers(transcript)
        return spliced, {
            (n if n is not None else i): (n if n is not None else i)
            for i, n in enumerate(numbers)
        }

    spans = _turn_spans(transcript)
    kept: list[str] = []
    remap: dict[int, int | None] = {}
    for index, (declared, speaker, body, _start, _end) in enumerate(spans):
        old = declared if declared is not None else index
        if index in replaced and replaced[index] is None:
            remap[old] = None
            continue
        text = replaced[index] if index in replaced else body
        remap[old] = len(kept)
        kept.append(f"[{len(kept)}] {speaker}: {str(text).strip()}")
    return ("\n".join(kept) + "\n" if kept else ""), remap


def _numbering_problems(transcript: str, *, required: bool) -> list[Problem]:
    """§6.1's message-numbering rule: `[0]`, `[1]`, `[2]`, … with no gaps.

    Numbering is what lets a finding name a *span* of messages rather than one quote, so it
    has to be exactly right or every downstream reference is off. Three ways it goes wrong
    and all three are caught here: absent when required, present on some messages but not
    others, and present but not matching position (a gap, a repeat, or a start other than 0).

    ``required`` is False by default so that unnumbered transcripts stay *parseable* — the
    pre-v0.4 stores and the test fixtures — while `author.py` and `editor.py` pass True so
    nothing newly written can skip it.
    """
    numbers = message_numbers(transcript)
    if not re.search(TURN_LABEL, transcript):
        return []
    declared = [n for n in numbers if n is not None]
    if not declared:
        if not required:
            return []
        return [Problem("unnumbered", 0,
                        "no message carries a [n] number; every message must be numbered "
                        "from [0] so findings can point at one", "")]
    if len(declared) != len(numbers):
        missing = [i for i, n in enumerate(numbers) if n is None]
        return [Problem("numbering_partial", missing[0],
                        f"{len(missing)} of {len(numbers)} messages carry no [n] number "
                        f"(first at position {missing[0]}); numbering is all or nothing", "")]
    wrong = [(i, n) for i, n in enumerate(numbers) if n != i]
    if wrong:
        index, got = wrong[0]
        return [Problem("misnumbered", index,
                        f"message at position {index} is numbered [{got}]; numbers must run "
                        f"[0], [1], [2], … with no gaps or repeats", "")]
    return []


def check_session(
    transcript: str, *, self_report: str | None = None,
    beats: Sequence[Mapping[str, Any]] = (),
    require_numbering: bool = False,
    free_offers: Sequence[int] = (),
) -> list[Problem]:
    """Every deterministic §6.1 violation in one authored session.

    Empty means the session passes the rules; it does not mean the session is clean, since
    the unquantified generalizations and everything about register remain the §7.2 audit's
    job. This function is the floor, not the gate.

    ``self_report`` is the §4.2 scripted self-description, **declared by the author**, and it
    is exempt. It has to be, and the reason is measured: run without the exemption over the
    50 sessions of `probe_sessions/`, this check flags `gen-003-s0`'s *"I usually shop around
    and compare prices"* — which is not an authoring defect but the scripted claim the hard
    case exists to test, on a subject whose gold is `ignores_price`. A retry loop would have
    regenerated it until the quota member stopped being a quota member. That is the third
    time on this project that a gate has eaten the construction it was meant to protect
    (§4.4's coherence audit on the anti-correlation quota, §7.2's fidelity audit on this same
    quota), and the first time it was caught before the gate was wired in.

    ``free_offers`` is the same mechanism one level up: the message numbers of bot turns the
    author wrote outside `_OFFER_TEMPLATE`'s skeleton. Declared, they pass `_offer_problems`
    and are counted; undeclared, they are a finding. That is the whole difference between an
    escape rate you read and one an audit has to discover.

    The exemption is a **declared span, not a session-level pass**: the author returns the
    line, and only that line is exempt. A rule cannot infer which self-summary was scripted —
    intent is not on the page — so the artifact carries it. This is the same anchor §7.2's
    scoped fidelity audit and §7.3's grader need for the same subject.
    """
    exempt = (self_report or "").strip().lower()
    problems: list[Problem] = _numbering_problems(
        transcript, required=require_numbering
    )
    for index, turn in enumerate(user_turns(transcript)):
        lowered = turn.lower()
        # Narrow on purpose: the declared line is exempt from the self-summary rule, which
        # is the rule it is scripted to break, and from `norm_comparison`, which is the same
        # rule reached sideways. It is not exempt from `distribution_enumeration`, and the
        # rest of the turn is not exempt from anything.
        scripted = bool(exempt and exempt in lowered)
        if not scripted:
            for pattern, detail in SELF_SUMMARY_PATTERNS:
                match = re.search(pattern, lowered)
                if match:
                    problems.append(
                        Problem("self_summary", index, detail, _excerpt(turn, match))
                    )
                    break
        problems.extend(_distribution_problems(index, turn, lowered, scripted=scripted))
    problems.extend(_disclosure_problems(transcript, beats))
    problems.extend(_blanket_denial_problems(transcript, beats))
    problems.extend(_alternation_problems(transcript))
    problems.extend(_third_party_problems(transcript, beats))
    problems.extend(_intake_problems(transcript, beats))
    problems.extend(_screening_problems(transcript, beats))
    problems.extend(_offer_problems(transcript, beats, free_offers))
    return problems


#: Ways a companion gets named. Deliberately about *people*, not about constraints: the rule
#: below asks whether anyone other than the user is in the conversation at all, which is a
#: question a word list can answer. Whether a particular requirement belongs to them is not,
#: and is left to the audit.
COMPANION_MARKERS: tuple[str, ...] = (
    "one of them", "one of the", "my sister", "my brother", "my mate", "my friend",
    "my wife", "my husband", "my partner", "my cousin", "my mum", "my mother",
    "my dad", "my father", "my colleague", "my daughter", "my son", "someone",
    "somebody", "the others", "neither of them", "both of them", "they can't",
    "they cannot", "they don't", "she can't", "he can't", "she doesn't", "he doesn't",
    "whoever", "a friend of mine", "the person i'm", "the people i'm",
)


def _alternation_problems(transcript: str) -> list[Problem]:
    """Two turns from the same speaker in a row.

    Pure form, so it gates: there is no reading of the transcript format under which one
    speaker labels twice on purpose. It is here because the pilot shipped one —
    `gen-001-s1` has two consecutive `BOT:` turns, the second asking a question the first
    had already moved past — and none of the §6.1 rules could see it, being rules about
    what a *user turn says*. One in 67 is nine expected in a 612-session corpus.
    """
    turns = speaker_turns(transcript)
    found: list[Problem] = []
    users = -1
    for index, (speaker, text) in enumerate(turns):
        if speaker == "USER":
            users += 1
        if index and speaker == turns[index - 1][0]:
            found.append(Problem(
                "speaker_repeats", max(users, 0),
                f"turn {index} is a second consecutive {speaker} turn",
                _excerpt(text, 0, 90),
            ))
    return found


def _third_party_problems(
    transcript: str, beats: Sequence[Mapping[str, Any]]
) -> list[Problem]:
    """A companion's constraint written with no companion in the room.

    The narrow half of the attribution question, and the only half a rule should hold. A
    `third_party` beat asks the author to put a requirement in somebody else's mouth; if the
    session names nobody else at all, the requirement landed on the user, and the trap has
    silently become an ordinary — and false — piece of evidence about them. That failure is
    total and lexically visible, so it gates.

    The converse — a *user* beat that drifted onto a companion, which is how the defect was
    found in the pilot — is **not** here, and deliberately. Its signature is a companion
    marker in a session that legitimately has companions in it: 8 of the pilot's 33
    pinning-beat sessions carry one and only 3 were misattributed. A gate at that precision
    would re-author two good sessions for every real catch, and this project has already
    spent three gates eating the constructions they were meant to protect. `verify.py` puts
    the question to a reader instead.
    """
    if not any(b.get("attributed") == THIRD_PARTY for b in beats):
        return []
    lowered = transcript.lower()
    if any(marker in lowered for marker in COMPANION_MARKERS):
        return []
    fields_named = sorted({
        str(b.get("field_id")) for b in beats if b.get("attributed") == THIRD_PARTY
    })
    return [Problem(
        "third_party_unattributed", 0,
        f"{', '.join(fields_named)}: the companion whose requirement this is never appears "
        "in the session, so the requirement reads as the user's own",
        _excerpt(transcript, 0, 90),
    )]


def _disclosure_problems(
    transcript: str, beats: Sequence[Mapping[str, Any]]
) -> list[Problem]:
    """Flag a `disclosure` beat whose subject the **user** raises first.

    ADR-0035 decision 3 rests entirely on the bot asking: a preference can be volunteered and
    an absence cannot, so an absence the user brings up unasked is profile recitation with
    extra steps. The move was measured working — three for three on `gen-000` — and the way
    it failed was not the way the design worried about. The author put the bot's question in
    the user's mouth and then had the bot take it back:

        USER: one thing before I go — is there anything I should know about what you eat?
        BOT: Sorry, that's my question. Anything I should know ... before I send the details?

    Nothing caught it. The rules above are about the *content* of a user turn; this one is
    about which speaker reaches the subject first, which is why it needs `speaker_turns` and
    the beat list rather than `user_turns` alone.

    Only fires when a marker actually appears and its first appearance is a user turn. A beat
    whose marker appears nowhere is **not** flagged here: that is the under-probed case, it is
    a phrasing judgement rather than a rule, and wiring it into the retry gate unmeasured is
    how a gate ends up eating the construction it was built to protect. `probe_coverage`
    reports it instead.

    **This rule and `DISCLOSURE_PROBES` ship together, and the coupling is not incidental.**
    Run against the store that produced the example above, this check finds nothing — the
    user's question was *"anything I should know about what you eat or drink?"*, which carries
    no marker, so the rule is blind to the very session that motivated it. What it covers is a
    role-slip on a question that names its field, and the brief is what makes questions name
    their fields. Read as a general who-speaks-first rule it would be over-trusted. The
    generic-question case is caught by `probe_coverage`, which flagged all three of those
    sessions.
    """
    found: list[Problem] = []
    turns = speaker_turns(transcript)
    user_index = {
        position: rank
        for rank, (position, _) in enumerate(
            (i, t) for i, (speaker, t) in enumerate(turns) if speaker == "USER"
        )
    }
    for beat in beats:
        if beat.get("move") != DISCLOSURE_MOVE:
            continue
        probe = DISCLOSURE_PROBES.get(str(beat.get("field_id")))
        if probe is None:
            continue
        field_id, markers = str(beat.get("field_id")), probe[1]
        for position, (speaker, text) in enumerate(turns):
            lowered = text.lower()
            hit = next((m for m in markers if m in lowered), None)
            if hit is None:
                continue
            if speaker == "USER":
                found.append(Problem(
                    "disclosure_volunteered", user_index.get(position, position),
                    f"{field_id}: the user reaches the subject before the bot asks "
                    f"({hit!r}); the disclosure move requires the bot to ask first",
                    _excerpt(text, lowered.index(hit)),
                ))
            break
    return found



#: The unbounded half of a disclosure answer, as narrow markers.
#:
#: `DISCLOSURE_PROBES` closes the scope of the bot's *question* and the brief spends a
#: paragraph closing the scope of the user's *answer* — "AND SO MUST THE USER'S ANSWER … never
#: a bare 'nothing like that'". Only the first of those was ever read by anything. `gen-001-s1`
#: is what that costs: the bot asked a correctly scoped question ("a particular diet,
#: vegetarian or halal"), the author wrote the prescribed reply, and then added a second
#: sentence — *"No, no particular diet. Nothing on that front."* The card carries a shellfish
#: allergy first disclosed two sessions later, both readers took the second sentence as ruling
#: it out, and the page states the negation of ground truth on a field nothing scheduled.
#:
#: **Sentence-scoped, like `_intake_asks`, and that is the whole design.** The turn as a whole
#: does name its field — "particular diet" is right there — so a rule asking "does this answer
#: mention the field" passes the defect. What a reader reads is the unbounded sentence, and it
#: is unbounded whatever the sentence before it said.
#:
#: This is the construction half of an instruction that had three paragraphs of prose and no
#: check, which is the shape `_OFFER_TEMPLATE` was built for and `_intake_problems`' docstring
#: concedes about its own fourth pass. It is not a fourth paragraph.
_BLANKET_DENIAL = re.compile(
    r"\bnothing (?:like that|of the (?:sort|kind)|on that (?:front|score)"
    r"|to (?:flag|declare|worry about|mention|note)|i can think of|at all|there|else)\b"
    r"|\bnone (?:of that|of the sort|at all)\b"
    r"|\bno(?:pe)?[,.]? nothing\b"
    r"|\ball (?:fine|good|clear|easy) (?:on that|there|here|either way)\b"
    r"|\bno (?:special |particular )?(?:requirements?|restrictions?|needs)\b",
    re.I,
)

#: What counts as the ANSWER having named its field — deliberately WIDER than
#: `DISCLOSURE_PROBES`' question markers, and the asymmetry is the same one `OFFER_RAISED_EXTRA`
#: makes. A question marker answers "is this the ask the schedule scheduled", where a near miss
#: costs a report line. An anchor answers "is this sentence confined to the field", where a near
#: miss is a retry on a page nothing is wrong with. *"No dietary restrictions"* and *"no, stairs
#: are fine"* are both scoped and neither carries a question marker.
_ANSWER_ANCHORS: dict[str, tuple[str, ...]] = {
    "dietary": ("diet", "vegetarian", "vegan", "halal", "kosher", "gluten", "dairy",
                "pescatarian"),
    "allergies": ("allerg", "nuts", "peanut", "shellfish", "gluten", "sesame"),
    "accessibility_needs": ("access", "step-free", "step free", "stairs", "wheelchair",
                            "ramp", "lift"),
    "alcohol_free_required": ("alcohol", "drink", "the bar", "teetotal", "sober"),
}


def _blanket_denial_problems(
    text: str, beats: Sequence[Mapping[str, Any]]
) -> list[Problem]:
    """The user's answer to a scheduled disclosure, denying more than the beat asked about.

    Scoped to **one turn**: the first user turn after the bot turn that carries the beat's
    question marker. That is narrow on purpose and in the direction this project errs. The
    rule is about the answer to a scheduled ask, not about denial as a figure of speech — a
    user saying *"nothing else, thanks"* while booking is ordinary English and is not on the
    hook for a constraint field.

    Three cases it deliberately does not reach:

    * **the question the user asked first.** If the marker's first appearance is a user turn,
      `_disclosure_problems` already has the page and reporting it twice would gate one defect
      through two rules.
    * **a probe that never names its field.** No marker, no answering turn to find. That is
      the under-probed case and `probe_coverage` owns it, unenforced, for the reason its own
      docstring gives.
    * **a sentence that names the field.** `_ANSWER_ANCHORS`, wide on purpose.
    """
    found: list[Problem] = []
    turns = speaker_turns(text)
    user_index = {
        position: rank
        for rank, (position, _) in enumerate(
            (i, t) for i, (speaker, t) in enumerate(turns) if speaker == "USER"
        )
    }
    for beat in beats:
        if beat.get("move") != DISCLOSURE_MOVE:
            continue
        field_id = str(beat.get("field_id"))
        probe = DISCLOSURE_PROBES.get(field_id)
        if probe is None:
            continue
        asked = next(
            (i for i, (_s, line) in enumerate(turns)
             if any(m in line.lower() for m in probe[1])),
            None,
        )
        if asked is None or turns[asked][0] != "BOT":
            continue
        answer = next(
            (i for i in range(asked + 1, len(turns)) if turns[i][0] == "USER"), None
        )
        if answer is None:
            continue
        line = turns[answer][1]
        anchors = _ANSWER_ANCHORS.get(field_id, ())
        offset = 0
        for sentence in re.split(r"(?<=[.?!])\s+", line):
            match = _BLANKET_DENIAL.search(sentence)
            if match is not None and not any(a in sentence.lower() for a in anchors):
                found.append(Problem(
                    "blanket_denial", user_index.get(answer, answer),
                    f"{field_id}: the user answers the {field_id} question with a denial "
                    f"that names no field ({match.group(0)!r}), so it is not confined to "
                    "the one thing that was asked. This person may hold a requirement of an "
                    "entirely different kind — and a reader takes the sentence as ruling "
                    "that one out too, leaving the page stating the opposite of the truth "
                    "about them. Say what is being ruled out: "
                    f"{DISCLOSURE_PROBES[field_id][2]}",
                    _excerpt(line, offset + match.start()),
                ))
                break
            offset += len(sentence) + 1
    return found


#: An invitation to disclose that carries no question mark. *"Let me know if there are any
#: allergies"* forces the same answer as asking, so the gate has to reach it, and a bare
#: keyword match cannot: the bot also says *"the Larch Room lists allergens per dish"* and
#: *"they don't serve alcohol at all"*, which are facts about a venue and not questions about
#: the user. Measured before the gate was wired in — those two shapes were 2 of 5 fires on one
#: store and 1 of 3 on another, and a retry loop would have regenerated correct sessions.
_INTAKE_INVITE = re.compile(
    r"let me know|tell me if|do let me know|feel free to (?:say|mention|flag)", re.I
)


def _intake_asks(line: str, pattern: re.Pattern[str] = None) -> list[re.Match[str]]:
    """The intake matches in a bot turn that are actually *put to the user*.

    Sentence-scoped on purpose. The unit is the sentence because that is where the question
    mark lives: a turn that asks one thing and describes another would otherwise flag on the
    description, and a turn that only describes would flag on nothing at all.
    """
    out: list[re.Match[str]] = []
    offset = 0
    for sentence in re.split(r"(?<=[.?!])\s+", line):
        if "?" in sentence or _INTAKE_INVITE.search(sentence):
            match = (pattern or INTAKE_ASK).search(sentence)
            if match is not None:
                out.append((sentence, match, offset))
        offset += len(sentence) + 1
    return out


def _screening_problems(
    text: str, beats: Sequence[Mapping[str, Any]]
) -> list[Problem]:
    """A bot turn that screens the user for a **preference** this session did not brief.

    ADR-0041, and the same rule as `_intake_problems` on the fields that gate cannot reach. The
    bot asks *"any part of town?"*, the user has to answer, and with no fact in front of it the
    author invents one — which lands against the card as often as not. `gen-004-s9` and
    `gen-004-s11` are the two the reader arm caught, both `travel_radius` against
    `own_neighborhood`; the ask itself is on 39 of 67 sessions in the current store.

    **Any** beat on the field exempts it, unlike the intake gate's disclosure-only carve-out: a
    preference is legitimately reached by `request`, `rejection` or `reaction`, and a session
    that briefs `travel_radius` at all is a session where the question has an answer to land on.
    """
    briefed = {str(beat.get("field_id")) for beat in beats}
    found: list[Problem] = []
    seen: set[str] = set()
    rank = 0
    for speaker, line in speaker_turns(text):
        if speaker == "USER":
            rank += 1
            continue
        for sentence, match, offset in _intake_asks(line, SCREENING_ASK):
            asked = sorted({
                field_id for field_id, pattern in SCREENING_FIELD_PATTERNS.items()
                if re.search(pattern, sentence, re.I)
            } - briefed)
            if not asked or ",".join(asked) in seen:
                continue
            seen.add(",".join(asked))
            found.append(Problem(
                "unbriefed_screening", rank,
                f"the bot screens the user about {', '.join(asked)}, which this session did "
                "not brief; with nothing in front of it the answer is invented, and an "
                "invented preference lands against the card as often as not",
                _excerpt(line, offset + match.start()),
            ))
    return found


def _offer_problems(
    text: str, beats: Sequence[Mapping[str, Any]],
    free_offers: Sequence[int] = (),
) -> list[Problem]:
    """A bot turn that volunteers one of `_OFFER_TEMPLATE`'s six closed attributes.

    The construction half of the offer template. The block above tells the author what an
    offer turn may carry; this reads what it did carry, because the failure this tree keeps
    re-finding is not an author disobeying but an author obeying while the page comes out
    false — `_INTAKE_BAR`'s `avoid` block, and three ADRs of prose before it.

    Two carve-outs, and both are declarations rather than inferences:

    * **a beat on the field**, exactly as `_screening_problems` has it. A session briefed on
      `wait_tolerance` is one where the queue is the point, and `event_types_liked` and
      `event_types_avoided` exempt each other because they are one page vocabulary.
    * **a declared escape**. `free_offers` is the author naming the message numbers it wrote
      free, on the `self_report` mechanism and for the same stated reason: a rule cannot tell
      a deliberate escape from the mistake it looks like, so the artifact carries it. A
      declared escape passes here and is counted by the free arm; an undeclared one is a
      finding, which is what makes the escape rate a number to read rather than one to
      discover.

    Not a bar on every bot turn: it fires on the attribute, not on the turn, so
    acknowledgment, admin and answering what the user asked never reach it — they carry no
    attribute. The `rank` reported is the bot turn's position among bot turns, and the
    message number is in the detail, because that is the address the editor and the reader
    both point with.
    """
    briefed: set[str] = set()
    for beat in beats:
        field_id = str(beat.get("field_id"))
        briefed.add(field_id)
        for key, group in OFFER_ATTRIBUTE_ALIASES.items():
            if field_id in group:
                briefed.add(key)
    declared = {int(n) for n in free_offers or ()}
    turns = speaker_turns(text)
    found: list[Problem] = []
    seen: set[tuple[str, str]] = set()
    raised: set[str] = set()
    rank = 0
    for number, (speaker, line) in enumerate(turns):
        if speaker != "BOT":
            # "The free lane recombines, never introduces." An attribute the user has
            # already put on the page is not one the offer volunteered: the reaction it
            # could produce has already happened, and a bot that cannot answer *"can I
            # book it?"* is the `venue-pool-general` failure again — a bot with nothing to
            # work from asks, and an interview is where invented answers come from. The
            # rule fires on introducing, which is what the audit column measured.
            raised.update(
                field_id for field_id in OFFER_ATTRIBUTE_PATTERNS
                if _raises(line, field_id)
            )
            continue
        rank += 1
        if number in declared:
            continue
        for field_id, pattern in OFFER_ATTRIBUTE_PATTERNS.items():
            if field_id in briefed or field_id in raised:
                continue
            match = re.search(pattern, line, re.I)
            if match is None or (field_id, match.group(0).lower()) in seen:
                continue
            seen.add((field_id, match.group(0).lower()))
            found.append(Problem(
                "offer_attribute", rank - 1,
                f"message [{number}]: the bot volunteers {field_id} ({match.group(0)!r}), "
                "which is one of the six attributes an offer turn does not carry and which "
                "this session did not brief. Nine of the eleven bot-attributed blocking "
                "findings in `runs/audit-0056.json` are a volunteered room attribute, and "
                "`gen-004-s3` is the heat one. Leave it out, or declare the message in "
                "`free_offers`",
                _excerpt(line, match.start()),
            ))
    for number, (speaker, line) in enumerate(turns):
        if speaker != "BOT":
            continue
        match = _OFFER_LABEL.search(line)
        if match is not None:
            found.append(Problem(
                "offer_labelled", number,
                f"message [{number}]: the bot prints the offer's slots as a form "
                f"({match.group(1)}:) instead of saying them. The slots are what the turn "
                "carries, not a template to fill in on the page; a labelled turn is not a "
                "person talking",
                _excerpt(line, match.start()),
            ))
    for number in sorted(declared):
        if number >= len(turns) or turns[number][0] != "BOT":
            found.append(Problem(
                "escape_undeclarable", 0,
                f"`free_offers` names message [{number}], which is not a bot message in this "
                "session; a declared escape has to point at the turn it excuses",
                "",
            ))
    return found


def _intake_problems(
    text: str, beats: Sequence[Mapping[str, Any]]
) -> list[Problem]:
    """A bot turn that screens the user for a constraint this session did not brief.

    **The rule this enforces is the most expensive line in the brief, and two passes of prose
    did not close it.** Nine of 67 sessions had the bot ask *"anything I should note, allergies
    or anything you avoid?"* and the author, with no positive fact to answer from, wrote *"no,
    nothing to flag"* for people carrying `dairy_free`, `vegan` and a shellfish allergy — the
    page then stating the negation of ground truth. `_INTAKE_BAR` took that to 2; a second
    clause naming the reservation moment, where the ask reads as venue admin rather than
    screening, took it to 0 **denials** and left 2 asks. Both survivors were booking-shaped,
    which is what the second clause had just named, so the ask migrated into the frame the
    prose forbade rather than out of it. A third pass of prose was the move not to make.

    What the survivors cost is worth stating precisely, because it is not the original defect.
    Neither contradicts its card — `gen-000`'s `allergies` and `gen-004`'s
    `accessibility_needs` are both empty, so *"no, nothing like that"* is true. But neither
    field is ever scheduled for either subject, so the derived map calls the cell UNEVIDENCED
    for the whole timeline while the page answers it, and a twin that reads the page correctly
    commits to `[]` where the key says abstain. The same defect mirrored: evidence the map does
    not have, rather than the negation of what it does.

    **Fires on the question, not the answer**, for the same reason `_INTAKE_BAR` bars the
    question: an unasked question cannot be answered wrongly, and the answer is the easier half
    to write around. So this gate does not consult the card. A truthful denial and a false one
    are the same defect here — one falsifies the page, the other evidences an unscheduled cell —
    and a gate that only fired on the false half would pass `gen-000-s0` and `gen-004-s22`,
    which are exactly the two sessions it exists to reject.

    The carve-out is per **field**, not per session, and it is read off the beats: a
    `disclosure` beat scheduled that ask, `DISCLOSURE_PROBES` requires it to name its field,
    and the map counts the answer. A generic ask is never exempt, because a question that names
    no field cannot be the one the schedule scheduled — and because it is answered for all four
    at once, which is the cross-field leak §8.1 punishes a twin for acting on.

    Turn indices are over **user** turns, like every other `Problem`, so the index reported is
    the user turn that answers the ask — which is the turn a reader opens the session to.
    """
    briefed = {
        str(beat.get("field_id")) for beat in beats
        if beat.get("move") == DISCLOSURE_MOVE
    }
    found: list[Problem] = []
    seen: set[str] = set()
    rank = 0
    for speaker, line in speaker_turns(text):
        if speaker == "USER":
            rank += 1
            continue
        for sentence, match, offset in _intake_asks(line):
            unbriefed = sorted(unbriefed_intake_fields(sentence, briefed))
            if not unbriefed:
                continue
            key = ",".join(unbriefed)
            if key in seen:
                continue
            seen.add(key)
            found.append(Problem(
                "unbriefed_intake", rank,
                f"the bot screens the user about {', '.join(unbriefed)}, which this session "
                "did not brief; the bot never asks what the user avoids, has an allergy to, "
                "needs for access, or cannot drink unless a beat above names that field",
                _excerpt(line, offset + match.start()),
            ))
    return found


def probe_coverage(
    transcript: str, beats: Sequence[Mapping[str, Any]]
) -> list[tuple[str, bool]]:
    """Per `disclosure` beat, whether its field-naming marker appears at all. Not a gate.

    A `False` here is the defect the `DISCLOSURE_PROBES` table exists to fix — a disclosure
    that never names its field, leaving the derived map asserting a cell the page does not
    carry. It is reported rather than enforced because the marker sets are narrow on purpose
    and a miss can be an acceptable paraphrase.
    """
    out: list[tuple[str, bool]] = []
    lowered = transcript.lower()
    for beat in beats:
        if beat.get("move") != DISCLOSURE_MOVE:
            continue
        probe = DISCLOSURE_PROBES.get(str(beat.get("field_id")))
        if probe is None:
            continue
        out.append((str(beat.get("field_id")), any(m in lowered for m in probe[1])))
    return out


#: Surfaces that put a *card value* on the page, for the contradiction probe. Separate from
#: `BIN_SURFACE`, which answers "which bin is this turn in"; these answer "did the user just
#: do the thing the card says they do not do".
_ALCOHOL_SURFACES: tuple[str, ...] = (
    "wine bar", "cocktail", "a pint", "pints", "the bar", "beers", "a beer",
    "prosecco", "gin", "whisky", "whiskey", "happy hour", "a bottle of",
)

_DIET_SURFACES: dict[str, tuple[str, ...]] = {
    "vegetarian": ("steak", "chicken", "pork", "beef", "bacon", "lamb", "burger",
                   "prawn", "brisket", "ham", "duck"),
    "vegan": ("steak", "chicken", "pork", "beef", "bacon", "lamb", "cheese", "prawn",
              "brisket", "ham", "duck", "butter", "cream", "egg", "eggs", "milk"),
    "pescatarian": ("steak", "chicken", "pork", "beef", "bacon", "lamb", "brisket",
                    "ham", "duck"),
    "halal": ("pork", "bacon", "ham"),
    "kosher": ("pork", "bacon", "ham", "prawn", "shellfish"),
    "gluten_free": ("bread", "pasta", "noodles", "battered", "dumplings", "pastry"),
    "dairy_free": ("cheese", "cream", "butter", "milk", "mozzarella", "parmesan"),
    "keto": ("bread", "pasta", "rice", "potatoes", "chips", "dessert"),
    "low_sodium": (),
}


def contradiction_probes(
    transcript: str, card: Mapping[str, Any], *, beats: Sequence[Mapping[str, Any]] = ()
) -> list[Problem]:
    """User turns that appear to do what the card says this person does not do.

    **Report-only, and lossy in both directions.** It cannot see polarity — *"not the
    Vietnamese"* and *"the Vietnamese sounds better"* share every token that matters — so a
    hit is a place to look rather than a finding. It exists because looking is otherwise
    manual: every contradiction in the pilot audit (`gen-002` choosing a wine bar five
    sessions before requiring an alcohol-free room, `gen-003` picking the cuisine they avoid
    in session 0, `gen-004` reporting the pork dish "like four times" while vegan) is a hit
    here, and each was found by reading rather than by any rule.

    Fields with a beat in this session are skipped: the user declining an avoided cuisine
    *is* the scheduled evidence, and flagging it would report the dataset working. **Only the
    subject's own beats skip it.** A third-party beat under ADR-0036 names the field while
    saying nothing about the subject, so keying this control on the field alone blinded the
    probe exactly where the brief was also blind: `gen-003-s2` schedules `cuisines_avoided` as
    a companion's *french*, the guard dropped the subject's own *vietnamese, sushi*, the page
    booked a kaiseki lunch, and this probe skipped the field that would have caught it. Found
    by the reader arm on 2026-08-18; the probe is the cheaper of the two arms and should have
    had it.
    """
    here = {
        str(b.get("field_id")) for b in beats
        if str(b.get("attributed") or OWN) == OWN
    }
    # A field with an own beat is not skipped outright — only the members that beat's brief
    # actually names are. A narrowing set beat is briefed with a proper subset, so skipping the
    # whole field blinded this probe on the remainder, which is the half the brief was also
    # blind on: `gen-001-s7` briefed `latin_american`, the page chose middle eastern, and the
    # probe that exists to catch exactly that stayed silent. Same shape as the ADR-0036 case in
    # this docstring, one axis over — attribution there, set membership here.
    held: dict[str, set[str]] = {}
    for beat in beats:
        if str(beat.get("attributed") or OWN) != OWN:
            continue
        rest = withheld_members(beat)
        if rest:
            held.setdefault(str(beat.get("field_id")), set()).update(str(r) for r in rest)

    def unbriefed(field_id: str, values: Sequence[Any]) -> tuple[str, ...]:
        """Card members this session's brief left unnamed, so this probe still owns them."""
        if field_id not in here:
            return tuple(str(v) for v in values)
        remainder = held.get(field_id, set())
        return tuple(str(v) for v in values if str(v) in remainder)

    probes: list[tuple[str, tuple[str, ...]]] = []
    if card.get("alcohol_free_required") and "alcohol_free_required" not in here:
        probes.append(("alcohol_free_required", _ALCOHOL_SURFACES))
    for diet in unbriefed("dietary", list(card.get("dietary") or ())):
        probes.append(("dietary", _DIET_SURFACES.get(diet, ())))
    allergens = unbriefed("allergies", list(card.get("allergies") or ()))
    probes.append(("allergies", tuple(a.replace("_", " ") for a in allergens)))
    avoided = unbriefed("cuisines_avoided", list(card.get("cuisines_avoided") or ()))
    probes.append(("cuisines_avoided", tuple(c.replace("_", " ") for c in avoided)))

    found: list[Problem] = []
    for index, turn in enumerate(user_turns(transcript)):
        lowered = turn.lower()
        for field_id, surfaces in probes:
            for surface in surfaces:
                match = re.search(r"\b" + re.escape(surface) + r"\b", lowered)
                if match:
                    found.append(Problem(
                        "contradiction_suspect", index,
                        f"{field_id}: the card excludes this, and the user turn names "
                        f"{surface!r}",
                        _excerpt(turn, match),
                    ))
                    break
    return found


def _distribution_problems(
    index: int, turn: str, lowered: str, *, scripted: bool = False,
) -> list[Problem]:
    """Two rules over the same bin surfaces, with different arities.

    `distribution_enumeration` needs two bins under a frequency marker — that is a user
    reciting the *shape* of a distribution. `norm_comparison` needs one bin and a claim
    about where the norm sits, which is the only other way a page can contradict a
    distribution and was the one the pilot's editor introduced while repairing the other.

    ``scripted`` marks a turn carrying the declared §4.2 self-report, which is exempt from
    `norm_comparison` for the reason `check_session`'s docstring gives at length: a scripted
    self-description saying "we usually spend about thirty each" is the construction, not a
    defect in it, and a gate that eats it would regenerate the quota member until it stopped
    being one. It stays subject to `distribution_enumeration` — reciting a whole shape is
    beyond what any self-report needs.
    """
    hits = {
        field_id: {
            name for name, surfaces in bins.items()
            if any(re.search(surface, lowered) for surface in surfaces)
        }
        for field_id, bins in BIN_SURFACE.items()
    }
    found: list[Problem] = []
    if any(marker in lowered for marker in FREQUENCY_MARKERS):
        for field_id, hit in hits.items():
            if len(hit) >= 2:
                found.append(Problem(
                    "distribution_enumeration", index,
                    f"{field_id}: {len(hit)} bins in one turn under a frequency marker "
                    f"({', '.join(sorted(hit))})",
                    _excerpt(turn, 0, 90),
                ))
    if not scripted:
        # No bin is required, and that is the whole reason this rule catches anything.
        # `BIN_SURFACE` is narrow by design and carries `£3\d` but not "thirty to fifty",
        # so requiring a bin hit found **zero** of the three real norm claims in the pilot
        # corpus, `gen-004-s8` included. Rule 3b bans rating today against the norm outright,
        # so the phrase alone is the defect; the bin, when there is one, is only detail.
        named = sorted(field_id for field_id, hit in hits.items() if hit)
        where = f"{', '.join(named)}: " if named else ""
        for pattern, detail in NORM_COMPARISON_PATTERNS:
            match = re.search(pattern, lowered)
            if match:
                found.append(Problem(
                    "norm_comparison", index,
                    f"{where}{detail} — a single occasion may fall in any bin, but "
                    "calling it usual or unusual asserts where the mode is",
                    _excerpt(turn, match, 90),
                ))
                break
    return found


# --- the session brief -------------------------------------------------------


#: What each card field is called in the portrait, in the order the portrait prints them.
#:
#: The label is deliberately not the `field_id`. A brief that prints `noise_preference:
#: needs_quiet` has handed the author the trait vocabulary `AUTHOR_SYSTEM` rule 2 bans the
#: *user* from reciting, and an author that has just read a scale name is the one most likely
#: to write it back as dialogue. The portrait says what is true of the person in the same
#: register a friend would use, and the value it prints is the field's own behavioural anchor
#: (`fields.Field.anchors`), which is already written that way.
#:
#: The three distribution fields are absent and that is the point — see `persona_portrait`.
PORTRAIT_LABELS: dict[str, str] = {
    "dietary": "what they do not eat",
    "allergies": "allergies",
    "accessibility_needs": "access they need",
    "alcohol_free_required": "alcohol",
    "cuisines_liked": "food they seek out",
    "cuisines_avoided": "food they decline",
    "spice_tolerance": "heat",
    "adventurousness": "ordering",
    "deal_seeking": "price, as effort",
    "noise_preference": "the room, as volume",
    "formality_ceiling": "how formal they will go",
    "design_sensitivity": "the room, as looks",
    "solo_dining_comfort": "eating alone",
    "travel_radius": "how far they go",
    "planning_style": "lead time",
    "wait_tolerance": "queueing",
    "event_types_liked": "events they seek out",
    "event_types_avoided": "events they decline",
    "participation_vs_spectating": "at an event",
    "rating_reliance": "ratings",
    "novelty_vs_loyalty": "repeat visits",
}


def _portrait_value(field_id: str, value: Any) -> str:
    """One card value in the portrait's register — an anchor, a list, or a plain sentence."""
    spec = fields.BY_ID[field_id]
    if spec.metric == fields.METRIC_EXACT:
        # The only boolean, and both directions have to read as a fact rather than a flag:
        # `False` printed as "false" is a value an author can misread as "not established".
        return ("they will not go anywhere that serves alcohol" if value
                else "no requirement — a bar on site is fine")
    if spec.metric == fields.METRIC_JACCARD:
        if not value:
            return "none"
        return ", ".join(str(v).replace("_", " ") for v in value)
    if spec.metric == fields.METRIC_ORDINAL:
        try:
            return spec.anchors[spec.levels.index(value)]
        except (ValueError, IndexError):          # a card outside the registry's vocabulary
            return str(value).replace("_", " ")
    raise ValueError(f"{field_id} has no portrait rendering for metric {spec.metric!r}")


def persona_portrait(
    card: Mapping[str, Any], idiosyncrasies: str = ""
) -> list[str]:
    """The whole truth about the subject, as the author is allowed to see it.

    v0.5's one change to generation. Through v0.4 the author saw the card only obliquely —
    `continuity_for`'s `standing` / `avoid` / `settled_once` guards, which are surgical
    projections of it — and never the person. The cost is legible in the corpus: an author
    writing a stranger has nothing to make the unbriefed two thirds of a turn out of, so it
    invents, and an invention is uncorrelated with the card by construction. That is where
    `runs/audit-0057.json`'s contradictions on unbriefed fields come from.

    Showing the card is the obvious fix and it is also the obvious hazard, because the thing
    this dataset measures is what a reader can recover from the page, and an author who knows
    everything can put everything on it. **The portrait is therefore paired with, and is
    useless without, the fence `session_brief` prints under it.** Seeing the card licenses
    nothing: the beats are still the whole of what may be evidenced. What the card buys is
    that the author's *inventions* — the dish, the aside, the reason for saying no — land in
    character instead of at random, and an in-character invention that stays under the
    evidence bar is exactly what a real conversation is made of.

    Two things are held back, and both are structural rather than cautious:

    * **The three distribution fields are never printed.** `continuity_for` shows the *drawn*
      bin for this session's outing and never the shape, and its comment gives the reason: an
      author told "usually two, sometimes one" has been handed the sentence rule 3b forbids
      the user to say. A mass table is that sentence in tabular form, and it is the one part
      of the card whose disclosure would break a rule rather than risk one. Party size, price
      and visit time reach the author exactly as they did in v0.4 — as THIS PARTICULAR OUTING.
    * **A distractor session gets no portrait at all** (`session_brief`). It briefs no beats,
      so the fence has nothing to fence *to*, and the block would be a card handed to a
      session whose whole requirement is to carry no signal from it.

    ``idiosyncrasies`` is §8.5's free text and is printed when the card has it. It is the most
    persona-ish thing on the card and the least scored — nothing in `scorer.py` reads it — so
    it is the cheapest texture the author can be given.
    """
    lines: list[str] = []
    for field_id, label in PORTRAIT_LABELS.items():
        if field_id not in card:
            continue
        lines.append(f"  {label + ':':<28}{_portrait_value(field_id, card[field_id])}")
    if not lines:
        return []
    text = (idiosyncrasies or "").strip()
    if text:
        lines += ["", f"  {'and:':<28}{text}"]
    return lines


#: The fence the portrait is useless without.
#:
#: Rule 4 of `AUTHOR_SYSTEM` — *write only what you are asked to evidence* — was written for an
#: author that had no way to write anything else. Under a portrait it is the only rule holding
#: the dataset up, so it is restated here at the point of temptation, in the terms the card
#: creates: knowing a fact is not an occasion to use it, and the specific failure is the
#: *apt* detail rather than the wrong one. An author that has just read "seeks the hottest
#: thing on the menu" writes the user asking for extra chilli, which is in character, is not a
#: contradiction, and is a `spice_tolerance` cell the schedule did not schedule — a leak §8.1
#: punishes a twin for acting on, arriving through the one door that does not look like a door.
_PORTRAIT_FENCE: tuple[str, ...] = (
    "YOU HAVE JUST BEEN TOLD EVERYTHING TRUE ABOUT THIS PERSON. ALMOST NONE OF IT MAY REACH "
    "THE PAGE.",
    "",
    "It is there so that what you invent to fill an ordinary turn — the dish they settle on, "
    "the aside, the reason they say no to something — is in character rather than at random. "
    "It is not a list of things to get in. The beats below are the whole of what this session "
    "evidences, and the portrait adds nothing to that list.",
    "",
    "The dangerous move is not writing something FALSE about them. It is writing something "
    "TRUE that no beat asked for. A user who asks for the hottest thing on the menu has had "
    "their heat tolerance settled by a session that was never asked to settle it, and the "
    "page is now evidence for a field this session does not carry — which is precisely the "
    "measurement this dataset exists to take, spoiled. Being in character is the point; being "
    "informative about a field with no beat is the defect.",
    "",
    "So: a field named by a beat below is evidenced, as that beat says. A field named in the "
    "portrait and nowhere else is background — it shapes what you would never write, and it "
    "does not put anything on the page. A reader must finish this session no better able to "
    "say where the person sits on it than before they started.",
)



def session_brief(
    *,
    session: int,
    beats: Sequence[Mapping[str, Any]],
    turns: int,
    date: str = "",
    prior_date: str = "",
    is_distractor: bool = False,
    distractor_scenario: str | None = None,
    continuity: Mapping[str, Any] | None = None,
    card: Mapping[str, Any] | None = None,
    idiosyncrasies: str = "",
) -> str:
    """The per-session authoring prompt.

    v0.5 adds ``card`` and ``idiosyncrasies``, and they are the one change to generation in
    this tree: the author is shown who the person is, as `persona_portrait` renders them,
    under the fence `_PORTRAIT_FENCE` prints. Both are optional and both default to nothing,
    so a v0.4 corpus still briefs exactly as it did — which matters because the brief is
    fingerprinted, and a portrait that appeared unbidden would invalidate every cached
    transcript on disk. A corpus built by `schedule.py` carries the card at the item level, so
    `author.Chain.from_item` is where it is attached; see that method. The two reasons the
    portrait is safe to show, and the two things it withholds, are in `persona_portrait`.

    A distractor session is briefed without it. That branch returns before the portrait is
    reached, deliberately: it schedules no beats, so the fence has nothing to fence to, and
    the block would be the whole card handed to the one session whose entire requirement is to
    leave a reader knowing nothing new.

    v0.4 (see the design note in `author.py`'s module docstring): each session is authored
    from the card and its own scheduled beats alone, never from another session's text. There
    is no ``prior`` parameter — a session is not told what earlier conversations said, only
    (optionally) when the last one was, via ``prior_date``, which is schedule data, not
    authored text. Corrections stay coherent without it because a correction beat already
    carries `old_value`/`new_value` structured in its own `note` (`schedule.py`); a plain
    reinforcement beat is simply the same `gold_value` shown again through a fresh occasion,
    which needs nothing about what an earlier session actually said. What is lost is a named
    entity recurring by coincidence (the same venue mentioned twice) — accepted, not a defect
    the scorer looks for: the map counts fields, not restaurant names.

    Two v0 parameters are gone and two are new. ``tier`` is gone with the tiers — a session no
    longer belongs to a lane, and telling the author "this is a sparse tier session" was
    asking it to perform a property of the *corpus* inside a single conversation.
    ``turns`` replaces the hardcoded "8-16 turns" that every v0 session carried, and ``date``
    is what makes the timeline a timeline rather than an ordering: the gap since the last
    conversation is signal a real person acts on, and a distribution over visit times means
    nothing without a period to be spread across.
    """
    lines = [f"Write conversation {session + 1} in this person's history with the app."]
    if date:
        lines.append(f"It takes place on {date}.")
    lines.append("")
    if date and prior_date:
        lines += [
            f"Their last conversation with this app was on {prior_date}; this one is on "
            f"{date}. Let the gap show in pacing if it is long enough to matter — a season "
            "changing, an offhand 'it's been a while' — without claiming to remember what "
            "was said last time, because nothing here says what that was. Do not announce "
            "the date.",
            "",
        ]

    if is_distractor:
        scenario = distractor_scenario or DISTRACTOR_SCENARIOS[0]
        lines += [
            "THIS SESSION CARRIES NO PERSONA SIGNAL AT ALL.",
            "",
            f"Scenario: {scenario}",
            "",
            "Write that scenario and nothing else. The user is not choosing where to go, "
            "when to go, or who with — there is no decision in this session for a "
            "preference to show up in, and that is the point. Do not add one to make the "
            "session feel real.",
            "",
            "This session is used to measure whether a system stays still when given "
            "nothing. A reader must finish it knowing no more about the person than before "
            "it — including how far they will travel, when they eat, what they can afford, "
            "and who they go with.",
            "",
            # A distractor briefs no beats at all, so the bar's carve-out is empty here and
            # the rule is absolute. This is the session with the strictest no-signal
            # requirement, and the pilot showed it is not automatically met: `gen-001-s4` is a
            # distractor whose scenario contradicted `planning_style` outright.
            *_INTAKE_BAR,
            "",
            f"About {turns} turns — a ceiling, not a target. The errand above is the whole "
            "session; when it is settled, stop. There is nothing else this conversation is "
            "for, and turns spent looking for something to fill them with are exactly where "
            "the persona signal this session must not carry gets invented.",
        ]
        return "\n".join(lines)

    portrait = persona_portrait(card or {}, idiosyncrasies)
    if portrait:
        lines += [
            "=== WHO THIS PERSON IS ===",
            *portrait,
            "",
            *_PORTRAIT_FENCE,
            "",
        ]

    lines += _continuity_lines(continuity or {})

    lines += [
        "Evidence exactly these things about the user, each through the conversational "
        "move named. Nothing else about them should become inferable.",
        "",
    ]
    # Beats repeat by design — the schedule may put the same field in one session twice, and
    # the map counts both. Rendering `beat_brief` twice is what it used to do, and the pilot
    # showed the cost: `gen-004-s12` carried "FIELD noise_preference — via REACTION" twice
    # verbatim, `gen-004-s17` the same for `event_types_avoided`, and at n=50 eleven
    # field-subject cells reached their pin threshold on a repeat the brief had asked for
    # once. Two beats are two moments or the map is overstating the page, so the count is
    # stated and the separation is required.
    for beat, repeats in _group_beats(beats):
        block = beat_brief(beat)
        if repeats > 1:
            block += (
                f"\n  THIS HAPPENS {repeats} TIMES IN THIS CONVERSATION, as "
                f"{repeats} separate moments with other talk between them — a different "
                f"occasion, a different candidate, a different reason each time. Not one "
                f"mention restated, and not one long turn covering it {repeats} ways."
            )
        lines += [block, ""]

    # There is deliberately no per-field clause here for two beats on ONE field with
    # DIFFERENT moves. ADR-0043 added one, on a reader finding of three sessions where the map
    # counted 2 and the reader "found 1" — and the finding was the instrument: `verify.reconcile`
    # keyed the reader's beats into a dict by field, so a reader that answers with two entries
    # of `moments: 1` had its first entry overwritten. The excerpts show all three pages
    # carrying both moments, in both stores, across a full re-author. The clause is gone
    # because the defect was not there, and because it moved 30 briefs — three sessions plus
    # everything downstream of them — for nothing. `runs/result-repeat-brief.txt`.

    lines += ["Move definitions:"]
    for move, definition in MOVE_DEFINITIONS.items():
        lines.append(f"  {move:10s} {definition}")
    lines += [
        "",
        "A request or a rejection should make the value clear to a careful reader. A "
        "reaction or an anecdote should narrow it without pinning it down — leave a reader "
        "able to say 'somewhere in this range' but not 'exactly this'.",
        "",
        f"About {turns} turns, and that is a ceiling on the deliberation rather than a "
        "target to fill. The beats above are everything this conversation has to do: when "
        "they are done and the user has what they came for, the conversation is over. Ending "
        "early is not a truncated session, it is a short one, and short is what most real "
        "conversations with an app like this are. Do not add a further round of "
        "back-and-forth to reach the number.",
        "",
        "Do not label anything. Do not have the user explain themselves.",
        "",
        "This is a recommendations app for places to go — restaurants, bars, cafes, venues, "
        "events. The user is deciding where to go and will probably go. It is not a delivery "
        "service, a booking desk or a support channel: there are no orders to chase, no "
        "refunds, and no couriers.",
        "",
        *_VENUE_POOL,
        "",
        *_OFFER_TEMPLATE,
        "",
        *_INTAKE_BAR,
    ]
    return "\n".join(lines)


#: What the bot is allowed to put forward, and it is wider than the pages have been.
#:
#: The three `events` fields are scheduled in proportion to their share of the card — 16 of the
#: pilot's 124 beats, against 3 of 24 fields — and yet **40 of 57 pages carry no event
#: vocabulary at all**, and 9 of the 17 that do are the sessions where an event beat dragged one
#: onto the page. Events reach the corpus only when something schedules them.
#:
#: The cause is the scenery, not the schedule. `_OCCASION_LABELS` tells the author who is going,
#: when, and roughly what it costs, and never what kind of outing this is — so a meal is the
#: only thing left to write, and the price phrasing says "a head", which reinforces it. This
#: block widens what the *bot* offers rather than what the *user* wants, which is the whole
#: point: a suggestion the user does not act on is not evidence about the user, so this changes
#: the page without touching a single cell of the map. The alternative — drawing an outing kind
#: from the card as a fourth occasion — would need occasion accounting for a Jaccard set field,
#: where there is no equivalent of "one bin, one occasion", and it is not taken.
#:
#: The last clause is what keeps it free. Without it the author writes the user seeking out the
#: gig, which pins `event_types_liked`, or wincing at the noise, which pins `noise_preference` —
#: exactly the leaks the beats above exist to authorize.
#:
#: **Generalising this clause to every unbriefed axis was tried, measured, and reverted.**
#: `runs/prediction-venue-pool-general.txt` / `runs/result-venue-pool-general.txt`, and the
#: store it produced is kept at `runs/authored-venue-pool/`.
#:
#: The diagnosis behind the attempt stands and is independent: of 13 blocking contradictions on
#: fields the brief never names, **10 came from the bot offering a candidate and the user
#: reacting to an attribute nothing had briefed** — a loud hall, a market across town, a queue,
#: a place they had been before. Three came from an open-ended closer. **None came from the bot
#: screening**, which is `_INTAKE_BAR`'s and `SCREENING_FIELD_PATTERNS`'s job and which the same
#: pass shows working: one area/budget ask in 57 sessions, on a session that had briefed the
#: field. So the lever is the offer, not the question.
#:
#: What failed is the remedy. Widening the clause from the events fields to every axis, and
#: cutting `noise_preference` out of its permitted-grounds list (it read *"the hour, the price,
#: the room, who is coming"* — licensing by name the largest blind field), made the corpus
#: **worse on every axis measured**:
#:
#:     blocking            47 -> 56       BLIND contradictions   13 -> 15
#:     leaks               23 -> 28       wait_tolerance          2 -> 5
#:     noise_preference     3 -> 3        over_delivered         55 -> 59
#:     free-arm findings   22 -> 31       question_budget         4 -> 11
#:     re-authorings        0 -> 7        exhausted               0 -> 1
#:
#: The mechanism is legible in `question_budget` and it is the brief's own warning turned on
#: itself: closing every ground the user may decide on leaves the bot with nothing to work from,
#: so it asks — and `_INTAKE_BAR` says at length that an interview is where invented answers
#: come from. `third_party_unattributed` was 5 of the 7 retries for the same reason: with the
#: user short of legitimate grounds, the author reached for a companion instead.
#:
#: The lesson is narrow and worth keeping. A grounds bar works when the axis it closes is one
#: the conversation does not need — nobody has to decide by event format. It backfires when the
#: axis is one the conversation runs on, and *a room, a queue, a distance and a return visit are
#: what choosing a place is made of*. Those are the fields ADR-0044 and ADR-0053 would reach by
#: stating the axis as part of the outing and crediting it, not by forbidding it.
_VENUE_POOL: tuple[str, ...] = (
    "The bot's suggestions are not only restaurants. A candidate can be a bar, a cafe, a "
    "market, a venue with something on that night — a quiz, a screening, live music, a "
    "tasting, a class — as readily as it can be a table. Vary it: a run of sessions in which "
    "every suggestion is a place to eat is not what this app returns.",
    "",
    "What is on at a place is the bot's to mention and not the user's to want. Unless a beat "
    "above names an events field, the user neither seeks the format out nor turns it down for "
    "being that format: they take it or leave it on the same grounds as anywhere else — the "
    "hour, the price, the room, who is coming. A user who books BECAUSE it is a comedy night, "
    "or declines BECAUSE it is one, has settled a field nothing here scheduled.",
)


#: The bot does not screen the user, and this is the most expensive line in the brief.
#:
#: Nine of the pilot's 67 sessions put the **negation of ground truth** on the page, and every
#: one of them ran the same way: the bot asked an intake question the session had not briefed —
#: *"anything I should note, allergies or anything you avoid?"* — and the author, having no
#: positive fact to answer from, wrote *"no, nothing to flag"* for a user whose card carries
#: `dairy_free`, `vegan` or a shellfish allergy.
#:
#: The `avoid` block is what made it possible and it was working as designed. It is purely
#: negative — it forbids the user ordering the cheese and never says the user *is* dairy-free —
#: so a denial satisfies it perfectly. No cheese appears anywhere. The instruction was obeyed
#: and the page came out false.
#:
#: The bar goes on the **question**, not on the answer, because the question is what forces
#: the invention. An author asked to write a turn will write one; the only reliable way to stop
#: it inventing a constraint's status is for nothing in the conversation to require a status.
#: A positive counterpart to `avoid` — "they are dairy-free, say so if asked" — was the other
#: candidate and is weaker: it hands the author a fact and an occasion to state it, which pins
#: a field the schedule did not schedule, and that is the leak §8.1 punishes a twin for acting
#: on. Saying nothing is the only move that neither contradicts the card nor evidences it.
#:
#: The exception is a `disclosure` beat, and it is an exception rather than a hole: there the
#: schedule *has* scheduled the ask, `DISCLOSURE_PROBES` requires the bot to name the field,
#: and the map counts the answer. Those beats appear in the block above, so "unless a beat
#: above names that field" is the whole of the carve-out.
#:
#: **v0.5 makes that carve-out the whole rule.** Through v0.4 the question budget read *"draw
#: every one of them from the beats above OR FROM THE OUTING DESCRIBED IN THIS OUTING"*, and
#: the second clause was a hole the size of the scenery: party size, hour, price and formality
#: are four fields the bot could ask about in any session, on the grounds that the outing
#: mentions them. Under a portrait that hole gets worse rather than better — an author that
#: has read the card can answer any of those four in character, which is exactly the leak
#: `_PORTRAIT_FENCE` names, arriving as an answer to a question that looked licensed. So the
#: permitted set is now the beats and nothing else, and the scenery is something the *user*
#: brings rather than something the bot elicits.
#:
#: The risk this takes is the `venue-pool-general` mechanism running in reverse: close the
#: bot's questions and it may compensate by asking more of what is left, or stall for want of
#: anything to open with. It should not, because closing a *question* removes the bot's need
#: for an answer rather than removing its grounds to act — `_VENUE_POOL` and `_OFFER_TEMPLATE`
#: leave the offer intact, and the outing still reaches the page through the user's own
#: opening turn. `question_budget` in the audit is where that would show up, and it is the
#: number to read first on the run that measures this tree.
_INTAKE_BAR: tuple[str, ...] = (
    "The bot does not screen the user. It never asks whether they have allergies, dietary "
    "requirements, access needs, or anything they avoid; it never invites them to flag or "
    "note anything; and it does not ask what they are in the mood for as a way of getting "
    "the same list. The ONLY exception is a field named by a beat above — if a beat asks "
    "for that question, ask exactly that one.",
    "",
    "This holds when the bot is booking. Taking a reservation is where the question feels "
    "most like ordinary admin rather than screening, and that is exactly where it must not "
    "be asked: no 'anything I should note for the reservation', no 'any dietary "
    "requirements to pass on', no 'anything I should know before I put it through'. The "
    "bot books what was asked for and says so. Passing a requirement to a venue is only "
    "ever something the user raises first.",
    "",
    "This is not a style note, and it is the rule most easily broken by writing a helpful "
    "assistant. An unasked question cannot be answered wrongly. When the bot asks something "
    "the session did not brief, the user must reply, and the reply is invented — so a person "
    "who is vegan says 'no, nothing to flag' because nothing in front of you said they were, "
    "and the conversation now states the opposite of the truth about them.",
    "",
    "The same holds for preferences, not only for constraints. Do not ask what part of town "
    "they want, how far they will travel, or what their budget is unless a beat above names "
    "that field. 'Any area in mind?' reads as ordinary helpfulness and is the single most "
    "common way this goes wrong.",
    "",
    "EVERY QUESTION THE BOT ASKS IS ABOUT A FIELD A BEAT ABOVE NAMES. That is the whole "
    "permitted list, and it is not a style note: a question about anything else has no right "
    "answer in front of you, so the answer gets invented, and an invented answer is a cell of "
    "the record this session was never asked to fill. If no beat above names a field, the bot "
    "has no question about it — including the ordinary ones. Not the area, not the budget, "
    "not the party size, not the hour, not what they are in the mood for. Those come from the "
    "user or they do not come at all.",
    "",
    "ASK AT MOST THREE SUCH QUESTIONS BEFORE THE FIRST SUGGESTION — two is usually "
    "enough, one is fine, and none at all is fine. This is a ceiling, not a quota: do not "
    "manufacture a question to reach it, and a session whose beats are all reactions and "
    "anecdotes may correctly contain no bot question at all. Where the user's opening turn "
    "already says what the "
    "outing is, the bot suggests straight away and asks nothing. A bot that keeps asking turns the session into an interview, and "
    "an interview is where invented answers come from. A "
    "recommendation does not require knowing everything about someone. The bot suggests on "
    "what it has been told, the user reacts, and the reaction is where the persona shows. "
    "What the bot must never do is put forward a candidate that goes against something the "
    "user has already said or that the notes above forbid.",
    "",
    "The same holds for how the session ENDS. The bot does not need to confirm, recap, or "
    "check whether there is anything else — and the user does not need to be walked to a "
    "decision. A user who asks, gets one suggestion, and takes it has had a complete "
    "conversation. Let it end there. Turns added after the beats are spent have nothing "
    "left to evidence, so they get filled with the bot asking and the user answering, which "
    "is the interview this whole section exists to prevent.",
)


#: What the bot's offer turn may carry, and it is a skeleton rather than a paragraph.
#:
#: **The evidence is a speaker column.** `runs/audit-0056.json` carries 33 blocking findings;
#: 11 of them are attributed to a BOT turn, and nine of those eleven are a *venue attribute the
#: bot volunteered* — the room, the queue, the format, the deal — with the user then reacting to
#: it on a field nothing had briefed. `gen-001-s6` is the shape twice over: the bot offers a
#: place with *"a bar running along one side and it's fairly busy late"* and a stall board of
#: *"rotating specials"*, and the page comes back carrying `noise_preference` and contradicting
#: `deal_seeking`. Neither attribute was asked for by anything.
#:
#: This is the same diagnosis `_VENUE_POOL` states from an earlier run — *"the lever is the
#: offer, not the question"* — and it is the second remedy tried for it. The first, widening the
#: grounds bar to every unbriefed axis, was measured and reverted, and the mechanism of its
#: failure is why this block is shaped the way it is: closing the *user's* grounds left the bot
#: nothing to work from, so it asked, and `question_budget` went 4 -> 11. **This block does not
#: close a ground.** The user may still decide on the room, the queue, the distance or the
#: price; what changes is that an offer turn does not put an unbriefed one of those in front of
#: them. The bot keeps its grounds and the page loses the volunteered attribute.
#:
#: Three things it deliberately is not:
#:
#: * **Not a fourth pass of prose at `_INTAKE_BAR`.** Three ADRs (0039, 0041, 0042) have been
#:   spent instructing the bot and `_intake_problems`' own docstring concedes the fourth was the
#:   move not to make. The lever here is construction: the offer turn is filled from a slot
#:   list, and `_offer_problems` reads the closed attributes off the page rather than trusting
#:   the instruction.
#: * **Not a bar on every bot turn.** Acknowledgment, admin, *"I can't look that up"* and
#:   answering a question the user asked are free, and they are most of what responding to the
#:   user is. They carry no attribute, so there is nothing in them to leak.
#: * **Not silent.** An offer that steps outside the skeleton is declared back in `free_offers`
#:   by message number — the `self_report` mechanism, for the reason `AUTHOR_SYSTEM` already
#:   gives for it: a check downstream cannot tell a deliberate escape from the mistake it looks
#:   like unless the author says which line it is. Escape rate is then a number to read rather
#:   than something an audit discovers.
#:
#: The KIND slot keeps `_VENUE_POOL`'s pool intact — a bar, a cafe, a market, a venue with
#: something on are all still offerable, because a run of sessions in which every suggestion is
#: a table is not what this app returns, and that widening was measured (18 of 57 sessions
#: carrying event vocabulary -> 27 of 57). What the slot list closes is the format *particular*
#: — the quiz, the comedy night, the tasting — on the same carve-out every other closed
#: attribute gets: unless a beat above names the field.
_OFFER_TEMPLATE: tuple[str, ...] = (
    "WHEN THE BOT PUTS A CANDIDATE FORWARD, THAT TURN CARRIES FOUR THINGS AND NOTHING "
    "ELSE:",
    "",
    "  * what it is called;",
    "  * what kind of place it is — a restaurant, a bar, a cafe, a market, or a venue with "
    "something on;",
    "  * one plain line of what it is — the food, the drink, the thing that is on;",
    "  * how it answers what the user has ALREADY said in this conversation, or what a beat "
    "above asks this session to evidence.",
    "",
    "THIS IS WHAT THE TURN CONTAINS, NOT A FORM TO FILL IN. The bot says it in ordinary "
    "sentences, the way it says everything else — one or two lines of speech. It never "
    "prints a label, a field name, a heading or a bullet: a turn that reads `NAME: ...` is "
    "not a person talking and is unusable, and the brief already says do not label anything.",
    "",
    "The bot never volunteers any of these about a candidate, in any turn, unless a beat "
    "above names that field:",
    "",
    "  * how loud, quiet, lively or busy the room is;",
    "  * what it looks like — the decor, the fittings, the furniture, the styling;",
    "  * whether you queue, wait, walk in, or cannot book;",
    "  * what format the thing on is — a quiz, a gig, a screening, a tasting, a class;",
    "  * a deal, a special, a happy hour, an early-bird — anything priced as an offer. "
    "A set menu is the food, and saying what is on it is fine;",
    "  * how hot a dish is — spicy, chilli in it, a kick of heat, how hot it comes. Naming "
    "the dish is the food and is fine; naming its heat is not.",
    "",
    "These six are where an offer goes wrong. Five of them are measured: a bot that mentions "
    "the queue gets a user who says how they feel about queuing, on a field this session was "
    "never asked to evidence. The sixth is the same shape with the reaction forced — heat is "
    "the one attribute where declining is as much evidence as accepting, so an unbriefed "
    "chilli dish on the menu leaves no reply that is both in character and off the record. The bot may still name the price, the hour, the area and the "
    "party size, because those are what the user came with.",
    "",
    "The rest of the bot's turns are free and are not a skeleton: agreeing, booking, saying "
    "it cannot look something up, and answering a question the user actually asked. Answer "
    "what was asked and no more — if the user asks whether it is loud, say, and do not add "
    "the decor while you are there.",
    "",
    "IF YOU DO WRITE A BOT TURN THAT NAMES ONE OF THE FIVE ANYWAY, SAY SO. Return the "
    "message numbers in a `free_offers` key: {\"transcript\": \"...\", \"free_offers\": [5]}. "
    "A declared one is allowed; an undeclared one is indistinguishable from the mistake it "
    "looks like. Keep it rare, and keep it recombining: an escape works with what the "
    "conversation has already put on the page, and does not introduce an attribute nothing "
    "in the session had mentioned.",
)


#: The six closed attributes, as the narrow markers a rule can read.
#:
#: Same discipline as `DISCLOSURE_PROBES`' markers and `SCREENING_FIELD_PATTERNS`: narrow
#: enough that a match is the defect rather than the construction, and it errs toward missing a
#: paraphrase. It reads BOT turns only, skips any message the author declared in `free_offers`,
#: and exempts a field the session briefs — a beat on `wait_tolerance` is a session where the
#: queue is the point.
#:
#: `event_types_liked` and `event_types_avoided` share one pattern and exempt each other: they
#: are the same page-level vocabulary, and a session briefed on either is one where naming the
#: format is what the beat asks for.
OFFER_ATTRIBUTE_PATTERNS: dict[str, str] = {
    "noise_preference": (
        r"\bquiet(?:er)?\b|\blively\b|\bbuzzy\b|\bloud\b|\bnoisy\b|\browdy\b"
        r"|\bhushed\b|\bbusy late\b|\bgets busy\b|\bthe music\b"
    ),
    "design_sensitivity": (
        r"\bd[eé]cor\b|\bstylish\b|\bminimalist\b|\bwarehouse\b|\bshared tables\b"
        r"|\bcandlelit\b|\bfairy lights\b|\bexposed brick\b|\bstripped[- ]back\b"
        r"|\bmismatched\b|\bthe interior\b"
    ),
    "wait_tolerance": (
        r"\bqueue(?:s|ing)?\b|\bwalk[- ]?ins?\b|\bno reservations\b"
        r"|\b(?:just|you|and) turn up\b|\bfirst come\b|\bwait(?:ing)? (?:time|list)\b"
        r"|\bcan'?t book\b|\bdon'?t take bookings\b"
    ),
    "deal_seeking": (
        r"\bspecials?\b|\bhappy hour\b|\bearly bird\b|\bprix fixe\b"
        r"|\bdiscount(?:ed)?\b|\btwo for one\b|\bdeals?\b(?!\s+with)|\boffer on\b"
    ),
    "event_types_liked": (
        r"\bquiz\b|\bcomedy\b|\bopen mic\b|\bkaraoke\b|\bgig\b|\blive music\b"
        r"|\bscreening\b|\btasting\b(?![- ](?:menu|led|style))|\bworkshop\b|\bclass\b"
    ),
    # The sixth, and it is not a room attribute like the other five — it is on the plate.
    # `gen-004-s3` is the case and it is unwinnable at the turn the reader blamed: the bot
    # lists "a cacio e pepe, a smoked tomato orecchiette, and an artichoke thing with chilli"
    # to a card whose `spice_tolerance` is `seeks_hottest` and whose map holds the field
    # UNEVIDENCED at every checkpoint. The user takes the chilli dish and the page leaks a
    # must-unevidenced field; the user passes it and the page contradicts the card. The
    # author lost at the bot's turn, and the reader filed it against the user's.
    #
    # `\bhot\b` is deliberately absent: "a couple of hot things" on a food-hall counter is
    # temperature, it appears in `gen-001-s1` verbatim, and a marker that cannot tell the two
    # apart fires on the construction. The heat words here cannot mean anything else.
    "spice_tolerance": (
        r"\bchill?i(?:es|s)?\b|\bspicy\b|\bspiciness\b|\bfiery\b|\bhot sauce\b"
        r"|\bscotch bonnet\b|\bhabaneros?\b|\bjalape[nñ]os?\b|\bsichuan\b"
        r"|\bszechuan\b|\bpiri[- ]piri\b|\bnduja\b|\bharissa\b|\bvindaloo\b"
        r"|\bhow hot\b|\bheat level\b|\bkicks? of heat\b"
    ),
}

#: The offer's slots printed as a form rather than said.
#:
#: **Measured, on the first 14 sessions authored under `_OFFER_TEMPLATE`: 11 of them did this.**
#: The block's first draft laid the slots out as a labelled list, and the author transcribed the
#: list — *"[5] BOT: Here's one.\n\nNAME: Tarik's Grill House\nKIND: A restaurant with a
#: counter."* The brief has said "Do not label anything" since v0, three paragraphs further down,
#: and it did not survive contact with a block that looked like a form. That is the fourth time
#: on this project that a prose instruction lost to a construction, and the answer is the same
#: one every time: the block now says the slots are what the turn CONTAINS, and this reads what
#: it printed.
_OFFER_LABEL = re.compile(r"(?:^|\n)\s*(NAME|KIND|ONE|FIT|PLACE|WHY)\s*:", re.I)


#: Fields whose brief exempts each other, because they are one vocabulary on the page.
OFFER_ATTRIBUTE_ALIASES: dict[str, tuple[str, ...]] = {
    "event_types_liked": ("event_types_liked", "event_types_avoided"),
}


#: What counts as the USER having already put an axis on the page — deliberately WIDER than
#: what counts as the bot volunteering one.
#:
#: The asymmetry is the point and it is not sloppiness. Reading a user turn answers "is this
#: axis already live in this conversation?", where a near miss costs a bot turn that cannot
#: answer the question it was just asked. Reading a bot turn answers "did the offer introduce
#: it?", where a near miss is a retry on a page nothing is wrong with. Different question,
#: different direction to err in.
#:
#: *"Can I book a table?"* is the case that forced it: it carries no queue and no walk-in, so
#: the narrow pattern does not see it, and the bot's honest *"they don't take bookings"* came
#: back a finding.
OFFER_RAISED_EXTRA: dict[str, str] = {
    "noise_preference": r"\bbusy\b|\batmosphere\b|\bmusic\b|\bnoise\b",
    "design_sensitivity": r"\bd[eé]cor\b|\blooks? like\b|\bsmart\b|\bscruffy\b",
    "wait_tolerance": r"\bbook(?:ing|ed|able)?\b|\breserv\w*|\bwait\b|\btable for\b",
    "deal_seeking": r"\bdeals?\b|\bspecials?\b|\bcheap\w*|\boffers?\b",
    "event_types_liked": r"\bevent\w*|\bon that night\b|\bsomething on\b",
    "spice_tolerance": r"\bspic\w*|\bchill?i\w*|\bheat\b|\bhow hot\b|\bmild\b|\bburn\w*",
}


def _raises(line: str, field_id: str) -> bool:
    """Whether a user turn has already put `field_id`'s axis on the page."""
    pattern = OFFER_ATTRIBUTE_PATTERNS[field_id]
    extra = OFFER_RAISED_EXTRA.get(field_id)
    return bool(
        re.search(pattern, line, re.I)
        or (extra and re.search(extra, line, re.I))
    )


#: How a diet reads as a thing the user does not order. Spelled out rather than passed as the
#: schema token because the author is writing dialogue, and "the card says `dairy_free`" is
#: not something a person is; not ordering the cheese is.
_DIET_EXCLUSIONS: dict[str, str] = {
    "vegetarian": "meat, poultry, fish or seafood",
    "vegan": "meat, fish, dairy, cheese, eggs or honey",
    "pescatarian": "meat or poultry",
    "halal": "pork, or anything not halal",
    "kosher": "pork, shellfish, or meat and dairy in the same meal",
    "gluten_free": "bread, pasta, batter or anything else with wheat in it",
    "dairy_free": "cheese, cream, butter or milk",
    "low_sodium": "anything heavily salted, cured or brined",
    "keto": "bread, pasta, rice, potatoes or sugar",
}

#: How a drawn bin reads as one concrete outing. The map counts these occasions, so the
#: phrasing has to make the bin unmistakable on the page while staying a plan rather than a
#: statistic — "the user and one other person" is an occasion; "usually two of them" is the
#: profile recitation rule 3 bans, in the brief's own voice.
_OCCASION_LABELS: dict[str, str] = {
    "party_size_distribution": "who is going",
    "visit_time_distribution": "when",
    "price_band_distribution": "roughly what it costs",
}

#: What kind of place this outing is, which the scenery never said.
#:
#: Every occasion above describes an outing without ever saying what sort of outing it is, so
#: the author wrote the only one the app seemed to offer — a reserved sit-down table — for
#: everybody. That is fine for a person who would go anywhere and impossible for one capped at
#: counter service, and the 2026-08-21 pilot measured the difference: 11 contradictions on
#: `formality_ceiling`, 13 of 15 blocking findings on the three `counter_service` subjects and
#: none at all on the two `white_tablecloth` ones. Drawn from the card by
#: `schedule.formality_occasion`, never chosen here.
_FORMALITY_PHRASING: dict[str, str] = {
    "counter_service": "somewhere you order at a counter or a stall and carry it yourself — "
                       "no table service, no booking a table",
    "casual_sit_down": "a casual sit-down place with table service, nothing formal",
    "smart_casual": "a smart-casual restaurant — booked, table service, a step up from "
                    "everyday",
    "white_tablecloth": "a formal restaurant — white tablecloth, the top of what this person "
                        "would ever book",
}

_PARTY_PHRASING: dict[str, str] = {
    "1": "the user on their own",
    "2": "the user and one other person",
    "3-5": "a small group, three to five",
    "6+": "a big group, six or more",
}

_TIME_PHRASING: dict[str, str] = {
    "weekday-lunch": "lunch on a weekday",
    "weekday-evening": "a weeknight evening",
    "weekend-day": "daytime at the weekend",
    "weekend-evening": "a weekend evening",
    "late-night": "late at night",
}

#: ADR-0045: bins are concrete per-head amounts, so the scenery states a real figure rather
#: than a word — the author has a number to work into the page instead of a vibe to
#: paraphrase, which is what makes the field readable by `_read_price_amount` at all.
_PRICE_PHRASING: dict[str, str] = {
    "<£15": "cheap — comfortably under £15 a head",
    "£15-30": "mid-range — somewhere around £15 to £30 a head",
    "£30-50": "a step up — somewhere around £30 to £50 a head",
    "£50+": "a blowout — well over £50 a head, no ceiling",
}

#: How far ahead the outing is, from the card's `planning_style`. ADR-0040.
#:
#: The horizon is not an extra fact the brief volunteers — it is **already** determined by the
#: session date and the visit-time bin, and the author was settling it by guessing. Guessing
#: put `gen-004-s9` (`fully_spontaneous`) on a table booked for a future Saturday and
#: `gen-003-s8` (`books_weeks_ahead`) on one booked for the coming one: two of the nine
#: contradictions the reader arm found on 2026-08-18, and unwritable rather than careless.
#: Stating it makes the already-readable value the card's own instead of a coin flip.
#:
#: The same-day entries are only satisfiable because `schedule._alignment_days` puts the
#: session on a day the drawn bin can happen on. Without that alignment this phrasing would be
#: a brief that contradicts itself.
#: `decides_same_day` was reworded on 2026-08-21 and the old string is the reason. It read
#: *"and it is for today, decided now rather than in advance"* — which is the sentence
#: `fully_spontaneous` means. Both levels are "for today"; what separates them is where the
#: person is when they decide, and the anchors say so exactly ("picks in the morning for that
#: evening" against "decides on the pavement, already out"). An author handed "decided now" at
#: a late-night bin writes a user deciding on the spot, because that is what it says.
#: `gen-001-s6` is the measured case: the brief obeyed, the page landed one rung down, and the
#: editor burned three attempts on wording that could not win against its own brief.
_HORIZON_PHRASING: dict[str, str] = {
    "fully_spontaneous": "and it is for today — they are out already and sorting it now",
    "decides_same_day": "and it is for later today — settled earlier in the day, before they "
                        "set out rather than once they are out",
    "books_days_out": "and it is a few days off",
    "books_weeks_ahead": "and it is a few weeks off, booked well ahead",
}

#: Appended to the horizon where the outing is somewhere you cannot book. ADR-0053 draws the
#: format from the card and ADR-0040 draws the lead time from the card, and the two draws are
#: independent — so a `books_days_out` subject capped at `counter_service` gets a brief that
#: says the outing is days away *and* that there is no table to reserve. Eight of the pilot's
#: 57 sessions carry that pair. The author resolves it the only way the page allows, by having
#: the user refuse to book, and the reader files the refusal as a short lead time:
#: `gen-002-s3` is *"no we're not booking anything, we'd rather just walk up"* on a
#: `books_days_out` card.
#:
#: A **suffix** rather than a second table, deliberately. `test_the_horizon_the_brief_writes_
#: is_the_horizon_the_map_credits` asserts the drift guard by looking for a
#: `_HORIZON_PHRASING` value inside the "when" sentence; a parallel table would pass that
#: check by not being reached, and the guard would quietly stop covering these eight sessions.
#: Keeping the base phrase a substring keeps them covered.
_HORIZON_UNBOOKABLE: str = (
    " — and there is nothing to book at a place like this, so deciding ahead means the plan "
    "was made days ago, not that a table was held. Nobody books, and nobody not-booking is "
    "the user deciding late"
)

#: The levels the suffix is about. `fully_spontaneous` and `decides_same_day` do not book
#: either, so on those the sentence would be answering a question the brief never raised.
_HORIZON_BOOKS: frozenset[str] = frozenset({"books_days_out", "books_weeks_ahead"})

_OCCASION_PHRASING: dict[str, Any] = {
    "party_size_distribution": lambda b: _PARTY_PHRASING.get(b, b),
    "visit_time_distribution": lambda b: _TIME_PHRASING.get(b, b),
    "price_band_distribution": lambda b: _PRICE_PHRASING.get(b, b),
}


def continuity_for(
    *,
    card: Mapping[str, Any],
    established: Sequence[str] = (),
    this_session: Sequence[str] = (),
    this_session_own: Sequence[str] | None = None,
    suppress: Sequence[str] = (),
    occasions: Mapping[str, str] | None = None,
    formality: str = "",
    withheld: Mapping[str, Sequence[Any]] | None = None,
) -> dict[str, Any]:
    """The continuity block for one session, from the card and the prefix alone.

    ``established`` is the fields whose own beats have already landed — so the standing block
    is a function of sessions before this one and nothing else, and the whole thing stays
    prefix-stable. ``this_session`` is every field this session has a beat on, at any
    attribution: a field being written *now* is never also silenced now.

    ``this_session_own`` is the subset of those beats that are **the subject's own**, and it is
    what the standing/avoid guards key on. Keying them on `this_session` was a live defect: a
    third-party beat names a field without saying anything about the subject, so
    `gen-003-s2` — where the scheduled `cuisines_avoided` beat is a companion's *french* under
    ADR-0036 — dropped the subject's own *vietnamese, sushi* line from the brief entirely, and
    the page booked a kaiseki lunch. The reader arm read that as a contradiction against the
    card. The rest of the block still keys on `this_session`: scenery and `settled_once` are
    about whether the *page* covers the field, which any attribution does.

    ``withheld`` closes the second half of that same defect, one axis over. A scheduled field
    silences its guard, but a narrowing beat on a set-valued field is briefed with only a
    proper subset (`withheld_members`), so the members the brief declined to name were left
    both unmentioned and unguarded. Passing them here guards exactly those and nothing else:
    the beat still narrows on what it was given, and the remainder goes back to never coming
    up. Five pilot sessions carried the hole, all `cuisines_avoided`.

    **The remainder is always guarded as `avoid`, never as `standing`, even when the field is
    established.** `standing` states a value, and this is the one session actively writing that
    field — handing the author the withheld member as a true fact it may use is how a narrowing
    beat becomes a pinning one, which is the whole thing `briefed_subset` exists to prevent.
    Silence is the only instruction that neither contradicts the card nor evidences it.

    `accessibility_needs` is deliberately out of scope. Its guard is `standing`-only — a
    positive requirement the venue must meet, with no `avoid` phrasing to fall back on — and
    nothing in the pilot narrows it: every scheduled accessibility beat is a `disclosure` of an
    empty list, for which `withheld_members` is empty anyway. Inventing a silence phrasing for
    a case with no instance is how untested prose gets into the most expensive block there is.
    """
    here = set(this_session)
    guarded = set(this_session if this_session_own is None else this_session_own)
    done = set(established)
    held = {str(k): list(v) for k, v in (withheld or {}).items() if v}
    standing: list[str] = []
    avoid: list[str] = []

    def members(field_id: str, values: Sequence[Any]) -> tuple[list[Any], bool]:
        """What still needs guarding for this field, and whether the field is being written.

        Returns the full list when the field has no beat here, the withheld remainder when it
        does, and an empty list when a beat covers the field completely — which is the old
        behaviour, now reached by exhausting the remainder rather than by skipping the field.
        """
        if field_id not in guarded:
            return list(values), False
        remainder = [v for v in values if v in held.get(field_id, ())]
        return remainder, True

    diets, diets_written = members("dietary", list(card.get("dietary") or ()))
    if diets:
        excluded = "; ".join(_DIET_EXCLUSIONS.get(d, d.replace("_", " ")) for d in diets)
        settled = "dietary" in done and not diets_written
        (standing if settled else avoid).append(
            f"they do not eat {excluded}" if settled
            else f"the user ordering, eating or asking after {excluded}"
        )

    allergens, allergens_written = members("allergies", list(card.get("allergies") or ()))
    if allergens:
        listed = ", ".join(a.replace("_", " ") for a in allergens)
        settled = "allergies" in done and not allergens_written
        (standing if settled else avoid).append(
            f"they cannot have {listed}" if settled
            else f"the user ordering or eating anything with {listed} in it"
        )

    if card.get("alcohol_free_required") and "alcohol_free_required" not in guarded:
        (standing if "alcohol_free_required" in done else avoid).append(
            "they do not drink and will not go somewhere that serves alcohol"
            if "alcohol_free_required" in done
            else "the user drinking, ordering a drink, or picking a bar or wine bar"
        )

    needs = list(card.get("accessibility_needs") or ())
    if needs and "accessibility_needs" in done and "accessibility_needs" not in guarded:
        standing.append(
            "the venue has to work for them: " + ", ".join(n.replace("_", " ") for n in needs)
        )

    disliked, disliked_written = members(
        "cuisines_avoided", list(card.get("cuisines_avoided") or ())
    )
    if disliked:
        listed = ", ".join(c.replace("_", " ") for c in disliked)
        settled = "cuisines_avoided" in done and not disliked_written
        (standing if settled else avoid).append(
            f"they will not go for {listed}" if settled
            else f"the cuisines {listed} — as somewhere the user goes or wants to go"
        )

    # The **drawn** bin, not the shape. ADR-0037: these occasions are counted by the map, so
    # the session has to carry the one the schedule drew rather than something the author
    # picked from a described distribution. Naming the shape here would also be the §6.1
    # collision in the brief's own voice — an author told "usually two, sometimes one" has
    # been handed the sentence the user is forbidden to say.
    drawn = dict(occasions or {})
    scenery = {
        name: _OCCASION_PHRASING[field_id](drawn[field_id])
        for field_id, name in _OCCASION_LABELS.items()
        if field_id in drawn and field_id not in here
    }
    # The horizon rides on "when", because it is the same fact: which instance of the drawn
    # bin this is. Suppressed when `planning_style` has a beat here — that session is writing
    # the field, and the guard rule is the same one the standing block follows.
    #
    # `horizon_lands` is the condition and it lives in `schedule.py`, because the map now
    # credits `planning_style` at WEAK on exactly the sessions this line writes it. Two copies
    # of the rule would let the map claim a cell the brief never asked for, and the divergence
    # would be silent — the page would simply be missing the sentence the grade rests on.
    planning = str(card.get("planning_style") or "")
    horizon = _HORIZON_PHRASING.get(planning)
    if horizon and horizon_lands(occasions=drawn, here=here):
        # The venue format is drawn independently of the lead time, so the two can arrive
        # jointly unwritable — days of notice for a place with nothing to reserve. Say which
        # of the two the author is meant to honour rather than leaving it to pick.
        if formality == "counter_service" and planning in _HORIZON_BOOKS:
            horizon = f"{horizon}{_HORIZON_UNBOOKABLE}"
        scenery["when"] = f"{scenery['when']}, {horizon}"
    # Price is the one occasion a conversation can genuinely leave alone, and saying so is
    # what stops the author supplying a number of its own. Party size and time are always
    # drawn, so this branch only ever fires for price.
    # What kind of place. Unlike the three above this is not a bin the map counts as an
    # occasion — `_map_of` reads occasions and feeds placement, so counting it there would move
    # other fields' beats across the corpus. It is credited as an entailment instead, on the
    # same terms as the horizon and by the same argument. Suppressed where the field has a beat
    # here: that session is writing the ceiling deliberately.
    if formality and "formality_ceiling" not in set(here):
        scenery["what kind of place"] = _FORMALITY_PHRASING.get(formality, "")
    if scenery and "price_band_distribution" not in drawn | dict.fromkeys(here):
        scenery["roughly what it costs"] = (
            "does not come up — nobody settles a number, and the user does not react to one"
        )

    settled_once = [
        f"{fid.replace('_', ' ')}: {card.get(fid)!r}" for fid in suppress if fid not in here
    ]
    return {
        "standing": standing, "avoid": avoid,
        "scenery": scenery, "settled_once": settled_once,
    }


def _group_beats(
    beats: Sequence[Mapping[str, Any]]
) -> list[tuple[Mapping[str, Any], int]]:
    """Beats in order, with identical ones collapsed to `(beat, count)`.

    Identity is the whole instruction — field, move, value, bin, attribution — because two
    beats that differ anywhere read as two different asks and must stay two blocks. Only a
    literal repeat becomes a count.
    """
    grouped: list[tuple[Mapping[str, Any], int]] = []
    keys: list[str] = []
    for beat in beats:
        key = repr(sorted(
            (k, repr(v)) for k, v in beat.items() if k != "note"
        ))
        if keys and keys[-1] == key:
            grouped[-1] = (grouped[-1][0], grouped[-1][1] + 1)
            continue
        if key in keys:                       # a repeat with something else in between
            index = keys.index(key)
            grouped[index] = (grouped[index][0], grouped[index][1] + 1)
            continue
        keys.append(key)
        grouped.append((beat, 1))
    return grouped


def _continuity_lines(continuity: Mapping[str, Any]) -> list[str]:
    """The blocks that keep a session from contradicting sessions it cannot see the point of.

    Every one of these answers a defect measured in the pilot, and each is phrased to fix the
    contradiction **without** creating evidence, which is the only difficult part:

    * ``standing`` — hard constraints the user has already established. `gen-002` declared an
      alcohol-free requirement at s8 having chosen a wine bar at s5, and `gen-004` declared
      themselves vegan at s3 after reporting the pork dish "like four times" at s2. The prior
      transcripts are in the prompt verbatim and were not enough: what a constraint needs is
      to be named as *still binding*, not merely to be readable in the scrollback.
    * ``avoid`` — values the card carries that this session must not contradict **and must
      not evidence**. This is the delicate one. Handing the author a list invites it to have
      the user decline the item, which would pin a field the schedule has not scheduled, so
      the instruction is silence rather than refusal: these things do not come up at all.
    * ``scenery`` — the incidental shapes. A conversation about where to eat cannot avoid
      instantiating a party size, a time of day and a price level, so "unevidenced" was never
      achievable for those three fields by asking. 14 of 121 unevidenced distribution cells in
      the pilot carried a countable instance in a user turn, and the instances **disagreed**
      with gold on three subject-fields out of eight — `gen-002` showed a party of 3-5 three
      times against a gold mode of 2 at 0.58. Consistency here does not make the field
      evidenced; it makes the page stop lying about it.
    * ``settled_once`` — the `single_mention` hard case. `gen-001`'s shellfish allergy was
      scheduled exactly once at s2 and the author, being consistent, raised it again at s4.
      Consistency is the right instinct and it voids the construction, so the suppression has
      to be asked for explicitly.
    """
    lines: list[str] = []
    standing = list(continuity.get("standing") or ())
    if standing:
        lines += [
            "=== STILL TRUE OF THIS PERSON (already established; do not contradict) ===",
            *(f"  - {line}" for line in standing),
            "",
            "These are settled. Write the session as someone for whom they hold. Do not "
            "restate them, do not have the user explain them again, and do not have the bot "
            "confirm them back — they are simply true, the way a fact about yourself is true "
            "without being announced.",
            "",
        ]
    avoid = list(continuity.get("avoid") or ())
    if avoid:
        lines += [
            "=== NEVER COMES UP ===",
            *(f"  - {line}" for line in avoid),
            "",
            "The user does not choose these, order these, enjoy these or suggest these. "
            "**They also do not turn them down, complain about them, or remark on them.** "
            "Nothing here is a topic. If the bot would naturally offer one, it offers "
            "something else instead. A reader must finish this session no better able to say "
            "how the user feels about any of them than before it.",
            "",
        ]
    scenery = dict(continuity.get("scenery") or {})
    if scenery:
        lines += [
            "=== THIS PARTICULAR OUTING ===",
            *(f"  - {name}: {shape}" for name, shape in sorted(scenery.items())),
            "",
            "This is the occasion the session is about, and it should be plain from the "
            "conversation without anyone announcing it — it comes out in booking a table, "
            "saying when, settling on somewhere. State it ONCE, as this outing. Do not say "
            "how typical it is, do not compare it to other times, and never use 'usually / "
            "mostly / sometimes' about it: what the reader is entitled to is one occasion, "
            "and the pattern is theirs to accumulate across the whole history.",
            "",
        ]
    settled = list(continuity.get("settled_once") or ())
    if settled:
        lines += [
            "=== SAID ONCE ALREADY — DO NOT RAISE AGAIN ===",
            *(f"  - {line}" for line in settled),
            "",
            "It was stated earlier in this history and it still holds, but neither the user "
            "nor the bot brings it up in this session. It does not get re-checked, "
            "re-confirmed or re-explained. This is deliberate: the dataset is measuring "
            "whether something mentioned once is remembered.",
            "",
        ]
    return lines


def beat_brief(beat: Mapping[str, Any]) -> str:
    """The instruction for one scheduled beat."""
    field = fields.BY_ID[beat["field_id"]]
    parts = [f"FIELD {beat['field_id']} — via {beat['move'].upper()}"]
    if field.doc:
        parts.append(f"  what it is: {field.doc}")

    value = beat["gold_value"]
    if beat.get("attributed") == THIRD_PARTY:
        parts.append(
            f"  SOMEONE ELSE'S REQUIREMENT — {value!r}. This belongs to a person the user is "
            f"going with, and the user is only relaying it. Say plainly whose it is: 'one of "
            f"them can't have X', 'my sister needs Y'. The venue has to satisfy it, so the "
            f"user presses on it exactly as hard as they would their own."
        )
        parts.append(
            f"  THE USER DOES NOT SHARE IT, and the conversation must leave that legible. "
            f"They are accommodating somebody. Do not have the user say they have this "
            f"requirement, imply they do, or use 'we' where 'they' is meant — and equally, "
            f"do not have them announce that they *don't* have it, which is the same "
            f"profile recitation from the other side. Whose it is should be plain from how "
            f"it is said, not from a disclaimer."
        )
        if beat.get("note"):
            parts.append(f"  hint: {beat['note']}")
        return "\n".join(parts)

    if not beat.get("supports_gold", True):
        parts.append(
            f"  SCRIPTED SELF-DESCRIPTION. The user says something about themselves that "
            f"implies the OPPOSITE of {value!r}. This is a deliberate falsehood the person "
            f"believes about themselves. Their actual behaviour elsewhere shows {value!r}. "
            f"Write the claim as a passing remark, not a confession. This is the one place "
            f"rule 3 does not apply — the self-summary is the artifact being tested — and "
            f"the §7.2 audit is told so."
        )
        return "\n".join(parts)

    narrowing = beat.get("move") in NARROWING_MOVES
    ordinal_interval = (
        BRIEF_ORDINAL_INTERVAL
        and narrowing
        and bool(field.anchors)
        and isinstance(value, str)
        and value in field.levels
    )
    if ordinal_interval:
        parts.append(_interval_instruction(field, value))
    elif narrowing and field.metric == fields.METRIC_JACCARD and isinstance(value, list):
        parts.append(_partial_set_instruction(value))
    else:
        parts.append(f"  true value: {value!r}")
        if field.anchors and isinstance(value, str) and value in field.levels:
            index = field.levels.index(value)
            parts.append(f"  behaves like this: {field.anchors[index]}")
            # BOTH neighbours, and the downward one was missing until 2026-08-21. An ordinal
            # has two ways to be wrong and the guard named one of them, so every measured
            # overshoot downward went unwarned: `gen-000-s6` is briefed
            # `planning_style: decides_same_day` — a beat on the field, so this is the only
            # instruction the author gets about it — and wrote a user who is "out of the house
            # already" and will "wander down once I've finished up here", which is the anchor
            # for the rung below, quoted almost word for word. The brief warned it off
            # `books_days_out` and said nothing about `fully_spontaneous`.
            if index + 1 < len(field.anchors):
                parts.append(
                    f"  MUST NOT read as the next level up, which is: "
                    f"{field.anchors[index + 1]}"
                )
            if index > 0:
                parts.append(
                    f"  MUST NOT read as the next level down, which is: "
                    f"{field.anchors[index - 1]}"
                )
    if beat.get("move") == "disclosure":
        parts.append(
            "  DISCLOSED ABSENCE. This person has none of these. The BOT must ask first — "
            "checking before booking, confirming a detail — and the user answers plainly "
            "that there is nothing, short and unremarkable. The user must NOT raise the "
            "subject themselves and must NOT ask the question on the bot's behalf; "
            "unasked, that is the profile-recitation rule 3 bans."
        )
        probe = DISCLOSURE_PROBES.get(str(beat.get("field_id")))
        if probe is not None:
            parts.append(f"  THE BOT'S QUESTION MUST NAME THIS FIELD. {probe[0]}")
            parts.append(
                "  AND SO MUST THE USER'S ANSWER. Have the reply say what is being ruled "
                f"out: {probe[2]} A blanket denial is not confined to this field. This "
                "person may hold a requirement of an entirely different kind, and a "
                "reader takes 'nothing like that' as ruling that one out as well — so "
                "the page ends up stating the opposite of the truth about them. A reader "
                "should finish the session knowing there are none OF THIS PARTICULAR "
                "THING, not wondering which of the person's requirements just got ruled "
                "out."
            )
    elif field.is_constraint:
        parts.append(
            "  This is a hard requirement, not a preference. A person states a requirement "
            "plainly when it matters — but state it as this person would in passing, not as "
            "a form field."
        )
    if beat.get("move") != "disclosure":
        parts.append(
            "  THIS IS THE USER'S OWN. It must be theirs on the page — not a companion's, "
            "not the group's, not something they are passing on for somebody else. The "
            "pilot lost two fields to this: a subject's alcohol-free requirement was "
            "written as 'one of the four doesn't go anywhere there's a bar', and a "
            "subject's own avoided cuisines became 'someone with us who won't touch "
            "either'. Both read as evidence about a person who is not the subject."
        )
    if field.metric == fields.METRIC_JSD:
        parts.append(_distribution_instruction(field, value, beat.get("bin")))
    if beat.get("note"):
        parts.append(f"  hint: {beat['note']}")
    return "\n".join(parts)


#: §5's narrowing moves. Kept here rather than imported from `schedule` because what this
#: module needs is the distinction between a beat that may pin and one that may not, and the
#: move names are the only part of §5's model that survived measurement.
NARROWING_MOVES: frozenset[str] = frozenset({"reaction", "anecdote"})

#: **Off. ADR-0012's ordinal interval brief is reverted (ADR-0015).** Completing the 2x2 of
#: {plain, interval} authoring x {commitment, licensed-set} question showed the *question*
#: carrying the entire effect — §11's earned condition moves ~+38 points on either text — while
#: this brief moved it by noise and cost correctness on the fields the schedule pins. Split by
#: metric family under the licensed question, where the reading is cleanest, the damage is
#: exactly where this brief applies and nowhere else:
#:
#:     ordinal      77% -> 52%    reports-gold on schedule-`strong` cells (n = 44)
#:     set-valued  100% -> 100%   `_partial_set_instruction`, unchanged and kept
#:     other        88% ->  88%   never briefed with an interval
#:
#: The diagnosis behind ADR-0012 stands — `probe_reauthor/sweep.py` falsified §5's move
#: ranking, and that finding is independent. What failed is the remedy: the over-pinning was in
#: the *reading*, and §7.3's commitment question converted willingness to guess into `strong`.
#: Once that question was replaced the brief retained only its cost.
#:
#: The flag exists rather than the code being deleted so that `probe_reauthor --variant
#: interval` stays **re-authorable**: three ADRs and every table in that probe's README rest on
#: text this brief produced, and a revert that makes them unreproducible destroys the evidence
#: for itself. Set it True to reproduce that arm; nothing on the shipped path does.
BRIEF_ORDINAL_INTERVAL: bool = False


def licensed_interval(field: fields.Field, value: str) -> tuple[str, ...]:
    """The levels a narrowing beat may leave open, including the true one.

    **Superseded (ADR-0015) and off the shipped path** — reachable only under
    `BRIEF_ORDINAL_INTERVAL`, which exists to keep the interval arm re-authorable. Read that
    flag's note before using this. It stays correct code for the configuration it belongs to.

    Two adjacent levels: the value and one neighbour. Two rather than three because the
    weak band is defined as "narrowed but not pinned" and `WEAK_CREDIT` (§8.1) is priced on
    one remaining binary ambiguity — a wider interval would credit abstention against a
    weaker constraint than the scorer assumes.

    The neighbour is chosen deterministically toward the middle of the scale, so an interval
    never falls off the end and the extreme levels are not systematically easier to pin than
    the middle ones — which would put a bias in the weak band aligned with the value, and
    §4.3 already spends effort keeping that kind of correlation out.
    """
    index = field.levels.index(value)
    if index == 0:
        neighbour = 1
    elif index == len(field.levels) - 1:
        neighbour = index - 1
    else:
        neighbour = index + 1 if index < (len(field.levels) - 1) / 2 else index - 1
    lo, hi = sorted((index, neighbour))
    return field.levels[lo:hi + 1]


def _interval_instruction(field: fields.Field, value: str) -> str:
    """Why a narrowing beat is briefed with an interval and not with a value.

    Measured (`probe_reauthor/sweep.py`, ADR-0012). §5's model said the move controls whether
    a beat pins: a `request` pins, an `anecdote` only narrows. It does not. At one supporting
    beat, `anecdote` produced a judged `strong` **69%** of the time against `request`'s 50%,
    and 63% of those single-beat commitments landed on **gold** against a 22% chance floor.

    The cause is a decision that worked. This brief used to hand the author `true value: X`
    and X's anchor for *every* beat, with the move changing only the framing verb — and
    ADR-0006 spent three rewrite passes making each anchor performable and recoverable as a
    **distinct level**. So a narrowing beat was a performance of the exact level told as a
    story, and a competent reader recovered the level, exactly as designed. No threshold, move
    choice or beat budget fixes that, because the information in the beat is the level itself.

    A beat that must not pin therefore cannot be told which level to perform. It is given the
    interval and the two anchors it must sit *between*, and the ambiguity it must preserve.
    """
    interval = licensed_interval(field, value)
    indices = [field.levels.index(level) for level in interval]
    excluded = [
        f"NOT {field.anchors[i]}" for i in range(len(field.levels)) if i not in indices
    ]
    return "\n".join([
        f"  NARROWING BEAT — do NOT pin this field. The reader must finish able to say "
        f"'somewhere in this range' and unable to choose within it.",
        f"  licensed range: {', '.join(interval)} — the true value is one of these and the "
        f"text must not reveal which.",
        f"  it must read as consistent with both of: "
        + " / ".join(field.anchors[i] for i in indices),
        f"  and rule out: {'; '.join(excluded)}" if excluded else "",
        f"  Show one occasion consistent with BOTH of the licensed levels. Do not perform "
        f"either anchor distinctly — a performance a reader can name is a pin, which is the "
        f"one thing this beat must not be.",
    ]).replace("\n\n", "\n")


def briefed_subset(value: Sequence[Any]) -> list[Any]:
    """The members a narrowing beat is allowed to name. One definition, three readers.

    `_partial_set_instruction` renders it, `withheld_members` is its complement, and
    `continuity_for` guards that complement. Three copies of `value[:-1]` is how the guard and
    the brief drift apart, which is the defect `withheld_members` exists to close.
    """
    return list(value[:-1] if len(value) > 1 else value)


def withheld_members(beat: Mapping[str, Any]) -> list[Any]:
    """The set members this beat's brief will NOT name, and which therefore need guarding.

    **The hole this closes.** A narrowing beat on a set-valued field is briefed with a proper
    subset, because naming the whole list pins a field that is only meant to narrow. The
    `NEVER COMES UP` guard in `continuity_for` then keys on the FIELD, so scheduling the field
    switched the guard off for the members the brief had just declined to name. The author was
    told to mention one avoided cuisine, told nothing about the other, and had its protection
    removed on both — `gen-001-s7` (`['latin_american', 'middle_eastern']`, briefed
    `latin_american`) wrote the user choosing a kebab place and committing to it. Five sessions
    in the pilot carried it, all `cuisines_avoided`; both readers found one.

    **This is ADR-0036's bug on a second axis.** That ADR made the same guard
    *attribution*-aware, after a companion's `french` beat made `contradiction_probes` skip the
    subject's own `vietnamese, sushi` and the page booked a kaiseki lunch: *"keying this
    control on the field alone blinded the probe exactly where the brief was also blind."* The
    guard was still not *value*-aware. Same sentence, different axis.

    Empty unless the beat is the subject's own, supports gold, narrows, and carries a
    multi-member list — which is exactly the condition under which `beat_brief` reaches
    `_partial_set_instruction`, and it is written to stay that way.
    """
    if beat.get("attributed") == THIRD_PARTY or not beat.get("supports_gold", True):
        return []
    if beat.get("move") not in NARROWING_MOVES:
        return []
    field = fields.BY_ID.get(str(beat.get("field_id")))
    value = beat.get("gold_value")
    if field is None or field.metric != fields.METRIC_JACCARD or not isinstance(value, list):
        return []
    shown = briefed_subset(value)
    return [member for member in value if member not in shown]


def _partial_set_instruction(value: list[Any]) -> str:
    """The set analogue of a licensed interval.

    An ordinal is narrowed by leaving two levels open; a set is narrowed by leaving its
    **extent** open, which is what §5.1 already says — *"a set's extent is unknown until the
    mentions stop"*. The brief was not honouring it: a narrowing beat still received the
    whole list, so an author naming all three members hands a reader the complete set and
    the field pins on one beat, exactly as the ordinals did.

    So a narrowing beat gets a proper subset and is told the extent must stay open. The
    scorer's metric is Jaccard, so a reader who commits to the subset scores partial credit
    rather than zero — which is the right shape for the weak band: narrowed, not pinned.
    """
    shown = briefed_subset(value)
    return (
        f"  NARROWING BEAT — do NOT pin this field. Mention only: {shown!r}, and leave the "
        f"extent open: nothing in the text may suggest the list is complete. No 'that's "
        f"about it', no summing up, no exhaustive answer to a direct question. A reader must "
        f"finish knowing some of what belongs here and unable to say whether there is more."
    )


def _distribution_instruction(
    field: fields.Field, value: Any, target_bin: str | None = None,
) -> str:
    """§6.1's widest collision, as an authoring technique rather than an exemption.

    §5.1 prices a distribution at several mentions because a shape over bins needs several
    observations. The trap is that an author told to convey a shape conveys it the only way
    one turn can — by listing the bins with relative weights — and that is precisely what a
    leakage auditor calls a filled-in availability profile, correctly. Unlike the other two
    §6.1 collisions this is on every subject, since all three distribution fields are.

    So the brief asks for **one bin, as one occasion**, and lets the shape accumulate across
    the beats the schedule already spread over sessions. `check_session` enforces the
    negative half.

    **Fixed, ADR-0028 decision 5.** Until 2026-08-16 the bin named here was `max(value, ...)`
    — the *heaviest* — because `schedule.Beat` carried no target bin, so every one of a
    field's ~5 beats briefed the same bucket and nothing accumulated: the shape could not
    emerge from five draws of the mode. Measured on the 20 sparse subjects at the time, mean
    1−JSD(aggregated, gold) was 0.631 against a point-mass ceiling of 0.675, with 48.9% of
    aggregates a literal point mass, against gold cards carrying 2.43 buckets at ≥10% mass.

    `target_bin` is now `Beat.bin`, drawn per beat from the gold distribution
    (`_sample_distribution_bin`) so the beat sequence *is* a sample of the shape rather than a
    repeated performance of its mode. Falls back to the old heaviest-bin behaviour when
    `target_bin` is `None` — a beat built before this field existed, or a caller that never
    sampled one — so this function has no beat it cannot brief.
    """
    bin_id = target_bin if target_bin is not None else (
        max(value, key=lambda k: value[k]) if isinstance(value, Mapping) else value
    )
    return (
        f"  DISTRIBUTION FIELD. Give exactly ONE bin in this session, as one concrete "
        f"occasion — this session's bin is {bin_id!r}, and the schedule spends other sessions "
        f"on the rest. Do NOT list bins, do NOT compare their frequencies, and do not use "
        f"'mostly / sometimes / every now and then' about them. A user who says which slots "
        f"they use and how often has filled in a form; a user who mentions one plan is a "
        f"user. The shape is the reader's job to accumulate, not this turn's job to state."
    )


def distractor_scenario_for(subject_id: str, session: int) -> str:
    """A stable scenario per (subject, session), spread over the library.

    Deterministic rather than random for the same reason the card grid is: two subjects that
    share a distractor scenario are a visible property of the dataset, and a draw would put
    sampling noise on top of a spread that can simply be even.
    """
    key = sum(ord(c) for c in subject_id) + session * 7
    return DISTRACTOR_SCENARIOS[key % len(DISTRACTOR_SCENARIOS)]


def summarize(problems: Iterable[Problem]) -> dict[str, int]:
    """Counts by kind, for the per-run table the §7.4 report carries."""
    counts: dict[str, int] = {}
    for problem in problems:
        counts[problem.kind] = counts.get(problem.kind, 0) + 1
    return dict(sorted(counts.items()))
