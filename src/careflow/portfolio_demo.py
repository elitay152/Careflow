"""Run the small fictional pipeline in fresh schemas; no model or cloud calls."""

import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from .analytics import WAREHOUSE_CHECKS, WAREHOUSE_MODELS, build_warehouse
from .db import connect
from .demo import generate
from .pipeline import ingest, report
from .public_export import export_public_snapshot

ROOT = Path(__file__).resolve().parents[2]


def run(database_url):
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("The portfolio demo requires PostgreSQL")
    schema = "portfolio_demo_" + uuid4().hex[:12]
    analytics = schema + "_analytics"
    output = ROOT / "data/portfolio-demo" / schema
    output.mkdir(parents=True, exist_ok=False)
    source = generate(output / "input", extended=True)
    base = create_engine(url)
    engine = None
    names = (
        "DBT_RAW_SCHEMA",
        "DBT_TARGET_SCHEMA",
        "DBT_TARGET_PATH",
        "DBT_LOG_PATH",
        "CAREFLOW_ASSISTANT_DATABASE_URL",
    )
    previous = {name: os.environ.get(name) for name in names}
    try:
        with base.begin() as connection:
            for name in (schema, analytics):
                connection.execute(text(f'CREATE SCHEMA "{name}"'))
        query = dict(url.query)
        query["options"] = (
            query.get("options", "") + f" -csearch_path={schema}"
        ).strip()
        engine = connect(url.set(query=query))
        first = ingest(engine, source)
        before = report(engine)
        expected = {
            "patients": 3,
            "encounters": 3,
            "conditions": 3,
            "medications": 3,
            "observations": 0,
            "loads": 1,
            "quarantine": 1,
        }
        if (
            before["counts"] != expected
            or first["accepted"] != 12
            or first["rejected"] != 1
        ):
            raise RuntimeError("The demo load did not match the fictional fixture")
        rerun = ingest(engine, source)
        if rerun["status"] != "skipped" or report(engine) != before:
            raise RuntimeError("The duplicate-safe rerun changed demo state")
        os.environ.update(
            {
                "DBT_RAW_SCHEMA": schema,
                "DBT_TARGET_SCHEMA": analytics,
                "DBT_TARGET_PATH": str(output / "dbt"),
                "DBT_LOG_PATH": str(output / "dbt-logs"),
            }
        )
        os.environ.pop("CAREFLOW_ASSISTANT_DATABASE_URL", None)
        build_warehouse(database_url)
        export = export_public_snapshot(
            base,
            output / "careflow.json",
            raw_schema=schema,
            analytics_schema=analytics,
            dbt_artifacts=output / "dbt",
            archive=ROOT / "docs/validation-results.json",
            archive_date="2026-10-20",
            cohort_label="Hand-authored three-patient portfolio demo",
            synthetic_confirmed=True,
        )
        snapshot = json.loads(export.read_text())
        quality = snapshot["current"]["quality"]
        if (
            quality["status"] != "passed"
            or quality["models_passed"] != WAREHOUSE_MODELS
            or quality["checks_passed"] != WAREHOUSE_CHECKS
        ):
            raise RuntimeError("The demo warehouse evidence did not pass")
        print(
            "\nDemo passed: 3 fictional patients; 12 accepted records; 1 intentional quarantine."
        )
        print(
            f"Unchanged input skipped on rerun. dbt: {WAREHOUSE_MODELS} models and {WAREHOUSE_CHECKS} checks passed."
        )
        print(
            f"Baseline assistant examples: {len(snapshot['assistant']['examples'])}; no model API call."
        )
        print(f"Fresh schemas retained: {schema}, {analytics}")
        print(f"Aggregate export: {export.relative_to(ROOT)}")
        print(f"dbt evidence: {(output / 'dbt').relative_to(ROOT)}")
        print(
            "Portfolio cohort, website snapshots, and usage ledger were not modified."
        )
        return output
    finally:
        if engine is not None:
            engine.dispose()
        base.dispose()
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit(
            "Set DATABASE_URL or run bash scripts/portfolio_demo.sh with the local .env configured."
        )
    run(database_url)


if __name__ == "__main__":
    main()
