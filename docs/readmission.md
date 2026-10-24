# UCI discharge-time readmission benchmark

UCI dataset 296, **Diabetes 130-US Hospitals for Years 1999–2008**, is a separate
observed-data study. It does not augment the synthetic FHIR cohort or establish
clinical usefulness. The target is the dataset's recorded `<30` readmission label;
`>30` and `NO` are negative. Prediction occurs at discharge, with the index stay's
recorded diagnoses, testing, and treatment available.

## Fetch and reproduce

```bash
bash scripts/install.sh
.venv/bin/python -m careflow.uci_readmission --download
```

The adapter follows the same metadata-then-CSV flow as UCI's `ucimlrepo` library,
using Python's standard library so no pandas/library dependency is needed. It calls
`https://archive.ics.uci.edu/api/dataset?id=296`, verifies dataset identity and row
count, checks the official dataset page for the expected CC BY 4.0 license link,
and accepts only the fixed HTTPS dataset CSV URL. Redirects, oversized responses,
unexpected labels, duplicate encounter IDs, invalid counts, and incomplete datasets
stop the run. Standard missing-value tokens are normalized without regard to case
or surrounding whitespace. The laboratory category `None` is retained; invalid
negative or infinite diagnoses remain errors. Requests time out without automatic
retries. There are no paid models.

Raw CSV, API metadata, and license-page evidence remain under ignored
`data/external/uci296/`. Without `--download`, the command reuses that local cache.
The aggregate-only result is `data/public/readmission.json`. Each result records
CSV/metadata/license-page SHA-256 values and the protocol hash. Set
`UCI_READMISSION_SHA256` to a previously reviewed CSV hash to require byte-identical
input; the first download has no configured byte pin and is labeled accordingly.
A failed download or benchmark does not overwrite the previous export.

## Fixed evaluation

[config/uci-readmission-protocol.json](../config/uci-readmission-protocol.json)
was committed before running the real-data benchmark. It specifies:

- Exclude recorded death/hospice disposition codes 11, 13, 14, 19, 20, and 21.
  Retain all ages and other dispositions; count exclusions explicitly.
- Assign all encounters for a patient to one split using a stable patient-ID hash:
  approximately 70% training, 15% validation, and 15% test patients.
- Fit numeric median imputation, scaling, and categorical encoding on training only.
  Diagnosis codes become coarse ICD-9 groups. IDs and target labels are never
  predictors; race, payer, weight, and medical specialty are excluded.
- Compare training-prevalence and smoothed prior-inpatient-visit baselines with
  fixed logistic regression and histogram gradient boosting. No hyperparameter
  search or outcome-based resplitting occurs.
- Select highest validation average precision with fixed model-order tie breaks,
  then score test outcomes without refitting or selecting on test.

The export reports average precision, AUROC, Brier score, fixed probability-bin
calibration, and review budgets for the highest-risk 10% and 20% of encounters.
Budgets use `ceil(n*capacity)`; ties use an outcome-independent encounter-ID hash.
Precision/recall at a budget describe retrospective ranking, not a validated clinical
threshold or an intervention's effectiveness. No individual probabilities, patient
IDs, encounter rows, or serialized model files are exported.

## Deployment

The dashboard's main-branch deployment checks out a reviewed backend commit, fetches
UCI, runs this benchmark on CPU, validates and syncs only the aggregate JSON, then
runs the normal security/build/browser checks before deploying. PR checks stay
offline and exercise the committed `not_run` state and in-memory test fixtures.
Every main deployment retrieves the dataset again; the SHA-256 repository variable
can pin the data after its first successful run. There is no scheduled refresh.

If UCI access, license evidence, parsing, model convergence, or checks fail, the new
website deployment stops and the previous live Pages site is retained. A manual
run of the existing website workflow can retry after the upstream issue is resolved;
there are no automatic retries or fabricated fallback scores. Generated results
are published with that build, not committed into either source repository. Their
fingerprint is available in the page and public `data/readmission.json` download.

## Evidence and limitations

The authoring environment receives HTTP 403 from UCI. **Real-data results have not
been verified here.** Automated tests use clearly synthetic, private fixtures;
those scores are never copied into public data. The first Actions deployment must
succeed before any UCI model-performance claim can be made.

The cohort is older and diabetes-specific. `NO` does not independently establish
complete follow-up or capture readmissions elsewhere. The source lacks calendar
admission dates and hospital identifiers for chronological or held-out-hospital
validation. Multiple encounters per patient create correlated encounter-level
metrics; no confidence intervals or subgroup fairness claims are made. This is not
a validated severity or socioeconomic assessment, a clinical scoring service, or
a claim that predicted readmissions are preventable.

Source attribution is included in the export and website: UCI ML Repository,
Diabetes 130-US Hospitals for Years 1999–2008; Strack et al. (2014). The runtime
source-page check records the CC BY 4.0 reuse basis and the transformations applied.
