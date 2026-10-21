"""Aggregate recorded demand by facility and provider ZIP area, never whole-site census."""
import hashlib

from sqlalchemy import text

from .forecast import evaluate_forecast, shift_month

SETTINGS = ("ambulatory", "emergency", "inpatient", "other")
MIN_ANNUAL_EVENTS = 120
MIN_RECENT_ACTIVE_MONTHS = 10
MIN_TRAINING_ACTIVE_MONTHS = 36


def build_demand(monthly, rows, *, facility_count, inpatient_patients):
    if not monthly:
        return None
    months = [str(row["encounter_month"]) for row in monthly[-120:]]
    position = {month: i for i, month in enumerate(months)}
    annual_start = shift_month(months[-1], -11)
    recent_indices = [i for i, month in enumerate(months) if month >= annual_start]
    consecutive = len(months) == 120 and all(month == shift_month(months[0], i) for i, month in enumerate(months))
    groups = {}
    totals = [0] * len(months)
    matched = assigned = recent_matched = recent_assigned = 0
    for row in rows:
        month = str(row["encounter_month"])
        if month not in position:
            continue
        index = position[month]
        n = int(row["encounter_count"])
        totals[index] += n
        if row["facility_id"] is not None:
            matched += n
            if month >= annual_start:
                recent_matched += n
        if row["zip_area"] != "unknown":
            assigned += n
            if month >= annual_start:
                recent_assigned += n
        scopes = []
        if row["facility_id"] is not None:
            key = "site_" + hashlib.sha256(row["facility_id"].encode()).hexdigest()[:12]
            scopes.append(("facility", key, (row["facility_name"] + (" · " + row["facility_city"] if row.get("facility_city") else ""))[:200]))
        if row["zip_area"] != "unknown":
            scopes.append(("zip_area", row["zip_area"], row["zip_area"][:2] + " · ZIP " + row["zip_area"][3:] + "xx"))
        for scope, key, label in scopes:
            signature = (scope, key, row["care_setting"])
            group = groups.setdefault(signature, {"scope": scope, "key": key,
                "label": label, "care_setting": row["care_setting"], "counts": [0] * len(months)})
            group["counts"][index] += n
    expected = [row["encounter_count"] for row in monthly[-120:]]
    if totals != expected:
        raise ValueError("Regional demand does not reconcile with all-cohort encounters")
    published = []
    for signature in sorted(groups):
        group = groups[signature]
        counts = group["counts"]
        annual = sum(counts[i] for i in recent_indices)
        if annual == 0:
            continue
        eligible = (consecutive and annual >= MIN_ANNUAL_EVENTS
                    and sum(n > 0 for n in counts[-12:]) >= MIN_RECENT_ACTIVE_MONTHS
                    and sum(n > 0 for n in counts[-72:-12]) >= MIN_TRAINING_ACTIVE_MONTHS)
        group.update(annual_count=annual, eligible=eligible,
                     forecast=evaluate_forecast([{"encounter_month": month, "encounter_count": n}
                         for month, n in zip(months, counts, strict=True)]) if eligible else None)
        published.append(group)
    return {"months": months, "facility_count": facility_count,
            "coverage": {"finished_encounters": sum(totals), "matched_provider_encounters": matched,
                         "assigned_zip_encounters": assigned,
                         "annual_finished_encounters": sum(totals[i] for i in recent_indices),
                         "annual_matched_provider_encounters": recent_matched,
                         "annual_assigned_zip_encounters": recent_assigned},
            "hospitalization": {"annual_encounters": sum(row["encounter_count"] for row in rows
                    if row["care_setting"] == "inpatient" and str(row["encounter_month"]) >= annual_start),
                                "annual_patients": inpatient_patients},
            "groups": published}


def export_demand(connection, warehouse, raw, monthly):
    if not monthly:
        return None
    start, end = str(monthly[-min(120, len(monthly))]["encounter_month"]), shift_month(monthly[-1]["encounter_month"], 1)
    rows = connection.execute(text(f'''SELECT encounter_month, care_setting, zip_area,
        facility_id, facility_name, facility_city, encounter_count FROM {warehouse}.mart_regional_demand
        WHERE encounter_month >= cast(:start as date) AND encounter_month < cast(:end as date)
        ORDER BY encounter_month, care_setting, zip_area, facility_id'''), {"start": start, "end": end}).mappings().all()
    patients = connection.scalar(text(f'''SELECT count(distinct patient_id) FROM {warehouse}.fct_encounters
        WHERE status='finished' AND care_setting='inpatient'
          AND encounter_month >= cast(:start as date) AND encounter_month < cast(:end as date)'''),
        {"start": shift_month(end, -12), "end": end})
    facilities = connection.scalar(text(f'SELECT count(*) FROM {raw}.facilities'))
    return build_demand(monthly, rows, facility_count=facilities, inpatient_patients=patients)
