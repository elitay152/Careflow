"""Fixed earlier-season residual bands for a retrospective flu experiment."""

from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path

INTERVAL_PROTOCOL_PATH = (
    Path(__file__).resolve().parents[2] / "config/flusight-interval-protocol.json"
)


def interval_bounds(point, radius):
    if any(not math.isfinite(x) or x < 0 for x in (point, radius)):
        raise ValueError("Interval inputs must be finite and nonnegative")
    return max(0, math.floor(point - radius)), math.ceil(point + radius)


def interval_metrics(rows, model, radius, level):
    if not rows:
        return {
            "n": 0,
            "coverage": None,
            "mean_width": None,
            "below": 0,
            "above": 0,
            "mean_interval_score": None,
        }
    below = above = 0
    widths, scores = [], []
    alpha = 1 - level / 100
    for row in rows:
        lower, upper = interval_bounds(row[model], radius)
        actual = row["actual"]
        below += actual < lower
        above += actual > upper
        widths.append(upper - lower)
        scores.append(
            upper
            - lower
            + 2 / alpha * (max(0, lower - actual) + max(0, actual - upper))
        )
    n = len(rows)
    return {
        "n": n,
        "coverage": (n - below - above) / n,
        "mean_width": sum(widths) / n,
        "below": below,
        "above": above,
        "mean_interval_score": sum(scores) / n,
    }


def build_intervals(validation, holdout, point_protocol):
    protocol = json.loads(INTERVAL_PROTOCOL_PATH.read_text())
    cutoff = protocol["calibration_end"]
    earliest_origin = (
        date.fromisoformat(point_protocol["holdout"]["start"])
        - timedelta(weeks=max(point_protocol["horizons"]))
    ).isoformat()
    if cutoff > earliest_origin:
        raise ValueError("Interval calibration overlaps a holdout forecast origin")
    observed = [r for r in holdout if r["horizon"] == 1]
    peak = max(observed, key=lambda r: r["actual"])["week"]
    peak_date = date.fromisoformat(peak)
    start, end = ((peak_date + timedelta(weeks=w)).isoformat() for w in (-2, 2))
    result = []
    for horizon in point_protocol["horizons"]:
        calibration = [
            r
            for r in validation
            if r["horizon"] == horizon
            and protocol["calibration_start"] <= r["week"] <= cutoff
        ]
        part = [r for r in holdout if r["horizon"] == horizon]
        if not calibration or not part:
            raise ValueError("Intervals require calibration and evaluation predictions")
        n = len(calibration)
        ranks = [
            {"level": level, "rank": math.ceil((n + 1) * level / 100)}
            for level in protocol["levels"]
        ]
        if any(r["rank"] > n for r in ranks):
            raise ValueError("Insufficient calibration sample for interval quantile")
        models = []
        for model in point_protocol["models"]:
            residuals = sorted(abs(r["actual"] - r[model]) for r in calibration)
            bands = []
            for item in ranks:
                level, rank = item["level"], item["rank"]
                radius = residuals[rank - 1]
                phases = []
                for phase in ("before", "peak", "after"):
                    subset = [
                        r
                        for r in part
                        if (
                            "before"
                            if r["week"] < start
                            else "after" if r["week"] > end else "peak"
                        )
                        == phase
                    ]
                    phases.append(
                        {
                            "phase": phase,
                            **interval_metrics(subset, model, radius, level),
                        }
                    )
                bands.append(
                    {
                        "level": level,
                        "radius": radius,
                        "metrics": interval_metrics(part, model, radius, level),
                        "by_phase": phases,
                    }
                )
            models.append({"model": model, "bands": bands})
        result.append(
            {
                "horizon": horizon,
                "calibration_n": n,
                "quantile_ranks": ranks,
                "models": models,
            }
        )
    canonical = json.dumps(
        protocol, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return {
        "protocol": protocol,
        "protocol_sha256": hashlib.sha256(canonical).hexdigest(),
        "by_horizon": result,
    }
