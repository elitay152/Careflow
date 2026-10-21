# Aggregate exports for the separate website

The lightweight frontend lives in `elitay152/careflow-dashboard`. Careflow owns
ingestion, transformations, metric definitions, assistant responses, forecasting
and pipeline evidence. The website renders a small static JSON contract; it has
no database or model credentials and makes no backend or model requests.

## Refresh the snapshot

Use a synthetic-only database and stop concurrent loads/builds during export.
The standard demo can be refreshed with `bash scripts/bootstrap.sh`. Then:

```bash
cd /path/to/Careflow
set -a
. ./.env
set +a
.venv/bin/careflow export-public --confirm-synthetic \
  --dbt-artifacts warehouse/target \
  --archive docs/validation-results.json --archive-date 2026-10-20 \
  --output data/public/careflow.json

cd ../careflow-dashboard
npm run data:sync
npm run build
```

`--confirm-synthetic` is an explicit source-data attestation, not an automatic
de-identification mechanism. Never aim this exporter at real patient data.
Custom database schemas can be selected with `--raw-schema` and
`--analytics-schema`. Names must be simple identifiers. An existing exported
file is replaced atomically only after all queries and validation succeed.

The exporter checks dbt invocation IDs, database/schema identity, result counts
and the latest recorded ingestion watermark before attaching build evidence.
Artifacts from a different schema/database or preceding the latest load are
rejected. Missing optional build evidence is labeled unverified, never passed.
This does not independently re-run tests or prove the database was not manually
modified after a build; rebuild and export in a controlled, quiet workflow.

## Trigger a refresh from PostgreSQL

From the separate website checkout, run `npm run data:refresh -- --confirm-synthetic`.
It invokes this backend's refresh script, syncs the validated aggregate snapshot,
and builds the site. The default source is the separately loaded seed-43 cohort.
The website README documents schema/label overrides and publishing through the
existing Pages workflow. Both checkouts must contain the refresh implementation.

The backend portion can also run independently:

```bash
bash scripts/refresh_public.sh --confirm-synthetic \
  synthea_p1000_seed43 synthea_p1000_seed43_analytics \
  "Synthea 3.3.0 · seed 43 · 1,000 living-patient target"
```

This uses `.env`/`DATABASE_URL`, builds/tests dbt into a fresh ignored artifact
directory, then exports `data/public/careflow.json`. Concurrent refresh scripts
in this checkout are rejected with a local lock. Keep other loaders and dbt jobs
stopped during the refresh: the lock does not cover independent pipeline commands.
A failed build prevents export; the frontend validates bytes before replacing
its snapshot. The command does not ingest, publish, schedule jobs, use AWS, or
call a model. Build artifacts remain local under ignored `data/public/`.

The lower-level `export-public` command accepts `--cohort-label` so current data
is described accurately. Its default remains the hand-authored demo. Use matching
raw/analytics schemas and dbt artifacts when exporting another cohort. The older
108-patient archive remains explicitly separate historical evidence.

## Contract, version 3

- `current`: the selected cohort’s aggregate table counts, monthly utilization,
  coded condition prevalence, medication request statuses, source-load watermark
  and optional dbt evidence. No patient identifiers, raw input, source paths,
  database URLs or credentials are selected.
- `archive`: optional numeric fields explicitly selected from the committed
  Synthea validation result. `recorded_on` is its documented historical date,
  not a new run timestamp. The website includes this archive and labels it
  separately from current cohort analytics.
- `assistant`: three baseline answers calculated in Careflow using fixed
  templates. The public site replays these examples with their citations; it
  does not implement a new planner, accept arbitrary database queries or connect
  to a model. Replay time is not execution time.
- `forecast`: evaluated statistical comparisons when 120 consecutive observed months exist; otherwise an explicit reason and null evidence. Future predictions are gated on improvement over seasonal naive on held-out MAE.
- `exported_at`: actual JSON generation time. `source_loaded_at` and dbt
  `completed_at` remain separate. Refreshing the browser changes none of them.

Monthly data excludes absent months instead of inventing zero-valued rows.
Current prevalence uses all loaded patients, including deceased patients, as
the denominator; coded groups may overlap. Requests do not imply dispensing or
adherence. These are the same definitions used by the backend assistant.

The frontend commits only this reviewed aggregate snapshot, so it can build and
run independently of PostgreSQL, Codex settings or a backend connection. Its
refresh script copies and checks the contract; it does not transform clinical
data or attest that a source is synthetic. Review the generated JSON before
committing or publishing. Do not copy `.env`, usage ledgers or raw datasets.

## Condition groups

FHIR Condition is a resource type, not a guarantee of a chronic diagnosis.
Synthea also uses it for social context, history, and care situations. All source
records remain in PostgreSQL and the existing prevalence mart. Public snapshot
version 2 added a `group` to each `current.conditions` row; baseline assistant
responses retain the full mart's original semantics and explicitly document its
scope. Deploy the updated backend before using the website's version-3 refresh.

`src/careflow/condition_groups.json` is an explicit, project-specific mapping of
the 195 observed SNOMED CT codes in the seed-43 cohort. It is matched by exact
terminology system plus code, never a label substring at runtime. The labels
alongside codes make the mapping reviewable; this is not a SNOMED hierarchy,
clinical decision tool, or definition of chronic disease.

- `clinical`: clinical conditions and findings, including symptoms, risk findings,
  and acute conditions; not exclusively chronic disease.
- `social_context`: education, employment, housing, transport, and related social context.
- `history`: explicitly historical events/procedures.
- `care_context`: care reminders, awaiting transplant, suspected diagnosis, and hospice status.
- `unclassified`: codes absent from the mapping or from another terminology.

Unknown codes never silently become clinical. New cohorts may need a deliberate
mapping update. Nothing is discarded or subtracted from the denominator when
changing dashboard views. Each row still counts distinct affected patients among
all loaded patients, including deceased patients; codes can overlap. A context
bar counts recorded concepts, not adverse events or a validated social-risk score.

## Version 3 metrics and forecast

Version 3 adds `annual_utilization`, `utilization_summary`, and `chronic_summary`.
Monthly and annual series include encounter-level median durations and measured
counts alongside the mean. Annual patients are counted distinctly within each UTC
calendar year. The summary window is the latest observed month plus 11 preceding
calendar months, with an exclusive end date. It is independent of frontend chart
filters. Encounter counts include finished encounters only; duration statistics
exclude missing durations. A mean of monthly medians is never used.

The selected chronic subset is an explicit 19-code list in `utilization.py`:
type 2 diabetes, essential hypertension, ischemic heart disease, hyperlipidemia,
hypertriglyceridemia, chronic kidney disease stages 1–4 and end-stage renal disease,
chronic congestive heart failure, asthma and childhood asthma,
chronic obstructive bronchitis and emphysema, rheumatoid arthritis, Alzheimer's
disease and familial early-onset Alzheimer's disease, and epilepsy. This deliberately
limited project definition is not exhaustive or clinically validated. Generic heart-failure and risk-finding codes are not included merely because
they could be associated with chronic disease; this is a presentation cohort,
not a clinical inference.
`chronic_subset` flags export rows; the summary counts distinct patients with at
least one mapped active/recurrent/relapsed, confirmed record. The denominator
remains all loaded patients, including deceased patients. Codes and patients can
overlap across individual bars; the union is queried directly.

`forecast.py` implements dependency-free statistical models, with protocol
`rolling_origin_v1`. It uses the latest 120 consecutive observed months, refuses
to fill absent months with zeros, selects by MAE on three pre-holdout 12-month
validation folds, and reports all models on the final 12 months. Each candidate
uses a trailing 60-month fit window; seasonal naive needs only the prior year.
The candidate list, window, folds, tie order, and publication rule are fixed in
code before test scoring. Seasonal trend is least-squares linear trend plus
month fixed effects; predictions are clamped at zero.

The aggregate snapshot includes input SHA-256, evaluation time, exact split/fold
dates, validation metrics, held-out predictions/actuals, MAE, RMSE and WAPE. Future
values are refit on all available data only if the validation-selected model beats
seasonal naive on test MAE. Otherwise they remain null. These results are a
synthetic-data experiment, not a live healthcare demand forecast. Changing
historical coverage, incomplete source periods, deceased patients, and simulator
assumptions limit interpretation. No uncertainty intervals are estimated. No
LLM request, paid API, retry, or usage-ledger mutation is involved.

The website validator checks exact field allowlists, split/fold ordering, the
aggregate input hash, score arithmetic, validation-based selection, and the
publication gate. This checks artifact consistency, not independent authenticity
of the database or suitability for clinical decisions. Matching passed dbt
evidence remains separate. Use the updated backend before refreshing the v3 site.
