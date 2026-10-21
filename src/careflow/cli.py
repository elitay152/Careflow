import argparse
import json
import os
import re
from pathlib import Path

from .db import connect
from .demo import generate
from .pipeline import ingest, report


def main():
    parser = argparse.ArgumentParser(description="Synthetic FHIR healthcare pipeline")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", "sqlite:///careflow.db"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("generate-demo")
    demo.add_argument("--output", default="data/demo")
    demo.add_argument("--extended", action="store_true", help="Include conditions and medication requests")
    load = subparsers.add_parser("ingest")
    load.add_argument("path", type=Path)
    load.add_argument("--schema", help="Existing PostgreSQL raw schema for ingestion or replay")
    load.add_argument("--reprocess", action="store_true", help="Replay unchanged files after an ingestion upgrade")
    subparsers.add_parser("report")
    subparsers.add_parser("usage", help="Inspect persistent local model usage; no model request")
    subparsers.add_parser("warehouse-build", help="Build and test the PostgreSQL dbt warehouse")
    dashboard_parser = subparsers.add_parser("dashboard", help="Export the warehouse dashboard to HTML")
    dashboard_parser.add_argument("--output", default="data/dashboard.html")
    public = subparsers.add_parser("export-public", help="Export aggregate JSON for the separate static website")
    public.add_argument("--output", type=Path, default=Path("data/public/careflow.json"))
    public.add_argument("--raw-schema", default="public")
    public.add_argument("--analytics-schema", default="analytics")
    public.add_argument("--cohort-label", default="Hand-authored extended demo")
    public.add_argument("--dbt-artifacts", type=Path, help="Matching dbt manifest and run_results directory")
    public.add_argument("--risk-artifact", type=Path, help="Optional private risk summary; only validated aggregates are exported")
    public.add_argument("--archive", type=Path, help="Optional archived validation summary")
    public.add_argument("--archive-date", help="Documented YYYY-MM-DD date of the archived validation")
    public.add_argument("--confirm-synthetic", action="store_true", help="Confirm this database contains synthetic data only")
    risk = subparsers.add_parser("risk-audit", help="Build private 90-day admission snapshots and aggregate readiness evidence")
    risk.add_argument("--raw-schema", default="public")
    risk.add_argument("--observation-end", required=True, help="Attested exclusive simulator observation cutoff, YYYY-MM-DD")
    risk.add_argument("--output", type=Path, required=True, help="Private ignored output directory, outside Git artifacts")
    risk.add_argument("--confirm-synthetic", action="store_true")
    risk.add_argument("--external-schema", help="Separate-seed source schema for frozen independent evaluation")
    risk.add_argument("--train", action="store_true", help="Fit fixed local baselines/logistic model only when readiness gates pass; no API calls")
    ask = subparsers.add_parser("ask", help="Ask a supported warehouse analytics question")
    ask.add_argument("question")
    ask.add_argument("--mode", choices=["baseline", "llm"], default=None)
    args = parser.parse_args()
    if args.command == "risk-audit":
        if not args.confirm_synthetic:
            parser.error("--confirm-synthetic is required; observation-end must match the simulator run")
        from sqlalchemy import create_engine
        from .hospitalization import run_audit
        engine = create_engine(args.database_url)
        try:
            summary = run_audit(engine, args.raw_schema, args.observation_end, args.output, train=args.train, external_schema=args.external_schema)
            print(json.dumps(summary, indent=2))
        finally:
            engine.dispose()
        return
    if args.command == "export-public":
        if not args.confirm_synthetic:
            parser.error("--confirm-synthetic is required for public export")
        if args.archive and not args.archive_date:
            parser.error("--archive-date is required with --archive")
        from sqlalchemy import create_engine
        from .public_export import export_public_snapshot
        engine = create_engine(args.database_url)
        try:
            print(export_public_snapshot(engine, args.output, raw_schema=args.raw_schema,
                  analytics_schema=args.analytics_schema, dbt_artifacts=args.dbt_artifacts,
                  cohort_label=args.cohort_label,
                  archive=args.archive, archive_date=args.archive_date,
                  synthetic_confirmed=True, risk_artifact=args.risk_artifact))
        finally:
            engine.dispose()
        return
    if args.command == "generate-demo":
        print(generate(args.output, extended=args.extended))
        return
    if args.command == "usage":
        from .budget import UsageLedger
        print(json.dumps(UsageLedger().snapshot(), indent=2))
        return
    if args.command == "warehouse-build":
        from .analytics import build_warehouse
        build_warehouse(args.database_url)
        return
    if args.command == "ask":
        from .api import from_settings
        assistant = from_settings(mode=args.mode)
        try:
            print(json.dumps(assistant.ask(args.question), indent=2))
        finally:
            assistant.engine.dispose()
        return
    database_url = args.database_url
    if args.command == "ingest" and args.schema:
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url
        url = make_url(database_url)
        if url.get_backend_name() != "postgresql" or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", args.schema):
            parser.error("--schema requires PostgreSQL and a simple lowercase schema name")
        probe = create_engine(url)
        try:
            with probe.connect() as connection:
                if not connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=:schema)"), {"schema": args.schema}):
                    parser.error("--schema must name an existing schema")
        finally:
            probe.dispose()
        query = dict(url.query)
        query["options"] = (query.get("options", "") + f" -csearch_path={args.schema}").strip()
        database_url = url.set(query=query)
    engine = connect(database_url)
    try:
        if args.command == "dashboard":
            from .analytics import dashboard
            print(dashboard(engine, args.output))
        elif args.command == "report":
            print(json.dumps(report(engine), indent=2))
        else:
            paths = sorted(args.path.glob("*.json")) if args.path.is_dir() else [args.path]
            if not paths:
                parser.error("No JSON files found")
            for path in paths:
                print(json.dumps(ingest(engine, path, reprocess=args.reprocess)))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
