# GitHub and Codespaces

## Local development

```bash
git clone https://github.com/elitay152/Careflow.git
cd Careflow
bash scripts/bootstrap.sh
bash scripts/start_assistant.sh
```

Requires Python 3.11+, Docker, and Docker Compose. Baseline needs no API key.
For an existing PostgreSQL database, export `DATABASE_URL` and use
`bash scripts/bootstrap.sh --existing-database`.

## Codespaces

Create a Codespace from the repository. The devcontainer starts PostgreSQL and
bootstraps the demo. Run `bash scripts/start_assistant.sh`, then open port 8000
from the Ports panel. Keep forwarded ports private. Stop unused Codespaces to
control GitHub compute usage.

Optional model credentials belong in GitHub Codespaces secrets under
`CAREFLOW_LLM_API_KEY`. Actions secrets are a separate store. Restart the
Codespace when needed to inject changed secrets.

## Workflows

| Workflow | Trigger / purpose |
| --- | --- |
| Pipeline tests (`test`) | Push/PR; SQLite, PostgreSQL, dbt, and pipeline checks. |
| Security checks (`audit`) | Push/PR; dependency and tracked-file secret scans. |
| Synthea validation (`validate`) | Manual generation and source-to-warehouse validation. |
| Live assistant smoke test (`live-smoke`) | Manual; one potentially billable model attempt. |

Protect `main` with required PRs, `test` and `audit` checks, blocked force pushes,
and restricted deletion. Solo maintainers can use zero required approvals.

For the live smoke test, save `CAREFLOW_LLM_API_KEY` in repository Actions secrets
and run **Actions → Live assistant smoke test (one API request)**. It does not run
on pushes or PRs. Each fresh runner has an independent ledger; repeating the
workflow repeats the potential charge rather than sharing a global daily cap.
See [API costs](api-costs.md).
