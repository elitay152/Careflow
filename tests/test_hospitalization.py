from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from test_pipeline import engine

from careflow.hospitalization import FEATURES, make_snapshots, moment, partition, inpatient_episodes


def pid(split="train"):
    return next(f"p{i}" for i in range(1000) if partition(f"p{i}") == split)


def cohort():
    return [{"id": pid(), "birth_date": "1980-01-01", "death_date": None, "deceased": None}], [visit("2014-01-01")], []


def visit(start, end=None, klass="AMB", status="finished"):
    return {"patient_id": pid(), "started_at": start+"T00:00:00Z" if len(start)==10 else start,
            "ended_at": (end+"T00:00:00Z" if len(end)==10 else end) if end else start+"T01:00:00Z",
            "class_code": klass, "class_system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "status": status}


def snapshot(patients, encounters, diagnoses, index="2020-10-01"):
    rows, summary = make_snapshots(patients, encounters, diagnoses, "2025-01-01")
    return next((r for r in rows if r["index_date"] == index), None), summary


def test_only_completed_prior_visits_and_previously_recorded_diagnoses_are_features():
    patients, encounters, diagnoses = cohort()
    encounters += [visit("2020-09-01", klass="EMER"), visit("2020-09-30", "2020-10-02"), visit("2020-10-02")]
    diagnoses += [{"patient_id": pid(), "onset_date": "2010-01-01", "recorded_date": "2020-10-01", "code_system": "http://snomed.info/sct", "code": "44054006", "clinical_status": "active"}]
    row, _ = snapshot(patients, encounters, diagnoses)
    assert row["features"]["encounters_365d"] == row["features"]["emergency_365d"] == 1
    assert row["features"]["recorded_diabetes"] == 0
    diagnoses[0]["recorded_date"] = "2020-09-30"
    dated, _ = snapshot(patients, encounters, diagnoses)
    assert dated["features"]["recorded_diabetes"] == 1
    diagnoses[0]["clinical_status"] = "resolved"
    assert snapshot(patients, encounters, diagnoses)[0]["features"] == dated["features"]
    encounters[-1]["started_at"] = "2021-01-01T00:00:00Z"
    encounters[-1]["ended_at"] = "2021-01-01T01:00:00Z"
    assert snapshot(patients, encounters, diagnoses)[0]["features"] == dated["features"]


def test_overlapping_and_touching_stays_are_one_episode_but_readmissions_are_distinct():
    events = [{"start": moment(start), "end": moment(end), "class_code": "IMP", "class_system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "status": "finished"} for start,end in [("2020-09-01","2020-09-05"),("2020-09-03","2020-09-06"),("2020-09-06","2020-09-07"),("2020-09-08","2020-09-09")]]
    episodes = inpatient_episodes(events)
    assert len(episodes) == 2
    assert episodes[0] == (moment("2020-09-01"), moment("2020-09-07"))
    events[0]["end"] = None
    assert inpatient_episodes(events) == [(moment("2020-09-01"), None)]


def test_outcome_window_is_strict_after_index_and_inclusive_at_ninety_days():
    patients, encounters, diagnoses = cohort()
    end = moment("2020-10-01") + timedelta(days=90)
    for offset, expected in ((0,1),(1,0)):
        start=end+timedelta(seconds=offset)
        admission=visit(start.isoformat(), (start+timedelta(days=1)).isoformat(), "IMP")
        row,_=snapshot(patients,encounters+[admission],diagnoses)
        assert row["outcome"] == expected
    row,summary=snapshot(patients,encounters+[visit("2020-10-01","2020-10-03","IMP")],diagnoses)
    assert row is None and summary["exclusions"]["hospitalized_at_index"] == 1


def test_death_and_followup_are_not_negative_labels_by_default():
    patients, encounters, diagnoses = cohort()
    patients[0].update(death_date="2020-10-20",deceased=True)
    assert snapshot(patients,encounters,diagnoses)[0] is None
    row,_=snapshot(patients,encounters+[visit("2020-10-10","2020-10-12","IMP")],diagnoses)
    assert row["outcome"] == 1
    patients[0]["death_date"]=None
    rows,summary=make_snapshots(patients,encounters,diagnoses,"2025-01-01")
    assert rows == [] and summary["exclusions"]["unknown_death_date"] == 40


def test_feature_matrix_and_aggregate_readiness_never_share_patient_rows():
    patients,encounters,diagnoses=cohort()
    rows,summary=make_snapshots(patients,encounters,diagnoses,"2025-01-01")
    assert summary["status"] == "insufficient_data"
    assert summary["models"] == []
    assert set(rows[0]["features"]) == set(FEATURES)
    assert "patient_id" not in str(summary)
    assert summary["candidate_snapshots"] == summary["eligible_snapshots"] + sum(n for k,n in summary["exclusions"].items() if k!="outside_split")
    assert summary["assigned_snapshots"] == summary["eligible_snapshots"]-summary["exclusions"]["outside_split"]


def test_model_thresholds_and_validation_are_unchanged_when_test_labels_change():
    pytest.importorskip("sklearn")
    from careflow.risk_model import evaluate_models
    rows=[]
    for split,index in (("train","2020-10-01"),("validation","2022-10-01"),("test","2024-10-01")):
        for i in range(100):
            features={f:0 for f in FEATURES}
            features.update(age_years=20+i%60,encounters_90d=i%7,encounters_365d=i%7,inpatient_episodes_365d=int(i%5==0))
            rows.append({"patient_id":f"{split}-{i}","index_date":index,"split":split,"outcome":int(i%7>=5),"features":features})
    first=evaluate_models(rows)
    changed=deepcopy(rows)
    for row in changed:
        if row["split"]=="test":row["outcome"]=1-row["outcome"]
    second=evaluate_models(changed)
    for a,b in zip(first,second,strict=True):
        assert a["threshold"]==b["threshold"] and a["validation"]==b["validation"]
        assert sum(group["snapshots"] for group in a["calibration"])==100
    changed[-1]["patient_id"]=rows[0]["patient_id"]
    with pytest.raises(ValueError,match="overlap"):
        evaluate_models(changed)
    changed[-1]["patient_id"]="other"
    changed[0]["index_date"]="2024-01-01"
    with pytest.raises(ValueError,match="chronological"):
        evaluate_models(changed)


def test_private_outputs_cannot_be_written_to_a_public_or_tracked_directory(tmp_path):
    from careflow.hospitalization import run_audit
    with pytest.raises(ValueError,match="ignored data/risk"):
        run_audit(None,"public","2025-01-01",tmp_path)


def test_independent_cohort_cannot_change_the_fitted_model_or_thresholds():
    pytest.importorskip("sklearn")
    from careflow.risk_model import evaluate_models
    rows=[]
    for split,index in (("train","2020-10-01"),("validation","2022-10-01"),("test","2024-10-01")):
        for i in range(40):
            features={f:0 for f in FEATURES};features.update(age_years=30+i,encounters_90d=i%5)
            rows.append({"patient_id":f"{split}-{i}","index_date":index,"split":split,"outcome":int(i%5==4),"features":features})
    external=[{**row,"patient_id":"external-"+row["patient_id"],"split":"external"} for row in rows if row["split"]=="test"]
    first=evaluate_models(rows);second=evaluate_models(rows,external)
    for a,b in zip(first,second,strict=True):
        assert a["threshold"]==b["threshold"] and a["test"]==b["test"] and a["validation"]==b["validation"]
        assert b["external"]==b["test"]


def test_recorded_diagnosis_date_survives_ingestion_and_bad_dates_are_quarantined(engine,tmp_path):
    from sqlalchemy import select
    from careflow.db import conditions
    from careflow.pipeline import ingest
    from test_pipeline import patient,write_bundle
    condition={"resource":{"resourceType":"Condition","id":"c1","subject":{"reference":"Patient/p1"},
        "code":{"coding":[{"system":"http://snomed.info/sct","code":"44054006"}]},
        "onsetDateTime":"2020-01-01T00:00:00Z","recordedDate":"2020-02-02T01:00:00+02:00"}}
    assert ingest(engine,write_bundle(tmp_path,[patient(),condition]))["accepted"]==2
    with engine.connect() as c:assert c.scalar(select(conditions.c.recorded_date))=="2020-02-01"
    condition["resource"]["recordedDate"]="invalid"
    assert ingest(engine,write_bundle(tmp_path,[condition],"bad-date.json"))["rejected"]==1


def test_private_output_base_cannot_be_redirected_to_a_public_directory(tmp_path,monkeypatch):
    import careflow.hospitalization as module
    public=tmp_path/'public';public.mkdir()
    private=tmp_path/'risk';private.symlink_to(public,target_is_directory=True)
    monkeypatch.setattr(module,'PRIVATE_ROOT',private)
    with pytest.raises(ValueError,match="ignored data/risk"):
        module.run_audit(None,'public','2025-01-01',private/'cohort')
