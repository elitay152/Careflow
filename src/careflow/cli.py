import argparse
import json
import os
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
    load.add_argument("--reprocess", action="store_true", help="Replay unchanged files after an ingestion upgrade")
    subparsers.add_parser("report")
    subparsers.add_parser("usage", help="Inspect persistent local model usage; no model request")
    subparsers.add_parser("warehouse-build", help="Build and test the PostgreSQL dbt warehouse")
    dashboard_parser = subparsers.add_parser("dashboard", help="Export the warehouse dashboard to HTML")
    dashboard_parser.add_argument("--output", default="data/dashboard.html")
    ask = subparsers.add_parser("ask", help="Ask a supported warehouse analytics question")
    ask.add_argument("question")
    ask.add_argument("--mode", choices=["baseline", "llm"], default=None)
    args = parser.parse_args()
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
    engine = connect(args.database_url)
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
