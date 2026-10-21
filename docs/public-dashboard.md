# Aggregate website exports

Careflow owns ingestion, warehouse calculations, condition classification, and
forecast evaluation. `careflow-dashboard` renders a static aggregate snapshot.
The browser has no PostgreSQL connection or model credentials.

## Refresh

With both repos side by side and local PostgreSQL running, use the website terminal:

```bash
npm run data:refresh -- --confirm-synthetic
```

Defaults are backend `../Careflow`, raw schema `synthea_p1000_seed43`, analytics
schema `synthea_p1000_seed43_analytics`, and the seed-43 cohort label. Override
`--backend-root`, `--raw-schema`, `--analytics-schema`, and `--label` as needed.
Database configuration comes from the backend's `.env` or `DATABASE_URL`.

The command builds/tests dbt in a fresh ignored artifact directory, exports
`data/public/careflow.json`, validates and atomically syncs its bytes to the
website, then builds the site. A failed build or invalid export stops the
refresh. Review and merge the website snapshot to publish through GitHub Pages.
The local command does not ingest patients or publish automatically.

The backend portion also runs independently:

```bash
bash scripts/refresh_public.sh --confirm-synthetic \
  synthea_p1000_seed43 synthea_p1000_seed43_analytics \
  "Synthea 3.3.0 · seed 43 · 1,000 living-patient target"
```

Refresh commands share a local lock. Other loaders and dbt jobs must remain
stopped during refresh; they do not share that lock. Keep raw files, credentials,
usage ledgers, and database dumps outside the frontend.

## JSON contract

The current contract is version 4:

| Field | Contents |
| --- | --- |
| `current` | Cohort label, table counts, source-load time, monthly/annual utilization, 12-month KPIs, chronic-subset summary, grouped conditions, medication statuses, aggregate patient segments, and dbt evidence. |
| `archive` | Separately labeled numeric validation evidence. |
| `assistant` | Three exported baseline answers with plans and citations; replay only. |
| `forecast` | Evaluation evidence or an explicit insufficient-history reason; future predictions require the publication gate. |
| `exported_at` | Snapshot generation time, separate from source loading and dbt completion. |

See [metric definitions](metrics.md) for denominators, units, condition groups,
and forecast protocol. Lower-level `careflow export-public` accepts schema,
cohort-label, output, and matching dbt-artifact arguments.

The exporter checks dbt invocation, database/schema identity, result counts, and
source watermark. The website checks field allowlists, primitive types, aggregate
consistency, forecast input hash, score arithmetic, split/selection rules, and
publication gating. Schema validation does not authenticate the database or
prove text is synthetic; `--confirm-synthetic` is an explicit source attestation.

Patient segments are mutually exclusive aggregate cells across age band, recorded
administrative gender, selected clinical profile, and encounter-frequency band.
Age uses the last day of the KPI window for all loaded patients, including
those who died; it is not current age or age at death. Visits use the same
12-month window as the KPIs. Clinical profiles use confirmed active, recurrent,
or relapsed SNOMED concepts; prescription counts use current active status.
The export contains no patient identifiers or exact ages. Website segment
filters affect the explorer, while the trend and condition charts retain the
full cohort.
