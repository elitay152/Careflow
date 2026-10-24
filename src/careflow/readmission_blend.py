"""Development-only UCI probability blends; never score validation or test."""

import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
import warnings

from careflow import uci_readmission as uci

WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)
FOLDS = 5


def experiment(rows, protocol):
    import numpy as np
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.model_selection import GroupKFold
    from threadpoolctl import threadpool_limits

    # Discard held-out rows before accessing any outcomes or features.
    train = [r for r in rows if r["split"] == "train"]
    if len({r["patient"] for r in train}) < FOLDS:
        raise ValueError("At least five training patients are required")
    if any(len({r["features"][k] for r in train}) > 64 for k in uci.CATEGORICAL):
        raise ValueError("Unexpected categorical cardinality")
    names = [*uci.NUMERIC, *uci.CATEGORICAL]
    x = np.asarray([[r["features"][k] for k in names] for r in train], dtype=object)
    y = np.asarray([r["outcome"] for r in train])
    groups = np.asarray([r["patient"] for r in train])
    oof = {name: np.full(len(train), np.nan) for name in ("logistic", "boosting")}
    fold_sizes = []
    splitter = GroupKFold(n_splits=FOLDS, shuffle=True, random_state=24)
    for fit, score in splitter.split(x, y, groups):
        if set(groups[fit]) & set(groups[score]):
            raise ValueError("Patients overlap within a fold")
        if set(y[fit]) != {0, 1} or set(y[score]) != {0, 1}:
            raise ValueError("Every fold requires both outcomes")
        for name in oof:
            fitted = uci.make_estimator(name, protocol)
            with threadpool_limits(limits=1), warnings.catch_warnings():
                warnings.simplefilter("error", ConvergenceWarning)
                fitted.fit(x[fit], y[fit])
                oof[name][score] = fitted.predict_proba(x[score])[:, 1]
        fold_sizes.append({"fit_encounters": len(fit), "score_encounters": len(score)})
    candidates = []
    for weight in WEIGHTS:
        probabilities = (1 - weight) * oof["logistic"] + weight * oof["boosting"]
        candidates.append(
            {"boosting_weight": weight, "metrics": uci.metrics(train, probabilities)}
        )
    # Fixed tie order favors the lower boosting weight, including pure logistic.
    selected = max(candidates, key=lambda c: c["metrics"]["average_precision"])
    return {
        "role": "Exploratory training-only out-of-fold model selection; not an unbiased assessment of the selected blend",
        "training_encounters": len(train),
        "training_patients": len(set(groups)),
        "folds": fold_sizes,
        "selected_boosting_weight": selected["boosting_weight"],
        "candidates": candidates,
        "validation_scored": False,
        "test_scored": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument(
        "--expected-sha256",
        required=True,
        help="Reviewed CSV fingerprint from the deployed benchmark",
    )
    args = parser.parse_args()
    if len(args.expected_sha256) != 64 or any(
        c not in "0123456789abcdef" for c in args.expected_sha256
    ):
        parser.error("Expected a lowercase SHA-256 fingerprint")
    if args.download:
        metadata = uci.fetch(uci.API_URL, 2 * 1024 * 1024)
        license_page = uci.fetch(uci.PAGE_URL, 4 * 1024 * 1024)
        raw = uci.fetch(uci.validate_source(metadata, license_page), uci.MAX_CSV)
    else:
        metadata = uci.read_bounded(uci.CACHE / "metadata.json", 2 * 1024 * 1024)
        license_page = uci.read_bounded(
            uci.CACHE / "license-page.html", 4 * 1024 * 1024
        )
        raw = uci.read_bounded(uci.CACHE / "data.csv", uci.MAX_CSV)
    uci.validate_source(metadata, license_page)
    if uci.digest(raw) != args.expected_sha256:
        raise ValueError("UCI source differs from the reviewed benchmark")
    protocol = json.loads(uci.PROTOCOL_PATH.read_text())
    rows, _ = uci.parse_records(raw, protocol)
    result = {
        "experiment": "uci296_training_oof_blend_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "csv_sha256": uci.digest(raw),
        "benchmark_protocol_sha256": uci.digest(uci.canonical(protocol)),
        "scikit_learn": version("scikit-learn"),
        "method": {
            "folds": FOLDS,
            "splitter": "GroupKFold",
            "shuffle": True,
            "random_state": 24,
            "boosting_weights": WEIGHTS,
            "selection": "Highest pooled out-of-fold AP; ties choose lower boosting weight",
            "preprocessing": "Fit separately inside each fold using unchanged benchmark estimators",
        },
        "evaluation": experiment(rows, protocol),
    }
    output = uci.ROOT / "data/experiments/readmission-blend.json"
    uci.atomic_write(
        output, json.dumps(result, indent=2, allow_nan=False).encode() + b"\n"
    )
    print(
        "Training-only aggregate experiment saved to data/experiments/readmission-blend.json"
    )


if __name__ == "__main__":
    main()
