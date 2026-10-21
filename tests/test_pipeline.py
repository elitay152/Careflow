import json
import os
import uuid

import pytest
from sqlalchemy import create_engine, event, select, text

from careflow.db import conditions, encounters, loads, medications, metadata, patients, quarantine
from careflow.demo import generate
from careflow.pipeline import ingest, report


@pytest.fixture
def engine(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        base = create_engine(url)
        schema = "test_" + uuid.uuid4().hex
        with base.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = base.execution_options(schema_translate_map={None: schema})
    else:
        base = engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    metadata.create_all(engine)
    yield engine
    if url:
        with base.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}_analytics" CASCADE'))
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    base.dispose()


def write_bundle(tmp_path, entries, name="bundle.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"resourceType": "Bundle", "entry": entries}))
    return path


def patient(identifier="p1", **kwargs):
    return {"resource": {"resourceType": "Patient", "id": identifier,
                         "birthDate": "1990-01-01", **kwargs}}


def encounter(identifier="e1", reference="Patient/p1", **kwargs):
    return {"resource": {"resourceType": "Encounter", "id": identifier,
                         "subject": {"reference": reference}, "status": "finished",
                         "period": {"start": "2025-01-01T00:00:00Z"}, **kwargs}}


def test_demo_and_rerun(engine, tmp_path):
    path = generate(tmp_path)
    assert ingest(engine, path) == {"file": str(path), "status": "loaded", "accepted": 6, "rejected": 1, "ignored": 0}
    assert ingest(engine, path)["status"] == "skipped"
    assert report(engine)["counts"] == {"patients": 3, "encounters": 3, "conditions": 0, "medications": 0, "loads": 1, "quarantine": 1}


def test_updates_use_resource_identity(engine, tmp_path):
    ingest(engine, write_bundle(tmp_path, [patient()]))
    ingest(engine, write_bundle(tmp_path, [patient(gender="female")], "update.json"))
    assert report(engine)["counts"]["patients"] == 1
    with engine.connect() as connection:
        assert connection.scalar(select(patients.c.gender)) == "female"


def test_explicit_reprocessing_preserves_identity(engine, tmp_path):
    path = generate(tmp_path, extended=True)
    ingest(engine, path)
    counts = report(engine)["counts"]
    assert ingest(engine, path, reprocess=True)["accepted"] == 12
    assert report(engine)["counts"] == counts


def test_encounter_before_patient_and_uuid_reference(engine, tmp_path):
    p = patient()
    p["fullUrl"] = "urn:uuid:123"
    result = ingest(engine, write_bundle(tmp_path, [encounter(reference=p["fullUrl"]), p]))
    assert result["accepted"] == 2
    with engine.connect() as connection:
        assert connection.scalar(select(encounters.c.patient_id)) == "p1"


@pytest.mark.parametrize("entry", [
    patient(birthDate="not-a-date"),
    patient(birthDate="2999-01-01"),
    patient(gender="invalid"),
    patient(identifier=""),
    encounter(),  # Missing patient.
    encounter(status="invalid"),
    encounter(period={"start": "2025-01-01T00:00:00"}),
    encounter(period={"start": "2025-01-02T00:00:00Z", "end": "2025-01-01T00:00:00Z"}),
    {"resource": None},
    {"resource": {"id": "missing-type"}},
])
def test_invalid_records_are_quarantined(engine, tmp_path, entry):
    result = ingest(engine, write_bundle(tmp_path, [entry]))
    assert result["rejected"] == 1
    assert result["accepted"] == 0
    with engine.connect() as connection:
        assert connection.scalar(select(quarantine.c.reason))


def test_unsupported_resources_are_counted(engine, tmp_path):
    result = ingest(engine, write_bundle(tmp_path, [{"resource": {"resourceType": "Observation", "id": "o1"}}]))
    assert result["ignored"] == 1


def test_malformed_file_has_no_database_side_effects(engine, tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"resourceType":')
    with pytest.raises(ValueError):
        ingest(engine, path)
    assert all(value == 0 for value in report(engine)["counts"].values())


def test_database_failure_rolls_back_entire_file(engine, tmp_path):
    def fail_on_checkpoint(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO") and "loads" in statement:
            raise RuntimeError("simulated checkpoint failure")
    event.listen(engine, "before_cursor_execute", fail_on_checkpoint)
    path = write_bundle(tmp_path, [patient(), encounter()])
    try:
        with pytest.raises(RuntimeError, match="checkpoint"):
            ingest(engine, path)
    finally:
        event.remove(engine, "before_cursor_execute", fail_on_checkpoint)
    assert all(value == 0 for value in report(engine)["counts"].values())
    assert ingest(engine, path)["accepted"] == 2


def test_cross_file_reference(engine, tmp_path):
    ingest(engine, write_bundle(tmp_path, [patient()]))
    assert ingest(engine, write_bundle(tmp_path, [encounter()], "encounter.json"))["accepted"] == 1


def test_demo_preserves_existing_files(tmp_path):
    path = tmp_path / "demo.json"
    path.write_text("user data")
    with pytest.raises(ValueError, match="overwrite"):
        generate(tmp_path)
    assert path.read_text() == "user data"


def test_conditions_and_medications_are_loaded_and_updated(engine, tmp_path):
    path = generate(tmp_path, extended=True)
    assert ingest(engine, path)["accepted"] == 12
    assert report(engine)["counts"]["conditions"] == 3
    assert report(engine)["counts"]["medications"] == 3
    bundle = json.loads(path.read_text())
    for entry in bundle["entry"]:
        resource = entry["resource"]
        if resource.get("resourceType") == "MedicationRequest":
            resource["status"] = "stopped"
    path.write_text(json.dumps(bundle))
    ingest(engine, path)
    assert report(engine)["counts"]["medications"] == 3
    with engine.connect() as connection:
        assert set(connection.scalars(select(medications.c.status))) == {"stopped"}


@pytest.mark.parametrize("kind,change", [
    ("Condition", {"code": {}}),
    ("Condition", {"onsetDateTime": "invalid"}),
    ("Condition", {"clinicalStatus": {"coding": [{"system": "test", "code": "invalid"}]}}),
    ("MedicationRequest", {"status": "invalid"}),
    ("MedicationRequest", {"medicationCodeableConcept": None}),
    ("MedicationRequest", {"subject": {"reference": "Patient/missing"}}),
])
def test_invalid_clinical_records_quarantined(engine, tmp_path, kind, change):
    path = generate(tmp_path, extended=True)
    bundle = json.loads(path.read_text())
    resource = next(entry["resource"] for entry in bundle["entry"] if entry["resource"]["resourceType"] == kind)
    resource.update(change)
    result = ingest(engine, write_bundle(tmp_path, [patient("patient-1"), {"resource": resource}]))
    assert result["accepted"] == 1
    assert result["rejected"] == 1


def test_clinical_records_before_patient(engine, tmp_path):
    path = generate(tmp_path, extended=True)
    bundle = json.loads(path.read_text())
    path.write_text(json.dumps({**bundle, "entry": list(reversed(bundle["entry"]))}))
    assert ingest(engine, path)["accepted"] == 12


@pytest.mark.parametrize("reference", ["urn:uuid:med-1", "Medication/med-1"])
def test_synthea_medication_reference_after_request(engine, tmp_path, reference):
    path = generate(tmp_path, extended=True)
    request = next(entry for entry in json.loads(path.read_text())["entry"]
                   if entry["resource"]["resourceType"] == "MedicationRequest")
    concept = request["resource"].pop("medicationCodeableConcept")
    request["resource"]["medicationReference"] = {"reference": reference}
    medication = {"fullUrl": "urn:uuid:med-1", "resource": {
        "resourceType": "Medication", "id": "med-1", "code": concept}}
    p = patient("patient-1")
    p["fullUrl"] = "urn:uuid:patient-1"
    result = ingest(engine, write_bundle(tmp_path, [request, p, medication]))
    assert (result["accepted"], result["rejected"], result["ignored"]) == (2, 0, 1)
    with engine.connect() as connection:
        row = connection.execute(select(medications)).mappings().one()
        assert row["code"] == "860975"
        assert row["code_system"] == "http://www.nlm.nih.gov/research/umls/rxnorm"


def test_unresolved_medication_reference_is_quarantined(engine, tmp_path):
    request = {"resource": {"resourceType": "MedicationRequest", "id": "m1", "status": "active",
                           "subject": {"reference": "Patient/p1"},
                           "medicationReference": {"reference": "Medication/missing"}}}
    result = ingest(engine, write_bundle(tmp_path, [patient(), request]))
    assert result["rejected"] == 1
    with engine.connect() as connection:
        assert "resolve" in connection.scalar(select(quarantine.c.reason))


def test_ambiguous_medication_references_reject_file(engine, tmp_path):
    medications = [{"fullUrl": "urn:uuid:med-1", "resource": {
        "resourceType": "Medication", "id": f"med-{index}",
        "code": {"coding": [{"system": "test", "code": str(index)}]}}} for index in (1, 2)]
    with pytest.raises(ValueError, match="Ambiguous Medication"):
        ingest(engine, write_bundle(tmp_path, [patient(), *medications]))
    assert all(value == 0 for value in report(engine)["counts"].values())


def test_validation_detects_wrong_but_existing_patient_link(engine, tmp_path):
    from careflow.validation import check_resource_values, inventory
    path = generate(tmp_path, extended=True)
    ingest(engine, path)
    _, resources, _ = inventory([path])
    with engine.begin() as connection:
        assert check_resource_values(connection, resources) == []
        connection.execute(encounters.update().where(encounters.c.id == "encounter-1").values(patient_id="patient-2"))
        assert check_resource_values(connection, resources) == ["1 Encounter rows have source value or patient-link mismatches"]


def test_validation_detects_wrong_medication_code(engine, tmp_path):
    from careflow.validation import check_medication_codes, inventory
    path = generate(tmp_path, extended=True)
    ingest(engine, path)
    _, resources, _ = inventory([path])
    with engine.begin() as connection:
        assert check_medication_codes(connection, resources) == []
        connection.execute(medications.update().where(medications.c.id == "medication-1").values(code="wrong"))
        assert len(check_medication_codes(connection, resources)) == 1


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL needed to test schema protection")
def test_validation_never_reuses_existing_schema(engine, tmp_path):
    from careflow.validation import validate
    from sqlalchemy.exc import ProgrammingError
    path = generate(tmp_path, extended=True)
    ingest(engine, path)
    before = report(engine)
    schema = engine.get_execution_options()["schema_translate_map"][None]
    with pytest.raises(ProgrammingError):
        validate(tmp_path, os.environ["TEST_DATABASE_URL"], schema, tmp_path / "results")
    assert report(engine) == before


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL needed for dbt integration")
def test_warehouse_metrics_and_dashboard(engine, tmp_path, monkeypatch):
    from careflow.analytics import build_warehouse, dashboard
    schema = engine.get_execution_options()["schema_translate_map"][None]
    monkeypatch.setenv("DBT_RAW_SCHEMA", schema)
    monkeypatch.setenv("DBT_TARGET_SCHEMA", schema + "_analytics")
    path = generate(tmp_path, extended=True)
    bundle = json.loads(path.read_text())
    duplicate = json.loads(json.dumps(next(e for e in bundle["entry"] if e["resource"].get("id") == "condition-1")))
    duplicate["resource"]["id"] = "duplicate-condition"
    refuted = json.loads(json.dumps(duplicate))
    refuted["resource"]["id"] = "refuted-condition"
    refuted["resource"]["subject"]["reference"] = "Patient/patient-3"
    refuted["resource"]["verificationStatus"]["coding"][0]["code"] = "refuted"
    cancelled = encounter("cancelled", "Patient/patient-1", status="cancelled")
    boundary = encounter("boundary", "Patient/patient-1", period={
        "start": "2025-02-01T00:30:00+01:00", "end": "2025-02-01T01:00:00+01:00"})
    bundle["entry"].extend([duplicate, refuted, cancelled, boundary])
    path.write_text(json.dumps(bundle))
    ingest(engine, path)
    build_warehouse(os.environ["TEST_DATABASE_URL"])
    with engine.connect() as connection:
        row = connection.execute(text(f'SELECT * FROM "{schema}_analytics".mart_monthly_utilization')).mappings().one()
        assert str(row["encounter_month"]) == "2025-01-01"
        assert row["encounter_count"] == 4
        assert row["unique_patients"] == 3
        assert float(row["average_duration_minutes"]) == 52.5
        rows = connection.execute(text(f'SELECT * FROM "{schema}_analytics".mart_condition_prevalence')).mappings().all()
        diabetes = next(row for row in rows if row["code"] == "44054006")
        assert diabetes["affected_patients"] == 2
        assert diabetes["total_patients"] == 3
        assert float(diabetes["prevalence_fraction"]) == pytest.approx(2 / 3)
    output = dashboard(engine, tmp_path / "dashboard.html", schema=schema + "_analytics")
    assert "Diabetes mellitus type 2" in output.read_text()
    assert "3 recorded requests" in output.read_text()
    from careflow.analytics import ROOT
    from careflow.public_export import export_public_snapshot
    public = export_public_snapshot(engine, tmp_path / "public.json", raw_schema=schema,
                                    analytics_schema=schema + "_analytics",
                                    dbt_artifacts=ROOT / "warehouse" / "target",
                                    synthetic_confirmed=True, cohort_label="Synthetic integration cohort")
    exported = json.loads(public.read_text())
    assert exported["current"]["label"] == "Synthetic integration cohort"
    assert exported["current"]["quality"]["checks_passed"] == 32
    assert exported["current"]["counts"]["patients"] == 3
    assert all(row["group"] in {"clinical", "social_context", "history", "care_context", "unclassified"}
               for row in exported["current"]["conditions"])
    assert exported["schema_version"] == 2
    assert exported["current"]["counts"]["quarantine"] == 1
    assert exported["current"]["monthly_utilization"][0]["encounter_count"] == 4
    assert exported["assistant"]["execution"] == "exported_replay"
    assert exported["assistant"]["examples"][0]["rows"][0]["value"] == 4
    assert exported["forecast"]["predictions"] is None
    assert "patient-1" not in public.read_text()
    assert "source_path" not in public.read_text()
    if engine.url.password:
        assert engine.url.password not in public.read_text()
