select
    id as encounter_id, patient_id, status,
    cast(started_at as timestamptz) as started_at,
    cast(ended_at as timestamptz) as ended_at,
    source_hash
from {{ source('raw', 'encounters') }}
