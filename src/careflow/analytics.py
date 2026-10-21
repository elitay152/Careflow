import html
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE_MODELS = 13
WAREHOUSE_CHECKS = 49


def build_warehouse(database_url):
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("The dbt warehouse requires PostgreSQL")
    if not url.password:
        raise ValueError("DATABASE_URL must include the local database password")
    environment = dict(os.environ)
    environment.update({
        "DBT_HOST": url.host or "localhost", "DBT_PORT": str(url.port or 5432),
        "DBT_USER": url.username or "careflow", "DBT_PASSWORD": url.password,
        "DBT_DBNAME": url.database or "careflow",
        "DBT_SSLMODE": url.query.get("sslmode", "prefer"),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    })
    executable = Path(sys.executable).parent / "dbt"
    if not executable.exists():
        raise ValueError("Install the analytics extra: python -m pip install -e '.[dev,analytics]'")
    subprocess.run([
        str(executable), "build", "--project-dir", str(ROOT / "warehouse"),
        "--profiles-dir", str(ROOT / "warehouse"),
    ], env=environment, cwd=ROOT, check=True)
    # dbt swaps rebuilt tables; refresh the optional reader's grants afterwards.
    reader_url = os.getenv("CAREFLOW_ASSISTANT_DATABASE_URL")
    target_schema = environment.get("DBT_TARGET_SCHEMA", "analytics")
    if reader_url and target_schema == os.getenv("CAREFLOW_ANALYTICS_SCHEMA", "analytics"):
        from .reader import configure_reader
        configure_reader(database_url, target_schema, role=make_url(reader_url).username, existing_url=reader_url)


def dashboard(engine, output, schema="analytics"):
    if engine.dialect.name != "postgresql":
        raise ValueError("The analytics dashboard requires the PostgreSQL dbt warehouse")
    quoted_schema = engine.dialect.identifier_preparer.quote_schema(schema)
    with engine.connect() as connection:
        utilization = list(connection.execute(text(
            f"select * from {quoted_schema}.mart_monthly_utilization order by encounter_month"
        )).mappings())
        prevalence = list(connection.execute(text(
            f"select * from {quoted_schema}.mart_condition_prevalence order by affected_patients desc, code_system, code"
        )).mappings())
        medication_count = connection.scalar(text(f"select count(*) from {quoted_schema}.fct_medication_requests"))
    def table(rows, columns):
        head = "".join(f"<th>{html.escape(label)}</th>" for _, label in columns)
        body = "".join("<tr>" + "".join(
            f"<td>{html.escape(str(row[key]) if row[key] is not None else '—')}</td>"
            for key, _ in columns) + "</tr>" for row in rows)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    content = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Careflow | Synthetic healthcare analytics</title><style>
body{font:16px system-ui;max-width:1050px;margin:3rem auto;padding:0 1rem;color:#19313c;background:#f5f8f9}
h1{font-size:2.5rem}section{background:white;padding:1.5rem;margin:1rem 0;border-radius:12px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:.7rem;border-bottom:1px solid #dce6e9}
.badge{color:#186b65;font-weight:700}small{color:#52636a}</style>
<p class="badge">CAREFLOW · SYNTHETIC DATA</p><h1>Healthcare analytics</h1>
<p>A snapshot of loaded fictional records. Not clinical evidence.</p>"""
    content += "<section><h2>Monthly utilization</h2><p>Finished encounters by UTC start month.</p>"
    content += table(utilization, [("encounter_month", "Month"), ("encounter_count", "Encounters"),
                                   ("unique_patients", "Patients"), ("average_duration_minutes", "Average minutes")]) + "</section>"
    content += "<section><h2>Recorded condition prevalence</h2><p>Confirmed active conditions; denominator: all loaded patients.</p>"
    content += table(prevalence, [("condition_name", "Condition"), ("code", "Code"),
                                 ("affected_patients", "Patients"), ("total_patients", "Population"),
                                 ("prevalence_fraction", "Fraction")]) + "</section>"
    content += f"<section><h2>Medication requests</h2><p>{medication_count} recorded requests; these do not establish dispensing or adherence.</p></section>"
    content += "<small>Generated from Careflow dbt marts. Rebuild after each load to refresh.</small></html>"
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content)
    return output
