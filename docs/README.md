# Documentation

| document | read it for |
|---|---|
| [`SCORING.md`](SCORING.md) | how scoring works — the answer key, what the scorer reads, the audit |
| [`PIPELINE.md`](PIPELINE.md) | how a corpus is made: draw, author, audit, credit, edit, promote |
| [`GT-POLICIES.md`](GT-POLICIES.md) | the judgement calls in the gold, each with its reason. **Read this before disputing a cell** |
| [`GT-REVIEW.md`](GT-REVIEW.md) | the closed record of the 2026-08 gold review — how a suspected gold defect was verified before it was applied |
| [`FAILURE-MODES.md`](FAILURE-MODES.md) | the catalog of how systems fail here, with [`failure-modes/`](failure-modes/) holding a transcript-adjudicated example set per mode |
| [`SUT-RESULTS.md`](SUT-RESULTS.md) | what the submission side has measured, and on which base |
| [`distribution-shape.md`](distribution-shape.md) | why the three distribution fields are scored the way they are |
| [`weak-discernment.md`](weak-discernment.md) | what the WEAK band can and cannot separate |
| [`provenance/`](provenance/CANONICAL.md) | which gold base is canonical, and the manifest of the one the published data ships from |

## A note on the paths these documents cite

These documents were written inside the private tree the corpus was built in, and they are
**measurement records**: each one says what was run, on which artifacts, on which date. They have
been carried here with their substance intact rather than rewritten, because rewriting a record of
a run is the one edit that makes it worth less.

The cost is that many of them cite paths that do not exist in this repository — files under a
`runs/` directory, a `RESUME.md` pending-work log, a `v0.4/` tree, a `ui/` browser. **Read such a
path as "the evidence existed and was checked on that date", not as a link.** Where an artifact is
load-bearing for something a reader here would want to check, it has been carried into
[`provenance/`](provenance/).

The same goes for **ADR-numbered references** (e.g. "ADR-0065"): they name design records kept in
the unreleased development tree. Where a rule matters here, the document states the rule itself;
the number only says where its full argument lives. Likewise, a test count in a gate record
("pytest 956 passed") is the development tree's suite on that date; this repository ships 914
hermetic tests plus 2 data-gated ones.

What is live now, and maintained, is the repository [`README.md`](../README.md) and
[`PIPELINE.md`](PIPELINE.md). Those name paths that exist.

One consequence worth stating plainly: **a number in these documents was measured on the gold base
that was canonical the day it was taken**, which is generally not the base the published data
ships from. [`provenance/CANONICAL.md`](provenance/CANONICAL.md) carries the lineage, and each
document says which base it used. `SUT-RESULTS.md` opens with the nine paper runs on the current
base (`n50-tagfix-2026-10-15`); its older leaderboard tables predate the 18 + 4 gold fixes that
followed and were not re-scored.
