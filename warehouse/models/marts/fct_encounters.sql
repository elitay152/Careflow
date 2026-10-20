select
    encounter_id, patient_id, status, started_at, ended_at,
    cast(date_trunc('month', started_at at time zone 'UTC') as date) as encounter_month,
    extract(epoch from (ended_at - started_at)) / 60.0 as duration_minutes
from {{ ref('stg_encounters') }}
