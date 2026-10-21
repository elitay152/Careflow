"""Synthetic, event-dated 90-day inpatient-episode cohort; no patient rows are public."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

from sqlalchemy import text

from .utilization import CHRONIC_CODES

PROTOCOL = "admission_90d_v1"
FEATURES = ("age_years", "encounters_90d", "encounters_365d", "emergency_365d",
            "inpatient_episodes_365d", "days_since_completed_encounter",
            "recorded_chronic_codes", "recorded_diabetes", "recorded_hypertension")
SPLITS = (("train", "2015-01-01", "2020-10-01"),
          ("validation", "2021-01-01", "2022-10-01"),
          ("test", "2023-01-01", "2024-10-01"))
MIN_POSITIVE_PATIENTS = {"train": 50, "validation": 10, "test": 10}
EXCLUSIONS = ("under_18", "history_short", "dead_at_index", "unknown_death_date",
              "hospitalized_at_index", "censored", "outside_split")


def moment(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def partition(patient_id):
    bucket = int(hashlib.sha256((PROTOCOL + ":" + patient_id).encode()).hexdigest()[:8], 16) % 100
    return "train" if bucket < 70 else "validation" if bucket < 85 else "test"


def inpatient_episodes(encounters):
    stays = sorted(((e["start"], e["end"]) for e in encounters
                   if e["class_code"] == "IMP" and e["class_system"] == "http://terminology.hl7.org/CodeSystem/v3-ActCode"
                   and e["status"] in {"finished", "in-progress", "onleave"}), key=lambda item: item[0])
    merged = []
    for start, end in stays:
        if merged and (merged[-1][1] is None or start <= merged[-1][1]):
            previous = merged[-1][1]
            merged[-1] = (merged[-1][0], None if previous is None or end is None else max(previous, end))
        else:
            merged.append((start, end))
    return merged


def make_snapshots(patients, encounters, conditions, observation_end, *, external=False, all_periods=False):
    end = moment(observation_end)
    by_patient, diagnoses = defaultdict(list), defaultdict(list)
    for raw in encounters:
        if raw["status"] not in {"finished", "in-progress", "onleave"}:
            continue
        event = {**raw, "start": moment(raw["started_at"]), "end": moment(raw["ended_at"])}
        if event["end"] is not None and event["end"] < event["start"]:
            raise ValueError("Encounter end precedes start")
        by_patient[raw["patient_id"]].append(event)
    for raw in conditions:
        if raw["recorded_date"] and raw["onset_date"] and raw["code_system"] == "http://snomed.info/sct":
            diagnoses[raw["patient_id"]].append((date.fromisoformat(str(raw["recorded_date"])),
                date.fromisoformat(str(raw["onset_date"])), raw["code"]))
    anchors = [moment(f"{year}-{month:02d}-01") for year in range(2023 if external else 2015, 2025) for month in (1, 4, 7, 10)]
    rows, exclusions = [], Counter({key: 0 for key in EXCLUSIONS})
    eligible = episode_count = inpatient_count = 0
    for patient in sorted(patients, key=lambda p: p["id"]):
        pid = patient["id"]
        events = sorted(by_patient[pid], key=lambda e: e["start"])
        episodes = inpatient_episodes(events)
        episode_count += len(episodes)
        inpatient_count += sum(e["class_code"] == "IMP" and e["class_system"] == "http://terminology.hl7.org/CodeSystem/v3-ActCode" for e in events)
        born = date.fromisoformat(patient["birth_date"])
        death = date.fromisoformat(patient["death_date"]) if patient["death_date"] else None
        assignment = partition(pid)
        for index in anchors:
            age = index.year - born.year - ((index.month, index.day) < (born.month, born.day))
            horizon = index + timedelta(days=90)
            reason = None
            if age < 18:
                reason = "under_18"
            elif patient["deceased"] is True and death is None:
                reason = "unknown_death_date"
            elif death is not None and death <= index.date():
                reason = "dead_at_index"
            elif not events or events[0]["start"] > index - timedelta(days=365):
                reason = "history_short"
            elif any(start <= index and (finish is None or index < finish) for start, finish in episodes):
                reason = "hospitalized_at_index"
            else:
                admissions = [start for start, _ in episodes if index < start <= horizon and start < end
                              and (death is None or start.date() <= death)]
                # An observed positive is known even when death later truncates follow-up.
                if not admissions and (horizon >= end or (death is not None and death <= horizon.date())):
                    reason = "censored"
            if reason:
                exclusions[reason] += 1
                continue
            eligible += 1
            period = "train" if index.year <= 2020 else "validation" if index.year <= 2022 else "test"
            if external:
                period = "external"
            elif not all_periods and period != assignment:
                exclusions["outside_split"] += 1
                continue
            completed = [e for e in events if e["status"] == "finished" and e["start"] < index
                         and e["end"] is not None and e["end"] <= index]
            prior365 = [e for e in completed if e["start"] >= index - timedelta(days=365)]
            codes = {code for recorded, onset, code in diagnoses[pid] if recorded < index.date() and onset < index.date()}
            values = {"age_years": age,
                "encounters_90d": sum(e["start"] >= index - timedelta(days=90) for e in completed),
                "encounters_365d": len(prior365),
                "emergency_365d": sum(e["class_code"] == "EMER" and e["class_system"] == "http://terminology.hl7.org/CodeSystem/v3-ActCode" for e in prior365),
                "inpatient_episodes_365d": sum(index-timedelta(days=365) <= start < index and finish is not None and finish <= index for start, finish in episodes),
                "days_since_completed_encounter": min(366, (index-max(e["end"] for e in completed)).days) if completed else 366,
                "recorded_chronic_codes": len(codes.intersection(CHRONIC_CODES)),
                "recorded_diabetes": int("44054006" in codes), "recorded_hypertension": int("59621000" in codes)}
            rows.append({"patient_id": pid, "index_date": index.date().isoformat(), "split": period,
                         "outcome": int(bool(admissions)), "features": values})
    counts = []
    blockers = []
    for split, start, stop in SPLITS:
        selected = [r for r in rows if r["split"] == split]
        positive_patients = len({r["patient_id"] for r in selected if r["outcome"]})
        negatives = len({r["patient_id"] for r in selected if not r["outcome"]})
        counts.append({"split": split, "start": start, "end": stop, "snapshots": len(selected),
                       "patients": len({r["patient_id"] for r in selected}),
                       "positive_snapshots": sum(r["outcome"] for r in selected), "positive_patients": positive_patients, "negative_patients": negatives})
        if positive_patients < MIN_POSITIVE_PATIENTS[split]:
            blockers.append(split + "_positive_patients")
        if negatives < 50:
            blockers.append(split + "_negative_patients")
    missing_dates = sum(r["recorded_date"] is None for r in conditions)
    if missing_dates:
        blockers.append("missing_condition_recorded_dates")
    digest = hashlib.sha256(json.dumps({"protocol": PROTOCOL, "rows": rows}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    summary = {"protocol": PROTOCOL, "observation_end": end.date().isoformat(),
        "status": "insufficient_data" if blockers else "ready_not_trained", "blockers": blockers,
        "evaluated_at": datetime.now(timezone.utc).isoformat(), "source_loaded_at": None,
        "input_sha256": digest, "patients": len(patients), "candidate_snapshots": len(patients)*len(anchors),
        "eligible_snapshots": eligible, "assigned_snapshots": len(rows), "exclusions": dict(exclusions),
        "raw_inpatient_encounters": inpatient_count, "inpatient_episodes": episode_count,
        "conditions": len(conditions), "undated_conditions": missing_dates,
        "features": list(FEATURES), "splits": counts, "models": [], "external": None}
    return rows, summary


def load_cohort(engine, raw_schema):
    if engine.dialect.name != "postgresql" or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", raw_schema):
        raise ValueError("Risk audit requires PostgreSQL and a simple schema identifier")
    schema = '"' + raw_schema + '"'
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        def rows(sql):
            return [dict(r) for r in connection.execute(text(sql)).mappings()]
        patients = rows(f"SELECT id, birth_date, death_date, deceased FROM {schema}.patients ORDER BY id")
        encounters = rows(f"SELECT patient_id, status, started_at, ended_at, class_code, class_system FROM {schema}.encounters ORDER BY id")
        conditions = rows(f"SELECT patient_id, code, code_system, onset_date, recorded_date FROM {schema}.conditions ORDER BY id")
        loaded = connection.scalar(text(f"SELECT max(loaded_at) FROM {schema}.loads"))
    return patients, encounters, conditions, loaded


PRIVATE_ROOT = Path(__file__).resolve().parents[2] / "data" / "risk"


def run_audit(engine, raw_schema, observation_end, output, *, train=False, external_schema=None):
    root = Path(output).resolve()
    if PRIVATE_ROOT.resolve() != PRIVATE_ROOT or not root.is_relative_to(PRIVATE_ROOT) or root == PRIVATE_ROOT:
        raise ValueError("Private risk outputs must be in a subdirectory of ignored data/risk")
    if date.fromisoformat(observation_end).isoformat() != observation_end:
        raise ValueError("Observation cutoff must be an ISO date")
    patients, encounters, conditions, loaded = load_cohort(engine, raw_schema)
    rows, summary = make_snapshots(patients, encounters, conditions, observation_end)
    summary["source_loaded_at"] = loaded
    summary["_source"] = {"database": engine.url.database, "schema": raw_schema}
    external_rows = None
    if external_schema is not None:
        if not train or external_schema == raw_schema:
            raise ValueError("Independent evaluation requires --train and a different source schema")
        ep, ee, ec, eloaded = load_cohort(engine, external_schema)
        if {p["id"] for p in patients} & {p["id"] for p in ep}:
            raise ValueError("Independent source patients overlap the development cohort")
        external_rows, esummary = make_snapshots(ep, ee, ec, observation_end, external=True)
        positive_patients = len({r["patient_id"] for r in external_rows if r["outcome"]})
        negatives = len({r["patient_id"] for r in external_rows if not r["outcome"]})
        if positive_patients < 20 or negatives < 50 or esummary["undated_conditions"]:
            raise ValueError("Independent cohort does not pass event-count and date-coverage gates")
        summary["external"] = {"patients": len({r["patient_id"] for r in external_rows}),
            "snapshots": len(external_rows), "positive_snapshots": sum(r["outcome"] for r in external_rows),
            "positive_patients": positive_patients, "source_loaded_at": eloaded,
            "input_sha256": esummary["input_sha256"], "observation_end": observation_end}
        summary["_source"]["external_schema"] = external_schema
        summary["_source"]["external_loaded_at"] = eloaded
    if train and not summary["blockers"]:
        from .risk_model import evaluate_models
        summary["models"] = evaluate_models(rows, external_rows)
        summary["status"] = "evaluated"
    root.mkdir(parents=True, exist_ok=True)
    (root / "snapshots.jsonl").write_text("".join(json.dumps(row, sort_keys=True)+"\n" for row in rows))
    if external_rows is not None:
        (root / "external-snapshots.jsonl").write_text("".join(json.dumps(row, sort_keys=True)+"\n" for row in external_rows))
    (root / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False)+"\n")
    return summary
