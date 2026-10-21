"""Event-dated measurements; missingness and time since measurement remain explicit."""

from collections import defaultdict
from datetime import timedelta

from .hospitalization import moment
from .observations import UNITS

METRICS = ("bmi", "hba1c", "egfr", "creatinine", "systolic", "diastolic")
CLINICAL_FEATURES = tuple(
    f"{metric}_{suffix}"
    for metric in METRICS
    for suffix in ("latest", "age_days", "missing", "trend_90d", "trend_missing")
)


def add_measurements(rows, observations):
    history = defaultdict(list)
    for observation in observations:
        # Amended/corrected records lack the prior version needed for historical reconstruction.
        if observation["status"] != "final" or observation["available_at"] is None:
            continue
        history[observation["patient_id"]].append(
            (
                moment(observation["effective_at"]),
                moment(observation["available_at"]),
                observation["id"],
                observation["measurements"],
            )
        )
    result = []
    for row in rows:
        index = moment(row["index_date"])
        candidates = [
            o
            for o in history[row["patient_id"]]
            if index - timedelta(days=365) <= o[0] < index and o[1] < index
        ]
        values = dict(row["features"])
        for metric in METRICS:
            measured = sorted(
                (effective, available, oid, measurements[metric]["value"])
                for effective, available, oid, measurements in candidates
                if metric in measurements
                and measurements[metric]["unit"] == UNITS[metric]
            )
            # Identical effective timestamps represent one time point, not a longitudinal trend.
            latest = measured[-1] if measured else None
            earlier = [
                m for m in measured if latest and m[0] <= latest[0] - timedelta(days=30)
            ]
            previous = earlier[-1] if earlier else None
            trend = (
                (
                    (latest[3] - previous[3])
                    * 90
                    / ((latest[0] - previous[0]).total_seconds() / 86400)
                )
                if previous
                else None
            )
            values.update(
                {
                    f"{metric}_latest": latest[3] if latest else None,
                    f"{metric}_age_days": (
                        (index - latest[0]).total_seconds() / 86400 if latest else None
                    ),
                    f"{metric}_missing": int(latest is None),
                    f"{metric}_trend_90d": trend,
                    f"{metric}_trend_missing": int(previous is None),
                }
            )
        result.append({**row, "features": values})
    return result
