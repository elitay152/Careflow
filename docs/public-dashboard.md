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

## Contract, version 1

- `current`: the current demo's aggregate table counts, monthly utilization,
  coded condition prevalence, medication request statuses, source-load watermark
  and optional dbt evidence. No patient identifiers, raw input, source paths,
  database URLs or credentials are selected.
- `archive`: optional numeric fields explicitly selected from the committed
  Synthea validation result. `recorded_on` is its documented historical date,
  not a new run timestamp. The website includes this archive and labels it
  separately from current demo analytics.
- `assistant`: three baseline answers calculated in Careflow using fixed
  templates. The public site replays these examples with their citations; it
  does not implement a new planner, accept arbitrary database queries or connect
  to a model. Replay time is not execution time.
- `forecast`: explicitly `not_evaluated`, with null predictions and held-out
  metrics. The website cannot claim a trained or validated forecaster yet.
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
