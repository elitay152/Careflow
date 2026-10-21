select encounter_month, care_setting, zip_area, facility_id
from {{ ref('mart_regional_demand') }}
group by 1,2,3,4 having count(*) > 1
