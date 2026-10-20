# Enable the API with small, controlled usage

Careflow stays in **baseline mode by default**. Baseline makes no model requests and requires no model API billing. Live mode must be selected explicitly. No paid API calls were made while building or testing these controls.

## Controls already built

| Control | Default / enforced ceiling |
| --- | --- |
| Allowed model | `gpt-4.1-mini` only |
| Model attempts per UTC day | 5 |
| Model attempts over this workspace ledger's lifetime | 25 |
| Reserved token budget per UTC day | 50,000 |
| Reserved token budget over the ledger's lifetime | 250,000 |
| Question size | 1,000 characters |
| Serialized model payload size | 10,000 UTF-8 bytes |
| Maximum output per request | 256 tokens |
| Automatic provider retries / redirects | None |

All API, CLI, and evaluation model calls go through the same reservation code. Before sending a request, a SQLite transaction reserves the **full serialized payload byte count plus 1,024 input tokens of framing allowance**, and the maximum output tokens. This intentionally overestimates normal text input. The local token budget uses those conservative reservations, not a claim of exact provider tokenization or a dollar ceiling. Reported usage is stored separately. If reported usage exceeds a reservation, further model requests halt persistently.

Failures, timeouts, invalid plans, and interrupted calls retain their full allowance. There are no automatic refunds because a lost response does not establish that the provider did not bill it. Concurrent processes serialize reservations, and the ledger survives application restarts. Daily allowances reset at midnight UTC; lifetime allowances do not reset automatically. An unavailable or corrupt ledger blocks live requests. Baseline remains usable independently.

The ledger lives at **`data/llm-usage.sqlite3`**, at a fixed path relative to this checkout, regardless of the command's working directory. Keep it and its SQLite WAL files. Do not delete, restore an older copy, or clone it to reset usage. It stores attempt IDs, model, times, status, reservations, and reported usage—not questions, answers, or credentials.

These guards cover this workspace's application calls. Other applications using the key, other environments, and separate/restored workspace copies have independent state. Pair local controls with a small provider balance. API prices and billing enforcement are controlled by the provider; these application limits are not an account-wide hard dollar cap.

## Account setup

1. Open the [OpenAI API platform](https://platform.openai.com/) and create a separate project for Careflow. API billing is separate from a ChatGPT subscription.
2. In API billing, choose the smallest prepaid credit purchase offered for your account. **Leave automatic recharge off.** Review the current price, minimum purchase, and credit terms shown there before paying.
3. Set a low project budget/usage alert. Treat it as an alert unless the dashboard explicitly documents enforced blocking; don't rely on an alert as a spending cap or assume balance exhaustion stops every in-flight request instantly.
4. Create a project-scoped API key. If endpoint restrictions are available, allow the Responses API needed by this application.
5. Enter the key securely in the cloud environment's **`CAREFLOW_LLM_API_KEY`** requirement. The draft restricts its destination to `api.openai.com`. Review/save the draft and publish the environment; then verify the binding is available in the running session. Never paste a key into chat or commit it.

Current billing UI and prices could not be verified from this environment because the help site was blocked. Check the provider's [prepaid billing guidance](https://help.openai.com/en/articles/8264778-what-is-prepaid-billing) and the billing screen itself. The app deliberately does not embed a dollar-price estimate that could become stale.

## Start with one live question

With PostgreSQL, the built demo warehouse, and reader credentials configured as described in [assistant.md](assistant.md):

```bash
set -a
. ./.env
set +a
.venv/bin/careflow usage
# One explicit model attempt, after secure API credentials have been supplied:
.venv/bin/careflow ask 'Monthly encounters in 2025-01' --mode llm
.venv/bin/careflow usage
```

The expected demo result is 3 encounters for January 2025, with a metric citation. Check both the local ledger and provider usage dashboard after the call. A provider error still consumes one local attempt; investigate it before trying again. We have not yet validated a live call because no API key is configured.

Only then, if desired, start the browser in live mode:

```bash
CAREFLOW_ASSISTANT_MODE=llm bash scripts/start_assistant.sh
```

Stop an existing baseline server first if port 8000 is in use. Leave `CAREFLOW_ASSISTANT_MODE` unset for free baseline operation. The interface shows remaining attempts; `GET /usage` and `careflow usage` expose the ledger without making a provider call. Exhausted allowances return HTTP 429; an unavailable ledger returns 503, with no provider request sent in either preflight failure.

## Lower limits further

These optional environment variables can **lower**, but cannot raise, the fixed ceilings:

```bash
export CAREFLOW_LLM_DAILY_REQUEST_LIMIT=1
export CAREFLOW_LLM_LIFETIME_REQUEST_LIMIT=5
export CAREFLOW_LLM_DAILY_TOKEN_LIMIT=12000
export CAREFLOW_LLM_LIFETIME_TOKEN_LIMIT=60000
```

Restart the app after changing them. Existing ledger usage still counts. Raising a value above its built-in ceiling is rejected. Increasing ceilings or changing the model allowlist requires an intentional source-code change; there is no browser reset button.

## Small evaluation batches

The old 30-call batch exceeds the default daily request cap and is now refused before any requests. Start with:

```bash
.venv/bin/python -m careflow.evaluate --mode llm --suite all --max-cases 1 --output data/evaluation/live-first.json
```

Use `--offset 1` with a new output path for the next case, while allowance remains. If a token guard stops a batch, remaining cases are marked unrun and the command fails; it does not keep making requests. Do not weaken the limits just to get a complete evaluation in one run. Baseline evaluations remain free and can still run the full suite.
