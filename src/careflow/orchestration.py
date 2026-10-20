"""Serial daily asset job. Schedules require a running Dagster daemon."""
import os
from pathlib import Path

from dagster import (
    AssetExecutionContext, AssetSelection, DefaultScheduleStatus, Definitions,
    ScheduleDefinition, asset, define_asset_job,
)

from .analytics import ROOT, build_warehouse, dashboard
from .db import connect
from .pipeline import ingest


def database_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError("Set DATABASE_URL to the local PostgreSQL database")
    return url


@asset
def fhir_ingestion(context: AssetExecutionContext):
    directory = Path(os.getenv("CAREFLOW_INPUT_DIR", str(ROOT / "data" / "demo")))
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError(f"No JSON Bundles found in {directory}; generate demo data or set CAREFLOW_INPUT_DIR")
    engine = connect(database_url())
    try:
        results = [ingest(engine, path) for path in paths]
    finally:
        engine.dispose()
    context.add_output_metadata({
        "files": len(results), "accepted": sum(row["accepted"] for row in results),
        "rejected": sum(row["rejected"] for row in results),
        "skipped_files": sum(row["status"] == "skipped" for row in results),
    })


@asset(deps=[fhir_ingestion])
def analytics_warehouse():
    build_warehouse(database_url())


@asset(deps=[analytics_warehouse])
def analytics_dashboard(context: AssetExecutionContext):
    engine = connect(database_url())
    try:
        path = dashboard(engine, ROOT / "data" / "dashboard.html")
    finally:
        engine.dispose()
    context.add_output_metadata({"output_file": str(path)})


daily_pipeline = define_asset_job("daily_pipeline", selection=AssetSelection.all())
daily_schedule = ScheduleDefinition(
    job=daily_pipeline, cron_schedule="0 6 * * *", execution_timezone="UTC",
    default_status=DefaultScheduleStatus.STOPPED,
)
defs = Definitions(
    assets=[fhir_ingestion, analytics_warehouse, analytics_dashboard],
    jobs=[daily_pipeline], schedules=[daily_schedule],
)
