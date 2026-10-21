import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from careflow.utilization import CHRONIC_CODES, utilization_details
from careflow.condition_groups import condition_group
from careflow.segmentation import export_segments


def test_selected_chronic_codes_are_distinct_explicit_clinical_concepts():
    assert len(set(CHRONIC_CODES)) == len(CHRONIC_CODES) == 19
    assert all(condition_group("http://snomed.info/sct", code) == "clinical" for code in CHRONIC_CODES)
    assert "5251000175109" not in CHRONIC_CODES
    assert "84114007" not in CHRONIC_CODES


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL percentiles required")
def test_kpis_use_distinct_people_and_encounter_level_median_with_explicit_window():
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    schema = "test_utilization_" + uuid4().hex
    try:
        with engine.begin() as c:
            c.execute(text(f'CREATE SCHEMA "{schema}"'))
            c.execute(text(f'''CREATE TABLE "{schema}".fct_encounters (
                patient_id text, started_at timestamptz, encounter_month date,
                duration_minutes numeric, status text)'''))
            c.execute(text(f'''INSERT INTO "{schema}".fct_encounters VALUES
                ('p1','2024-02-01','2024-02-01',30,'finished'),
                ('p1','2025-01-01','2025-01-01',60,'finished'),
                ('p2','2025-01-02','2025-01-01',6000,'finished'),
                ('p2','2025-01-03','2025-01-01',null,'finished'),
                ('p3','2025-01-04','2025-01-01',999,'planned'),
                ('p4','2024-01-01','2024-01-01',100,'finished')'''))
            c.execute(text(f'''CREATE TABLE "{schema}".fct_conditions (
                patient_id text, code text, code_system text,
                clinical_status text, verification_status text)'''))
            c.execute(text(f'''INSERT INTO "{schema}".fct_conditions VALUES
                ('p1','44054006','http://snomed.info/sct','active','confirmed'),
                ('p1','59621000','http://snomed.info/sct','active','confirmed'),
                ('p2','44054006','http://snomed.info/sct','active','refuted'),
                ('p3','5251000175109','http://snomed.info/sct','active','confirmed')'''))
            annual, summary, chronic = utilization_details(c, f'"{schema}"', [{"encounter_month": "2025-01-01"}], 4)
            assert summary["start_month"] == "2024-02-01"
            assert summary["end_month_exclusive"] == "2025-02-01"
            assert summary["encounter_count"] == 4
            assert summary["unique_patients"] == 2
            assert summary["encounters_per_seen_patient"] == 2
            assert summary["cohort_share"] == 0.5
            assert summary["measured_duration_count"] == 3
            assert summary["median_duration_minutes"] == 60
            assert summary["average_duration_minutes"] == 2030
            assert annual[-1]["unique_patients"] == 2
            assert annual[-1]["median_duration_minutes"] == 3030
            assert chronic["affected_patients"] == 1
            c.execute(text(f'CREATE TABLE "{schema}".dim_patients (patient_id text, birth_date date, gender text)'))
            c.execute(text(f'''INSERT INTO "{schema}".dim_patients VALUES
                ('p1','1960-01-31','female'),('p2','2010-02-01','male'),
                ('p3',null,'unknown'),('p4','2030-01-01','other')'''))
            c.execute(text(f'CREATE TABLE "{schema}".fct_medication_requests (patient_id text, status text)'))
            c.execute(text(f'''INSERT INTO "{schema}".fct_medication_requests VALUES
                ('p1','active'),('p1','active'),('p2','completed')'''))
            segments = export_segments(c, f'"{schema}"', summary)
            assert segments["age_as_of"] == "2025-01-31"
            cells = segments["cells"]
            assert sum(row["patient_count"] for row in cells) == 4
            assert sum(row["seen_patients"] for row in cells) == 2
            assert sum(row["encounter_count"] for row in cells) == 4
            assert sum(row["active_request_count"] for row in cells) == 2
            diabetic = next(row for row in cells if row["clinical_profile"] == "diabetes_and_hypertension")
            assert diabetic["age_band"] == "65_79"
            assert diabetic["encounter_count"] == diabetic["active_request_count"] == 2
            assert sum(row["patient_count"] for row in cells if row["age_band"] == "unknown") == 2
            assert sum(row["patient_count"] for row in cells if row["clinical_profile"] == "no_selected_chronic") == 3
            assert all(set(row) == {"age_band", "gender", "clinical_profile", "utilization_band", "patient_count", "seen_patients", "encounter_count", "active_request_count"} for row in cells)
            assert export_segments(c, f'"{schema}"', None) is None
    finally:
        with engine.begin() as c:
            c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
