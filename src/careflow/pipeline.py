"""Transactional ingestion of a deliberately small FHIR R4 subset."""
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from .db import conditions, encounters, facilities, loads, medications, patients, quarantine, upsert
from .enrichment import encounter_context, ingest_facility


def required_string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def timestamp(value, label):
    value = required_string(value, label)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def coding(concept, label):
    if not isinstance(concept, dict) or not isinstance(concept.get("coding"), list) or not concept["coding"]:
        raise ValueError(f"{label}.coding must be a nonempty list")
    item = concept["coding"][0]
    if not isinstance(item, dict):
        raise ValueError(f"{label}.coding[0] must be an object")
    code = required_string(item.get("code"), f"{label}.code")
    system = required_string(item.get("system"), f"{label}.system")
    display = required_string(item.get("display", code), f"{label}.display")
    return {"code": code, "code_system": system, "display": display}


def clinical_date(value):
    if value is None:
        return None
    value = required_string(value, "clinical date")
    if len(value) == 10:
        return date.fromisoformat(value).isoformat()
    return datetime.fromisoformat(timestamp(value, "clinical date")).date().isoformat()


def status_code(resource, field, allowed):
    if field not in resource:
        return "unknown"
    code = coding(resource[field], field)["code"]
    if code not in allowed:
        raise ValueError(f"invalid {field}")
    return code


def normalize(resource, aliases, source_hash, medication_concepts=None):
    identifier = required_string(resource.get("id"), "id")
    common = {"id": identifier, "source_hash": source_hash}
    if resource["resourceType"] == "Patient":
        born = date.fromisoformat(required_string(resource.get("birthDate"), "birthDate"))
        if born > datetime.now(timezone.utc).date():
            raise ValueError("birthDate cannot be in the future")
        gender = resource.get("gender", "unknown")
        if gender not in {"male", "female", "other", "unknown"}:
            raise ValueError("invalid administrative gender")
        death_date = clinical_date(resource.get("deceasedDateTime"))
        if death_date and date.fromisoformat(death_date) < born:
            raise ValueError("death date precedes birth")
        deceased = resource.get("deceasedBoolean")
        if deceased is not None and type(deceased) is not bool:
            raise ValueError("deceasedBoolean must be boolean")
        if "deceasedBoolean" in resource and "deceasedDateTime" in resource:
            raise ValueError("Patient must use only one deceased choice")
        return patients, {**common, "birth_date": born.isoformat(), "gender": gender,
                          "death_date": death_date, "deceased": True if death_date else deceased}
    subject = resource.get("subject")
    if not isinstance(subject, dict):
        raise ValueError("subject must be an object")
    reference = required_string(subject.get("reference"), "subject.reference")
    patient_id = aliases.get(reference)
    if patient_id is None and reference.startswith("Patient/"):
        patient_id = reference.removeprefix("Patient/")
    if not patient_id:
        raise ValueError("subject must reference a local Patient or a Bundle fullUrl")
    common["patient_id"] = patient_id
    if resource["resourceType"] == "Condition":
        return conditions, {
            **common, **coding(resource.get("code"), "code"),
            "clinical_status": status_code(resource, "clinicalStatus", {
                "active", "recurrence", "relapse", "inactive", "remission", "resolved"}),
            "verification_status": status_code(resource, "verificationStatus", {
                "unconfirmed", "provisional", "differential", "confirmed", "refuted", "entered-in-error"}),
            "onset_date": clinical_date(resource.get("onsetDateTime")),
            "recorded_date": clinical_date(resource.get("recordedDate")),
        }
    if resource["resourceType"] == "MedicationRequest":
        status = resource.get("status")
        if status not in {"active", "on-hold", "cancelled", "completed", "entered-in-error", "stopped", "draft", "unknown"}:
            raise ValueError("invalid medication request status")
        concept = resource.get("medicationCodeableConcept")
        if "medicationReference" in resource:
            if "medicationCodeableConcept" in resource:
                raise ValueError("MedicationRequest must use only one medication choice")
            reference = resource["medicationReference"]
            if not isinstance(reference, dict):
                raise ValueError("medicationReference must be an object")
            reference = required_string(reference.get("reference"), "medicationReference.reference")
            if reference not in (medication_concepts or {}):
                raise ValueError("medicationReference must resolve to a Medication in this Bundle")
            concept = medication_concepts[reference]
        return medications, {
            **common, **coding(concept, "medication code"),
            "status": status, "authored_date": clinical_date(resource.get("authoredOn")),
        }
    period = resource.get("period")
    if not isinstance(period, dict):
        raise ValueError("period must be an object")
    started = timestamp(period.get("start"), "period.start")
    ended = timestamp(period["end"], "period.end") if period.get("end") else None
    if ended and datetime.fromisoformat(ended) < datetime.fromisoformat(started):
        raise ValueError("period.end precedes period.start")
    status = resource.get("status")
    if status not in {"planned", "arrived", "triaged", "in-progress", "onleave", "finished", "cancelled", "entered-in-error", "unknown"}:
        raise ValueError("invalid encounter status")
    return encounters, {**common, "patient_id": patient_id, "status": status,
                        "started_at": started, "ended_at": ended, **encounter_context(resource)}


def ingest(engine, path, reprocess=False):
    path = Path(path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    bundle = json.loads(raw)
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise ValueError("Input must be a FHIR Bundle")
    entries = bundle.get("entry")
    if not isinstance(entries, list):
        raise ValueError("Bundle.entry must be a list")
    # Resolve Synthea-style urn:uuid references; reject ambiguous aliases.
    aliases = {}
    medication_concepts = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        resource = entry.get("resource")
        if isinstance(resource, dict) and resource.get("resourceType") == "Medication":
            identifier = resource.get("id")
            references = [entry.get("fullUrl")]
            if isinstance(identifier, str) and identifier:
                references.append(f"Medication/{identifier}")
            for reference in references:
                if not isinstance(reference, str) or not reference:
                    continue
                concept = resource.get("code")
                if reference in medication_concepts and medication_concepts[reference] != concept:
                    raise ValueError("Ambiguous Medication reference")
                medication_concepts[reference] = concept
        if isinstance(resource, dict) and resource.get("resourceType") == "Patient":
            alias = entry.get("fullUrl")
            identifier = resource.get("id")
            if isinstance(alias, str) and isinstance(identifier, str):
                if alias in aliases and aliases[alias] != identifier:
                    raise ValueError("Ambiguous Patient fullUrl")
                aliases[alias] = identifier
    with engine.begin() as connection:
        existing = connection.execute(select(loads).where(loads.c.source_hash == digest)).first()
        if existing and not reprocess:
            return {"file": str(path), "status": "skipped", "accepted": 0, "rejected": 0, "ignored": 0}
        accepted = rejected = ignored = 0
        pending = []

        def reject(index, resource, reason):
            nonlocal rejected
            upsert(connection, quarantine, {
                "id": hashlib.sha256(f"{digest}:{index}".encode()).hexdigest(),
                "source_hash": digest, "entry_index": index,
                "reason": reason, "resource": resource,
            })
            rejected += 1

        for index, entry in enumerate(entries):
            resource = entry.get("resource") if isinstance(entry, dict) else None
            if not isinstance(resource, dict):
                reject(index, entry, "entry.resource must be an object")
                continue
            kind = resource.get("resourceType")
            if not isinstance(kind, str) or not kind:
                reject(index, resource, "resourceType must be a nonempty string")
                continue
            if kind == "Organization":
                try:
                    ingest_facility(connection, resource, entry.get("fullUrl"), digest)
                    accepted += 1
                except (ValueError, TypeError, KeyError, AttributeError) as error:
                    reject(index, resource, str(error))
                continue
            if kind not in {"Patient", "Encounter", "Condition", "MedicationRequest"}:
                ignored += 1
                continue
            try:
                table, values = normalize(resource, aliases, digest, medication_concepts)
            except (ValueError, TypeError, KeyError) as error:
                reject(index, resource, str(error))
                continue
            if table is not patients:
                pending.append((index, resource, table, values))
            else:
                upsert(connection, table, values)
                accepted += 1
        # Patients load first even if dependent records precede them in the Bundle.
        for index, resource, table, values in pending:
            exists = connection.execute(select(patients.c.id).where(
                patients.c.id == values["patient_id"])).first()
            if not exists:
                reject(index, resource, "referenced Patient does not exist")
                continue
            upsert(connection, table, values)
            accepted += 1
        checkpoint = dict(
            source_hash=digest, source_path=str(path.resolve()),
            loaded_at=datetime.now(timezone.utc).isoformat(),
            accepted=accepted, rejected=rejected, ignored=ignored,
        )
        if existing:
            connection.execute(loads.update().where(loads.c.source_hash == digest).values(**checkpoint))
        else:
            connection.execute(loads.insert().values(**checkpoint))
    return {"file": str(path), "status": "loaded", "accepted": accepted,
            "rejected": rejected, "ignored": ignored}


def report(engine):
    with engine.connect() as connection:
        counts = {table.name: connection.scalar(select(func.count()).select_from(table))
                  for table in (patients, encounters, conditions, medications, loads, quarantine)}
        grouped = connection.execute(select(
            patients.c.gender, func.count(encounters.c.id).label("encounters")
        ).select_from(patients.join(encounters)).group_by(patients.c.gender)).mappings()
        return {"counts": counts, "provider_organizations": connection.scalar(select(func.count()).select_from(facilities)),
                "encounters_by_gender": [dict(row) for row in grouped]}
