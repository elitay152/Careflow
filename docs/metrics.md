# Metric definitions

All metrics describe synthetic records. Warehouse calculations belong in
Careflow; the website renders aggregates and formats units.

| Metric | Definition |
| --- | --- |
| Monthly/annual encounters | Finished encounters grouped by UTC start month or calendar year. |
| Monthly/annual patients | Distinct patients with a finished encounter in that period; never a sum of smaller-period distinct counts. |
| Patients seen | Distinct patients in the latest observed month and its 11 preceding calendar months. |
| Encounters per seen patient | Finished encounters divided by distinct patients seen in that same 12-month window. |
| Mean/median duration | Encounter-level elapsed time for finished encounters with measured end times; missing durations are excluded. |
| Recorded condition prevalence | Distinct patients with active, recurrent, or relapsed, confirmed records per terminology code, divided by all loaded patients. |
| Medication requests | Request counts by status; not dispensing, administration, or adherence. |

The KPI window has an exclusive end date and stays fixed when chart filters
change. Duration storage uses minutes; analytics displays medians in minutes and means in hours or days.
Medians are computed over encounters, not monthly medians. Elapsed encounter
time is not hospital length of stay. Missing periods are not filled with zero.

The cohort includes deceased patients and has varying historical coverage.
Distinct patients seen in a year are not a historical population denominator.
Condition groups overlap; their counts must not be added.

## Clinical and contextual concepts

FHIR Condition records include clinical findings, social context, medical
history, and care situations. `condition_groups.json` maps exact SNOMED system/code
pairs to those views. Unknown codes remain unclassified; display-label substrings
are not used for classification.

The selected chronic subset uses 19 explicit codes in `utilization.py`: type 2
diabetes, essential hypertension, ischemic heart disease, hyperlipidemia,
hypertriglyceridemia, CKD stages 1–4 and end-stage renal disease, chronic congestive
heart failure, asthma/childhood asthma, chronic obstructive bronchitis, emphysema,
rheumatoid arthritis, Alzheimer's/familial early-onset Alzheimer's disease, and
epilepsy. Its patient total is a distinct union across qualifying codes. This is
a limited presentation cohort, not an exhaustive or clinically validated registry.

The assistant's condition metric queries the full recorded-condition mart.
Its literal name filter does not perform clinical concept expansion; a diabetes
substring can match prediabetes and complications as separate coded groups.

## Encounter forecast evaluation

The target is monthly finished encounter counts. The protocol uses the latest
120 consecutive observed months: 108 available training months and a final
12-month holdout. Missing months prevent evaluation.

Three earlier rolling 12-month validation windows rank five fixed candidates by
pooled MAE; ties favor seasonal naive. Each fit uses the trailing 60 months:

- Seasonal naive repeats the prior year's matching month.
- Seasonal mean averages matching months over five years.
- Seasonal trend fits a linear trend with month effects.
- Damped trend uses that fit but decays each future slope increment by 0.8.
- Poisson seasonal fits twelve month effects and a shared log-linear count trend
  by profiled likelihood with Newton iterations (50 maximum, tolerance 1e-10).
  The yearly log slope is bounded to [-2, 2] for stable extrapolation. Zero-count
  month groups produce zero expected counts. This is a conditional count mean,
  not a claim that encounter variance follows a Poisson distribution.

Protocol `rolling_origin_v2` exports every window's actuals/predictions, aggregate
validation MAE/RMSE/WAPE, final-year scores, dates, evaluation time, and input hash.
WAPE is undefined for zero observed totals. All predictions use only data before
that window; validation arithmetic is also checked by the website.

The final year has already been inspected, so the revised experiment labels it
a **development benchmark**, not an untouched test. Future values remain withheld
even if a candidate beats the baseline. No model settings are tuned to the final
year; independent evaluation is needed for a fresh performance claim.

The current cohort's seasonal mean ranks best on earlier windows (44.8 monthly
MAE). Seasonal naive performs best in the final-year comparison (27.9 MAE,
6.2% WAPE); the two additional models do not improve on it. A ranking difference
across periods is evidence of temporal instability, not proof that more patients
or a more complex model will fix forecasting.

No uncertainty intervals are estimated. Simulator assumptions, retrospective
cohort selection, changing coverage, and incomplete source periods limit
interpretation. Observed months are not proof of complete historical capture;
these results are not real healthcare demand or a population-normalized rate.

The latest 120 observed months have no gaps or zero-count months, which does not
prove complete source capture. PostgreSQL checks for 2020–2024 find no finished
encounters with missing ends or negative durations. Counts rise from 5,474 in
2020 to 6,680 in 2021, then fall to 5,688 in 2022. Visits of at most 15 minutes
rise from 1,538 to 2,936 in 2021; the stored fields do not establish why. That
change hurts last-year-repeat predictions in both 2021 and 2022. Annual patients
seen increase from 949 in 2023 to 961 in 2024 while visits fall from 5,724 to
5,401, so patient count alone does not explain activity.

The warehouse currently omits encounter category and historical population-at-risk
exposure. Patients seen is an outcome, not a valid prospective exposure variable.
A larger same-seed synthetic cohort is not evidence of better generalization.
Independent simulator seeds can test robustness; categorized encounters and
historical exposure would support a more interpretable target. No data anomaly
is deleted merely because it worsens a model's score.
