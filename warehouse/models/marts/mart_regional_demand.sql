select
    encounter_month, care_setting, zip_area,
    facility_id, facility_name, facility_city,
    count(*) as encounter_count
from {{ ref('fct_encounters') }}
where status = 'finished'
group by encounter_month, care_setting, zip_area, facility_id, facility_name, facility_city
