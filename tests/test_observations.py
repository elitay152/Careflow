from copy import deepcopy
from datetime import timedelta
import json

import pytest
from sqlalchemy import select

from test_pipeline import engine, patient, write_bundle
from careflow.db import observations
from careflow.observations import normalize_observation
from careflow.pipeline import ingest
from careflow.clinical_features import add_measurements, CLINICAL_FEATURES
from careflow.hospitalization import moment
from careflow.validation import inventory, observation_matches


def measurement(code="39156-5", value=26, unit="kg/m2", **extra):
    return {
        "resourceType": "Observation",
        "id": "o1",
        "status": "final",
        "subject": {"reference": "Patient/p1"},
        "code": {"coding": [{"system": "http://loinc.org", "code": code}]},
        "effectiveDateTime": "2024-01-01T00:00:00Z",
        "issued": "2024-01-02T00:00:00Z",
        "valueQuantity": {
            "value": value,
            "system": "http://unitsofmeasure.org",
            "code": unit,
        },
        **extra,
    }


def test_ingestion_identity_patient_order_provenance_and_independent_oracle(
    engine, tmp_path
):
    source = measurement()
    path = write_bundle(tmp_path, [{"resource": source}, patient()])
    first = ingest(engine, path)
    assert (first["accepted"], first["rejected"]) == (2, 0)
    assert ingest(engine, path)["status"] == "skipped"
    assert ingest(engine, path, reprocess=True)["accepted"] == 2
    with engine.connect() as connection:
        stored = connection.execute(select(observations)).mappings().one()
        assert stored["patient_id"] == "p1" and stored["source_hash"]
        assert observation_matches(stored, source)
        assert not observation_matches(
            {**stored, "measurements": {"bmi": {"value": 99}}}, source
        )
    counts, resources, _ = inventory([path])
    assert set(resources["Observation"]) == {"o1"}
    assert counts["Observation"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"effectiveDateTime": "2024-01-01"},
        {"effectiveDateTime": "2024-01-01T00:00:00"},
        {"issued": "invalid"},
        {"effectivePeriod": {"start": "2024-01-01T00:00:00Z"}},
        {
            "valueQuantity": {
                "value": True,
                "system": "http://unitsofmeasure.org",
                "code": "kg/m2",
            }
        },
        {
            "valueQuantity": {
                "value": -1,
                "system": "http://unitsofmeasure.org",
                "code": "kg/m2",
            }
        },
        {"valueQuantity": {"value": 25, "system": "other", "code": "kg/m2"}},
        {
            "valueQuantity": {
                "value": 25,
                "system": "http://unitsofmeasure.org",
                "code": "mg/dL",
            }
        },
        {
            "valueQuantity": {
                "value": 25,
                "system": "http://unitsofmeasure.org",
                "code": "kg/m2",
                "comparator": "<",
            }
        },
        {"valueString": "twenty"},
        {"dataAbsentReason": {"text": "unknown"}},
        {"status": "invalid"},
        {"subject": {"reference": "Patient/missing"}},
    ],
)
def test_invalid_selected_measurements_are_quarantined(engine, tmp_path, change):
    source = measurement(**change)
    result = ingest(engine, write_bundle(tmp_path, [patient(), {"resource": source}]))
    assert result["rejected"] == 1
    with engine.connect() as connection:
        assert connection.execute(select(observations)).first() is None


def test_creatinine_conversion_missing_issued_and_explicit_absence():
    source = measurement("2160-0", 88.4, "umol/L")
    normalized = normalize_observation(source)
    assert normalized["measurements"]["creatinine"] == {
        "value": 1.0,
        "unit": "mg/dL",
        "source_unit": "umol/L",
    }
    source.pop("issued")
    assert normalize_observation(source)["available_at"] is None
    source.pop("valueQuantity")
    source["dataAbsentReason"] = {"coding": [{"code": "unknown"}]}
    assert normalize_observation(source)["measurements"] == {}


def test_bp_panel_unit_distinction_and_ambiguous_codes():
    source = measurement("85354-9")
    source.pop("valueQuantity")
    source["component"] = [
        {
            "code": {"coding": [{"system": "http://loinc.org", "code": code}]},
            "valueQuantity": {
                "value": value,
                "system": "http://unitsofmeasure.org",
                "code": "mm[Hg]",
            },
        }
        for code, value in [("8480-6", 120), ("8462-4", 80)]
    ]
    values = normalize_observation(source)["measurements"]
    assert values["systolic"]["value"] == 120 and values["diastolic"]["value"] == 80
    source["component"].append(source["component"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_observation(source)
    assert (
        "egfr_unindexed_unit"
        in normalize_observation(measurement("33914-3", 50, "mL/min"))["measurements"]
    )
    source = measurement()
    source["code"]["coding"].append({"system": "http://loinc.org", "code": "4548-4"})
    with pytest.raises(ValueError, match="one supported"):
        normalize_observation(source)


def test_measurements_ignore_future_issuance_stale_corrected_and_same_day_records():
    row = {"patient_id": "p1", "index_date": "2024-07-01", "features": {}}

    def event(identifier, effective, issued, status="final", value=25):
        return {
            "id": identifier,
            "patient_id": "p1",
            **normalize_observation(
                measurement(
                    value=value,
                    effectiveDateTime=effective,
                    issued=issued,
                    status=status,
                )
            ),
        }

    events = [
        event("old", "2023-09-01T00:00:00Z", "2023-09-02T00:00:00Z", value=20),
        event("latest", "2024-06-01T00:00:00Z", "2024-06-02T00:00:00Z", value=25),
        event("future", "2024-06-15T00:00:00Z", "2024-07-02T00:00:00Z", value=99),
        event("same", "2024-07-01T00:00:00Z", "2024-07-01T00:00:00Z", value=99),
        event(
            "corrected", "2024-06-20T00:00:00Z", "2024-06-21T00:00:00Z", "corrected", 99
        ),
        event("stale", "2022-06-01T00:00:00Z", "2022-06-02T00:00:00Z", value=99),
    ]
    values = add_measurements([row], events)[0]["features"]
    assert values["bmi_latest"] == 25 and values["bmi_age_days"] == 30
    assert values["bmi_trend_90d"] == pytest.approx(5 * 90 / 274)
    assert values["hba1c_missing"] == 1 and values["hba1c_latest"] is None
    assert set(values) == set(CLINICAL_FEATURES)
    assert add_measurements([row], list(reversed(events)))[0]["features"] == values
    events[2]["measurements"]["bmi"]["value"] = 123
    assert add_measurements([row], events)[0]["features"] == values
    assert row["features"] == {}


def test_nonfinite_json_is_rejected_without_database_changes(engine, tmp_path):
    from careflow.pipeline import report

    with pytest.raises(ValueError, match="finite"):
        normalize_observation(measurement(value=float("nan")))
    with pytest.raises(ValueError, match="Non-finite JSON"):
        ingest(
            engine,
            write_bundle(
                tmp_path, [patient(), {"resource": measurement(value=float("nan"))}]
            ),
        )
    assert all(count == 0 for count in report(engine)["counts"].values())


def test_issued_before_effective_never_makes_a_future_measurement_available():
    source = measurement(
        effectiveDateTime="2024-01-03T01:00:00+01:00", issued="2024-01-01T00:00:00Z"
    )
    parsed = normalize_observation(source)
    assert parsed["available_at"] == "2024-01-03T00:00:00+00:00"
    row = {"patient_id": "p1", "index_date": "2024-01-02", "features": {}}
    assert (
        add_measurements([row], [{"id": "o1", "patient_id": "p1", **parsed}])[0][
            "features"
        ]["bmi_missing"]
        == 1
    )


def test_unindexed_gfr_and_unissued_values_do_not_become_features():
    rows = [{"patient_id": "p1", "index_date": "2024-07-01", "features": {}}]
    source = measurement("33914-3", 50, "mL/min")
    one = {"id": "o1", "patient_id": "p1", **normalize_observation(source)}
    source = measurement()
    source.pop("issued")
    two = {"id": "o2", "patient_id": "p1", **normalize_observation(source)}
    values = add_measurements(rows, [one, two])[0]["features"]
    assert values["egfr_missing"] == 1 and values["bmi_missing"] == 1
