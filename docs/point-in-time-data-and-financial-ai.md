# What a financial dataset knew on 1 December 2009

A financial AI system can produce a convincing historical answer from data that
was not available at the time. Point-in-time data helps make that failure visible.

Apple's fiscal 2008 diluted earnings per share is a concrete example. Apple first
reported **$5.36** on 27 October 2009. It later reported **$6.78** on 25 January
2010 after adopting a revenue-recognition standard retrospectively. A query as of
1 December 2009 should return $5.36. A current fundamentals panel can show $6.78
for every date, silently putting later knowledge into an earlier period.

Aletheia stores both the period a fact describes and the date that filing became
knowable. Its as-of query asks what the stored evidence supported on a specified
date. The original value appears in Apple's [2009 Form 10-K filed 27 October
2009](https://www.sec.gov/Archives/edgar/data/320193/000119312509214859/0001193125-09-214859-index.htm).
The later value appears in Apple's [Form 10-K/A filed 25 January
2010](https://www.sec.gov/Archives/edgar/data/320193/000119312510012091/0001193125-10-012091-index.htm).
The example and as-of query are also documented in the [repository README](../README.md).

## Measuring the size of the data-vintage problem

Study S002 asked how often a reported fact later appeared with another value in
one defined SEC cohort. Before running the aggregate, the study registered a 1%
threshold: below that level, the hypothesis would be considered overstated.

The cohort is a fixed-seed sample of 800 filers from a 2011 SEC
Assets/USD/CY2011Q4I frame, with a $500 million assets floor. The frame had 2,998
eligible filers out of 8,166. The evidence card records a data vintage of
27 July 2026.

At the study's fact-key grain — company, taxonomy, concept, unit, period start,
and period end — **357,842 of 7,133,070 distinct facts (5.02%)** had more than
one reported value. At raw-row grain, **516,187 of 13,447,437 rows (3.84%)**
differed from the first report. The preregistered threshold was crossed.

These are counts in this declared cohort, not an estimate for all public
companies. A value change does not mean the issuer was wrong. The count includes
reclassifications and adoption of new accounting standards, which the study has
not sized separately. The result describes how much of this corpus is
retroactive; it is not an issuer-error or fraud rate.

## A wrong headline exposed a real query defect

An earlier version reported 16.4%. The query had left `period_start` out of the
fact key and merged full-year facts with fourth-quarter facts that shared an end
date. Restoring the full key changed the result to 5.02% and exposed a real query
layer defect. The point-in-time view now raises `AmbiguousPeriod` when an
end-date-only request matches multiple reporting periods. The correction and
result are recorded in the [S002 memo](S002-restatement-contamination.md),
[evidence card](../data/evidence/S002-restatement-contamination.md), and
[point-in-time query code](../packages/engine/src/aletheia/pit/view.py).

This matters for AI-assisted research because reproducible arithmetic cannot
repair an incomplete definition of which reported facts are the same fact.
Source grain, period identity, and the evidence date have to be right before a
model's answer can be trusted.

## Controls and their limits

The system uses the later of filing date and SEC dissemination date when the
dissemination date is available. The query layer filters out facts whose
knowledge date is after the requested as-of date. A runtime check also rejects a
future row if it slips past that filter; the behavior is exercised by the
[lookahead guard tests](../packages/engine/tests/unit/test_lookahead_guard.py).

There is a dating limitation: the SEC submissions endpoint does not provide a
dissemination date for every record, so filing date is used as a fallback. In a
measured daily-index sample, 123 of 4,005 records (3.1%) were disseminated before
their filing dates; the largest observed gap was eleven months. For those
backfilled records, the fallback may make information appear knowable too early.
The repository documents this boundary rather than treating successful data
delivery as proof of exact historical availability.

The separate S001 return-predictive study has not run. Price data covered only 8
of 226 resolved names, too few for its planned quantile analysis. S002 therefore
makes no claim about alpha, investment performance, LLM accuracy, answer quality,
customer deployment, or productivity. It measures the underlying SEC corpus and
the behavior of the data controls around it.

## Reproduce and inspect

The repository contains the study protocol, code, and evidence card. A full
ingest takes about 80 minutes; the study takes tens of minutes.

- [Aletheia repository](https://github.com/j-poc/aletheia)
- [S002 evidence card](../data/evidence/S002-restatement-contamination.md)
- [Full S002 memo](S002-restatement-contamination.md)
- [README and reproduction instructions](../README.md)
