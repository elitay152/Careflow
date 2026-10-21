"""Pinned public influenza counts and a predefined rolling-origin benchmark."""

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
from urllib.request import urlopen

from .forecast import score

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = ROOT / "config/flusight-protocol.json"
MAX_BYTES = 8 * 1024 * 1024
MAX_SAFE_INTEGER = 2**53 - 1


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def parse_source(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError("FluSight source exceeds size limit")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if reader.fieldnames != [
        "date",
        "location",
        "location_name",
        "value",
        "weekly_rate",
    ]:
        raise ValueError("Unexpected FluSight columns")
    seen = set()
    history = []
    locations = set()
    missing_states = 0
    row_count = 0
    for row in reader:
        if None in row or None in row.values():
            raise ValueError("Malformed CSV row")
        week = date.fromisoformat(row["date"])
        if week.isoformat() != row["date"] or week.weekday() != 5:
            raise ValueError("Weeks must end on Saturday")
        location = row["location"]
        if location != "US" and (
            len(location) != 2 or not location.isascii() or not location.isdigit()
        ):
            raise ValueError("Invalid location code")
        key = (week, location)
        if key in seen:
            raise ValueError("Duplicate location-week")
        seen.add(key)
        locations.add(location)
        row_count += 1
        missing = row["value"] in ("", "NA")
        if missing and location == "US":
            raise ValueError("National observations cannot be missing")
        if missing:
            missing_states += 1
            continue
        value = float(row["value"])
        if (
            not math.isfinite(value)
            or value < 0
            or not value.is_integer()
            or value > MAX_SAFE_INTEGER
        ):
            raise ValueError("Counts must be finite nonnegative safe integers")
        if location == "US":
            history.append({"week": week.isoformat(), "count": int(value)})
    history.sort(key=lambda r: r["week"])
    if len(history) < 2 or any(
        date.fromisoformat(b["week"]) - date.fromisoformat(a["week"])
        != timedelta(days=7)
        for a, b in zip(history, history[1:])
    ):
        raise ValueError("National series must contain consecutive observed weeks")
    return history, {
        "rows": row_count,
        "locations": len(locations),
        "missing_state_values": missing_states,
        "national_missing_values": 0,
        "duplicate_location_weeks": 0,
        "national_week_gaps": 0,
    }


def predict(history, model, protocol, horizon=4):
    import numpy as np

    if (
        len(history) < protocol["minimum_history_weeks"]
        or model not in protocol["models"]
        or horizon not in protocol["horizons"]
    ):
        raise ValueError("Unsupported model, horizon, or insufficient history")
    values = [r["count"] for r in history]
    if model == "last_value":
        return [float(values[-1])] * horizon
    if model == "seasonal_naive":
        return [float(values[-53 + h]) for h in range(1, horizon + 1)]
    logs = np.log1p(values)
    if model == "damped_growth":
        width = protocol["growth_window_weeks"]
        t = np.arange(width, dtype=float)
        t -= t.mean()
        slope = float(np.dot(t, logs[-width:] - logs[-width:].mean()) / np.dot(t, t))
        slope = max(
            -protocol["growth_log_slope_bound"],
            min(protocol["growth_log_slope_bound"], slope),
        )
        result = [
            math.expm1(
                max(
                    0,
                    float(logs[-1])
                    + slope
                    * sum(protocol["growth_damping"] ** k for k in range(1, h + 1)),
                )
            )
            for h in range(1, horizon + 1)
        ]
    else:
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        def features(series, index, week):
            phase = 2 * math.pi * (week - date(2000, 1, 1)).days / 365.25
            return [series[index - lag] for lag in (1, 2, 3, 4, 52)] + [
                float(np.mean(series[index - 4 : index])),
                math.sin(phase),
                math.cos(phase),
            ]

        start = max(52, len(values) - protocol["autoregression_window_weeks"])
        x = [
            features(logs, i, date.fromisoformat(history[i]["week"]))
            for i in range(start, len(values))
        ]
        estimator = make_pipeline(
            StandardScaler(), Ridge(alpha=protocol["ridge_alpha"])
        )
        estimator.fit(x, logs[start:])
        extended = list(logs)
        last = date.fromisoformat(history[-1]["week"])
        result = []
        for h in range(1, horizon + 1):
            value = max(
                0,
                float(
                    estimator.predict(
                        [features(extended, len(extended), last + timedelta(weeks=h))]
                    )[0]
                ),
            )
            extended.append(value)
            result.append(math.expm1(value))
    if any(not math.isfinite(v) or v < 0 for v in result):
        raise ValueError("Nonfinite forecast")
    return result


def metric_rows(predictions, protocol):
    results = []
    for model in protocol["models"]:
        overall = score(
            [r["actual"] for r in predictions], [r[model] for r in predictions], model
        )
        by_horizon = []
        for h in protocol["horizons"]:
            part = [r for r in predictions if r["horizon"] == h]
            values = score([r["actual"] for r in part], [r[model] for r in part], model)
            by_horizon.append(
                {
                    "horizon": h,
                    "n": len(part),
                    **{k: v for k, v in values.items() if k != "model"},
                }
            )
        results.append({**overall, "n": len(predictions), "by_horizon": by_horizon})
    return results


def peak_diagnostics(predictions, protocol):
    """Descriptive analysis after scoring; never used for selection or gating."""
    observed = sorted(
        (r for r in predictions if r["horizon"] == 1), key=lambda r: r["week"]
    )
    peak = max(observed, key=lambda r: r["actual"])
    peak_date = date.fromisoformat(peak["week"])
    start, end = ((peak_date + timedelta(weeks=w)).isoformat() for w in (-2, 2))
    horizons = []
    for horizon in protocol["horizons"]:
        rows = sorted(
            (r for r in predictions if r["horizon"] == horizon), key=lambda r: r["week"]
        )
        models = []
        for model in protocol["models"]:
            total = sum(abs(r[model] - r["actual"]) for r in rows)
            phases = []
            for phase in ("before", "peak", "after"):
                part = [
                    r
                    for r in rows
                    if (
                        "before"
                        if r["week"] < start
                        else "after" if r["week"] > end else "peak"
                    )
                    == phase
                ]
                absolute = sum(abs(r[model] - r["actual"]) for r in part)
                phases.append(
                    {
                        "phase": phase,
                        "n": len(part),
                        "mae": absolute / len(part) if part else None,
                        "bias": (
                            sum(r[model] - r["actual"] for r in part) / len(part)
                            if part
                            else None
                        ),
                        "error_share": absolute / total if total else None,
                    }
                )
            predicted_peak = max(rows, key=lambda r: r[model])
            models.append(
                {
                    "model": model,
                    "phases": phases,
                    "peak": {
                        "week": predicted_peak["week"],
                        "count": predicted_peak[model],
                        "lag_weeks": (
                            date.fromisoformat(predicted_peak["week"]) - peak_date
                        ).days
                        // 7,
                        "count_error": predicted_peak[model] - peak["actual"],
                    },
                }
            )
        horizons.append({"horizon": horizon, "models": models})
    return {
        "method": "descriptive_peak_window_v1",
        "observed_peak": {"week": peak["week"], "count": peak["actual"]},
        "peak_window": {"start": start, "end": end},
        "by_horizon": horizons,
    }


def evaluate(history, protocol):
    from threadpoolctl import threadpool_limits

    cache = {}

    def window(start, end):
        rows = []
        for i, row in enumerate(history):
            if not start <= row["week"] <= end:
                continue
            for h in protocol["horizons"]:
                origin = i - h
                if origin + 1 < protocol["minimum_history_weeks"]:
                    raise ValueError("Evaluation window lacks required prior history")
                predictions = {}
                for model in protocol["models"]:
                    key = (origin, model)
                    if key not in cache:
                        cache[key] = predict(history[: origin + 1], model, protocol)
                    predictions[model] = cache[key][h - 1]
                rows.append(
                    {
                        "week": row["week"],
                        "horizon": h,
                        "actual": row["count"],
                        **predictions,
                    }
                )
        expected = (date.fromisoformat(end) - date.fromisoformat(start)).days // 7 + 1
        if len(rows) != expected * len(protocol["horizons"]):
            raise ValueError("Incomplete evaluation window")
        return rows

    with threadpool_limits(limits=1):
        validation = []
        seasons = []
        for season in protocol["validation"]:
            rows = window(season["start"], season["end"])
            validation.extend(rows)
            seasons.append(
                {**season, "targets": len(rows) // 4, "forecasts": len(rows)}
            )
        vm = metric_rows(validation, protocol)
        # Selection occurs before reading holdout scores; model order breaks exact ties.
        selected = min(vm, key=lambda r: r["mae"])["model"]
        held = window(protocol["holdout"]["start"], protocol["holdout"]["end"])
        hm = metric_rows(held, protocol)
        selected_mae = next(r["mae"] for r in hm if r["model"] == selected)
        publish = all(
            selected_mae < next(r["mae"] for r in hm if r["model"] == base)
            for base in ("last_value", "seasonal_naive")
        )
        future = None
        if publish:
            values = predict(history, selected, protocol)
            last = date.fromisoformat(history[-1]["week"])
            future = [
                {
                    "week": (last + timedelta(weeks=h)).isoformat(),
                    "horizon": h,
                    "prediction": values[h - 1],
                }
                for h in protocol["horizons"]
            ]
    return {
        "selected_model": selected,
        "validation": {"seasons": seasons, "metrics": vm, "predictions": validation},
        "holdout": {
            **protocol["holdout"],
            "targets": len(held) // 4,
            "forecasts": len(held),
            "metrics": hm,
            "predictions": held,
            "peak_diagnostics": peak_diagnostics(held, protocol),
        },
        "publication": {
            "status": "research_projection" if publish else "withheld",
            "reason": (
                "Validation winner beat both simple baselines on pooled holdout MAE."
                if publish
                else "Validation winner did not strictly beat both simple baselines on pooled holdout MAE."
            ),
        },
        "forecast": future,
    }


def build_artifact(raw, protocol):
    digest = hashlib.sha256(raw).hexdigest()
    if digest != protocol["source"]["sha256"]:
        raise ValueError("Pinned FluSight source checksum mismatch")
    history, quality = parse_source(raw)
    if (
        history[0]["week"] != protocol["source"]["first_week"]
        or history[-1]["week"] != protocol["source"]["last_week"]
    ):
        raise ValueError("Source dates differ from frozen protocol")
    now = datetime.now(timezone.utc).isoformat()
    source = protocol["source"]
    return {
        "schema_version": 1,
        "dataset_kind": "observed_public_aggregate",
        "generated_at": now,
        "source": {
            "name": "CDC/NHSN via FluSight",
            "repository": source["repository"],
            "revision": source["revision"],
            "sha256": digest,
            "data_url": f"{source['repository']}/blob/{source['revision']}/{source['path']}",
            "documentation_url": f"{source['repository']}/blob/{source['revision']}/target-data/README.md",
            "license_url": f"{source['repository']}/blob/{source['revision']}/LICENSE",
            "license_notice": (ROOT / "docs/licenses/FluSight-MIT.txt").read_text(),
            "attribution": "Observed laboratory-confirmed influenza hospital admissions reported to CDC/NHSN, distributed by the CDC FluSight Forecast Hub. No source model forecasts are used. No CDC endorsement is implied.",
            "verified_at": now,
            "quality": quality,
        },
        "protocol": protocol,
        "protocol_sha256": hashlib.sha256(canonical(protocol)).hexdigest(),
        "series_sha256": hashlib.sha256(canonical(history)).hexdigest(),
        "history": history,
        "evaluation": evaluate(history, protocol),
        "limitations": [
            "Retrospective evaluation uses a pinned revised dataset, not historical release vintages.",
            "Reporting requirements, coverage, and data revisions can change counts.",
            "Origins assume observations are available at week end; publication delays are not replayed.",
            "Horizon/weekly errors overlap and are correlated; this is one held-out season.",
            "These are reported influenza admissions, not unique patients or all-cause facility demand.",
            "Point forecasts have no estimated uncertainty intervals and are not an operational planning service.",
        ],
    }


def atomic_write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        staged = Path(f.name)
        f.write(raw)
    try:
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, help="Local copy of the exact pinned public CSV"
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Fetch the fixed public GitHub source; no credentials or paid calls",
    )
    args = parser.parse_args()
    if args.input and args.download:
        parser.error("Choose --input or --download")
    protocol = json.loads(PROTOCOL_PATH.read_text())
    source = protocol["source"]
    cache = ROOT / "data/external/flusight" / f"{source['revision']}.csv"
    output = ROOT / "data/public/flusight.json"
    if not output.resolve().is_relative_to(
        ROOT / "data/public"
    ) or not cache.resolve().is_relative_to(ROOT / "data/external/flusight"):
        raise ValueError("Outputs must remain in ignored local data directories")
    if args.download:
        url = f"https://raw.githubusercontent.com/cdcepi/FluSight-forecast-hub/{source['revision']}/{source['path']}"
        with urlopen(url, timeout=30) as response:
            raw = response.read(MAX_BYTES + 1)
    else:
        with (args.input or cache).open("rb") as source_file:
            raw = source_file.read(MAX_BYTES + 1)
    artifact = build_artifact(raw, protocol)
    if args.download:
        atomic_write(cache, raw)
    atomic_write(
        output, json.dumps(artifact, indent=2, allow_nan=False).encode() + b"\n"
    )
    print("Verified public influenza benchmark written to data/public/flusight.json.")


if __name__ == "__main__":
    main()
