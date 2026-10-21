# Security audit — October 20, 2026

This review covers the local synthetic-data portfolio workflow. It found and
fixed dependency and PostgreSQL patch gaps, missing API Host validation, and an
incomplete check when reusing a database reader role. It is not a production
certification or a live-model safety evaluation. No paid model calls were made.

## Scope and evidence

Reviewed the Python application, browser rendering, SQL templates and ingestion,
database role provisioning, usage ledger and model adapter, shell scripts, dbt
profiles, Docker/Codespaces configuration, and all GitHub workflows. The backend
baseline was main commit `e5a930b`; the dashboard repository at `9545007` contained
only a README and license, so there was no frontend application to audit.

The tools ran locally with Python 3.12.14. Dependency evidence, including package
versions and advisory IDs, is in [security-dependencies.json](security-dependencies.json).
Severity below reflects this project's exposure, not a universal CVSS score.

## Findings and changes

| Finding | Assessment | Evidence and correction |
| --- | --- | --- |
| Dependencies with published advisories | Mixed upstream severities; fix required | pip-audit 2.10.1 reported 38 advisory records, representing 20 unique package/advisory pairs across Dagster, Starlette, sqlparse, pytest and pip. Upgraded the affected stack, including FastAPI and dbt for compatibility. The follow-up scan audited 119 public packages with zero known advisories. Careflow itself is private source and has no public advisory match. |
| PostgreSQL 16.13 missing later security fixes | High priority maintenance | The official [16.15 release notes](https://www.postgresql.org/docs/16/release-16-15.html) and [16.14 notes](https://www.postgresql.org/docs/16/release-16-14.html) list security fixes. Updated local, Codespaces and workflow images to 16.15. Confirmed the running server reports 16.15. Preserved the database volume and made an ignored, private-format database backup before updating; `pg_restore --list` could read it. No logical slots, subscriptions, foreign servers, user mappings, or extensions other than plpgsql were present. Individual upstream exploit paths were not attempted. |
| Local assistant accepted arbitrary Host headers | Medium; can spend local live allowance if live mode is enabled | Before the fix, a request with `Host: attacker.example` reached `/ask` and returned 200. Loopback binding alone does not prevent browser DNS rebinding. Added exact-host validation: localhost and loopback by default, explicitly configured private tunnel hosts when needed. Regression tests prove rejected hosts cannot reserve allowance or contact the mock provider. The running server returns 400 for an untrusted host and 200 for a local host. |
| Reused reader role could have SET ROLE memberships | Medium; requires prior database misconfiguration | Provisioning checked privilege flags but not memberships or INHERIT. A NOINHERIT reader could still SET ROLE into a granted role. Reproduced raw-table access through a deliberately granted test role; provisioning previously accepted that state. It now rejects role memberships and inheritance before granting access. Tests restore their temporary grants afterward. This does not claim the model can emit SET ROLE through the fixed query templates. |
| Workflow supply-chain and token defaults | Hardening | Pinned checkout, setup-python, setup-java and upload-artifact to full upstream commit SHAs resolved through read-only Git. All workflows declare `contents: read`; checkout does not persist credentials. Added a test timeout and a separate security workflow that performs advisory and tracked-file secret checks without model calls. Hosted execution of the new workflow remains unverified. |

`security-constraints.txt` pins the patched Starlette and sqlparse versions.
`scripts/install.sh` upgrades pip and applies these constraints. Ad-hoc installs
should also use `-c security-constraints.txt`. Other transitive dependencies are
not fully locked; the security workflow checks the actual resolved installation.
Tooling and version pins need periodic review as new advisories appear.

## Boundaries verified

- The actual reader is NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOINHERIT,
  NOBYPASSRLS and NOREPLICATION, with no role memberships. Its explicit grants
  are SELECT on the two aggregate marts and SELECT on only the medication
  fact's `status` column. It cannot CREATE in public. PostgreSQL's default TEMP
  privilege remains; this does not grant access to raw records.
- Integration tests deny raw reads, patient-identifier reads, and writes with
  PostgreSQL permission errors. Read-only transactions also reject writes even
  using the test administrator. Queries have statement and lock timeouts.
- Plans accept only fixed metric IDs and validated filters. SQL uses fixed
  templates, validated schema identifiers and bound values. Injection strings
  remain literal search values. Invalid plans and provider failures stop before
  SQL. Results are capped at 50 rows with explicit truncation.
- Browser values use DOM textContent; the static dashboard escapes strings.
  The current dashboard export contains synthetic aggregates, not patient IDs
  or credentials. Patient/medication references are resolved locally; ingestion
  performs no HTTP fetch based on FHIR reference values.
- Model requests contain the question and metric definitions, not warehouse
  rows or database credentials. Model allowlist, input budgets, 256 output
  tokens, five UTC-daily and 25 lifetime attempts remain enforced. Tests cover
  concurrent reservations, restart persistence, interrupted/failed requests,
  exhausted/corrupt ledgers, no automatic retries, and persistent accounting
  overrun halts. Tests use temporary ledgers and mock HTTP transports. This
  workspace's real ledger was absent and was not created or reset by the audit.
- API errors suppress credentials and provider payloads. `.env` is ignored and
  mode 0600. Credentials were checked for presence without printing values.
  Baseline works without any API key. The Synthea downloader retains TLS and
  SHA-256 verification of its pinned JAR; it was reviewed, not run here.

## Secret and static scanning

detect-secrets 1.5.0 scanned tracked files and all unique blobs reachable through
the locally available refs before the audit commit: 68 backend blobs across three commits and two
dashboard blobs in one commit. Candidates were manually classified as the
documented CI-only/Codespaces demo passwords, the `.env.example` placeholder, or
a dataset checksum. No actual provider credential was identified. Verification
against credential providers was disabled. This cannot inspect deleted remote
refs, GitHub's secret stores, or historical artifacts not present locally.

The reviewed `.secrets.baseline` records exact candidate hashes, including the
dataset checksum; it does not exempt entire source files. CI rejects new
unreviewed candidates. It is a detection aid, not proof of no leaked secrets.

Bandit 1.9.4 reported ten candidates: eight SQL-string warnings and two
subprocess warnings. Manual review found the SQL sites use validated identifiers
or dialect quoting, fixed catalog values and bound filter parameters. The dbt
subprocess uses an argument list, a repository-local executable and no shell.
These candidates did not demonstrate an injection vulnerability. Existing
parameter-injection and PostgreSQL permission tests exercise these boundaries;
no assertions or scanner rules were disabled to obtain a passing result.

## Validation

- PostgreSQL 16.15: **97 tests passed**, including the new Host and reader-role
  regression tests. SQLite: **79 passed, 18 PostgreSQL tests skipped**.
- Full bootstrap/Dagster job: ingestion, **10 dbt models and 32 quality checks**,
  dashboard export and reader grant refresh succeeded on the upgraded stack.
- Baseline core evaluation: **24/24 passed**, zero model tokens.
- Running API: local requests succeeded, a cited January 2025 answer returned
  three demo encounters, and an untrusted Host was rejected.
- A clean, valid subset of the extended synthetic fixture also passed the
  source-to-warehouse validator and exercised its artifact export path. An exact
  byte check for both generated database passwords across 188 generated files
  (dbt outputs/logs, evaluation and this validation export) found zero matches.
  This checks these artifacts, not unavailable historical GitHub artifacts.
- `pip check` reported no incompatible requirements; shell syntax and
  `git diff --check` passed. Starlette emits a TestClient/httpx deprecation
  warning; it does not affect these test outcomes.

To repeat the checks:

```bash
bash scripts/install.sh
bash scripts/security_check.sh
.venv/bin/pytest -q
# Load the trusted local configuration without shell tracing:
set -a
. ./.env
set +a
TEST_DATABASE_URL="$DATABASE_URL" .venv/bin/pytest -q
```

The security script installs isolated scanner tools in a temporary directory,
queries public advisory metadata and checks tracked files. It does not print
local credentials or make model calls. Do not commit its temporary files,
`.env`, the backup, usage ledgers or raw/generated datasets.

## Remaining risks and limits

- This API and Dagster are local development services without user
  authentication or per-user rate limits. Keep Codespaces forwarding private.
  Host validation is not authentication. A public agent deployment requires
  authenticated, authorized server-side access, request/body/concurrency limits
  and deployment-specific monitoring. Public static frontend exports should
  stay aggregate-only, with agent credentials on a separate backend.
- A privileged database administrator can change grants and ownership.
  Provisioning is additive, not a full privilege-reconciliation system. Reaudit
  grants after schema/role changes; do not reuse an independently overprivileged
  role. Local ingestion uses the Docker bootstrap administrator; production
  migrations and runtime loaders need separate least-privilege accounts.
- The usage ledger is workspace-local. Other copies, restored snapshots and
  separate Actions runners do not share it. The manual live-test workflow still
  makes one potentially billable attempt per run; repeating it repeats that
  possible charge. Provider billing/account-wide hard caps and live semantic
  refusals were not verified. Never restore an older ledger to reset limits.
- Quarantine retains full input records and source paths. Only synthetic data
  is supported; this project has no real-patient-data compliance assessment.
- No base-OS/container image scanner, external penetration test, production TLS
  assessment, or fresh Codespaces run was performed. Container tags remain
  version-pinned rather than digest-pinned. Package scanning cannot assess
  bundled native libraries or unknown vulnerabilities.
- GitHub Actions API access returned Forbidden in this environment. Existing
  hosted workflow results and new hosted checks are unverified; none are
  evidence of live-model validation. Review GitHub's checks on the audit branch.

The official PostgreSQL website was blocked by the network proxy; release notes
were checked through the official `postgres/postgres` repository's
`REL_16_STABLE/doc/src/sgml/release-16.sgml` on raw.githubusercontent.com.
