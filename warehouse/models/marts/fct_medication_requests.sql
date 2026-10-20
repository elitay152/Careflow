select medication_request_id, patient_id, code, code_system, display, status, authored_date
from {{ ref('stg_medications') }}
