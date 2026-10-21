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
change. Duration storage uses minutes; the website displays hours or days.
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

Three earlier rolling 12-month validation folds select the model by MAE. Ties
favor seasonal naive. Candidates and settings are fixed before test scoring:

- Seasonal naive repeats the prior year's matching month.
- Seasonal mean averages matching months in a trailing 60-month window.
- Seasonal trend fits a linear trend with month effects in that same window.

All models report MAE, RMSE, and WAPE on the same holdout. WAPE is undefined when
the observed total is zero. Future values are refit/exported only if the
validation-selected model beats seasonal naive on held-out MAE. The current
snapshot withholds future predictions because that gate is not met.

The export includes split dates, validation scores, test actuals/predictions,
evaluation time, and an aggregate input hash. No uncertainty intervals are
estimated. Simulator assumptions, changing coverage, and incomplete source
periods limit interpretation; these results are not real healthcare demand.
