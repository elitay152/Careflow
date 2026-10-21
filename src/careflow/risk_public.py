"""Select and validate aggregate risk evidence; never export snapshot rows or scores."""
from datetime import datetime
import json
import math
from pathlib import Path
import re

from sqlalchemy import text

from .hospitalization import EXCLUSIONS, FEATURES, PROTOCOL, SPLITS

COUNTS = ("patients", "candidate_snapshots", "eligible_snapshots", "assigned_snapshots",
          "raw_inpatient_encounters", "inpatient_episodes", "conditions", "undated_conditions")
FIELDS = ("protocol", "observation_end", "status", "blockers", "evaluated_at", "source_loaded_at",
          "input_sha256", *COUNTS, "exclusions", "features", "splits", "models", "external")
METRIC_COUNTS = ("true_positive", "false_positive", "false_negative", "true_negative")
METRIC_RATES = ("average_precision", "brier", "precision", "recall")
SPLIT_COUNTS = ("snapshots", "patients", "positive_snapshots", "positive_patients", "negative_patients")
EXTERNAL_FIELDS = ("patients", "snapshots", "positive_snapshots", "positive_patients", "source_loaded_at", "input_sha256", "observation_end")


def keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("Risk evidence has unexpected fields")


def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("Risk counts must be nonnegative integers")


def rate(value, nullable=False):
    if value is None and nullable:
        return
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Risk rates must be finite probabilities")


def metrics(value):
    keys(value, (*METRIC_COUNTS, *METRIC_RATES))
    for key in METRIC_COUNTS:
        integer(value[key])
    for key in METRIC_RATES:
        rate(value[key], key == "precision")


def calibration(rows):
    if not isinstance(rows, list) or len(rows) != 5:
        raise ValueError("Risk calibration requires five fixed bins")
    bounds = (0, .01, .025, .05, .1, 1)
    for i, row in enumerate(rows):
        keys(row, ("lower", "upper", "snapshots", "mean_risk", "observed_rate"))
        if row["lower"] != bounds[i] or row["upper"] != bounds[i+1]:
            raise ValueError("Unexpected risk calibration boundaries")
        integer(row["snapshots"])
        for key in ("mean_risk", "observed_rate"):
            rate(row[key], not row["snapshots"])
            if not row["snapshots"] and row[key] is not None:
                raise ValueError("Empty risk bins must not fabricate a rate")


def validate_public_risk(value):
    keys(value, FIELDS)
    if value["protocol"] != PROTOCOL or value["status"] not in {"evaluated", "ready_not_trained", "insufficient_data"} or value["features"] != list(FEATURES):
        raise ValueError("Unknown risk protocol")
    allowed = {name + suffix for name, _, _ in SPLITS for suffix in ("_positive_patients", "_negative_patients")} | {"missing_condition_recorded_dates"}
    if not isinstance(value["blockers"], list) or len(value["blockers"]) != len(set(value["blockers"])) or any(item not in allowed for item in value["blockers"]):
        raise ValueError("Unknown risk readiness blocker")
    for key in COUNTS:
        integer(value[key])
    for key in ("evaluated_at", "source_loaded_at", "observation_end"):
        if value[key] is not None:
            datetime.fromisoformat(value[key])
    if not isinstance(value["input_sha256"], str) or not re.fullmatch("[a-f0-9]{64}", value["input_sha256"]):
        raise ValueError("Invalid risk input hash")
    keys(value["exclusions"], EXCLUSIONS)
    for count in value["exclusions"].values():
        integer(count)
    if not isinstance(value["splits"], list) or len(value["splits"]) != 3:
        raise ValueError("Risk evidence requires three disjoint splits")
    for row, (name, start, end) in zip(value["splits"], SPLITS, strict=True):
        keys(row, ("split", "start", "end", *SPLIT_COUNTS))
        if (row["split"], row["start"], row["end"]) != (name, start, end):
            raise ValueError("Unexpected risk split dates")
        for key in SPLIT_COUNTS:
            integer(row[key])
    external = value["external"]
    if external is not None:
        keys(external, EXTERNAL_FIELDS)
        for key in EXTERNAL_FIELDS[:4]:
            integer(external[key])
        datetime.fromisoformat(external["source_loaded_at"])
        datetime.fromisoformat(external["observation_end"])
        if not re.fullmatch("[a-f0-9]{64}", external["input_sha256"]):
            raise ValueError("Invalid independent risk input hash")
    if not isinstance(value["models"], list):
        raise ValueError("Invalid risk model list")
    expected = ["prevalence", "prior_admission", "logistic"] if value["status"] == "evaluated" else []
    if [row.get("model") for row in value["models"]] != expected:
        raise ValueError("Unexpected risk models")
    for model in value["models"]:
        keys(model, ("model", "threshold", "validation", "test", "calibration", "external", "external_calibration"))
        rate(model["threshold"])
        metrics(model["validation"]); metrics(model["test"]); calibration(model["calibration"])
        if external is None:
            if model["external"] is not None or model["external_calibration"] is not None:
                raise ValueError("Independent metrics need cohort evidence")
        else:
            metrics(model["external"]); calibration(model["external_calibration"])
    return value


def load_public_risk(path, connection, database, raw_schema, loaded_at):
    if path is None:
        return None
    evidence = json.loads(Path(path).read_text())
    source = evidence.get("_source", {})
    if source.get("database") != database or source.get("schema") != raw_schema or evidence.get("source_loaded_at") != loaded_at:
        return None
    if evidence.get("external") is not None:
        schema = source.get("external_schema", "")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            return None
        if connection.scalar(text("SELECT to_regclass(:table)"), {"table": schema + ".loads"}) is None:
            return None
        latest = connection.scalar(text(f'SELECT max(loaded_at) FROM "{schema}".loads'))
        if latest != source.get("external_loaded_at") or latest != evidence["external"].get("source_loaded_at"):
            return None
    public = {key: evidence[key] for key in FIELDS}
    return validate_public_risk(public)
