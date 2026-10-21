# Model credentials and spending controls

Baseline is the free default. Live mode must be selected explicitly and requires
`CAREFLOW_LLM_API_KEY`. Statistical forecast evaluation makes no model API calls.

## Enforced limits

| Control | Ceiling |
| --- | --- |
| Allowed model | `gpt-4.1-mini` |
| Attempts per UTC day | 5 |
| Lifetime attempts per workspace ledger | 25 |
| Reserved tokens per UTC day / lifetime | 50,000 / 250,000 |
| Question / serialized payload | 1,000 characters / 10,000 UTF-8 bytes |
| Output per request | 256 tokens |
| Automatic retries / redirects | None |

Before each call, a SQLite transaction reserves the serialized payload byte count
plus 1,024 input tokens of framing allowance and the maximum output tokens. This
is conservative accounting, not exact tokenization or a dollar cap. Reported
usage is separate; an overrun persistently blocks further calls.

Failed, interrupted, and invalid responses keep their reservations. Corrupt or
unavailable ledgers block live requests. Concurrent reservations serialize.
Daily allowance resets at midnight UTC; lifetime allowance does not reset.

Preserve **`data/llm-usage.sqlite3` and its WAL files**. Do not delete or restore
an older ledger to reset limits. It stores attempts and usage, not prompts,
answers, or credentials. Other workspaces, applications, and fresh Actions runners
have independent state; these limits are not an account-wide billing cap.

## Supply a key

Use a separate OpenAI project with a small prepaid balance, automatic recharge
off, and low usage alerts. Review current provider pricing and enforcement;
budget alerts alone are not a guaranteed hard cap.

Store the key as a GitHub Actions/Codespaces secret named `CAREFLOW_LLM_API_KEY`,
or inject it privately in a local Bash terminal:

```bash
read -rsp 'OpenAI API key: ' CAREFLOW_LLM_API_KEY
export CAREFLOW_LLM_API_KEY
```

Do not put credentials in commits, frontend files, chat, or command arguments.
Actions and Codespaces secrets are separate stores.

## Make one explicit attempt

With the demo warehouse and reader configured, load `.env` and run:

```bash
.venv/bin/careflow usage
.venv/bin/careflow ask 'Monthly encounters in 2025-01' --mode llm
.venv/bin/careflow usage
```

Check provider usage as well as the local ledger. Investigate failures before
manually trying again. The manual GitHub live-smoke workflow makes one potentially
billable attempt per run; separate runners do not share a global daily ledger.

Optional environment settings can lower, but not raise, the ceilings:

```bash
export CAREFLOW_LLM_DAILY_REQUEST_LIMIT=1
export CAREFLOW_LLM_LIFETIME_REQUEST_LIMIT=5
export CAREFLOW_LLM_DAILY_TOKEN_LIMIT=12000
export CAREFLOW_LLM_LIFETIME_TOKEN_LIMIT=60000
```

Restart the app after changes; existing usage still counts. For evaluation, use
`--max-cases 1` and fresh output paths as described in the [assistant guide](assistant.md).
