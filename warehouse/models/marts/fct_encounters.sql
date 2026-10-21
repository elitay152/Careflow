select
    e.encounter_id, e.patient_id, e.status, e.started_at, e.ended_at,
    e.class_code, e.class_system, e.type_codes, e.reason_codes,
    e.provider_reference, e.location_references,
    f.id as facility_id, f.name as facility_name,
    f.city as facility_city, f.state as facility_state,
    f.postal_code as facility_postal_code, f.country as facility_country,
    case when f.country in ('US', 'USA') and f.state ~ '^[A-Z]{2}$' and f.postal_code ~ '^[0-9]{5}(-?[0-9]{4})?$'
         then f.state || '-' || left(f.postal_code, 3) else 'unknown' end as zip_area,
    case when e.class_system = 'http://terminology.hl7.org/CodeSystem/v3-ActCode'
         then case e.class_code when 'AMB' then 'ambulatory' when 'EMER' then 'emergency'
                   when 'IMP' then 'inpatient' else 'other' end else 'other' end as care_setting,
    cast(date_trunc('month', e.started_at at time zone 'UTC') as date) as encounter_month,
    extract(epoch from (e.ended_at - e.started_at)) / 60.0 as duration_minutes
from {{ ref('stg_encounters') }} e
left join {{ source('raw', 'facility_aliases') }} a on a.id = e.provider_reference
left join {{ source('raw', 'facilities') }} f on f.id = a.facility_id
