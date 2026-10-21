select observation_id, patient_id, code, code_system, status,
       effective_at, issued_at, available_at, measurements
from {{ ref('stg_observations') }}
