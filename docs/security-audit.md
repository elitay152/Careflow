# Security boundaries

Careflow is a local development platform for synthetic data. FastAPI, Dagster,
and PostgreSQL bind to loopback; keep Codespaces forwarding private. The public
website contains static aggregate exports.

## Application and database

- Metric plans select fixed IDs and validated filters; SQL uses fixed templates,
  quoted schema identifiers, and bound values.
- Assistant transactions are read-only, limited to 50 rows, with statement and
  lock timeouts. Responses omit credentials and provider error payloads.
- The reader has aggregate-mart access and only the medication fact's `status`
  column. Provisioning rejects elevated flags, inheritance, and role memberships.
  dbt builds refresh the configured reader's grants.
- Exact Host validation allows loopback and explicitly configured private tunnel
  hostnames. It is not user authentication.
- FHIR references resolve locally; ingestion does not fetch reference URLs.
  Quarantine retains raw payloads and source paths, so only synthetic inputs are supported.

The API has no user authentication or per-user rate limits. A public live service
requires a separate authenticated backend and deployment controls. Local loading
uses bootstrap administrator credentials; production roles need separate design.

## Credentials and spending

`.env` is ignored; generated local credentials use restricted file permissions.
Model keys stay server-side. Baseline is the default. Live requests use the fixed
model allowlist and persistent attempt/token reservations described in
[API costs](api-costs.md). Preserve the ledger, including failed-call reservations.
Different workspaces and Actions runners do not share a global allowance.

## Checks

```bash
bash scripts/install.sh
bash scripts/security_check.sh
.venv/bin/pytest -q
TEST_DATABASE_URL="$DATABASE_URL" .venv/bin/pytest -q
```

CI scans resolved dependencies and tracked files, pins workflow actions, uses
read-only repository permissions, and avoids persisted checkout credentials.
The secret baseline records reviewed exact false positives, not whole-file
exemptions. Dependency advisories and scanner coverage are point-in-time checks;
passing tests do not establish production or real-patient-data compliance.

Repository maintainers, database administrators, and export authors remain
trusted. Schema validation does not de-identify data or prove source authenticity.
