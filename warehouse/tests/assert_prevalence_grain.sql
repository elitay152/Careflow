select code_system, code
from {{ ref('mart_condition_prevalence') }}
group by code_system, code
having count(*) > 1
