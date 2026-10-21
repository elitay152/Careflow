select condition_id, patient_id, code, code_system, display,
       clinical_status, verification_status, onset_date, recorded_date
from {{ ref('stg_conditions') }}
