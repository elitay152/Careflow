"""Selected LOINC quantities with explicit UCUM units; no clinical interpretation."""

from datetime import datetime, timezone
import math

LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
CODES = {
    "39156-5": "bmi",
    "4548-4": "hba1c",
    "33914-3": "egfr",
    "2160-0": "creatinine",
    "38483-4": "creatinine",
    "8480-6": "systolic",
    "8462-4": "diastolic",
    "85354-9": "blood_pressure",
}
UNITS = {
    "bmi": "kg/m2",
    "hba1c": "%",
    "egfr": "mL/min/{1.73_m2}",
    "creatinine": "mg/dL",
    "systolic": "mm[Hg]",
    "diastolic": "mm[Hg]",
}


def selected_codes(concept):
    if not isinstance(concept, dict) or not isinstance(concept.get("coding"), list):
        return set()
    return {
        c["code"]
        for c in concept["coding"]
        if isinstance(c, dict)
        and c.get("system") == LOINC
        and isinstance(c.get("code"), str)
        and c.get("code") in CODES
    }


def supported(resource):
    return bool(selected_codes(resource.get("code")))


def utc_timestamp(value, label):
    if not isinstance(value, str) or "T" not in value:
        raise ValueError(f"{label} requires a full timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{label} requires a timezone")
    return parsed.astimezone(timezone.utc)


def quantity(metric, item):
    choices = [key for key in item if key.startswith("value")]
    if (
        len(choices) > 1
        or (choices and choices != ["valueQuantity"])
        or (choices and "dataAbsentReason" in item)
    ):
        raise ValueError("Observation requires one quantity choice or an absent reason")
    q = item.get("valueQuantity")
    if q is None and isinstance(item.get("dataAbsentReason"), dict):
        reason = item["dataAbsentReason"]
        if not reason.get("text") and not any(
            isinstance(c, dict) and isinstance(c.get("code"), str) and c["code"]
            for c in reason.get("coding", [])
        ):
            raise ValueError("Observation absent reason must be specified")
        return None
    if not isinstance(q, dict):
        raise ValueError(
            "Selected Observation requires valueQuantity or dataAbsentReason"
        )
    value = q.get("value")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("Observation quantity must be finite and nonnegative")
    if q.get("comparator") is not None:
        raise ValueError("Censored Observation quantities are not supported")
    unit = q.get("code")
    if q.get("system") != UCUM:
        raise ValueError("Observation quantity requires a UCUM code")
    original_unit = unit
    if metric == "creatinine" and unit == "umol/L":
        value /= 88.4
        unit = "mg/dL"
    elif metric == "egfr" and unit == "mL/min":
        # Preserve the simulator's non-indexed unit, without asserting LOINC compatibility.
        return "egfr_unindexed_unit", {
            "value": float(value),
            "unit": unit,
            "source_unit": unit,
        }
    if unit != UNITS[metric]:
        raise ValueError("Unsupported unit for selected Observation code")
    return metric, {"value": float(value), "unit": unit, "source_unit": original_unit}


def normalize_observation(resource):
    codes = selected_codes(resource.get("code"))
    if len(codes) != 1:
        raise ValueError("Observation must identify one supported LOINC code")
    code = next(iter(codes))
    status = resource.get("status")
    if status not in {
        "registered",
        "preliminary",
        "final",
        "amended",
        "corrected",
        "cancelled",
        "entered-in-error",
        "unknown",
    }:
        raise ValueError("Invalid Observation status")
    if [key for key in resource if key.startswith("effective")] != [
        "effectiveDateTime"
    ]:
        raise ValueError("Only effectiveDateTime is supported")
    effective = utc_timestamp(resource.get("effectiveDateTime"), "effectiveDateTime")
    issued = (
        utc_timestamp(resource["issued"], "issued")
        if resource.get("issued") is not None
        else None
    )
    measurements = {}
    if code == "85354-9":
        if any(key.startswith("value") for key in resource):
            raise ValueError("Blood pressure panel requires component quantities only")
        components = resource.get("component")
        if not isinstance(components, list):
            raise ValueError("Blood pressure panel requires components")
        seen = set()
        for component in components:
            if not isinstance(component, dict):
                raise ValueError("Observation component must be an object")
            selected = selected_codes(component.get("code"))
            if not selected:
                continue
            if len(selected) != 1 or not selected <= {"8480-6", "8462-4"}:
                raise ValueError("Invalid blood pressure component code")
            child = next(iter(selected))
            if child in seen:
                raise ValueError("Duplicate blood pressure component")
            seen.add(child)
            result = quantity(CODES[child], component)
            if result:
                measurements[result[0]] = result[1]
        if not seen:
            raise ValueError("Blood pressure panel has no supported components")
    else:
        result = quantity(CODES[code], resource)
        if result:
            measurements[result[0]] = result[1]
    return {
        "code": code,
        "code_system": LOINC,
        "status": status,
        "effective_at": effective.isoformat(),
        "issued_at": issued.isoformat() if issued else None,
        "available_at": max(effective, issued).isoformat() if issued else None,
        "measurements": measurements,
    }
