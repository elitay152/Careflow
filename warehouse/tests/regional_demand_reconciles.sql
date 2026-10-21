select coalesce(a.encounter_month, b.encounter_month) as month
from (select encounter_month, sum(encounter_count) as n from {{ ref('mart_regional_demand') }} group by 1) a
full join {{ ref('mart_monthly_utilization') }} b using (encounter_month)
where a.n is distinct from b.encounter_count
