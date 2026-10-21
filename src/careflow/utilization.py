from sqlalchemy import text

from .forecast import shift_month


CHRONIC_CODES = (
    "44054006", "59621000", "414545008", "55822004", "302870006",
    "431855005", "431856006", "433144002", "431857002", "46177005",
    "88805009", "195967001", "233678006", "185086009",
    "87433001", "69896004", "26929004", "230265002", "84757009",
)


def utilization_details(connection, warehouse, monthly, total_patients):
    annual = connection.execute(text(f'''
        SELECT date_trunc('year', started_at at time zone 'UTC')::date AS encounter_month,
               count(*) AS encounter_count, count(distinct patient_id) AS unique_patients,
               avg(duration_minutes) AS average_duration_minutes,
               percentile_cont(0.5) within group (order by duration_minutes) AS median_duration_minutes,
               count(duration_minutes) AS measured_duration_count
        FROM {warehouse}.fct_encounters WHERE status='finished'
        GROUP BY 1 ORDER BY 1
    ''')).mappings().all()
    summary = None
    if monthly:
        start, end = shift_month(monthly[-1]["encounter_month"], -11), shift_month(monthly[-1]["encounter_month"], 1)
        row = connection.execute(text(f'''
            SELECT count(*) AS encounter_count, count(distinct patient_id) AS unique_patients,
                   count(duration_minutes) AS measured_duration_count,
                   percentile_cont(0.5) within group (order by duration_minutes) AS median_duration_minutes,
                   avg(duration_minutes) AS average_duration_minutes
            FROM {warehouse}.fct_encounters
            WHERE status='finished' AND encounter_month >= cast(:start as date)
                  AND encounter_month < cast(:end as date)
        '''), {"start": start, "end": end}).mappings().one()
        summary = {**row, "start_month": start, "end_month_exclusive": end,
                   "encounters_per_seen_patient": row["encounter_count"] / row["unique_patients"] if row["unique_patients"] else None,
                   "cohort_share": row["unique_patients"] / total_patients if total_patients else None}
    chronic_count = connection.scalar(text(f'''
        SELECT count(distinct patient_id) FROM {warehouse}.fct_conditions
        WHERE clinical_status in ('active', 'recurrence', 'relapse') AND verification_status='confirmed'
              AND code_system='http://snomed.info/sct' AND code = ANY(:codes)
    '''), {"codes": list(CHRONIC_CODES)})
    return annual, summary, {"affected_patients": chronic_count, "total_patients": total_patients,
                             "mapped_codes": len(CHRONIC_CODES)}
