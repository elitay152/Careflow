"""Local model-development comparison; never replaces public risk evidence."""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import argparse
import hashlib
import json
from importlib.metadata import version
import os
from pathlib import Path

from .hospitalization import (
    FEATURES,
    PRIVATE_ROOT,
    SPLITS,
    load_cohort,
    make_snapshots,
    moment,
    inpatient_episodes,
)

GROUPS = {
    "recorded_ischemic_heart_disease": ("414545008",),
    "recorded_heart_failure": ("88805009",),
    "recorded_kidney_disease": (
        "431855005",
        "431856006",
        "433144002",
        "431857002",
        "46177005",
    ),
    "recorded_obstructive_lung_disease": ("185086009", "87433001"),
    "recorded_asthma": ("195967001", "233678006"),
    "recorded_dementia": ("26929004", "230265002"),
}
ADDED_FEATURES = (
    "encounters_7d",
    "encounters_30d",
    "emergency_30d",
    "emergency_90d",
    "inpatient_episodes_90d",
    "inpatient_days_365d",
    "days_since_discharge",
    "encounters_previous_90d",
    "recorded_condition_codes",
    "new_condition_codes_90d",
    *GROUPS,
)


def enrich(rows, encounters, conditions):
    visits, diagnoses = defaultdict(list), defaultdict(list)
    for e in encounters:
        visits[e["patient_id"]].append(
            {**e, "start": moment(e["started_at"]), "end": moment(e["ended_at"])}
        )
    for c in conditions:
        if (
            c["recorded_date"]
            and c["onset_date"]
            and c["code_system"] == "http://snomed.info/sct"
        ):
            diagnoses[c["patient_id"]].append(
                (
                    date.fromisoformat(str(c["recorded_date"])),
                    date.fromisoformat(str(c["onset_date"])),
                    c["code"],
                )
            )
    result = []
    for row in rows:
        index = moment(row["index_date"])
        completed = [
            e
            for e in visits[row["patient_id"]]
            if e["status"] == "finished"
            and e["start"] < index
            and e["end"] is not None
            and e["end"] <= index
        ]
        recent = lambda days: [
            e for e in completed if e["start"] >= index - timedelta(days=days)
        ]
        # Build features from completed history only; future stays cannot extend an old episode.
        episodes = inpatient_episodes(completed)
        known = [
            (recorded, code)
            for recorded, onset, code in diagnoses[row["patient_id"]]
            if recorded < index.date() and onset < index.date()
        ]
        codes = {code for _, code in known}
        first_recorded = {
            code: min(recorded for recorded, c in known if c == code) for code in codes
        }
        values = {
            **row["features"],
            "encounters_7d": len(recent(7)),
            "encounters_30d": len(recent(30)),
            "emergency_30d": sum(
                e["class_code"] == "EMER"
                and e["class_system"]
                == "http://terminology.hl7.org/CodeSystem/v3-ActCode"
                for e in recent(30)
            ),
            "emergency_90d": sum(
                e["class_code"] == "EMER"
                and e["class_system"]
                == "http://terminology.hl7.org/CodeSystem/v3-ActCode"
                for e in recent(90)
            ),
            "inpatient_episodes_90d": sum(
                start >= index - timedelta(days=90) for start, end in episodes
            ),
            "inpatient_days_365d": sum(
                (end - max(start, index - timedelta(days=365))).total_seconds() / 86400
                for start, end in episodes
                if end > index - timedelta(days=365)
            ),
            "days_since_discharge": (
                min(366, (index - max(end for _, end in episodes)).days)
                if episodes
                else 366
            ),
            "encounters_previous_90d": len(recent(180)) - len(recent(90)),
            "recorded_condition_codes": len(codes),
            "new_condition_codes_90d": sum(
                recorded >= (index - timedelta(days=90)).date()
                for recorded in first_recorded.values()
            ),
            **{
                name: int(bool(codes.intersection(group)))
                for name, group in GROUPS.items()
            },
        }
        result.append({**row, "features": values})
    return result


def compare(rows, *, tabpfn=False, checkpoint=None):
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import (
        average_precision_score,
        brier_score_loss,
        precision_recall_curve,
    )
    from threadpoolctl import threadpool_limits

    parts = {
        name: [r for r in rows if r["split"] == name]
        for name in ("train", "validation", "test")
    }
    ids = {name: {r["patient_id"] for r in part} for name, part in parts.items()}
    if any(
        ids[a] & ids[b]
        for a, b in (("train", "validation"), ("train", "test"), ("validation", "test"))
    ):
        raise ValueError("Patient overlap")
    if any(
        not start <= r["index_date"] <= end
        for name, start, end in SPLITS
        for r in parts[name]
    ):
        raise ValueError("Invalid chronological split")
    results = []
    tabpfn_error = None
    for features_name, features in (
        ("original", FEATURES),
        ("enriched", (*FEATURES, *ADDED_FEATURES)),
    ):
        x = {
            name: np.asarray(
                [[r["features"][f] for f in features] for r in part], dtype=float
            )
            for name, part in parts.items()
        }
        y = {
            name: np.asarray([r["outcome"] for r in part])
            for name, part in parts.items()
        }
        if any(set(labels.tolist()) != {0, 1} for labels in y.values()):
            raise ValueError("Each split must contain both outcomes")
        estimators = [
            (
                "logistic",
                make_pipeline(
                    StandardScaler(),
                    LogisticRegression(C=1, max_iter=2000, random_state=0),
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
        ]
        if tabpfn and tabpfn_error is None:
            try:
                os.environ["TABPFN_DISABLE_TELEMETRY"] = "1"
                from tabpfn import TabPFNClassifier

                estimators.append(
                    (
                        "tabpfn",
                        TabPFNClassifier(
                            device="cpu",
                            n_estimators=2,
                            random_state=0,
                            model_path=checkpoint or "auto",
                        ),
                    )
                )
            except Exception as exc:
                tabpfn_error = type(exc).__name__
        if tabpfn_error:
            results.append(
                {
                    "model": "tabpfn",
                    "features": features_name,
                    "status": "unavailable",
                    "error_type": tabpfn_error,
                }
            )
        for name, model in estimators:
            # Fixed uniform deterministic row sample for CPU-only TabPFN, independent of labels.
            indices = sorted(
                range(len(parts["train"])),
                key=lambda i: hashlib.sha256(
                    (
                        parts["train"][i]["patient_id"]
                        + ":"
                        + parts["train"][i]["index_date"]
                    ).encode()
                ).hexdigest(),
            )
            selected = indices[:1000] if name == "tabpfn" else list(range(len(indices)))
            try:
                with threadpool_limits(limits=2):
                    model.fit(x["train"][selected], y["train"][selected])
                    predictions = {
                        split: model.predict_proba(x[split])[:, 1]
                        for split in ("validation", "test")
                    }
            except Exception as exc:
                if name != "tabpfn":
                    raise
                tabpfn_error = type(exc).__name__
                results.append(
                    {
                        "model": name,
                        "features": features_name,
                        "status": "unavailable",
                        "error_type": tabpfn_error,
                    }
                )
                continue
            p, r, t = precision_recall_curve(y["validation"], predictions["validation"])
            threshold = max(
                (2 * a * b / (a + b) if a + b else 0, a, float(c))
                for a, b, c in zip(p[:-1], r[:-1], t, strict=True)
            )[2]
            metrics = {}
            for split, probabilities in predictions.items():
                labels = y[split]
                predicted = probabilities >= threshold
                tp = int(((labels == 1) & predicted).sum())
                fp = int(((labels == 0) & predicted).sum())
                metrics[split] = {
                    "average_precision": float(
                        average_precision_score(labels, probabilities)
                    ),
                    "brier": float(brier_score_loss(labels, probabilities)),
                    "precision": tp / (tp + fp) if tp + fp else None,
                    "recall": tp / int(labels.sum()),
                    "true_positive": tp,
                    "false_positive": fp,
                    "positive_snapshots": int(labels.sum()),
                    "snapshots": len(labels),
                }
            results.append(
                {
                    "model": name,
                    "features": features_name,
                    "status": "evaluated",
                    "training_snapshots": len(selected),
                    "training_positive_snapshots": int(y["train"][selected].sum()),
                    "threshold": threshold,
                    **metrics,
                }
            )
    return {
        "package_versions": {name: version(name) for name in ("scikit-learn", "numpy")},
        "protocol": "admission_model_development_v1",
        "evaluation_role": "development_only_previously_inspected_test",
        "features": list((*FEATURES, *ADDED_FEATURES)),
        "results": results,
        "limitations": [
            "Repeated snapshots are correlated.",
            "No independent performance claim: inspected test is development evidence.",
            "Diagnosis flags indicate historical recording, not current severity.",
            "Socioeconomic and observation measurements are not ingested.",
            "CPU TabPFN uses a fixed 1000-row label-independent training subset; full-data models have a different training budget.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-synthetic", action="store_true", required=True)
    parser.add_argument("--raw-schema", required=True)
    parser.add_argument("--observation-end", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--tabpfn", action="store_true")
    parser.add_argument(
        "--tabpfn-checkpoint",
        help="Trusted official v2 checkpoint downloaded locally; never load an untrusted pickle",
    )
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if (
        PRIVATE_ROOT.resolve() != PRIVATE_ROOT
        or not output.is_relative_to(PRIVATE_ROOT)
        or output == PRIVATE_ROOT
    ):
        raise ValueError("Output must be under ignored data/risk")
    from sqlalchemy import create_engine

    if date.fromisoformat(args.observation_end).isoformat() != args.observation_end:
        raise ValueError("Observation cutoff must be an ISO date")
    patients, encounters, conditions, loaded = load_cohort(
        create_engine(os.environ["DATABASE_URL"]), args.raw_schema
    )
    rows, summary = make_snapshots(
        patients, encounters, conditions, args.observation_end
    )
    if summary["blockers"]:
        raise ValueError("Readiness gates failed")
    if args.tabpfn_checkpoint and not args.tabpfn:
        raise ValueError("--tabpfn-checkpoint requires --tabpfn")
    enriched = enrich(rows, encounters, conditions)
    result = compare(
        enriched,
        tabpfn=args.tabpfn,
        checkpoint=args.tabpfn_checkpoint,
    )
    if args.tabpfn:
        from importlib.metadata import PackageNotFoundError

        try:
            result["package_versions"]["tabpfn"] = version("tabpfn")
            result["package_versions"]["torch"] = version("torch")
        except PackageNotFoundError:
            pass
    if args.tabpfn_checkpoint:
        with Path(args.tabpfn_checkpoint).open("rb") as stream:
            result["checkpoint_sha256"] = hashlib.file_digest(
                stream, "sha256"
            ).hexdigest()
    result["experiment_input_sha256"] = hashlib.sha256(
        json.dumps(enriched, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    result["evaluated_at"] = datetime.now(timezone.utc).isoformat()
    result["source_loaded_at"] = loaded
    result["observation_end"] = args.observation_end
    result["source_schema"] = args.raw_schema
    result["source_input_sha256"] = summary["input_sha256"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print("Local aggregate experiment written; public dashboard evidence unchanged.")


if __name__ == "__main__":
    main()
