-- Current recorded, confirmed active conditions; not historical population prevalence.
with counts as (
    select code_system, code, max(display) as condition_name,
           count(distinct patient_id) as affected_patients
    from {{ ref('fct_conditions') }}
    where clinical_status in ('active', 'recurrence', 'relapse')
      and verification_status = 'confirmed'
    group by code_system, code
), population as (
    select count(*) as total_patients from {{ ref('dim_patients') }}
)
select counts.*, population.total_patients,
       counts.affected_patients::numeric / nullif(population.total_patients, 0) as prevalence_fraction
from counts cross join population
