-- Grain: one UTC calendar month. Cancelled/error/planned encounters are excluded.
select
    encounter_month,
    count(*) as encounter_count,
    count(distinct patient_id) as unique_patients,
    avg(duration_minutes) as average_duration_minutes,
    percentile_cont(0.5) within group (order by duration_minutes) as median_duration_minutes,
    count(duration_minutes) as measured_duration_count
from {{ ref('fct_encounters') }}
where status = 'finished'
group by encounter_month
