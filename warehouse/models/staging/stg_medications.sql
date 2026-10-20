select
    id as medication_request_id, patient_id, code, code_system, display, status,
    cast(authored_date as date) as authored_date, source_hash
from {{ source('raw', 'medications') }}
