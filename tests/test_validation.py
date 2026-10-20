import json
import pytest

from careflow.validation import expected_metrics, inventory, validate


def test_inventory_counts_auxiliary_and_unsupported_resources(tmp_path):
    records = [{"resourceType": kind, "id": identifier} for kind, identifier in [
        ("Patient", "p1"), ("Medication", "m1"), ("Observation", "o1")]]
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"resourceType": "Bundle", "entry": [{"resource": r} for r in records]}))
    counts, resources, manifest = inventory([path])
    assert dict(counts) == {"Patient": 1, "Medication": 1, "Observation": 1}
    assert set(resources["Patient"]) == {"p1"}
    assert set(resources["Medication"]) == {"m1"}
    assert manifest[0]["bytes"] == path.stat().st_size
    assert len(manifest[0]["sha256"]) == 64
    assert inventory([path])[2] == manifest


def test_source_metric_oracle_handles_statuses_duplicates_and_utc():
    def encounter(status):
        return {"status": status, "subject": {"reference": "Patient/p1"},
                "period": {"start": "2025-02-01T00:30:00+01:00", "end": "2025-02-01T01:00:00+01:00"}}
    def condition(status):
        return {"subject": {"reference": "Patient/p1"}, "code": {"coding": [{"system": "test", "code": "c1"}]},
                "clinicalStatus": {"coding": [{"code": "active"}]},
                "verificationStatus": {"coding": [{"code": status}]}}
    monthly, prevalence = expected_metrics({
        "Encounter": {"e1": encounter("finished"), "e2": encounter("finished"), "e3": encounter("cancelled")},
        "Condition": {"c1": condition("confirmed"), "c2": condition("confirmed"), "c3": condition("refuted")},
    })
    assert set(monthly) == {"2025-01-01"}
    assert monthly["2025-01-01"] == {"count": 2, "patients": {"Patient/p1"}, "minutes": [30, 30]}
    assert prevalence == {("test", "c1"): {"Patient/p1"}}


@pytest.mark.parametrize("schema", ["public;drop schema public", "public-name", "A" * 64])
def test_validation_rejects_unsafe_schema_before_connecting(tmp_path, schema):
    with pytest.raises(ValueError, match="Schema"):
        validate(tmp_path, "postgresql+psycopg://unused", schema, tmp_path / "results")
    assert not (tmp_path / "results").exists()
