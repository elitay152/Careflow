select patient_id, birth_date, gender
from {{ ref('stg_patients') }}
