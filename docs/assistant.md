# Careflow analytics assistant

The assistant interprets a question, retrieves metric definitions, chooses a validated metric/filter plan, and executes parameterized SQL against the dbt warehouse. Answers are rendered directly from query results and include source definitions, caveats, the plan, and query evidence. A small browser interface is served alongside the FastAPI API.

**Two explicit modes:** `baseline` uses a limited template grammar with no model calls; `llm` uses the OpenAI Responses API with a strict structured-output schema. There is no silent fallback between modes. Live model access has not yet been validated; the adapter is covered by mocked HTTP contract tests.

## Local setup

Start with the extended demo and built warehouse from the root README. Then, from the repository root:

```bash
bash scripts/install.sh
set -a
. ./.env
set +a
.venv/bin/python scripts/configure_assistant.py
bash scripts/start_assistant.sh
```

The configuration command creates a local `careflow_reader` role and adds generated reader credentials to ignored `.env`. It preserves the ingestion database credentials. If you rerun it, load `.env` first so it can reuse the reader account without resetting its password. These local database credentials are separate from the model API key.

The startup script serves the browser interface on loopback port 8000. It runs in the foreground; stop it with Ctrl+C. The interface displays **Template mode** until LLM mode is explicitly enabled. The generated dashboard from the previous milestone is a separate static HTML artifact; this interface queries the warehouse on demand.

For a CLI question, reload `.env` after reader configuration:

```bash
set -a
. ./.env
set +a
.venv/bin/careflow ask 'Condition prevalence for diabetes' --mode baseline
```

With the extended demo, this returns 2 of 3 patients (66.7%), the definition, denominator caveats, and SQL evidence.

## Supported questions

| Metric | Baseline example | Supported filters |
| --- | --- | --- |
| Monthly finished encounters | `Monthly encounters in 2025-01` | Explicit month or inclusive `from YYYY-MM to YYYY-MM` range |
| Monthly distinct patients | `Monthly distinct patients in 2025-01` | Same monthly filters |
| Mean encounter duration | `Average encounter duration by month in 2025-01` | Same monthly filters |
| Recorded condition prevalence | `Condition prevalence for diabetes` | Literal condition-name substring |
| Medication requests | `Medication requests by status` | Grouped by status; date and drug-name filters unsupported |

Broader paraphrases require LLM mode. The plan uses inclusive `start_month` and exclusive `end_month`; a natural-language inclusive January–March range becomes January 1–April 1. The API returns at most 50 rows, explicitly marks truncation, and orders monthly results newest first. A missing month is not synthesized as zero. Empty condition results do not establish a zero population count.

Questions about clinical recommendations, individual patients, demographic breakdowns, causal effects, forecasts, historical prevalence, or multiple metrics are outside the current interface. The model is instructed to refuse them; correctness of live semantic refusals still needs model evaluation. Definitions and limitations accompany each answered metric.

## Enable the model planner

The environment draft declares **`CAREFLOW_LLM_API_KEY`**, restricted to `api.openai.com`. Enter the key securely in environment settings, review/save the configuration, and publish the environment when ready. Never put a credential in chat, repository files, or committed example configuration.

After that binding is injected into the running environment:

```bash
export CAREFLOW_ASSISTANT_MODE=llm
export CAREFLOW_LLM_MODEL=gpt-4.1-mini
bash scripts/start_assistant.sh
```

Stop the baseline server first if it is already using port 8000. CLI mode is selected with `--mode llm`. A present key alone does not establish that model/network access works: make a representative question and run the evaluation below after configuring access.

The adapter calls `https://api.openai.com/v1/responses`, sends the question and retrieved metric definitions, requests a strict JSON plan with `store: false`, caps output at 256 tokens, and uses a 30-second HTTP timeout. A persistent usage ledger reserves allowance before each request. The default ceilings are five attempts per UTC day and 25 over the workspace ledger's lifetime, with conservative daily/lifetime token reservations. Only `gpt-4.1-mini` is allowed. See [API cost controls and account setup](api-costs.md) before enabling live mode.

Warehouse rows and database credentials are not sent to the model. Provider failures, refusals, malformed plans, and invalid filter combinations stop before SQL execution. There are no automatic provider retries or refunds of reserved allowance. An API health response verifies warehouse availability; it does not verify model connectivity.

## Query and permission boundaries

- The model emits one of five metric IDs and a small set of validated filters. It cannot supply SQL, table names, column names, schema names, or row limits.
- SQL comes from fixed templates. Filter values are bound parameters; schema selection is local configuration validated as an identifier.
- Queries run inside PostgreSQL read-only transactions with a 3-second statement timeout, 1-second lock timeout, and a 50-row response limit.
- The generated reader role has SELECT on two aggregate marts and only the `status` column of the medication fact. It cannot read raw patient tables or the fact's patient identifiers, or modify warehouse tables.
- dbt swaps rebuilt tables. Successful `build_warehouse` calls refresh the configured reader's grants for the active assistant schema, using local ingestion credentials; existing reader passwords are preserved.

The API binds to loopback and has no user authentication or rate limiting. This version is for local portfolio development with synthetic data. A shared deployment needs authentication, request budgets, and operational configuration before exposure.

The API validates the HTTP Host header to reduce DNS rebinding exposure. It accepts
`localhost`, `127.0.0.1` and `[::1]` by default. `scripts/start_assistant.sh` also
allows this Codespace's exact forwarded hostname when the Codespaces environment
variables are present. For another private tunnel, set `CAREFLOW_ALLOWED_HOSTS`
to a comma-separated list of exact hostnames, without schemes, paths or wildcards.
Keep forwarded ports private; Host validation does not authenticate users. See
the [security audit](security-audit.md) for tested boundaries and remaining risks.

## API

- `GET /` — browser interface.
- `GET /health` — warehouse readiness, selected mode, and explicit model-connectivity status.
- `GET /metrics` — supported definitions and limitations.
- `GET /usage` — mode and local model allowance; no provider call.
- `POST /ask` — JSON body `{"question":"Monthly encounters in 2025-01"}`.

Responses include `status`, `mode`, `model`, `answer`, `rows`, `citations`, `plan`, `query`, `truncated`, timing, and reported model token usage. Unsupported questions return an explicit unsupported status with no SQL. Invalid request bodies return 422, unavailable model plans 502, exhausted local usage allowances 429, and unavailable ledgers or database failures 503; error responses omit credentials and provider payloads. `generated_at` is request time, not a source-freshness watermark.

## Evaluation

Use the **extended demo warehouse**, since expected numeric answers are tied to its three-patient fixture:

```bash
set -a
. ./.env
set +a
.venv/bin/python -m careflow.evaluate --mode baseline --suite core --output data/evaluation/my-baseline-core.json
# Once model access works: start with one model request.
.venv/bin/python -m careflow.evaluate --mode llm --suite all --max-cases 1 --output data/evaluation/my-llm-first.json
```

Output paths must be fresh; the runner refuses existing paths before model calls. A live batch larger than the remaining request allowance is refused before any provider request. Use `--max-cases` and `--offset` for small batches; the full 30-case live batch exceeds the default daily cap. The core suite contains 24 metric, numeric-answer, unsupported-question, and adversarial cases. Six additional paraphrases test broader language understanding. Results distinguish plan correctness, numeric rows, evidence presence, retrieval recall, latency, token usage, errors, and cases left unrun after a usage guard stops the batch. Any failed or unrun case yields a nonzero exit status. Latency summaries include refusals. Evaluation token totals cover usable planner responses; the ledger additionally retains full reservations for failed calls and records reported usage when available. Dollar cost is left unknown rather than inferred from hardcoded prices.

Measured baseline results: **24/24 core cases passed**; across all 30 cases, **24 passed and 6 paraphrases were declined**, consistent with the baseline's bounded grammar. Retrieval recall at three was 100% on this small curated set. These are baseline measurements, not live LLM accuracy claims. [Machine-readable core results](assistant-baseline-results.json) and [full results](assistant-baseline-all-results.json) retain the evidence. Live model accuracy, latency, and cost remain unverified until access is supplied.

## Use the validated Synthea warehouse

Keep demo evaluation separate from larger-data exploration. To target the retained validation warehouse:

```bash
set -a
. ./.env
set +a
export CAREFLOW_ANALYTICS_SCHEMA=synthea_v330_seed42_final_analytics
.venv/bin/python scripts/configure_assistant.py
.venv/bin/careflow ask 'Condition prevalence for diabetes' --mode baseline
```

Use your own schema name if you regenerated the validation. Reader grants apply only to chosen aggregate relations. Set `CAREFLOW_ANALYTICS_SCHEMA=analytics` to return to the demo. Do not run the fixture-specific evaluation against Synthea and interpret different expected counts as model errors.

This path was exercised against the retained 108-patient warehouse. A literal `diabetes` name filter matches multiple coded groups, including prediabetes and complications; the response presents each group separately and does not pool them or assert a single diabetes prevalence. The interface is grounded in recorded terminology labels, not a clinical terminology expansion service.
