"""Read-only development investigation of the inpatient target and alert stability."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re

from sqlalchemy import create_engine, text
from .hospitalization import (
    FEATURES,
    PRIVATE_ROOT,
    inpatient_episodes,
    make_snapshots,
    moment,
    partition,
)

SNOMED = "http://snomed.info/sct"
CLASS_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
# Exact source codes describe context; surgery/ICU alone do not establish urgency.
CONTEXT = {
    "449411000124106": "skilled_nursing",
    "56876005": "rehabilitation_detox",
    "183495009": "explicit_nonurgent_orthopedic",
    "305408004": "surgical_department",
    "305411003": "surgical_department",
    "305432006": "transplant_department",
    "305351004": "intensive_care",
    "397821002": "intensive_care",
    "1505002": "isolation",
    "305342007": "ward",
    "308251003": "oncology_department",
    "76464004": "hospital_observation",
    "32485007": "generic_hospital_admission",
    "185347001": "generic_problem",
    "185389009": "followup",
    "387713003": "surgical_procedure",
}
FOLDS = (2019, 2020, 2021, 2022)
BUDGET = 0.05


def episode_inventory(patients, encounters):
    by_patient = defaultdict(list)
    for raw in encounters:
        by_patient[raw["patient_id"]].append(
            {**raw, "start": moment(raw["started_at"]), "end": moment(raw["ended_at"])}
        )
    episodes = {}
    contexts = Counter()
    types = Counter()
    reasons = Counter()
    adult = 0
    nonurgent = 0
    emergency = 0
    people = {p["id"]: p for p in patients}
    for pid, events in by_patient.items():
        actual = [
            e for e in events if e["status"] in {"finished", "in-progress", "onleave"}
        ]
        inpatient = [
            e
            for e in actual
            if e["class_code"] == "IMP" and e["class_system"] == CLASS_SYSTEM
        ]
        episodes[pid] = []
        for start, end in inpatient_episodes(actual):
            members = [
                e
                for e in inpatient
                if start <= e["start"] and (end is None or e["start"] <= end)
            ]
            codes = {
                c["code"]
                for e in members
                for c in (e.get("type_codes") or [])
                if c["system"] == SNOMED
            }
            tags = {CONTEXT[c] for c in codes if c in CONTEXT}
            if not tags:
                tags = {"unclassified"}
            episodes[pid].append((start, end, tags))
            born = moment(people[pid]["birth_date"])
            age = (
                start.year
                - born.year
                - ((start.month, start.day) < (born.month, born.day))
            )
            if age < 18 or not moment("2015-01-01") <= start < moment("2025-01-01"):
                continue
            adult += 1
            contexts.update(tags)
            types.update(codes)
            reasons.update(
                {
                    (c["system"], c["code"])
                    for e in members
                    for c in (e.get("reason_codes") or [])
                }
            )
            nonurgent += int("explicit_nonurgent_orthopedic" in tags)
            emergency += int(
                any(
                    e["class_code"] == "EMER"
                    and e["class_system"] == CLASS_SYSTEM
                    and start - timedelta(days=1) <= e["start"] <= start
                    for e in actual
                )
            )
    return episodes, {
        "adult_episodes_2015_2024": adult,
        "episode_context_memberships": dict(contexts),
        "type_code_episode_counts": dict(types),
        "reason_code_episode_counts": [
            {"system": s, "code": c, "episodes": n}
            for (s, c), n in reasons.most_common()
        ],
        "explicit_nonurgent_episodes": nonurgent,
        "urgency_not_established_by_type": adult - nonurgent,
        "episodes_with_emergency_start_in_previous_24h": emergency,
        "limitations": [
            "Context memberships overlap; do not add their counts.",
            "Surgery and intensive care do not establish elective or emergency status.",
            "No validated childbirth or unplanned-admission classification is inferred.",
            "An IMP encounter is not necessarily an acute hospital admission.",
        ],
    }


def admission_mix(rows, episodes):
    result = {}
    for split in ("train", "validation", "test"):
        positive = [r for r in rows if r["split"] == split and r["outcome"]]
        counts = Counter()
        for row in positive:
            index = moment(row["index_date"])
            horizon = index + timedelta(days=90)
            tags = {
                tag
                for start, _, tags in episodes.get(row["patient_id"], [])
                if index < start <= horizon
                for tag in tags
            }
            if not tags:
                raise ValueError("Positive snapshot has no matching inpatient episode")
            counts.update(tags)
        result[split] = {
            "positive_snapshots": len(positive),
            "positive_patients": len({r["patient_id"] for r in positive}),
            "context_memberships": dict(counts),
        }
    return result


def choose_f1(labels, probabilities):
    from sklearn.metrics import precision_recall_curve

    p, r, t = precision_recall_curve(labels, probabilities)
    return max(
        (2 * a * b / (a + b) if a + b else 0, a, float(c))
        for a, b, c in zip(p[:-1], r[:-1], t, strict=True)
    )[2]


def budget_threshold(probabilities):
    import numpy as np

    budget = math.floor(BUDGET * len(probabilities))
    return float(np.nextafter(sorted(probabilities, reverse=True)[budget], np.inf))


def top_budget(rows, probabilities):
    selected = [False] * len(rows)
    batches = defaultdict(list)
    for i, row in enumerate(rows):
        batches[row["index_date"]].append(i)
    for indices in batches.values():
        ranked = sorted(
            indices,
            key=lambda i: (
                -probabilities[i],
                hashlib.sha256(rows[i]["patient_id"].encode()).hexdigest(),
            ),
        )
        for i in ranked[: math.floor(BUDGET * len(indices))]:
            selected[i] = True
    return selected


def decision_metrics(rows, predicted):
    tp = sum(
        bool(p) and r["outcome"] == 1 for r, p in zip(rows, predicted, strict=True)
    )
    fp = sum(
        bool(p) and r["outcome"] == 0 for r, p in zip(rows, predicted, strict=True)
    )
    positives = sum(r["outcome"] for r in rows)
    return {
        "snapshots": len(rows),
        "patients": len({r["patient_id"] for r in rows}),
        "positive_snapshots": positives,
        "positive_patients": len({r["patient_id"] for r in rows if r["outcome"]}),
        "alerts": tp + fp,
        "alert_fraction": (tp + fp) / len(rows),
        "true_positive_snapshots": tp,
        "false_positive_snapshots": fp,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / positives if positives else None,
    }


def evaluate_fold(train, calibration, score, *, bootstrap=False):
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import average_precision_score, brier_score_loss
    from threadpoolctl import threadpool_limits

    groups = (train, calibration, score)
    ids = [{r["patient_id"] for r in group} for group in groups]
    if any(ids[i] & ids[j] for i, j in ((0, 1), (0, 2), (1, 2))):
        raise ValueError("Patient overlap across fold roles")
    if (
        not all(groups)
        or not max(r["index_date"] for r in train)
        < min(r["index_date"] for r in calibration)
        or not max(r["index_date"] for r in calibration)
        < min(r["index_date"] for r in score)
    ):
        raise ValueError("Fold roles must be chronological and nonempty")
    roles = {
        role: {
            "snapshots": len(group),
            "patients": len({r["patient_id"] for r in group}),
            "positive_snapshots": sum(r["outcome"] for r in group),
            "positive_patients": len({r["patient_id"] for r in group if r["outcome"]}),
        }
        for role, group in zip(
            ("train", "calibration", "development_score"), groups, strict=True
        )
    }
    warnings = [
        role + "_few_positive_patients"
        for role, counts in roles.items()
        if counts["positive_patients"] < (50 if role == "train" else 10)
    ]
    if any({r["outcome"] for r in group} != {0, 1} for group in groups):
        return {
            "status": "insufficient_outcomes",
            "roles": roles,
            "warnings": warnings,
            "models": [],
        }
    x = [
        np.asarray([[r["features"][f] for f in FEATURES] for r in group], dtype=float)
        for group in groups
    ]
    y = [np.asarray([r["outcome"] for r in group]) for group in groups]
    models = []
    for name, estimator in [
        ("prevalence", None),
        (
            "logistic",
            make_pipeline(
                StandardScaler(), LogisticRegression(C=1, max_iter=2000, random_state=0)
            ),
        ),
        (
            "gradient_boosting",
            HistGradientBoostingClassifier(
                max_iter=100,
                max_leaf_nodes=7,
                min_samples_leaf=30,
                l2_regularization=1,
                early_stopping=False,
                random_state=0,
            ),
        ),
    ]:
        with threadpool_limits(limits=2):
            if estimator is None:
                cp = np.full(len(calibration), float(y[0].mean()))
                sp = np.full(len(score), float(y[0].mean()))
            else:
                estimator.fit(x[0], y[0])
                cp = estimator.predict_proba(x[1])[:, 1]
                sp = estimator.predict_proba(x[2])[:, 1]
        threshold = choose_f1(y[1], cp)
        limited = budget_threshold(cp)
        policies = {
            "calibration_f1": {
                "threshold": threshold,
                **decision_metrics(score, sp >= threshold),
            },
            "calibration_5pct_threshold": {
                "threshold": limited,
                "calibration_alert_fraction": float((cp >= limited).mean()),
                **decision_metrics(score, sp >= limited),
            },
            "per_snapshot_top5pct": decision_metrics(score, top_budget(score, sp)),
        }
        instability = None
        if bootstrap:
            rng = np.random.default_rng(0)
            people = sorted(ids[1])
            positions = {
                pid: [i for i, r in enumerate(calibration) if r["patient_id"] == pid]
                for pid in people
            }
            thresholds = []
            for _ in range(200):
                selected = [
                    i
                    for pid in rng.choice(people, size=len(people), replace=True)
                    for i in positions[pid]
                ]
                if len(set(y[1][selected])) == 2:
                    thresholds.append(choose_f1(y[1][selected], cp[selected]))
            instability = {
                "patient_bootstrap_resamples": 200,
                "valid_resamples": len(thresholds),
                "f1_threshold_p10_p50_p90": (
                    [float(v) for v in np.quantile(thresholds, [0.1, 0.5, 0.9])]
                    if thresholds
                    else None
                ),
                "interpretation": "Calibration-only sensitivity diagnostic, not a performance confidence interval.",
            }
        models.append(
            {
                "model": name,
                "calibration_average_precision": float(
                    average_precision_score(y[1], cp)
                ),
                "development_average_precision": float(
                    average_precision_score(y[2], sp)
                ),
                "development_brier": float(brier_score_loss(y[2], sp)),
                "policies": policies,
                "threshold_stability": instability,
            }
        )
    return {
        "status": "evaluated",
        "roles": roles,
        "warnings": warnings,
        "models": models,
    }


def rolling_diagnostics(rows):
    results = []
    for year in FOLDS:
        train = [
            r
            for r in rows
            if partition(r["patient_id"]) == "train"
            and int(r["index_date"][:4]) <= year - 3
        ]
        cal = [
            r
            for r in rows
            if partition(r["patient_id"]) == "validation"
            and year - 2 <= int(r["index_date"][:4]) < year
        ]
        score = [
            r
            for r in rows
            if partition(r["patient_id"]) == "test" and int(r["index_date"][:4]) == year
        ]
        results.append(
            {
                "score_year": year,
                "train_through": year - 3,
                "calibration_years": [year - 2, year - 1],
                **evaluate_fold(train, cal, score),
            }
        )
    original = [
        [
            r
            for r in rows
            if partition(r["patient_id"]) == role and lo <= r["index_date"] <= hi
        ]
        for role, lo, hi in [
            ("train", "2015-01-01", "2020-10-01"),
            ("validation", "2021-01-01", "2022-10-01"),
            ("test", "2023-01-01", "2024-10-01"),
        ]
    ]
    return {
        "earlier_windows": results,
        "original_development_window": evaluate_fold(*original, bootstrap=True),
        "alert_budget": BUDGET,
        "features": list(FEATURES),
        "limitations": [
            "All results are development evidence; no untouched evaluation is claimed.",
            "Patients recur across rolling windows; windows are not independent studies.",
            "F1 and fixed thresholds use calibration outcomes only.",
            "Top-5% ranking uses only scores available at each snapshot date; ties use a fixed patient hash.",
            "A calibration-derived 5% threshold may alert more than 5% later under distribution shift.",
            "The floor of 5% is enforced per snapshot; small batches may have no alerts.",
        ],
    }


def run(engine, schema, output):
    root = Path(output).resolve()
    if (
        PRIVATE_ROOT.resolve() != PRIVATE_ROOT
        or not root.is_relative_to(PRIVATE_ROOT)
        or root == PRIVATE_ROOT
    ):
        raise ValueError("Output must be under ignored data/risk")
    if engine.dialect.name != "postgresql" or not re.fullmatch(
        r"[a-z][a-z0-9_]{0,62}", schema
    ):
        raise ValueError("PostgreSQL and simple schema required")
    with engine.connect() as connection:
        connection.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )

        def fetch(sql):
            return [dict(r) for r in connection.execute(text(sql)).mappings()]

        patients = fetch(
            f'SELECT id,birth_date,death_date,deceased FROM "{schema}".patients ORDER BY id'
        )
        encounters = fetch(
            f'SELECT id,patient_id,status,started_at,ended_at,class_code,class_system,type_codes,reason_codes FROM "{schema}".encounters ORDER BY id'
        )
        conditions = fetch(
            f'SELECT patient_id,code,code_system,display,onset_date,recorded_date FROM "{schema}".conditions ORDER BY id'
        )
        loaded = connection.scalar(text(f'SELECT max(loaded_at) FROM "{schema}".loads'))
    rows, summary = make_snapshots(
        patients, encounters, conditions, "2025-01-01", all_periods=True
    )
    original, _ = make_snapshots(patients, encounters, conditions, "2025-01-01")
    episodes, mix = episode_inventory(patients, encounters)
    source_labels = defaultdict(set)
    for condition in conditions:
        source_labels[(condition["code_system"], condition["code"])].add(
            condition["display"]
        )
    for reason in mix["reason_code_episode_counts"]:
        reason["source_displays"] = sorted(
            source_labels[(reason["system"], reason["code"])]
        )
    result = {
        "protocol": "admission_diagnostics_v1",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "source_loaded_at": loaded,
        "source_schema": schema,
        "observation_end": "2025-01-01",
        "evaluation_role": "development_only",
        "input_sha256": summary["input_sha256"],
        "context_input_sha256": hashlib.sha256(
            json.dumps(
                {"patients": patients, "encounters": encounters},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "admission_inventory": mix,
        "positive_snapshot_contexts": admission_mix(original, episodes),
        "stability": rolling_diagnostics(rows),
    }
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--confirm-synthetic", action="store_true", required=True)
    p.add_argument("--raw-schema", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    run(create_engine(os.environ["DATABASE_URL"]), a.raw_schema, a.output)
    print(
        "Aggregate development investigation written under ignored data/risk; no public exports changed."
    )


if __name__ == "__main__":
    main()
