select
    id as encounter_id, patient_id, status,
    class_code, class_system, type_codes, reason_codes, provider_reference, location_references,
    cast(started_at as timestamptz) as started_at,
    cast(ended_at as timestamptz) as ended_at,
    source_hash
from {{ source('raw', 'encounters') }}
