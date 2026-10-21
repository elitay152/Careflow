import json

import pytest
from sqlalchemy import create_engine, select, text

from careflow.db import connect, encounters, facilities, facility_aliases, patients
from careflow.pipeline import ingest
from test_pipeline import engine, patient, encounter, write_bundle


def organization(identifier="org1", value="registry1"):
    return {"fullUrl": f"urn:uuid:{identifier}", "resource": {
        "resourceType": "Organization", "id": identifier, "name": "Example Hospital",
        "identifier": [{"system": "https://example.test", "value": value}],
        "address": [{"city": "Boston", "state": "MA", "postalCode": "02115", "country": "US"}],
        "extension": [{"url": "http://synthetichealth.github.io/synthea/bed-count-extension", "valueInteger": 100}]}}


def test_enrichment_preserves_identity_and_resolves_conditional_provider_reference(engine, tmp_path):
    visit = encounter(**{"class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "IMP"},
                         "serviceProvider": {"reference": "Organization?identifier=https://example.test|registry1"},
                         "location": [{"location": {"reference": "Location/ward1"}}],
                         "type": [{"coding": [{"system": "http://snomed.info/sct", "code": "123"}]}]})
    path = write_bundle(tmp_path, [patient(deceasedDateTime="2025-01-02T00:00:00Z"), visit])
    assert ingest(engine, path)["accepted"] == 2
    org_path = write_bundle(tmp_path, [organization()], "org.json")
    assert ingest(engine, org_path)["accepted"] == 1
    assert ingest(engine, path, reprocess=True)["accepted"] == 2
    with engine.connect() as c:
        row = c.execute(select(encounters)).mappings().one()
        assert row["class_code"] == "IMP"
        assert row["location_references"] == ["Location/ward1"]
        assert row["type_codes"] == [{"system": "http://snomed.info/sct", "code": "123"}]
        assert c.scalar(select(facility_aliases.c.facility_id).where(facility_aliases.c.id == row["provider_reference"])) == "org1"
        assert c.scalar(select(patients.c.death_date)) == "2025-01-02"
        assert c.scalar(select(facilities.c.bed_count)) == 100


def test_ambiguous_provider_alias_cannot_reassign_a_facility(engine, tmp_path):
    assert ingest(engine, write_bundle(tmp_path, [organization()]))["accepted"] == 1
    result = ingest(engine, write_bundle(tmp_path, [organization("org2")], "other.json"))
    assert result["rejected"] == 1
    with engine.connect() as c:
        assert list(c.scalars(select(facilities.c.id))) == ["org1"]
        assert set(c.scalars(select(facility_aliases.c.facility_id))) == {"org1"}


@pytest.mark.parametrize("context", [{"class": {"code": "IMP"}}, {"location": [{}]}, {"type": "bad"}, {"serviceProvider": []}])
def test_malformed_optional_encounter_context_is_quarantined(engine, tmp_path, context):
    result = ingest(engine, write_bundle(tmp_path, [patient(), encounter(**context)]))
    assert result["accepted"] == 1 and result["rejected"] == 1


def test_additive_migration_is_repeatable_and_preserves_legacy_rows(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    old = create_engine(url)
    with old.begin() as c:
        c.execute(text('CREATE TABLE patients (id varchar PRIMARY KEY, birth_date varchar NOT NULL, gender varchar NOT NULL, source_hash varchar NOT NULL)'))
        c.execute(text("INSERT INTO patients VALUES ('p1','1990-01-01','unknown','hash')"))
        c.execute(text('CREATE TABLE encounters (id varchar PRIMARY KEY, patient_id varchar NOT NULL, status varchar NOT NULL, started_at varchar NOT NULL, ended_at varchar, source_hash varchar NOT NULL)'))
    old.dispose()
    for _ in range(2):
        engine = connect(url)
        with engine.connect() as c:
            row = c.execute(select(patients)).mappings().one()
            assert row["id"] == "p1" and row["deceased"] is None and row["death_date"] is None
            assert "class_code" in encounters.c
        engine.dispose()


def test_schema_replay_option_refuses_sqlite_and_unsafe_identifiers(monkeypatch, capsys):
    from careflow.cli import main
    for schema in ("safe_schema", "public;drop"):
        monkeypatch.setattr("sys.argv", ["careflow", "--database-url", "sqlite://", "ingest", "demo.json", "--schema", schema])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2
        assert "--schema requires PostgreSQL" in capsys.readouterr().err
