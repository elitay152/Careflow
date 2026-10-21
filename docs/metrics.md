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

Historical population-at-risk exposure is not available. Patients seen is an
outcome, not a valid prospective exposure variable.
A larger same-seed synthetic cohort is not evidence of better generalization.
Independent simulator seeds can test robustness; categorized encounters and
historical exposure would support a more interpretable target. No data anomaly
is deleted merely because it worsens a model's score.

## Facility and regional demand

`mart_regional_demand` groups finished encounters by provider Organization,
provider three-digit ZIP area, care setting, and UTC start month. Exact HL7
v3-ActCode classes AMB, EMER, and IMP map to ambulatory, emergency, and inpatient;
other or missing systems/classes stay in other. This measures recorded encounters,
not unique patients, admission spells, staffed capacity, or all provider workload.
An inpatient encounter is not automatically a distinct hospitalization episode.

Provider identity resolves through local fullUrl, Organization/id, and conditional
identifier aliases. Ambiguous aliases are quarantined instead of reassigned.
Unknown references remain unmatched and are reported in coverage; joins must
preserve encounter counts. US five-digit ZIPs and nine-digit ZIP+4 values, with
or without a hyphen, use their first three digits. Provider names and routing are
Synthea reference/simulation data, not evidence of real service availability.

The latest 120 globally observed months supply a shared time axis. A group's
absent row means zero recorded cohort encounters only when the overall month
exists; it does not establish complete coverage, facility opening dates, or zero
real demand. Missing global months still prevent evaluation. Each view is
exclusive within itself; adding facility and ZIP views double-counts encounters.

A comparison requires 120 consecutive global months, at least 120 encounters
in the latest 12, activity in at least 10 of those 12, and at least 36 active
months in the preceding 60. These fixed descriptive gates do not establish
statistical reliability. The same five-model development protocol is used;
future predictions remain withheld. Sparse groups retain actual histories
without fitted curves or accuracy claims. The current cohort supports seven
facility ambulatory comparisons and thirteen ZIP-area comparisons; facility
emergency and inpatient series remain sparse.

The latest year has 61 inpatient encounters among 54 patients. A patient-level
hospitalization model is not trained. Additional independent events, feature
availability at prediction time, episode definitions, and complete follow-up are
needed. More synthetic histories can test simulator robustness, not clinical
validity. Current active diagnoses, future observations, and final medication
statuses must not leak into historical risk features. Patient death information
is retained for later censoring work; it does not establish complete follow-up.
Reference bed counts describe provider catalog metadata and cannot turn this
selected cohort into a whole-hospital occupancy measure.
