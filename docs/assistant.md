# Analytics assistant

The local FastAPI/browser assistant retrieves metric definitions, validates a
metric/filter plan, and executes fixed parameterized SQL with a restricted reader.
Answers include values, definitions, caveats, plan, and query evidence.

`baseline` is the default bounded grammar and makes no model calls. Optional
`llm` mode uses structured OpenAI output to select a plan; neither mode accepts
model-generated SQL. Mocked adapter tests are not live-model accuracy evidence.

## Run

```bash
bash scripts/bootstrap.sh
bash scripts/start_assistant.sh
```

The browser runs on loopback port 8000. For a CLI question, load local configuration:

```bash
set -a
. ./.env
set +a
.venv/bin/careflow ask 'Condition prevalence for diabetes' --mode baseline
```

To select a larger warehouse, set `CAREFLOW_ANALYTICS_SCHEMA` and run
`.venv/bin/python scripts/configure_assistant.py` with `.env` loaded. Reload `.env`
after configuration to use the reader credentials. The seed-43 portfolio schema
is `synthea_p1000_seed43_analytics`; the bootstrap demo uses `analytics`.

## Supported metrics

| Metric | Baseline example | Filters |
| --- | --- | --- |
| Finished encounters | `Monthly encounters in 2024-12` | Month or inclusive `from YYYY-MM to YYYY-MM` range |
| Distinct patients | `Monthly distinct patients in 2024-12` | Same monthly filters |
| Mean duration | `Average encounter duration by month in 2024-12` | Same monthly filters; API values use minutes |
| Recorded conditions | `Condition prevalence for diabetes` | Literal name substring |
| Medication requests | `Medication requests by status` | Status grouping; no date/drug-name filter |

Plans use an inclusive start and exclusive end date. Monthly results are newest
first, capped at 50 rows with explicit truncation. Missing months are absent,
not zero. Condition queries include clinical and contextual records; the website's
curated views do not change this metric. [Definitions](metrics.md) explain scope.

Clinical recommendations, patient-level queries, causal inference, multiple
metrics, historical prevalence, and forecasting are outside this interface.
Live semantic behavior requires explicit model evaluation.

## Optional live mode

Supply `CAREFLOW_LLM_API_KEY` through local terminal injection or GitHub secrets;
follow [cost controls](api-costs.md). Then select live mode explicitly:

```bash
CAREFLOW_ASSISTANT_MODE=llm bash scripts/start_assistant.sh
# Or one CLI attempt:
.venv/bin/careflow ask 'Monthly encounters in 2024-12' --mode llm
```

Only `gpt-4.1-mini` is allowed. The Responses API receives the question and metric
definitions, not warehouse rows or credentials. Requests use structured output,
`store: false`, a 30-second timeout, 256 output tokens, and no automatic retries.
Failures stop before SQL and retain the ledger reservation. There is no silent
fallback between planner modes.

## API and access

| Route | Purpose |
| --- | --- |
| `GET /` | Browser interface |
| `GET /health` | Warehouse readiness and selected mode; not a model connectivity test |
| `GET /metrics` | Definitions and limitations |
| `GET /usage` | Local allowance without a provider request |
| `POST /ask` | JSON body such as `{"question":"Monthly encounters in 2024-12"}` |

Invalid bodies return 422, unavailable model plans 502, exhausted allowance 429,
and unavailable ledgers/database failures 503. Errors omit credentials/provider
payloads. Response generation time is not a source freshness watermark.

The reader can select aggregate marts and the medication fact's status column.
Queries run read-only with statement/lock timeouts. Exact Host validation accepts
loopback and configured private tunnel hosts through `CAREFLOW_ALLOWED_HOSTS`.
Host validation is not authentication; keep this unauthenticated development
service private. See [security boundaries](security-audit.md).

## Evaluation

Expected numeric answers use the three-patient extended demo, not Synthea:

```bash
.venv/bin/python -m careflow.evaluate --mode baseline --suite core --output data/evaluation/core.json
# Explicitly billable; one case with a fresh output path:
.venv/bin/python -m careflow.evaluate --mode llm --suite all --max-cases 1 --output data/evaluation/live.json
```

The core suite has 24 cases; the full suite adds six broader paraphrases outside
the baseline grammar. Output paths must be fresh. Live batches exceeding the
remaining allowance are refused before provider access. `--offset` selects a
later case. Failed or unrun cases produce a nonzero exit code. Run reports stay
local under ignored `data/`.
