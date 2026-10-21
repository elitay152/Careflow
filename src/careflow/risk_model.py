"""Fixed exploratory logistic protocol with patient-disjoint, chronological evaluation."""
from itertools import combinations
import warnings

from .hospitalization import FEATURES, SPLITS


def evaluate_models(rows, external_rows=None):
    import numpy as np
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, brier_score_loss, precision_recall_curve
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from threadpoolctl import threadpool_limits

    parts = {split: [r for r in rows if r["split"] == split] for split in ("train", "validation", "test")}
    windows = list(SPLITS)
    if external_rows is not None:
        parts["external"] = external_rows
        windows.append(("external", "2023-01-01", "2024-10-01"))
    for name, start, end in windows:
        if any(not start <= row["index_date"] <= end for row in parts[name]):
            raise ValueError("Snapshots must respect chronological split windows")
    ids = {name: {r["patient_id"] for r in part} for name, part in parts.items()}
    if any(ids[a] & ids[b] for a, b in combinations(parts, 2)):
        raise ValueError("Patients must not overlap across splits")
    x = {name: np.asarray([[r["features"][f] for f in FEATURES] for r in part], dtype=float) for name, part in parts.items()}
    labels = {name: np.asarray([r["outcome"] for r in part], dtype=int) for name, part in parts.items()}
    if any(set(y.tolist()) != {0, 1} for y in labels.values()):
        raise ValueError("Each split must contain both outcomes")
    pipeline = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, solver="lbfgs", max_iter=2000, random_state=0))
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        pipeline.fit(x["train"], labels["train"])
    train_rate = float(labels["train"].mean())
    admission_index = FEATURES.index("inpatient_episodes_365d")
    prior = x["train"][:, admission_index] > 0
    prior_rates = {value: (float(labels["train"][prior == value].sum()) + 1) / (int((prior == value).sum()) + 2) for value in (False, True)}

    def metrics(y, probabilities, threshold):
        predicted = probabilities >= threshold
        tp, fp = int(((y == 1) & predicted).sum()), int(((y == 0) & predicted).sum())
        fn, tn = int(((y == 1) & ~predicted).sum()), int(((y == 0) & ~predicted).sum())
        return {"average_precision": float(average_precision_score(y, probabilities)),
                "brier": float(brier_score_loss(y, probabilities)),
                "precision": tp / (tp+fp) if tp+fp else None, "recall": tp / (tp+fn),
                "true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn}

    def calibration(y, probabilities):
        bins = []
        bounds = (0.0, 0.01, 0.025, 0.05, 0.1, 1.0)
        for lower, upper in zip(bounds[:-1], bounds[1:], strict=True):
            mask = (probabilities >= lower) & ((probabilities <= upper) if upper == 1 else (probabilities < upper))
            count = int(mask.sum())
            bins.append({"lower": lower, "upper": upper, "snapshots": count,
                         "mean_risk": float(probabilities[mask].mean()) if count else None,
                         "observed_rate": float(y[mask].mean()) if count else None})
        return bins

    results = []
    for model in ("prevalence", "prior_admission", "logistic"):
        probabilities = {}
        for name in parts.keys() - {"train"}:
            if model == "prevalence":
                probabilities[name] = np.full(len(parts[name]), train_rate)
            elif model == "prior_admission":
                probabilities[name] = np.asarray([prior_rates[bool(n)] for n in x[name][:, admission_index] > 0])
            else:
                probabilities[name] = pipeline.predict_proba(x[name])[:, 1]
        precision, recall, thresholds = precision_recall_curve(labels["validation"], probabilities["validation"])
        # Optimize a decision threshold on validation only; no model refit or test tuning.
        choices = [(2*p*r/(p+r) if p+r else 0, p, float(t)) for p, r, t in zip(precision[:-1], recall[:-1], thresholds, strict=True)]
        threshold = max(choices)[2]
        results.append({"model": model, "threshold": threshold,
            "validation": metrics(labels["validation"], probabilities["validation"], threshold),
            "test": metrics(labels["test"], probabilities["test"], threshold),
            "calibration": calibration(labels["test"], probabilities["test"]),
            "external": metrics(labels["external"], probabilities["external"], threshold) if external_rows is not None else None,
            "external_calibration": calibration(labels["external"], probabilities["external"]) if external_rows is not None else None})
    return results
