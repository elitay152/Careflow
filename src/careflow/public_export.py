"""Export a small, explicit aggregate contract for the separate static website."""
from collections import Counter
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile

from sqlalchemy import text

from .assistant import Assistant, BaselinePlanner, json_value, readonly
from .condition_groups import condition_group
from .forecast import evaluate_forecast
from .utilization import CHRONIC_CODES, utilization_details
from .segmentation import export_segments


TABLES = ("patients", "encounters", "conditions", "medications", "loads", "quarantine")
FHIR_TYPES = (
    "AllergyIntolerance", "CarePlan", "CareTeam", "Claim", "Condition", "Device",
    "DiagnosticReport", "DocumentReference", "Encounter", "ExplanationOfBenefit",
    "ImagingStudy", "Immunization", "Location", "Medication", "MedicationAdministration",
    "MedicationRequest", "Observation", "Organization", "Patient", "Practitioner",
    "PractitionerRole", "Procedure", "Provenance", "SupplyDelivery",
)
EXAMPLES = ("Monthly encounters in 2025-01", "Condition prevalence for diabetes",
            "Medication requests by status")


def identifier(value):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
        raise ValueError("Export schemas must be simple lowercase identifiers")
    return f'"{value}"'


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Evidence timestamps must include a timezone")
    return parsed


def warehouse_evidence(directory, database, raw_schema, analytics_schema, loaded_at):
    if directory is None:
        return {"status": "unverified", "completed_at": None, "models_passed": None,
                "checks_passed": None, "checks_total": None, "invocation_id": None}
    root = Path(directory)
    results = json.loads((root / "run_results.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    meta = results["metadata"]
    if not meta.get("invocation_id") or meta["invocation_id"] != manifest["metadata"].get("invocation_id"):
        raise ValueError("dbt evidence must come from one matching invocation")
    if loaded_at and timestamp(meta["invocation_started_at"]) < timestamp(loaded_at):
        raise ValueError("Warehouse evidence predates the latest ingestion")
    nodes = manifest["nodes"]
    for result in results["results"]:
        node = nodes[result["unique_id"]]
        if node["resource_type"] == "model" and (node["database"] != database or node["schema"] != analytics_schema):
            raise ValueError("dbt evidence targets another warehouse")
    if not manifest["sources"] or any(
        source["database"] != database or source["schema"] != raw_schema
        for source in manifest["sources"].values()
    ):
        raise ValueError("dbt evidence targets another source schema")
    outcomes = Counter((row["unique_id"].split(".")[0], row["status"]) for row in results["results"])
    passed = outcomes == Counter({("model", "success"): 10, ("test", "pass"): 32})
    if len({row["unique_id"] for row in results["results"]}) != len(results["results"]):
        raise ValueError("dbt evidence contains duplicate results")
    timestamp(meta["generated_at"])
    return {"status": "passed" if passed else "failed", "completed_at": meta["generated_at"],
            "models_passed": outcomes[("model", "success")],
            "checks_passed": outcomes[("test", "pass")],
            "checks_total": sum(count for (kind, _), count in outcomes.items() if kind == "test"),
            "invocation_id": meta["invocation_id"]}


def archive_summary(path, recorded_on):
    """Select numeric evidence only; never serialize arbitrary source fields."""
    if path is None:
        return None
    date.fromisoformat(recorded_on)
    source = json.loads(Path(path).read_text())
    if source["status"] != "passed" or source.get("issues"):
        raise ValueError("Archived validation must have passed")
    counts = {name: source["database_counts"][name] for name in TABLES}
    resources = {name: source["input_resources"][name] for name in FHIR_TYPES if name in source["input_resources"]}
    fields = {name: source[name] for name in (
        "accepted", "rejected", "ignored", "input_files", "dbt_models_passed", "dbt_tests_passed",
    )}
    if any(type(value) is not int or value < 0 for value in [*counts.values(), *resources.values(), *fields.values()]):
        raise ValueError("Archived counts must be nonnegative integers")
    return {"label": "Synthea 3.3.0 · seed 42", "recorded_on": recorded_on,
            "evidence_url": "https://github.com/elitay152/Careflow/blob/main/docs/validation-results.json",
            "counts": counts, "resources": resources, **fields}


def export_public_snapshot(engine, output, *, raw_schema="public", analytics_schema="analytics",
                           dbt_artifacts=None, archive=None, archive_date=None,
                           synthetic_confirmed=False, cohort_label="Hand-authored extended demo"):
    if not synthetic_confirmed:
        raise ValueError("Confirm the database contains only synthetic data before public export")
    if not isinstance(cohort_label, str) or not cohort_label.strip() or len(cohort_label) > 200:
        raise ValueError("Cohort label must contain 1–200 characters")
    if engine.dialect.name != "postgresql":
        raise ValueError("The public export requires the PostgreSQL warehouse")
    raw, warehouse = identifier(raw_schema), identifier(analytics_schema)
    with readonly(engine) as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        counts = {table: connection.scalar(text(f'SELECT count(*) FROM {raw}."{table}"')) for table in TABLES}
        loaded_at = connection.scalar(text(f"SELECT max(loaded_at) FROM {raw}.loads"))
        def rows(sql):
            return [{key: json_value(value) for key, value in row.items()}
                    for row in connection.execute(text(sql)).mappings()]
        monthly = rows(f"SELECT encounter_month, encounter_count, unique_patients, average_duration_minutes, median_duration_minutes, measured_duration_count FROM {warehouse}.mart_monthly_utilization ORDER BY encounter_month")
        conditions = rows(f"SELECT condition_name, code_system, code, affected_patients, total_patients, prevalence_fraction FROM {warehouse}.mart_condition_prevalence ORDER BY affected_patients DESC, code_system, code")
        medications = rows(f"SELECT status, count(*) AS request_count FROM {warehouse}.fct_medication_requests GROUP BY status ORDER BY status")
        annual, summary, chronic = utilization_details(connection, warehouse, monthly, counts["patients"])
        segments = export_segments(connection, warehouse, summary)
        annual = [{key: json_value(value) for key, value in row.items()} for row in annual]
        if summary is not None:
            summary = {key: json_value(value) for key, value in summary.items()}
    for row in conditions:
        row["group"] = condition_group(row["code_system"], row["code"])
        row["chronic_subset"] = row["code_system"] == "http://snomed.info/sct" and row["code"] in CHRONIC_CODES
    quality = warehouse_evidence(dbt_artifacts, engine.url.database, raw_schema, analytics_schema, loaded_at)
    assistant = Assistant(engine, BaselinePlanner(), analytics_schema)
    examples = []
    questions = list(EXAMPLES)
    if monthly:
        questions[0] = f"Monthly encounters in {monthly[-1]['encounter_month'][:7]}"
    for question in questions:
        result = assistant.ask(question)
        examples.append({"question": question, **{key: result[key] for key in (
            "status", "answer", "rows", "citations", "plan",
        )}})
    forecast = evaluate_forecast(monthly)
    snapshot = {
        "schema_version": 4, "synthetic_only": True,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "current": {"label": cohort_label, "raw_schema": raw_schema,
                    "analytics_schema": analytics_schema, "source_loaded_at": loaded_at,
                    "counts": counts, "monthly_utilization": monthly, "conditions": conditions,
                    "annual_utilization": annual, "utilization_summary": summary, "chronic_summary": chronic,
                    "segments": segments,
                    "medication_status": medications, "quality": quality},
        "archive": archive_summary(archive, archive_date),
        "assistant": {"mode": "baseline", "execution": "exported_replay", "examples": examples},
        "forecast": forecast,
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=output.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(snapshot, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()
    return output
