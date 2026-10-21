select 'encounters' as resource
where (select count(*) from {{ ref('fct_encounters') }}) !=
      (select count(*) from {{ source('raw', 'encounters') }})
union all
select 'conditions' as resource
where (select count(*) from {{ ref('fct_conditions') }}) !=
      (select count(*) from {{ source('raw', 'conditions') }})
union all
select 'medications' as resource
where (select count(*) from {{ ref('fct_medication_requests') }}) !=
      (select count(*) from {{ source('raw', 'medications') }})
union all
select 'observations' as resource
where (select count(*) from {{ ref('fct_observations') }}) !=
      (select count(*) from {{ source('raw', 'observations') }})
