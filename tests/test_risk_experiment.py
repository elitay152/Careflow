from copy import deepcopy

from careflow.risk_experiment import ADDED_FEATURES, enrich
from careflow.hospitalization import FEATURES


def test_enrichment_excludes_future_ongoing_and_wrong_system_events():
    row = {
        "patient_id": "p",
        "index_date": "2024-01-01",
        "split": "test",
        "outcome": 0,
        "features": dict.fromkeys(FEATURES, 0),
    }

    def encounter(
        start,
        end,
        klass="AMB",
        system="http://terminology.hl7.org/CodeSystem/v3-ActCode",
    ):
        return dict(
            patient_id="p",
            started_at=start,
            ended_at=end,
            status="finished",
            class_code=klass,
            class_system=system,
        )

    visits = [
        encounter("2023-12-15", "2023-12-18", "IMP"),
        encounter("2023-12-30", "2023-12-31", "EMER"),
        encounter("2023-12-31", "2024-01-02", "IMP"),
        encounter("2024-01-02", "2024-01-03", "EMER"),
        encounter("2023-12-20", "2023-12-21", "EMER", "unrecognized"),
    ]
    diagnoses = [
        dict(
            patient_id="p",
            recorded_date="2023-12-10",
            onset_date="2020-01-01",
            code="88805009",
            code_system="http://snomed.info/sct",
        ),
        dict(
            patient_id="p",
            recorded_date="2024-01-01",
            onset_date="2020-01-01",
            code="431855005",
            code_system="http://snomed.info/sct",
        ),
    ]
    values = enrich([row], visits, diagnoses)[0]["features"]
    assert values["encounters_30d"] == 3
    assert values["emergency_30d"] == 1
    assert values["inpatient_days_365d"] == 3
    assert values["days_since_discharge"] == 14
    assert (
        values["recorded_heart_failure"] == 1 and values["recorded_kidney_disease"] == 0
    )
    assert values["new_condition_codes_90d"] == 1
    assert set(values) == set(FEATURES + ADDED_FEATURES)
    changed = deepcopy(visits)
    changed[-2]["ended_at"] = "2025-01-01"
    assert enrich([row], changed, diagnoses)[0]["features"] == values
    assert row["features"] == dict.fromkeys(FEATURES, 0)


def test_new_condition_count_uses_first_recording_and_clips_inpatient_days():
    row = {
        "patient_id": "p",
        "index_date": "2024-01-01",
        "features": dict.fromkeys(FEATURES, 0),
    }
    visits = [
        dict(
            patient_id="p",
            started_at="2022-12-01",
            ended_at="2023-01-03",
            status="finished",
            class_code="IMP",
            class_system="http://terminology.hl7.org/CodeSystem/v3-ActCode",
        )
    ]
    diagnoses = [
        dict(
            patient_id="p",
            recorded_date=d,
            onset_date="2020-01-01",
            code="88805009",
            code_system="http://snomed.info/sct",
        )
        for d in ("2020-01-01", "2023-12-01")
    ]
    result = enrich([row], visits, diagnoses)[0]["features"]
    assert result["inpatient_days_365d"] == 2
    assert result["new_condition_codes_90d"] == 0


def test_experiment_thresholds_do_not_use_test_outcomes():
    from careflow.risk_experiment import compare

    rows = []
    for split, index in (
        ("train", "2020-10-01"),
        ("validation", "2022-10-01"),
        ("test", "2024-10-01"),
    ):
        for i in range(100):
            features = dict.fromkeys(FEATURES + ADDED_FEATURES, 0)
            features["age_years"] = 20 + i
            rows.append(
                dict(
                    patient_id=f"{split}-{i}",
                    index_date=index,
                    split=split,
                    outcome=int(i % 10 == 0),
                    features=features,
                )
            )
    before = compare(rows)
    changed = deepcopy(rows)
    for row in changed:
        if row["split"] == "test":
            row["outcome"] = 1 - row["outcome"]
    after = compare(changed)
    assert len(before["results"]) == 4
    for a, b in zip(before["results"], after["results"], strict=True):
        assert a["threshold"] == b["threshold"] and a["validation"] == b["validation"]
    changed[-1]["patient_id"] = changed[0]["patient_id"]
    import pytest

    with pytest.raises(ValueError, match="Patient overlap"):
        compare(changed)


def test_clinical_comparison_handles_missing_values_without_test_fitting():
    from careflow.clinical_features import add_measurements
    from careflow.risk_experiment import compare

    rows = []
    for split, index in (
        ("train", "2020-10-01"),
        ("validation", "2022-10-01"),
        ("test", "2024-10-01"),
    ):
        for i in range(100):
            features = dict.fromkeys(FEATURES + ADDED_FEATURES, 0)
            features["age_years"] = 20 + i
            rows.append(
                dict(
                    patient_id=f"{split}-{i}",
                    index_date=index,
                    split=split,
                    outcome=int(i % 10 == 0),
                    features=features,
                )
            )
    rows = add_measurements(rows, [])
    before = compare(rows, clinical=True)
    changed = deepcopy(rows)
    for row in changed:
        if row["split"] == "test":
            row["features"]["bmi_latest"] = 99
            row["features"]["bmi_missing"] = 0
    after = compare(changed, clinical=True)
    assert len(before["results"]) == 6
    assert before["measurement_coverage"]["train"]["bmi"]["snapshots_with_value"] == 0
    for a, b in zip(before["results"], after["results"], strict=True):
        assert a["threshold"] == b["threshold"] and a["validation"] == b["validation"]
