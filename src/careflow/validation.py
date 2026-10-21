"""Source reconciliation and measured end-to-end Synthea validation.

Creates fresh PostgreSQL schemas; never clears existing data. Input must be synthetic.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
from statistics import median
from time import perf_counter

from .analytics import WAREHOUSE_MODELS, WAREHOUSE_CHECKS

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from .analytics import build_warehouse, dashboard
from .db import conditions, connect, encounters, medications, patients, quarantine, facilities, observations
from .pipeline import ingest, report
from .observations import supported as supported_observation, CODES


TABLES = {"Patient": patients, "Encounter": encounters, "Condition": conditions,
          "MedicationRequest": medications, "Organization": facilities, "Observation": observations}


def inventory(paths):
    counts = Counter()
    resources = {kind: {} for kind in (*TABLES, "Medication")}
    manifest = []
    for path in paths:
        raw = path.read_bytes()
        bundle = json.loads(raw)
        if bundle.get("resourceType") != "Bundle" or not isinstance(bundle.get("entry"), list):
            raise ValueError(f"Not a supported FHIR Bundle: {path.name}")
        manifest.append({"file": path.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        for entry in bundle["entry"]:
            resource = entry["resource"]
            kind = resource["resourceType"]
            counts[kind] += 1
            if kind in resources and (kind != "Observation" or supported_observation(resource)):
                resources[kind][resource["id"]] = resource
    return counts, resources, manifest


def expected_metrics(resources):
    """Compute metric expectations directly from source, without pipeline normalization."""
    monthly = defaultdict(lambda: {"count": 0, "patients": set(), "minutes": []})
    for resource in resources["Encounter"].values():
        if resource["status"] != "finished":
            continue
        start = datetime.fromisoformat(resource["period"]["start"].replace("Z", "+00:00"))
        key = start.astimezone(timezone.utc).strftime("%Y-%m-01")
        group = monthly[key]
        group["count"] += 1
        group["patients"].add(resource["subject"]["reference"])
        end = resource["period"].get("end")
        if end:
            group["minutes"].append((datetime.fromisoformat(end.replace("Z", "+00:00")) - start).total_seconds() / 60)
    prevalence = defaultdict(set)
    for resource in resources["Condition"].values():
        clinical = resource.get("clinicalStatus", {}).get("coding", [{}])[0].get("code")
        verification = resource.get("verificationStatus", {}).get("coding", [{}])[0].get("code")
        if clinical in {"active", "recurrence", "relapse"} and verification == "confirmed":
            code = resource["code"]["coding"][0]
            prevalence[(code["system"], code["code"])].add(resource["subject"]["reference"])
    return monthly, prevalence


def check_warehouse(engine, schema, resources):
    issues = []
    quoted = engine.dialect.identifier_preparer.quote_schema(schema)
    monthly, prevalence = expected_metrics(resources)
    with engine.connect() as connection:
        observed_monthly = {str(row["encounter_month"]): row for row in connection.execute(
            text(f"select * from {quoted}.mart_monthly_utilization")).mappings()}
        if set(observed_monthly) != set(monthly):
            issues.append("Warehouse monthly keys differ from source")
        for month, expected in monthly.items():
            actual = observed_monthly.get(month)
            if actual is None:
                continue
            if actual["encounter_count"] != expected["count"] or actual["unique_patients"] != len(expected["patients"]):
                issues.append(f"Utilization counts differ for {month}")
            average = sum(expected["minutes"]) / len(expected["minutes"]) if expected["minutes"] else None
            actual_average = actual["average_duration_minutes"]
            if (average is None) != (actual_average is None) or (average is not None and abs(float(actual_average) - average) > 1e-6):
                issues.append(f"Duration average differs for {month}")
            expected_median = median(expected["minutes"]) if expected["minutes"] else None
            observed_median = actual["median_duration_minutes"]
            if (expected_median is None) != (observed_median is None) or (expected_median is not None and abs(float(observed_median) - expected_median) > 1e-6):
                issues.append(f"Duration median differs for {month}")
            if actual["measured_duration_count"] != len(expected["minutes"]):
                issues.append(f"Measured duration count differs for {month}")
        observed_prevalence = {(row["code_system"], row["code"]): row for row in connection.execute(
            text(f"select * from {quoted}.mart_condition_prevalence")).mappings()}
        if set(observed_prevalence) != set(prevalence):
            issues.append("Warehouse condition keys differ from source")
        population = len(resources["Patient"])
        for key, expected in prevalence.items():
            actual = observed_prevalence.get(key)
            if actual is None:
                continue
            if actual["affected_patients"] != len(expected) or actual["total_patients"] != population:
                issues.append(f"Condition population differs for {key}")
            if abs(float(actual["prevalence_fraction"]) - len(expected) / population) > 1e-9:
                issues.append(f"Condition prevalence differs for {key}")
    return issues, {"months_reconciled": len(monthly), "condition_groups_reconciled": len(prevalence)}


def check_medication_codes(connection, resources):
    """Independent source oracle for Synthea's UUID/relative medication references."""
    issues = []
    actual = {row["id"]: row for row in connection.execute(select(medications)).mappings()}
    for identifier, request in resources["MedicationRequest"].items():
        concept = request.get("medicationCodeableConcept")
        if concept is None:
            reference = request["medicationReference"]["reference"]
            medication_id = reference.removeprefix("urn:uuid:").removeprefix("Medication/")
            concept = resources["Medication"][medication_id]["code"]
        code = concept["coding"][0]
        observed = actual.get(identifier)
        if observed is None or (observed["code_system"], observed["code"]) != (code["system"], code["code"]):
            issues.append(f"Medication code differs from source for {identifier}")
    return issues


def observation_matches(row, source):
    """Independent source oracle: do not call the production quantity normalizer."""
    def utc(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    effective = utc(source["effectiveDateTime"])
    issued = utc(source["issued"]) if source.get("issued") else None
    if (row["status"] != source["status"] or utc(row["effective_at"]) != effective
            or (utc(row["issued_at"]) if row["issued_at"] else None) != issued
            or (utc(row["available_at"]) if row["available_at"] else None) != (max(effective, issued) if issued else None)):
        return False
    code = next(c["code"] for c in source["code"]["coding"] if c.get("system") == "http://loinc.org" and c.get("code") in CODES)
    if row["code"] != code or row["code_system"] != "http://loinc.org":
        return False
    expected = {}
    for item in source.get("component", []) if code == "85354-9" else [source]:
        quantity = item.get("valueQuantity")
        if quantity is None:
            continue
        child = next((c["code"] for c in item.get("code", {}).get("coding", []) if c.get("system") == "http://loinc.org" and c.get("code") in CODES), None)
        if child is None:
            continue
        metric = CODES[child]
        unit = quantity["code"]
        value = quantity["value"]
        normalized_unit = unit
        if metric == "creatinine" and unit == "umol/L":
            value /= 88.4
            normalized_unit = "mg/dL"
        if metric == "egfr" and unit == "mL/min":
            metric = "egfr_unindexed_unit"
        expected[metric] = {"value": float(value), "unit": normalized_unit, "source_unit": unit}
    return row["measurements"] == expected


def check_resource_values(connection, resources):
    """Reconcile patient links and key stored values against unnormalized Synthea data."""
    issues = []
    for kind, table in TABLES.items():
        mismatches = 0
        for row in connection.execute(select(table)).mappings():
            source = resources[kind].get(row["id"])
            if source is None:
                mismatches += 1
                continue
            if kind == "Patient":
                valid = row["birth_date"] == source["birthDate"] and row["gender"] == source.get("gender", "unknown")
            elif kind == "Organization":
                address = next((a for a in source.get("address", []) if a.get("use") == "work"), (source.get("address") or [{}])[0])
                valid = row["name"] == source.get("name", "Unnamed facility") and all(
                    row[column] == address.get(field) for column, field in
                    (("city", "city"), ("state", "state"), ("postal_code", "postalCode"), ("country", "country")))
            else:
                reference = source["subject"]["reference"]
                patient_id = reference.removeprefix("urn:uuid:").removeprefix("Patient/")
                valid = row["patient_id"] == patient_id
                if kind == "Observation":
                    valid = valid and observation_matches(row, source)
                elif kind == "Encounter":
                    start = datetime.fromisoformat(source["period"]["start"].replace("Z", "+00:00"))
                    valid = valid and row["status"] == source["status"] and datetime.fromisoformat(row["started_at"]) == start
                    end = source["period"].get("end")
                    valid = valid and ((end is None and row["ended_at"] is None) or
                                       (end is not None and row["ended_at"] is not None and
                                        datetime.fromisoformat(row["ended_at"]) == datetime.fromisoformat(end.replace("Z", "+00:00"))))
                elif kind == "Condition":
                    code = source["code"]["coding"][0]
                    clinical = source.get("clinicalStatus", {}).get("coding", [{}])[0].get("code", "unknown")
                    verification = source.get("verificationStatus", {}).get("coding", [{}])[0].get("code", "unknown")
                    valid = valid and (row["code_system"], row["code"], row["clinical_status"], row["verification_status"]) == (
                        code["system"], code["code"], clinical, verification)
                    recorded = source.get("recordedDate")
                    expected_recorded = (datetime.fromisoformat(recorded.replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
                                         if recorded and len(recorded) > 10 else recorded)
                    valid = valid and row["recorded_date"] == expected_recorded
                else:
                    valid = valid and row["status"] == source["status"]
            if not valid:
                mismatches += 1
        if mismatches:
            issues.append(f"{mismatches} {kind} rows have source value or patient-link mismatches")
    return issues


def validate(directory, database_url, schema, output):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", schema):
        raise ValueError("Schema must contain 1–40 lowercase letters, digits, or underscores and start with a letter")
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("Synthea validation requires PostgreSQL")
    paths = sorted(Path(directory).glob("*.json"))
    if not paths:
        raise ValueError("No JSON Bundles found")
    counts, resources, manifest = inventory(paths)
    if not resources["Patient"]:
        raise ValueError("No patients found; this would not validate the healthcare workflow")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    base = create_engine(url)
    try:
        with base.begin() as connection:
            # No IF NOT EXISTS: existing user schemas must never be reused or reset.
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            postgres_version = connection.scalar(text("show server_version"))
    finally:
        base.dispose()
    query = dict(url.query)
    query["options"] = (query.get("options", "") + f" -csearch_path={schema}").strip()
    engine = connect(url.set(query=query))
    issues = []
    started = perf_counter()
    results = []
    try:
        for index, path in enumerate(paths, 1):
            results.append(ingest(engine, path))
            if index % 20 == 0:
                print(f"Ingested {index}/{len(paths)} files", flush=True)
        ingest_seconds = perf_counter() - started
        first_report = report(engine)
        accepted = sum(row["accepted"] for row in results)
        rejected = sum(row["rejected"] for row in results)
        ignored = sum(row["ignored"] for row in results)
        if rejected:
            issues.append(f"{rejected} supported records were quarantined; inspect rejection reasons")
        if accepted + rejected + ignored != sum(counts.values()):
            issues.append("Input resource accounting does not balance")
        if accepted + rejected != sum(counts[kind] for kind in TABLES if kind != "Observation") + len(resources["Observation"]):
            issues.append("Supported resource accounting does not balance")
        with engine.connect() as connection:
            for kind, table in TABLES.items():
                observed = set(connection.scalars(select(table.c.id)))
                if observed != set(resources[kind]):
                    issues.append(f"{kind} IDs do not reconcile with source")
            orphans = {}
            for table in (encounters, conditions, medications, observations):
                missing = connection.execute(select(table.c.id).select_from(
                    table.outerjoin(patients, table.c.patient_id == patients.c.id)
                ).where(patients.c.id.is_(None))).all()
                orphans[table.name] = len(missing)
                if missing:
                    issues.append(f"Orphan patient references in {table.name}")
            rejections = dict(Counter(connection.scalars(select(quarantine.c.reason))))
            issues.extend(check_resource_values(connection, resources))
            medication_issues = check_medication_codes(connection, resources)
            if medication_issues:
                issues.append(f"{len(medication_issues)} medication codes do not reconcile with source")
        started = perf_counter()
        reruns = [ingest(engine, path) for path in paths]
        rerun_seconds = perf_counter() - started
        if any(row["status"] != "skipped" for row in reruns) or report(engine) != first_report:
            issues.append("Unchanged rerun did not preserve database state")
        os.environ["DBT_RAW_SCHEMA"] = schema
        os.environ["DBT_TARGET_SCHEMA"] = schema + "_analytics"
        os.environ["DBT_TARGET_PATH"] = str(output / "dbt")
        started = perf_counter()
        build_warehouse(database_url)
        warehouse_seconds = perf_counter() - started
        artifact = json.loads((output / "dbt" / "run_results.json").read_text())
        dbt_results = Counter((row["unique_id"].split(".")[0], row["status"]) for row in artifact["results"])
        if dbt_results != Counter({("model", "success"): WAREHOUSE_MODELS, ("test", "pass"): WAREHOUSE_CHECKS}):
            issues.append("Unexpected dbt model/test outcomes")
        metric_issues, metric_counts = check_warehouse(engine, schema + "_analytics", resources)
        issues.extend(metric_issues)
        dashboard(engine, output / "dashboard.html", schema=schema + "_analytics")
        result = {
            "status": "passed" if not issues else "failed", "issues": issues,
            "schema": schema, "postgres_version": postgres_version,
            "python_version": platform.python_version(),
            "input_files": len(paths), "input_bytes": sum(item["bytes"] for item in manifest),
            "input_resources": dict(sorted(counts.items())),
            "manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
            "accepted": accepted, "rejected": rejected, "ignored": ignored,
            "database_counts": first_report["counts"],
            "provider_organizations": first_report["provider_organizations"], "orphan_counts": orphans,
            "rejection_reasons": rejections,
            "ingest_seconds": round(ingest_seconds, 3),
            "accepted_resources_per_second": round(accepted / ingest_seconds, 1),
            "rerun_seconds": round(rerun_seconds, 3),
            "skipped_files": sum(row["status"] == "skipped" for row in reruns),
            "warehouse_seconds": round(warehouse_seconds, 3),
            "dbt_models_passed": dbt_results[("model", "success")],
            "dbt_tests_passed": dbt_results[("test", "pass")], **metric_counts,
        }
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (output / "results.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        return result
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--schema", required=True, help="Fresh isolated schema; existing schemas are never reset")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not os.getenv("DATABASE_URL"):
        parser.error("Set DATABASE_URL to the local PostgreSQL database")
    result = validate(args.input, os.environ["DATABASE_URL"], args.schema, args.output)
    sys.exit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
