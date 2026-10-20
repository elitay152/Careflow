select 'utilization' as metric
from {{ ref('mart_monthly_utilization') }}
where encounter_count < unique_patients or unique_patients <= 0
   or average_duration_minutes < 0
union all
select 'prevalence' as metric
from {{ ref('mart_condition_prevalence') }}
where prevalence_fraction < 0 or prevalence_fraction > 1
   or affected_patients > total_patients
union all
select 'duration' as metric
from {{ ref('fct_encounters') }}
where duration_minutes < 0
