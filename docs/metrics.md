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

The latest year has 61 inpatient encounters among 54 patients. Inpatient-demand
counts and patient-level 90-day risk are distinct targets. Reference bed counts
cannot turn this selected cohort into a whole-hospital occupancy measure.

## 90-day inpatient risk

Protocol `admission_90d_v1` uses quarterly adult snapshots from 2015 through 2024.
A patient needs at least 365 days since the first available encounter and must
not already be in an inpatient episode. Overlapping/touching IMP encounters,
including transfers across providers, merge into one episode. A new episode
starting strictly after the snapshot and at most 90 days later is positive.

The observation end is an explicitly attested, exclusive simulator cutoff, not
last-visit inference or enrollment evidence. Undated deaths are excluded. A
negative needs observable follow-up through the horizon; a death before then is
censored rather than labeled negative. An admission observed before death remains
positive. This estimates outcomes among classifiable synthetic snapshots, not a
competing-risk survival probability for a general population.

Features are age; completed encounters over 90/365 days; completed emergency
encounters and prior completed inpatient episodes over 365 days; days since a
completed encounter (capped at 366); and previously recorded selected chronic,
type-2-diabetes, and hypertension codes. Diagnosis onset and recorded dates must
both strictly precede the snapshot date. Current condition/verification and
medication statuses are not features. Completed encounters must end no later than
the snapshot. Event-dated reconstruction is not a full bitemporal chart history.

A fixed SHA-256 patient partition assigns 70%/15%/15% to train/validation/test.
Only 2015–2020 snapshots enter training, 2021–2022 validation, and 2023–2024 the
internal test; snapshots outside a patient's assigned period are not modeled.
Patient sets do not overlap. Minimum gates are 50 distinct positive training
patients, ten each in validation/internal test, 50 patients with negative snapshots
per split, and complete condition recorded-date coverage. These are computability
guards, not proof of sufficient power or reliability. Repeated quarterly snapshots
are correlated and must not be counted as independent patients.

The pipeline fits StandardScaler on training data only, followed by fixed L2
logistic regression (C=1, lbfgs, max_iter=2000). Baselines use training prevalence
and Laplace-smoothed training outcome rates by prior admission. Each decision
threshold maximizes validation F1; ties favor precision and then the higher
threshold. No refit or threshold tuning uses either test cohort. All three models
report average precision, Brier score, precision/recall, confusion counts, and
five fixed probability calibration bins; empty bins have null rates.

Independent evaluation uses a new simulator seed with disjoint patient IDs,
2023–2024 snapshots, and at least 20 positive patients. The original model,
scaler, and threshold are frozen. It tests simulator-seed robustness, not clinical
validity. Patient rows stay in ignored local artifacts. Public evidence includes
only aggregate scores, split/coverage counts, input hashes, and source watermarks.
Stale evidence is withheld after either source cohort changes. The model is an
evaluation experiment, not a deployed clinical scoring service.

### Local feature and model comparison

`python -m careflow.risk_experiment` compares the original nine features with 16
additional event-dated features: 7/30-day completed visits, 30/90-day emergency
visits, 90-day completed inpatient episodes, inpatient days in the preceding year,
days since discharge (capped at 366), visits in the preceding 90–180-day window,
distinct previously recorded condition codes, newly recorded codes in 90 days,
and six exact-code history flags (ischemic heart disease, heart failure, kidney
disease, obstructive lung disease, asthma, and dementia). Overlapping completed
stays merge; inpatient days are clipped to the lookback. These are recorded-history
and utilization proxies, not a validated comorbidity index or illness-severity score.
A diagnosis may remain in history after resolution. Absence means no qualifying
record, not proof of no disease. Counts include contextual Condition codes.

The fixed comparison uses standardized logistic regression and histogram gradient
boosting (100 iterations, seven leaves, minimum 30 samples per leaf, L2=1, no early
stopping). Validation F1 alone selects decision thresholds. Both previously
inspected test cohorts are development evidence; this experiment uses the original
internal test only and makes no fresh independent performance claim. A new seed
must be reserved before a final comparison; patient snapshots remain correlated.

Current development results use 26 positive snapshots among 853 internal-test
snapshots. Average precision measures ranking, not accuracy. The prevalence
baseline's average precision is 0.0305.

| Features | Model | Validation AP | Development AP |
| --- | --- | ---: | ---: |
| Original (9) | Logistic | 0.4167 | 0.0393 |
| Original (9) | Boosting | 0.4106 | 0.0533 |
| Utilization/comorbidity (25) | Logistic | 0.4200 | 0.0366 |
| Utilization/comorbidity (25) | Boosting | 0.3851 | 0.0334 |
| With measurements (55) | Logistic | 0.3980 | 0.0395 |
| With measurements (55) | Boosting | 0.3586 | 0.0366 |

All six validation-selected thresholds have zero development-test recall. Adding
measurements has not improved performance; no replacement model is published.
Measurement coverage is uneven: indexed eGFR is available in 17% of training
snapshots and 13% of development-test snapshots; BMI and blood pressure cover
roughly 81% of development-test snapshots. Missingness remains explicit. These
results motivate further target/cohort/validation work, not test-driven tuning.

Selected LOINC Observations persist BMI (39156-5), HbA1c (4548-4), indexed eGFR
(33914-3), creatinine (2160-0/38483-4), and systolic/diastolic pressure
(8480-6/8462-4, standalone or in panel 85354-9). Quantity codes must use UCUM:
kg/m2, %, mL/min/{1.73_m2}, mg/dL, and mm[Hg], respectively. Creatinine in umol/L
is divided by 88.4; original units remain in provenance. Unknown units, censored
quantities, ambiguous supported codes, invalid dates, and duplicate pressure
components are quarantined. Nonstandard JSON NaN/Infinity fails the file before
any writes. Explicit absent results persist without a measurement value.

A source inconsistency uses eGFR's indexed LOINC code with plain mL/min. These
quantities remain separately labeled `egfr_unindexed_unit`, preserving their unit;
they do not become indexed eGFR or model features. Values are not filtered merely
for being extreme. This subset validates format/units, not clinical plausibility.

The `clinical` feature set adds 30 fields to the earlier 25: latest value,
measurement age in days, missing flag, 90-day-scaled change, and trend-missing flag
for six measurement types. A result needs final status, a full effective timestamp,
and an issued timestamp. Availability is the later timestamp, strictly before the
snapshot; measurement time must also precede the snapshot and be within 365 days.
Missing issued dates and amended/corrected records cannot supply historical values.
The trend uses the latest eligible value and the most recent earlier measurement
at least 30 days before it, both within the lookback. Timestamp ties are resolved
by issuance and resource ID. No measurements from subsequent visits enter features.
Creatinine blood/serum codes are combined only for this synthetic experiment.

Missing numeric feature values remain null until model preprocessing. Logistic regression and
boosting each fit a median imputer on training rows only; entirely missing training
columns use zero, while explicit missingness flags remain available. Standardization
for logistic regression is also training-only. The comparison reports coverage by
measurement and split. These measurements are predictors, not a validated illness
severity score. Observation values and patient-level features are never public.

Socioeconomic history is unavailable. Patient-level lifetime DALY/QALY extensions
remain excluded because they are not dated historical measurements. Present-day
addresses and contextual codes must not be reinterpreted as historical income.

TabPFN is opt-in, local, and isolated from the application environment. The v2
experiment uses `tabpfn==2.2.1`, two estimators, CPU inference, and 1,000 training
rows chosen by a fixed patient/date hash without consulting labels. Its training
budget differs from the full-data models. Telemetry is disabled. Model-weight
license/attribution requirements are separate from the package license. Hugging
Face checkpoint access is blocked in this workspace (HTTP 403); no TabPFN scores
are available. An unavailable checkpoint is recorded as unavailable, never as a
zero score. Only trusted official checkpoints may be loaded; checkpoint files can
execute code during deserialization.

```bash
.venv/bin/python -m careflow.risk_experiment --confirm-synthetic \
  --raw-schema synthea_p1000_seed43 --observation-end 2025-01-01 \
  --output data/risk/model-comparison/clinical.json
```

Use a separate virtual environment for TabPFN: its scikit-learn requirements
conflict with the application's pinned version. With that environment's Python,
set `PYTHONPATH=src`, add `--tabpfn`, and optionally pass
`--tabpfn-checkpoint /path/to/trusted-official-v2.ckpt`. Set
`TABPFN_MODEL_CACHE_DIR` and `HF_HOME` to writable ignored directories for automatic
checkpoint downloads. The local aggregate JSON records package versions and,
when explicitly provided, the checkpoint SHA-256. Outputs stay under ignored
`data/risk`; this command neither changes dashboard exports nor makes paid calls.

## Inpatient-target and threshold investigation

`admission_diagnostics_v1` audits exact encounter type/reason codes after merging
IMP spells. These codes describe outcomes; they never become prediction features.
Among 567 recorded adult episodes in 2015–2024, 137 involve skilled nursing and 81
rehabilitation/detox contexts. Only 16 have an explicit non-urgent orthopedic type;
surgical/ICU codes do not establish admission urgency. Context groups overlap.
The later development set has 26 positive snapshots: 12 involve skilled nursing
and six rehabilitation/detox. This is a mixed-setting inpatient target, not a
validated unplanned acute-hospital admission target.

Earlier rolling score years are fixed at 2019–2022. Each fit uses training-patient
snapshots from 2015 through three years before scoring, the next two years from
calibration patients, and the score year from distinct scoring patients. The same
nine original features, fixed logistic/boosting specifications, and prevalence
baseline are used. No scoring outcomes select model settings or thresholds.
Patients recur across windows, so these are correlated development comparisons.

Policies compare calibration-selected F1, a calibration-derived 5% threshold, and
at most 5% alerts per quarterly screening cohort. Ties use a fixed patient hash;
floor rounding can reduce the alert fraction. A frozen threshold can exceed its
calibration alert budget later. Patient-level bootstrap resampling of calibration
patients (200 draws, seed 0) describes threshold sensitivity, not a confidence
interval for performance. Low event counts are reported, not hidden.

In the original later development window, boosting's per-screening 5% budget
captures 4/26 positive snapshots with 36 false alerts; logistic captures 2/26 with
38 false alerts. Earlier boosting recalls at this budget range from 0% to 16.7%.
Logistic's calibration-bootstrap F1 threshold spans 2.7% to 32.4% at the 10th–90th
percentiles. Zero F1-threshold recall partly reflects threshold instability;
these results do not establish useful clinical prediction.

```bash
.venv/bin/python -m careflow.admission_diagnostics --confirm-synthetic \
  --raw-schema synthea_p1000_seed43 --observation-end 2025-01-01 \
  --output data/risk/admission-diagnostics/summary.json
```

Synthea is rule-based and stochastic. Its pinned 3.3.0
[appendicitis module](https://github.com/synthetichealth/synthea/blob/v3.3.0/src/main/resources/modules/appendicitis.json)
uses sex/age distributions and random branches; its lung-cancer and COVID modules
combine clinical attributes with simulated state. Observed histories may omit
state that drives event timing. This limits what the current features can learn;
it does not prove synthetic data are wholly random or universally unpredictable.

## Separate public-data forecasting candidate

[CDC FluSight target data](https://github.com/cdcepi/FluSight-forecast-hub/tree/main/target-data)
contain observed weekly laboratory-confirmed influenza hospital admissions sourced
from NHSN. The inspected file spans February 5, 2022–October 17, 2026, with 13,038
location-week rows across 53 locations. Its national series has 246 consecutive
weeks, no missing values, and no duplicate location-week rows. There are 36
missing state-level values; they must not be converted to zero.

This is a candidate for a separate real-data forecasting track, not an imported
Careflow cohort or an implemented website forecast. It measures influenza
admissions at national/state level, not individual-facility or all-cause demand.
Reporting requirements, revisions, and coverage changes need explicit handling.
A latest-revision backtest would be a retrospective benchmark, not evidence of
real-time performance. The repository's MIT software license alone does not settle
the underlying target-data reuse terms; dataset-specific terms and attribution
remain to be verified before publication. The inspected revision, input hash,
and quality profile stay in ignored `data/risk/forecast-data-review/`.
