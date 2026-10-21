from datetime import date, datetime, timezone
import hashlib
import json
import math
from statistics import mean


MODELS = ("seasonal_naive", "seasonal_mean", "seasonal_trend")


def shift_month(value, offset):
    month = date.fromisoformat(str(value))
    year, index = divmod(month.year * 12 + month.month - 1 + offset, 12)
    return date(year, index + 1, 1).isoformat()


def predict(values, model, horizon=12):
    if model not in MODELS or len(values) < 60:
        raise ValueError("Forecast models require at least 60 observed months")
    history = values[-60:]
    if model == "seasonal_naive":
        return [float(history[-12 + i % 12]) for i in range(horizon)]
    groups = [[i for i in range(60) if i % 12 == month] for month in range(12)]
    slope = 0.0
    if model == "seasonal_trend":
        # Within-month demeaning separates a shared trend from month effects.
        numerator = sum((i - mean(g)) * (history[i] - mean(history[j] for j in g))
                        for g in groups for i in g)
        denominator = sum((i - mean(g)) ** 2 for g in groups for i in g)
        slope = numerator / denominator
    intercepts = [mean(history[i] - slope * i for i in g) for g in groups]
    return [max(0.0, intercepts[i % 12] + slope * (60 + i)) for i in range(horizon)]


def score(actual, predicted, model):
    errors = [abs(a - p) for a, p in zip(actual, predicted, strict=True)]
    return {"model": model, "mae": mean(errors),
            "rmse": math.sqrt(mean(e * e for e in errors)),
            "wape": sum(errors) / sum(actual) if sum(actual) else None}


def evaluate_forecast(monthly):
    result = {"status": "not_evaluated", "predictions": None, "held_out_metrics": None,
              "evaluation": None, "reason": "Need 120 consecutive observed months; missing months are not filled with zero."}
    if len(monthly) < 120:
        return result
    series = monthly[-120:]
    if any(str(row["encounter_month"]) != shift_month(series[0]["encounter_month"], i)
           for i, row in enumerate(series)):
        return result
    values = [row["encounter_count"] for row in series]
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("Forecast counts must be nonnegative integers")
    training, actual = values[:-12], values[-12:]
    folds, validation_actual = [], []
    validation_predictions = {model: [] for model in MODELS}
    for stop in (len(training) - 36, len(training) - 24, len(training) - 12):
        validation_actual.extend(training[stop:stop + 12])
        folds.append({"training_end": str(series[stop - 1]["encounter_month"]),
                      "test_start": str(series[stop]["encounter_month"]),
                      "test_end": str(series[stop + 11]["encounter_month"])})
        for model in MODELS:
            validation_predictions[model].extend(predict(training[:stop], model))
    validation_metrics = [score(validation_actual, validation_predictions[model], model) for model in MODELS]
    selected = min(validation_metrics, key=lambda row: row["mae"])["model"]
    held_out = {model: predict(training, model) for model in MODELS}
    metrics = [score(actual, held_out[model], model) for model in MODELS]
    baseline_mae = metrics[0]["mae"]
    selected_mae = next(row["mae"] for row in metrics if row["model"] == selected)
    publish = selected != "seasonal_naive" and selected_mae < baseline_mae
    canonical = [{"month": str(row["encounter_month"]), "count": row["encounter_count"]} for row in series]
    result.update(status="evaluated", held_out_metrics=metrics,
                  reason=("The validation-selected model beat seasonal naive on held-out MAE."
                          if publish else "Future predictions withheld: the validation-selected model did not beat seasonal naive on held-out MAE."),
                  evaluation={
                      "protocol": "rolling_origin_v1", "evaluated_at": datetime.now(timezone.utc).isoformat(),
                      "input_sha256": hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                      "training_start": str(series[0]["encounter_month"]),
                      "training_end": str(series[-13]["encounter_month"]),
                      "test_start": str(series[-12]["encounter_month"]),
                      "test_end": str(series[-1]["encounter_month"]),
                      "training_months": 108, "model_window_months": 60,
                      "folds": folds, "validation_metrics": validation_metrics,
                      "selected_model": selected, "selected_beats_baseline": publish,
                      "test_predictions": [
                          {"encounter_month": str(row["encounter_month"]), "actual": actual[i],
                           **{model: held_out[model][i] for model in MODELS}}
                          for i, row in enumerate(series[-12:])],
                  })
    if publish:
        result["predictions"] = [
            {"encounter_month": shift_month(series[-1]["encounter_month"], i + 1), "value": value}
            for i, value in enumerate(predict(values, selected))]
    return result
