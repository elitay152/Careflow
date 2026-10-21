# Careflow in three minutes

Careflow is Eli Taylor's healthcare data engineering portfolio: a synthetic FHIR
pipeline, a tested PostgreSQL/dbt warehouse, and a public aggregate dashboard.
A separate observed influenza dataset supports the forecasting experiment.
Agentic development helped build the project; the public assistant replays bounded,
cited baseline answers and does not call a model.

## Walk through the website

Open the [overview](https://elitay152.github.io/careflow-dashboard/#overview).
The four tour cards follow this route:

| Stop | What to show | Engineering point |
| --- | --- | --- |
| [Source](https://elitay152.github.io/careflow-dashboard/#cohort) | Synthea explanation, supported resource inventory, and provider reference data. | A defined FHIR subset with explicit accepted, ignored, and quarantined records. |
| [Pipeline evidence](https://elitay152.github.io/careflow-dashboard/#health) | Load/build/export timestamps and the matching dbt evidence. | Transactional loads, source provenance, safe reruns, and tested transformations. The public site is an exported snapshot, not a live monitor. |
| [Analytics](https://elitay152.github.io/careflow-dashboard/#analytics) | Encounter trends, duration definitions, and patient segmentation filters. | SQL marts become interpretable metrics with clear denominators and aggregate exports. |
| [Forecasting](https://elitay152.github.io/careflow-dashboard/#forecasting) | Observed flu admissions, persistence comparison, peak errors, and band coverage. | The selected model loses to persistence on holdout; future projections are withheld. Nominal interval levels are not achieved coverage guarantees. |

The portfolio cohort has 1,194 fictional patients. Clinical histories are simulated;
provider names are reference data. Flu admissions are public national aggregates,
kept separate from that cohort. These experiments are not clinical validation.

## Run the website without a database

In `careflow-dashboard`, with Node 24 installed:

```bash
npm ci
npm run build
npm run preview
```

Open the loopback URL printed by Vite. This uses the reviewed committed snapshots;
it does not ingest records, refresh PostgreSQL, or call a model.

## Reproduce the pipeline on a small fixture

In Careflow, with the Python dependencies installed and local PostgreSQL running:

```bash
bash scripts/portfolio_demo.sh
```

The command reads `DATABASE_URL` from the local `.env` or environment. For a first
local setup, run `bash scripts/bootstrap.sh` first; it installs dependencies,
creates local credentials, and starts Docker PostgreSQL. Alternatively use
`bash scripts/install.sh` with an existing local PostgreSQL database and export
`DATABASE_URL` privately. The demo needs schema-creation permission. Do not run
it alongside other dbt builds in the same checkout.

Each demo run creates fresh `portfolio_demo_<id>` and matching analytics schemas,
then performs this sequence:

1. Generate a hand-authored FHIR Bundle with three fictional patients.
2. Accept 12 supported records and quarantine one intentionally invalid birth date.
3. Replay the unchanged file and verify that it is skipped without changing counts.
4. Build 13 dbt models and pass all 49 quality checks.
5. Export aggregate JSON with matching build evidence and three baseline assistant examples.

The command fails if these checks disagree. It prints the schemas and output path
under ignored `data/portfolio-demo/<schema>/`; inspect `careflow.json`,
`dbt/run_results.json`, `dbt/manifest.json`, and `dbt-logs/dbt.log` there.
The JSON separates current demo metrics from the normal export’s labeled
reference-validation evidence. Fresh schemas and files remain for inspection;
repeated runs retain separate copies.
It does not replace the portfolio cohort, backend's usual export, website snapshots,
reader grants, or persistent model-usage ledger. No model, S3, or other cloud API
is required. There is no HTTP service to expose for this terminal demo.

The small fixture demonstrates mechanics, not the larger cohort's volumes or
forecast performance. For those, see [Synthea validation](validation.md) and the
[pinned flu benchmark](flusight.md). The website's normal refresh remains a separate,
explicit action described in [aggregate exports](public-dashboard.md).
