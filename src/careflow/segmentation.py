from datetime import date, timedelta

from sqlalchemy import text

from .utilization import CHRONIC_CODES


def export_segments(connection, warehouse, summary):
    if summary is None:
        return None
    start, end = summary["start_month"], summary["end_month_exclusive"]
    cells = connection.execute(text(f'''
        WITH visits AS (
            SELECT patient_id, count(*) AS encounters
            FROM {warehouse}.fct_encounters
            WHERE status='finished' AND encounter_month >= cast(:start as date)
                  AND encounter_month < cast(:end as date)
            GROUP BY patient_id
        ), clinical AS (
            SELECT patient_id,
                   bool_or(code='44054006') AS diabetes,
                   bool_or(code='59621000') AS hypertension,
                   bool_or(code=ANY(:codes)) AS selected_chronic
            FROM {warehouse}.fct_conditions
            WHERE code_system='http://snomed.info/sct'
                  AND clinical_status in ('active','recurrence','relapse')
                  AND verification_status='confirmed'
            GROUP BY patient_id
        ), prescriptions AS (
            SELECT patient_id, count(*) AS active_requests
            FROM {warehouse}.fct_medication_requests WHERE status='active'
            GROUP BY patient_id
        ), patient_metrics AS (
            SELECT p.patient_id, p.gender,
                   CASE WHEN p.birth_date <= cast(:end as date)-1
                        THEN extract(year from age(cast(:end as date)-1,p.birth_date)) END AS age,
                   CASE WHEN c.diabetes AND c.hypertension THEN 'diabetes_and_hypertension'
                        WHEN c.diabetes THEN 'diabetes_without_hypertension'
                        WHEN c.hypertension THEN 'hypertension_without_diabetes'
                        WHEN c.selected_chronic THEN 'other_selected_chronic'
                        ELSE 'no_selected_chronic' END AS clinical_profile,
                   coalesce(v.encounters,0) AS encounters,
                   coalesce(rx.active_requests,0) AS active_requests
            FROM {warehouse}.dim_patients p
            LEFT JOIN visits v USING (patient_id)
            LEFT JOIN clinical c USING (patient_id)
            LEFT JOIN prescriptions rx USING (patient_id)
        ), grouped AS (
            SELECT *,
                   CASE WHEN age IS NULL THEN 'unknown'
                        WHEN age < 18 THEN '0_17' WHEN age < 35 THEN '18_34'
                        WHEN age < 50 THEN '35_49' WHEN age < 65 THEN '50_64'
                        WHEN age < 80 THEN '65_79' ELSE '80_plus' END AS age_band,
                   CASE WHEN encounters=0 THEN 'none' WHEN encounters=1 THEN 'one'
                        WHEN encounters <= 5 THEN 'two_to_five' ELSE 'six_plus' END AS utilization_band
            FROM patient_metrics
        )
        SELECT age_band, gender, clinical_profile, utilization_band,
               count(*) AS patient_count,
               count(*) FILTER (WHERE encounters>0) AS seen_patients,
               sum(encounters)::bigint AS encounter_count,
               sum(active_requests)::bigint AS active_request_count
        FROM grouped GROUP BY age_band,gender,clinical_profile,utilization_band
        ORDER BY age_band,gender,clinical_profile,utilization_band
    '''), {"start": start, "end": end, "codes": list(CHRONIC_CODES)}).mappings().all()
    return {"start_month": start, "end_month_exclusive": end,
            "age_as_of": (date.fromisoformat(end)-timedelta(days=1)).isoformat(),
            "cells": [dict(row) for row in cells]}
