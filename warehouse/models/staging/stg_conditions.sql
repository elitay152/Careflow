select
    id as condition_id, patient_id, code, code_system, display,
    clinical_status, verification_status,
    cast(onset_date as date) as onset_date, cast(recorded_date as date) as recorded_date, source_hash
from {{ source('raw', 'conditions') }}
