# Run Careflow directly from GitHub

The project runs independently of Codex environment settings or publication. Ordinary Git checkout, Python, PostgreSQL, and optional provider credentials are sufficient. Codex secret-save failures do not prevent baseline development or use of GitHub's own secret storage.

## Run locally

Requires Python 3.11+, Docker, and Docker Compose:

```bash
git clone https://github.com/elitay152/careflow.git
cd careflow
bash scripts/bootstrap.sh
bash scripts/start_assistant.sh
```

The bootstrap installs dependencies, creates local database credentials if absent, starts PostgreSQL, loads the extended fictional demo, builds/tests the warehouse, and configures the reader. It preserves existing `.env` and input files. It makes no model API calls. Use the assistant on local port 8000. If you have an existing PostgreSQL database instead, export `DATABASE_URL` and use `bash scripts/bootstrap.sh --existing-database`.

## Develop in GitHub Codespaces

1. On the repository, choose **Code → Codespaces → Create codespace** on the branch containing this project.
2. The committed devcontainer starts a PostgreSQL sidecar and bootstraps the demo through its post-create command.
3. In the Codespaces terminal, run `bash scripts/start_assistant.sh`.
4. In the **Ports** panel, open port 8000 in the browser. Keep the forwarded port private.

This starts baseline mode without an API key. Codespaces compute has its own GitHub allowance/billing; stop the Codespace when finished. The devcontainer's fixed PostgreSQL password is a local demo credential, not an API credential, and its database port is not published publicly.

For live mode in Codespaces, save `CAREFLOW_LLM_API_KEY` in **GitHub's Codespaces secrets**, granting access to this repository, then restart the Codespace as required for injection. Codespaces secrets and Actions secrets are separate stores. Follow [the cost-control guide](api-costs.md) and test one explicit live CLI question first. The persistent workspace ledger limits that Codespace's usage; separate Codespaces have independent ledgers.

## One live test through GitHub Actions

This bypasses the Codex secret editor and does not require a local machine:

1. In the GitHub repository, open **Settings → Secrets and variables → Actions → New repository secret**.
2. Name it **`CAREFLOW_LLM_API_KEY`** and paste the actual OpenAI secret key into GitHub's secure value field. Save it there; no Codex publish step is needed.
3. The workflow must be present on the default branch for its manual Run workflow button to appear. Review and merge the project branch first if necessary.
4. Open **Actions → Live assistant smoke test (one API request) → Run workflow**.
5. Download **live-assistant-results** after completion to inspect the result and recorded usage.

Only the manual live-test workflow receives the key. It builds the demo, runs **one potentially billable model request**, and makes no automatic provider retries. Pushes and pull requests run baseline tests without an API key. A missing key fails before model access. An invalid key fails the single attempt; investigate it before manually running again.

The live workflow has no schedule and does not trigger on pushes. Each GitHub-hosted runner has a fresh local ledger: the workspace's five-per-day/lifetime caps **do not aggregate across separate workflow runs**. Each manual run allows one model attempt; repeating the workflow repeats that potential charge. Keep auto-recharge off, use a small provider balance, and compare provider usage when testing. GitHub Actions usage is subject to your account's own allowance/billing.

## Secret hygiene

Never put an API key in a commit, issue, chat, workflow source, or command argument. The workflows reference GitHub's masked secret variable. `.env`, generated datasets, virtual environments, and usage ledgers are ignored. The model receives only the question and metric definitions; no database credentials are sent.

The live workflow and Codespaces configuration are provided for GitHub execution. Their hosted behavior must be verified on GitHub; local checks alone do not establish a successful hosted run.
