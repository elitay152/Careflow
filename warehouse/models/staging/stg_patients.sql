select id as patient_id, cast(birth_date as date) as birth_date, gender, source_hash
from {{ source('raw', 'patients') }}
