select observation_id from {{ ref('fct_observations') }}
where (issued_at is null and available_at is not null)
   or (issued_at is not null and available_at is distinct from greatest(issued_at, effective_at))
