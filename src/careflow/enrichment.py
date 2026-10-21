"""Retain coded encounter context and resolve provider references without network calls."""
from urllib.parse import parse_qs, urlencode, urlsplit

from sqlalchemy import select

from .db import facilities, facility_aliases, upsert


def optional_text(value, field):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def canonical_reference(reference):
    reference = optional_text(reference, "reference")
    if reference is None:
        return None
    parts = urlsplit(reference)
    if parts.path == "Organization" and parts.query:
        query = parse_qs(parts.query, keep_blank_values=True)
        if set(query) != {"identifier"} or len(query["identifier"]) != 1:
            raise ValueError("Organization identifier reference must contain one identifier")
        return "Organization?" + urlencode({"identifier": query["identifier"][0]})
    return reference


def coded_list(resource, field):
    if field not in resource:
        return []
    concepts = resource[field]
    if not isinstance(concepts, list):
        raise ValueError(f"{field} must be an array")
    result = []
    for concept in concepts:
        if not isinstance(concept, dict) or not isinstance(concept.get("coding", []), list):
            raise ValueError(f"{field} must contain CodeableConcept objects")
        for code in concept.get("coding", []):
            if not isinstance(code, dict):
                raise ValueError(f"{field} coding must be an object")
            system = optional_text(code.get("system"), f"{field}.system")
            value = optional_text(code.get("code"), f"{field}.code")
            if not system or not value:
                raise ValueError(f"{field} coding needs system and code")
            result.append({"system": system, "code": value})
    return result


def encounter_context(resource):
    klass = resource.get("class", {})
    if not isinstance(klass, dict):
        raise ValueError("class must be an object")
    system = optional_text(klass.get("system"), "class.system")
    code = optional_text(klass.get("code"), "class.code")
    if bool(system) != bool(code):
        raise ValueError("class needs both system and code")
    provider = resource.get("serviceProvider", {})
    if not isinstance(provider, dict):
        raise ValueError("serviceProvider must be an object")
    locations = resource.get("location", [])
    if not isinstance(locations, list):
        raise ValueError("location must be an array")
    references = []
    for location in locations:
        if not isinstance(location, dict) or not isinstance(location.get("location"), dict):
            raise ValueError("location must contain a location reference")
        ref = optional_text(location["location"].get("reference"), "location.reference")
        if not ref:
            raise ValueError("location.reference is required")
        references.append(ref)
    return {"class_system": system, "class_code": code,
            "type_codes": coded_list(resource, "type"), "reason_codes": coded_list(resource, "reasonCode"),
            "provider_reference": canonical_reference(provider.get("reference")),
            "location_references": references}


def ingest_facility(connection, resource, full_url, source_hash):
    identifier = optional_text(resource.get("id"), "Organization.id")
    if not identifier:
        raise ValueError("Organization.id is required")
    name = optional_text(resource.get("name"), "Organization.name") or "Unnamed facility"
    addresses = resource.get("address", [])
    if not isinstance(addresses, list) or any(not isinstance(a, dict) for a in addresses):
        raise ValueError("Organization.address must be an array of objects")
    address = next((a for a in addresses if a.get("use") == "work"), addresses[0] if addresses else {})
    values = {"id": identifier, "name": name, "source_hash": source_hash,
              **{target: optional_text(address.get(source), f"address.{source}")
                 for target, source in (("city", "city"), ("state", "state"), ("postal_code", "postalCode"), ("country", "country"))}}
    values["bed_count"] = None
    for extension in resource.get("extension", []):
        if extension.get("url") == "http://synthetichealth.github.io/synthea/bed-count-extension":
            count = extension.get("valueInteger")
            if type(count) is not int or count < 0:
                raise ValueError("bed count must be a nonnegative integer")
            values["bed_count"] = count
    aliases = {f"Organization/{identifier}"}
    if full_url is not None:
        aliases.add(optional_text(full_url, "Organization.fullUrl"))
    identifiers = resource.get("identifier", [])
    if not isinstance(identifiers, list):
        raise ValueError("Organization.identifier must be an array")
    for item in identifiers:
        if not isinstance(item, dict):
            raise ValueError("Organization.identifier must contain objects")
        system = optional_text(item.get("system"), "identifier.system")
        value = optional_text(item.get("value"), "identifier.value")
        if system and value:
            aliases.add("Organization?" + urlencode({"identifier": f"{system}|{value}"}))
    for alias in aliases:
        owner = connection.scalar(select(facility_aliases.c.facility_id).where(facility_aliases.c.id == alias))
        if owner is not None and owner != identifier:
            raise ValueError("Ambiguous Organization alias")
    upsert(connection, facilities, values)
    for alias in sorted(aliases):
        upsert(connection, facility_aliases, {"id": alias, "facility_id": identifier})
