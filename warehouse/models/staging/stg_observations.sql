select id as observation_id, patient_id, code, code_system, status,
       cast(effective_at as timestamptz) as effective_at,
       cast(issued_at as timestamptz) as issued_at,
       cast(available_at as timestamptz) as available_at,
       measurements, source_hash
from {{ source('raw', 'observations') }}
